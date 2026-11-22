#!/usr/bin/env python
"""错例表：原版 vs ext45 在**同一批独立样本**上的逐条对照。

第一张表只看三个数（每个模型对）：
  1) 原版把 A 判成 B、把 B 判成 A 的次数（互认错）
  2) 其中 ext45 判对的次数（救回）
  3) ext45 介入后把原本正确的 A/B 判错的次数（改错）
并按 balanced accuracy 的净贡献排序：net = (救回 - 改错) * 2 / 18 / N

用法：
  python scripts/pair_error_table.py \
      --orig-npz results/repro/final-logits.npz \
      --ext-probs results/ext45_probs.parquet \
      --truth data/eval_truth.csv \
      --out results/pair_error_table.json

输入约定：
  orig-npz   : np.savez(ids=..., labels=[18], logits=(n,18))，原版 --save_final_logits 的直接产物
  ext-probs  : parquet，列 id + 18 个模型名（概率；或 logits，加 --ext-logits）
  truth      : csv，列 id, model
输出：
  - 逐对表（互错 / 救回 / 改错 / net）
  - 每对的样本 id 清单，便于人工看改对/改错的具体文本
  - 若提供 --group-col，附加"组内可介入"口径（同 prompt 的 top-2 都是这一对）
"""
import argparse, json, os
from collections import Counter, defaultdict

import numpy as np


def load_orig(path):
    z = np.load(path)
    ids = [str(x) for x in z["ids"]]
    labels = [str(x) for x in z["labels"]]
    logits = np.asarray(z["logits"], dtype=np.float64)
    assert logits.shape == (len(ids), len(labels)), (logits.shape, len(ids), len(labels))
    return ids, labels, logits


def load_ext(path, ids_ref, labels, is_logits=False):
    import pandas as pd
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    df["id"] = df["id"].astype(str)
    df = df.set_index("id").reindex(ids_ref)
    missing = df[labels].isna().any(axis=1).sum()
    assert missing == 0, f"ext 缺少 {missing} 条样本"
    m = df[labels].to_numpy(dtype=np.float64)
    if not is_logits:
        m = np.log(np.clip(m, 1e-12, None))
    return m


def load_truth(path):
    import pandas as pd
    df = pd.read_csv(path)
    return dict(zip(df["id"].astype(str), df["model"].astype(str)))


def balanced_accuracy(pred_idx, true_idx, n_class):
    recalls = []
    for c in range(n_class):
        m = true_idx == c
        if m.sum() == 0:
            continue
        recalls.append((pred_idx[m] == c).mean())
    return float(np.mean(recalls)), recalls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig-npz", required=True)
    ap.add_argument("--ext-probs", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--ext-logits", action="store_true")
    ap.add_argument("--out", default="results/pair_error_table.json")
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--group-col", default="", help="同 prompt 分组列（可选，用于组内可介入口径）")
    args = ap.parse_args()

    ids, labels, orig_logits = load_orig(args.orig_npz)
    ext_logits = load_ext(args.ext_probs, ids, labels, is_logits=args.ext_logits)
    truth = load_truth(args.truth)
    lab2i = {l: i for i, l in enumerate(labels)}

    keep = [i for i, _id in enumerate(ids) if _id in truth]
    ids = [ids[i] for i in keep]
    orig_logits = orig_logits[keep]
    ext_logits = ext_logits[keep]
    y = np.array([lab2i[truth[_id]] for _id in ids])

    o = orig_logits.argmax(1)
    e = ext_logits.argmax(1)

    ba_o, rec_o = balanced_accuracy(o, y, len(labels))
    ba_e, rec_e = balanced_accuracy(e, y, len(labels))

    pairs = defaultdict(lambda: {"orig_confuse": 0, "rescued": 0, "broken": 0,
                                 "rescued_ids": [], "broken_ids": [], "confuse_ids": []})
    for k in range(len(ids)):
        # 原版互认错：真实 A 判 B 或真实 B 判 A
        if o[k] != y[k]:
            a, b = tuple(sorted((int(y[k]), int(o[k]))))
            key = (labels[a], labels[b])
            pairs[key]["orig_confuse"] += 1
            pairs[key]["confuse_ids"].append(ids[k])
            if e[k] == y[k]:
                pairs[key]["rescued"] += 1
                pairs[key]["rescued_ids"].append(ids[k])
        # ext45 把正确的改错，且改到这一对里的另一个
        if o[k] == y[k] and e[k] != y[k]:
            a, b = tuple(sorted((int(y[k]), int(e[k]))))
            pairs[(labels[a], labels[b])]["broken"] += 1
            pairs[(labels[a], labels[b])]["broken_ids"].append(ids[k])

    rows = []
    n = len(ids)
    for (A, B), d in pairs.items():
        net = d["rescued"] - d["broken"]
        rows.append({
            "pair": [A, B],
            "orig_confuse": d["orig_confuse"],
            "rescued": d["rescued"],
            "broken": d["broken"],
            "net_hits": net,
            # 一对模型各 +1 次召回 ≈ 2/18/N 的 BA
            "net_ba_delta": round(net * 2.0 / len(labels) / n, 6),
            "rescued_ids": d["rescued_ids"][:50],
            "broken_ids": d["broken_ids"][:50],
        })
    rows.sort(key=lambda r: (-r["net_hits"], -r["orig_confuse"]))

    out = {
        "n_samples": n,
        "orig_balanced_accuracy": round(ba_o, 5),
        "ext_balanced_accuracy": round(ba_e, 5),
        "delta_ba": round(ba_e - ba_o, 5),
        "orig_recall": {labels[i]: round(r, 4) for i, r in enumerate(rec_o)},
        "ext_recall": {labels[i]: round(r, 4) for i, r in enumerate(rec_e)},
        "pairs_top": rows[: args.topk],
        "pair_count": len(rows),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: out[k] for k in ["n_samples", "orig_balanced_accuracy",
                                          "ext_balanced_accuracy", "delta_ba"]},
                     ensure_ascii=False, indent=2))
    print(f"\n{'pair':44s} {'互错':>4s} {'救回':>4s} {'改错':>4s} {'净':>4s}")
    for r in rows[: args.topk]:
        print(f"{r['pair'][0]:>22s} ↔ {r['pair'][1]:<20s} {r['orig_confuse']:4d} "
              f"{r['rescued']:4d} {r['broken']:4d} {r['net_hits']:+4d}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
