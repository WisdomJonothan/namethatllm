"""GPT-5.6 luna/terra/sol 三兄弟专家：用 open pool 的同题配对数据训练，在 eval_v2 上试跑。

目的：回答“已有的同题数据能不能兑现前二组合理想上限的一部分”，而不是先花钱买新数据。

- 训练集：open pool 中同时出现 luna/terra/sol 的 prompt 组（按 prompt_group 分组切分）
- 评估集：eval_v2 中最终预测前两名恰为 (luna,terra) 或 (luna,sol) 的行
- 指标：整场 balanced accuracy 的增量（只改这些行），与理想上限对比

用法：python scripts/gpt_trio_expert.py
产出：results/gpt_trio_expert.json
"""
import itertools
import json
import os

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIO = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]


def load_pool():
    p = pd.read_parquet(os.path.join(ROOT, "data", "pool", "ntl_open_pool.parquet"))
    q = p[p.model.isin(TRIO)].copy()
    cnt = q.groupby("prompt_group").model.nunique()
    full = set(cnt[cnt == 3].index)
    q = q[q.prompt_group.isin(full)]
    return q[["text", "model", "prompt_group"]].reset_index(drop=True)


def build_model():
    feats = FeatureUnion([
        ("ch", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_features=200000,
                               sublinear_tf=True)),
        ("wd", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, max_features=200000,
                               sublinear_tf=True, lowercase=True)),
    ])
    return feats, LogisticRegression(C=4.0, max_iter=2000, multi_class="multinomial")


def main():
    q = load_pool()
    prompts = sorted(q.prompt_group.unique())
    rng = np.random.RandomState(0)
    rng.shuffle(prompts)
    cut = int(len(prompts) * 0.8)
    tr_p, va_p = set(prompts[:cut]), set(prompts[cut:])
    tr = q[q.prompt_group.isin(tr_p)]
    va = q[q.prompt_group.isin(va_p)]

    feats, clf = build_model()
    Xtr = feats.fit_transform(tr.text.fillna("").astype(str))
    clf.fit(Xtr, tr.model.values)
    va_pred = clf.predict(feats.transform(va.text.fillna("").astype(str)))
    pool_val_acc = float((va_pred == va.model.values).mean())

    # ---- eval_v2 side ----
    fl = np.load(os.path.join(ROOT, "runs", "diag1", "final-logits.npz"), allow_pickle=True)
    ids = [str(x) for x in fl["ids"].tolist()]
    labels = [str(x) for x in fl["labels"].tolist()]
    z = np.asarray(fl["logits"], dtype=np.float64)
    ev = pd.read_csv(os.path.join(ROOT, "data", "eval_v2.csv"), dtype={"id": str})
    shipped = pd.read_parquet(os.path.join(ROOT, "runs", "diag1", "diag_pred.parquet"))
    smap = dict(zip(shipped.id.astype(str), shipped.model))
    ev = ev.set_index("id").loc[ids].reset_index()
    y = ev.model.values
    pred = np.array([smap[i] for i in ids], dtype=object)
    n_cls = {l: int((y == l).sum()) for l in labels}

    def ba(p):
        return float(np.mean([(p[y == l] == l).mean() for l in labels if (y == l).sum()]))

    top2 = np.argsort(-z, axis=1)[:, :2]
    pair = np.array([frozenset([labels[a], labels[b]]) for a, b in top2], dtype=object)

    probs = clf.predict_proba(feats.transform(ev.text.fillna("").astype(str)))
    trio_idx = [list(clf.classes_).index(t) for t in TRIO]
    probs = probs[:, trio_idx]

    base_ba = ba(pred)
    out = {"pool_val_acc": round(pool_val_acc, 4), "base_ba_eval_v2": round(base_ba, 4),
           "pairs": {}, "n_rows_in_pairs": int(sum(p == frozenset(x) for p in pair
                                                    for x in itertools.combinations(TRIO, 2)))}

    # paired subsets: (luna,terra) and (luna,sol)
    for a, b in [("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol")]:
        key = frozenset([a, b])
        m = np.array([(p == key) for p in pair]) & np.isin(pred, [a, b])
        idx = np.where(m)[0]
        # ideal ceiling: fix every row whose truth is in the pair but prediction wrong
        ideal = sum(1.0 / n_cls[y[i]] for i in idx if pred[i] != y[i] and y[i] in (a, b)) / 18
        # specialist prediction restricted to the pair
        col = {lab: k for k, lab in enumerate(TRIO)}
        sub = probs[idx][:, [col[a], col[b]]]
        spec = np.where(sub[:, 0] >= sub[:, 1], a, b)
        newp = pred.copy()
        newp[idx] = spec
        gain = ba(newp) - base_ba
        # per-class effect on the two siblings + luna guard
        detail = {}
        for lab in (a, b):
            mm = y == lab
            detail[lab] = {
                "n": int(mm.sum()),
                "acc_before": round(float((pred[mm] == lab).mean()), 4),
                "acc_after": round(float((newp[mm] == lab).mean()), 4),
            }
        out["pairs"][f"{a}|{b}"] = {
            "rows": int(len(idx)),
            "ideal_ceiling": round(ideal, 4),
            "specialist_gain": round(gain, 4),
            "realised_fraction": round(gain / ideal, 3) if ideal > 0 else None,
            "per_class": detail,
        }

    out["combined_specialist_gain"] = round(sum(v["specialist_gain"] for v in out["pairs"].values()), 4)
    out["combined_ceiling"] = round(sum(v["ideal_ceiling"] for v in out["pairs"].values()), 4)

    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    with open(os.path.join(ROOT, "results", "gpt_trio_expert.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
