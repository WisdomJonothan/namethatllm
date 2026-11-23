"""对 eval_v2 全量文本按给定 3 个作者条件打分（含作者不在候选集的文本），用于测'家族判别'能力。"""
import argparse, csv, json
import numpy as np, torch
from transformers import AutoModelForCausalLM, AutoTokenizer

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--csv", required=True)
ap.add_argument("--labels", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--max_rows", type=int, default=0); ap.add_argument("--max_tokens", type=int, default=320)
a = ap.parse_args()
labels = [x.strip() for x in a.labels.split(",")]

rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
if a.max_rows: rows = rows[: a.max_rows]
tok = AutoTokenizer.from_pretrained(a.model)
model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map="cuda").eval()
print("rows", len(rows), "labels", labels, flush=True)

S = np.zeros((len(rows), len(labels)), dtype=np.float32)
for i, r in enumerate(rows):
    for j, lab in enumerate(labels):
        p = tok(f"Written by {lab}:\n\n", return_tensors="pt", add_special_tokens=False).input_ids.cuda()
        t = tok((r["text"] or " "), return_tensors="pt", add_special_tokens=False).input_ids[:, : a.max_tokens].cuda()
        ids = torch.cat([p, t], 1)
        with torch.no_grad():
            lg = model(ids).logits[:, p.shape[1] - 1: -1, :].float()
        lp = torch.log_softmax(lg, -1)
        S[i, j] = float(lp.gather(-1, ids[:, p.shape[1]:].unsqueeze(-1)).mean())
    if (i + 1) % 200 == 0: print(i + 1, "/", len(rows), flush=True)

np.savez(a.out, scores=S, labels=np.array(labels), ids=np.array([r["id"] for r in rows]),
         gold=np.array([r["model"] for r in rows]), prompt=np.array([r.get("prompt_id", "") for r in rows]))
print("saved", a.out, flush=True)
