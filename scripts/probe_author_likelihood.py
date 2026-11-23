"""作者条件似然探针：同一段文本，分别以「候选作者」为条件前缀，算正文的平均 token log 概率，
取最大者判别。用来检验「预训练 LM 的作者条件似然」是否携带分类头以外的跨家族信息。

用法（服务器 GPU）：
  python probe_author_likelihood.py --model /tmp/reteco-qwen4b.xxx --csv trios.jsonl --out probe.json
trios.jsonl 每行 {"id":..., "text":..., "model":"真作者", "prompt_id":...}，labels 由 --labels 指定。
"""
import argparse, json
from collections import Counter, defaultdict

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--labels", required=True, help="逗号分隔的候选作者")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max_rows", type=int, default=0)
    ap.add_argument("--max_tokens", type=int, default=320)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    labels = [x.strip() for x in a.labels.split(",")]

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = [json.loads(l) for l in open(a.csv, encoding="utf-8")]
    rows = [r for r in rows if r["model"] in labels]
    if a.max_rows:
        rows = rows[: a.max_rows]

    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map="cuda")
    model.eval()

    def score(text, author):
        prefix = f"Written by {author}:\n\n"
        p_ids = tok(prefix, return_tensors="pt", add_special_tokens=False).input_ids.to("cuda")
        t_ids = tok(text or " ", return_tensors="pt", add_special_tokens=False).input_ids.to("cuda")
        t_ids = t_ids[:, : a.max_tokens]
        ids = torch.cat([p_ids, t_ids], dim=1)
        with torch.no_grad():
            out = model(ids)
            logits = out.logits[:, p_ids.shape[1] - 1: -1, :].float()
        lp = torch.log_softmax(logits, dim=-1)
        tgt = ids[:, p_ids.shape[1]:]
        got = lp.gather(-1, tgt.unsqueeze(-1)).squeeze(-1)
        return float(got.mean().item()), int(tgt.numel())

    preds, golds, detail = [], [], []
    for i, r in enumerate(rows):
        s = [score(r["text"], lab)[0] for lab in labels]
        p = int(np.argmax(s))
        preds.append(p); golds.append(labels.index(r["model"]))
        detail.append({"id": r.get("id"), "gold": r["model"], "pred": labels[p], "scores": s})
        if (i + 1) % 50 == 0:
            acc = float(np.mean([p_ == g_ for p_, g_ in zip(preds, golds)]))
            print(f"{i+1}/{len(rows)} acc={acc:.4f}", flush=True)

    preds, golds = np.array(preds), np.array(golds)
    rec = [float((preds[golds == c] == c).mean()) if (golds == c).any() else 0.0 for c in range(len(labels))]
    conf = Counter((labels[g], labels[p]) for g, p in zip(golds, preds))
    res = {
        "model": a.model, "labels": labels, "n": len(rows),
        "acc": float((preds == golds).mean()), "macro_recall": float(np.mean(rec)),
        "recall": {labels[i]: rec[i] for i in range(len(labels))},
        "confusion": {f"{g}->{p}": c for (g, p), c in conf.most_common()},
        "chance": 1.0 / len(labels),
    }
    print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
    json.dump({"summary": res, "detail": detail}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
