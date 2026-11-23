"""思路一 v1：微调 Qwen3-Embedding-0.6B（编码器 + 18 类线性头），prompt 分组 5 折折外 BA。"""
import argparse, json, numpy as np, pandas as pd, torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel, get_linear_schedule_with_warmup

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--csv", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--epochs", type=int, default=4)
ap.add_argument("--bs", type=int, default=16); ap.add_argument("--lr", type=float, default=3e-5)
ap.add_argument("--max_tokens", type=int, default=320)
a = ap.parse_args()

df = pd.read_csv(a.csv)
labels = sorted(df["model"].unique().tolist()); L = {l: i for i, l in enumerate(labels)}
y = df["model"].map(L).values
prompts = sorted(df["prompt_id"].unique()); rng = np.random.RandomState(0); rng.shuffle(prompts)
fof = {p: i % 5 for i, p in enumerate(prompts)}
fold = np.array([fof[p] for p in df["prompt_id"]])

tok = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True)

def batches(idx, bs, shuffle=False):
    idx = np.array(idx)
    if shuffle: idx = rng.permutation(idx)
    for i in range(0, len(idx), bs): yield idx[i:i + bs]

P = np.zeros((len(df), len(labels)), dtype=np.float32)
for f in range(5):
    torch.manual_seed(0)
    model = AutoModel.from_pretrained(a.model, dtype=torch.bfloat16, trust_remote_code=True).to("cuda")
    head = nn.Linear(model.config.hidden_size, len(labels)).to("cuda")
    opt = torch.optim.AdamW(list(model.parameters()) + list(head.parameters()), lr=a.lr, weight_decay=0.01)
    tr_idx = np.where(fold != f)[0]
    steps = (len(tr_idx) // a.bs + 1) * a.epochs
    sch = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    lossf = nn.CrossEntropyLoss()
    model.train()
    for ep in range(a.epochs):
        tot = 0.0
        for b in batches(tr_idx, a.bs, True):
            enc = tok(df["text"].iloc[b].fillna("").tolist(), padding=True, truncation=True,
                      max_length=a.max_tokens, return_tensors="pt").to("cuda")
            out = model(**enc).last_hidden_state
            last = enc["attention_mask"].sum(1) - 1
            e = out[torch.arange(len(b), device="cuda"), last].float()
            logits = head(e)
            loss = lossf(logits, torch.tensor(y[b], device="cuda"))
            opt.zero_grad(); loss.backward(); opt.step(); sch.step()
            tot += float(loss)
        print(f"fold {f} ep {ep} loss {tot/(len(tr_idx)//a.bs+1):.4f}", flush=True)
    model.eval()
    with torch.no_grad():
        for b in batches(np.where(fold == f)[0], 32):
            enc = tok(df["text"].iloc[b].fillna("").tolist(), padding=True, truncation=True,
                      max_length=a.max_tokens, return_tensors="pt").to("cuda")
            out = model(**enc).last_hidden_state
            last = enc["attention_mask"].sum(1) - 1
            e = out[torch.arange(len(b), device="cuda"), last].float()
            P[b] = torch.softmax(head(e), -1).float().cpu().numpy()
    pred = P[np.where(fold == f)[0]].argmax(1)
    rec = [float((pred[y[fold == f] == c] == c).mean()) for c in range(len(labels))]
    print(f"fold {f} BA {np.mean(rec):.4f} acc {float((pred==y[fold==f]).mean()):.4f}", flush=True)
    del model, head; torch.cuda.empty_cache()

pred = P.argmax(1)
rec = [float((pred[y == c] == c).mean()) for c in range(len(labels))]
print("OOF BA %.4f acc %.4f" % (np.mean(rec), float((pred == y).mean())), flush=True)
np.savez(a.out, probs=P, y=y, labels=np.array(labels), folds=fold, prompt_id=df["prompt_id"].values,
         recall=np.array(rec))
print("saved", a.out, flush=True)
