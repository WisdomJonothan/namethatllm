"""思路一 v2：Qwen3-Emb 微调 + 同题硬负例三元组（anchor, 同作者异题, 同题异作者）。"""
import argparse, json, numpy as np, pandas as pd, torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoTokenizer, get_linear_schedule_with_warmup
try:
    from transformers import Qwen3Model as BaseModel
except Exception:
    from transformers import AutoModel as BaseModel

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--csv", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--epochs", type=int, default=4)
ap.add_argument("--lr", type=float, default=3e-5); ap.add_argument("--max_tokens", type=int, default=320)
ap.add_argument("--folds", default="0"); ap.add_argument("--n_prompts", type=int, default=24)
ap.add_argument("--lam", type=float, default=0.5); ap.add_argument("--margin", type=float, default=0.30)
a = ap.parse_args()

df = pd.read_csv(a.csv).reset_index(drop=True)
labels = sorted(df["model"].unique().tolist()); L = {l: i for i, l in enumerate(labels)}
y = df["model"].map(L).values
prompts = sorted(df["prompt_id"].unique()); rng = np.random.RandomState(0); rng.shuffle(prompts)
fof = {p: i % 5 for i, p in enumerate(prompts)}
fold = np.array([fof[p] for p in df["prompt_id"]])
by_prompt = {p: g.index.values for p, g in df.groupby("prompt_id")}
by_pa = {}
for (p, m), g in df.groupby(["prompt_id", "model"]).groups.items():
    by_pa[(p, m)] = list(g)
by_author = {m: list(g.index.values) for m, g in df.groupby("model")}

tok = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True)

def embed(model, idx):
    enc = tok(df["text"].iloc[idx].fillna("").tolist(), padding=True, truncation=True,
              max_length=a.max_tokens, return_tensors="pt").to("cuda")
    out = model(**enc).last_hidden_state
    last = enc["attention_mask"].sum(1) - 1
    return out[torch.arange(len(idx), device="cuda"), last].float()

def make_batch(tr_idx, tr_set):
    """返回 (idx, y)：每组 = anchor + 同作者异题 + 同题异作者。"""
    idx, lab = [], []
    ps = [p for p in prompts if fof[p] != None and all(i in tr_set for i in by_prompt[p])]
    if not ps: return None, None
    pick = rng.choice(len(ps), size=min(a.n_prompts, len(ps)), replace=False)
    for k in pick:
        p = ps[k]; rows = by_prompt[p]
        if len(rows) < 2: continue
        i1, i2 = rng.choice(len(rows), size=2, replace=False)
        i1, i2 = rows[i1], rows[i2]
        y1 = df["model"].iloc[i1]
        cand = [j for j in by_author[y1] if j != i1 and df["prompt_id"].iloc[j] != p and j in tr_set]
        if not cand: continue
        pos = int(rng.choice(cand))
        idx += [i1, i2, pos]; lab += [int(y[i1]), int(y[i2]), int(y[i1])]
    return (np.array(idx) if idx else None), (np.array(lab) if lab else None)

folds = [int(x) for x in a.folds.split(",")]
P = np.zeros((len(df), len(labels)), dtype=np.float32)
for f in folds:
    torch.manual_seed(0)
    model = BaseModel.from_pretrained(a.model, dtype=torch.bfloat16, trust_remote_code=True).to("cuda")
    head = nn.Linear(model.config.hidden_size, len(labels)).to("cuda")
    opt = torch.optim.AdamW(list(model.parameters()) + list(head.parameters()), lr=a.lr, weight_decay=0.01)
    tr_idx = np.where(fold != f)[0]; tr_set = set(tr_idx.tolist())
    steps = max(1, (len(tr_idx) // (3 * a.n_prompts))) * a.epochs
    sch = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    model.train()
    for ep in range(a.epochs):
        tot = ce = tr = 0.0; nb = 0
        for _ in range(steps // a.epochs):
            idx, lab = make_batch(tr_idx, tr_set)
            if idx is None: continue
            e = embed(model, idx); e = e / e.norm(dim=-1, keepdim=True)
            logits = head(e)
            l_ce = F.cross_entropy(logits, torch.tensor(lab, device="cuda"))
            ea, ep_, en = e[0::3], e[1::3], e[2::3]
            d_pos = 1 - (ea * ep_).sum(-1); d_neg = 1 - (ea * en).sum(-1)
            l_tr = F.relu(a.margin + d_pos - d_neg).mean()
            loss = l_ce + a.lam * l_tr
            opt.zero_grad(); loss.backward(); opt.step(); sch.step()
            tot += float(loss); ce += float(l_ce); tr += float(l_tr); nb += 1
        print(f"fold {f} ep {ep} loss {tot/max(nb,1):.4f} ce {ce/max(nb,1):.4f} tri {tr/max(nb,1):.4f}", flush=True)
    model.eval()
    with torch.no_grad():
        for i in range(0, len(df), 32):
            b = np.arange(i, min(i + 32, len(df)))
            e = embed(model, b); e = e / e.norm(dim=-1, keepdim=True)
            P[b] = torch.softmax(head(e), -1).float().cpu().numpy()
    sel = np.where(fold == f)[0]; pred = P[sel].argmax(1)
    rec = [float((pred[y[sel] == c] == c).mean()) for c in range(len(labels))]
    print(f"fold {f} BA {np.mean(rec):.4f} acc {float((pred==y[sel]).mean()):.4f}", flush=True)
    del model, head; torch.cuda.empty_cache()
np.savez(a.out, probs=P, y=y, labels=np.array(labels), folds=fold, recall=np.array(rec))
print("saved", a.out, flush=True)
