"""在全部官方 train 上训一个最终编码器（无留出），然后给目标 CSV 打分。"""
import argparse, numpy as np, pandas as pd, torch, torch.nn as nn, torch.nn.functional as F
from transformers import AutoTokenizer, Qwen3Model, get_linear_schedule_with_warmup

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--train_csv", required=True)
ap.add_argument("--score_csv", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--epochs", type=int, default=4); ap.add_argument("--bs", type=int, default=16)
ap.add_argument("--lr", type=float, default=3e-5); ap.add_argument("--max_tokens", type=int, default=320)
a = ap.parse_args()

tr = pd.read_csv(a.train_csv); sc = pd.read_csv(a.score_csv)
labels = sorted(tr["model"].unique().tolist()); L = {l: i for i, l in enumerate(labels)}
y = tr["model"].map(L).values
print("train", len(tr), "score", len(sc), "classes", len(labels), flush=True)
tok = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True)

def embed(model, texts, bs=16):
    out = []
    for i in range(0, len(texts), bs):
        enc = tok(texts[i:i + bs], padding=True, truncation=True, max_length=a.max_tokens,
                  return_tensors="pt").to("cuda")
        h = model(**enc).last_hidden_state
        last = enc["attention_mask"].sum(1) - 1
        out.append(h[torch.arange(len(enc["input_ids"]), device="cuda"), last].float())
    return torch.cat(out)

torch.manual_seed(0)
model = Qwen3Model.from_pretrained(a.model, dtype=torch.bfloat16, trust_remote_code=True).to("cuda")
head = nn.Linear(model.config.hidden_size, len(labels)).to("cuda")
opt = torch.optim.AdamW(list(model.parameters()) + list(head.parameters()), lr=a.lr, weight_decay=0.01)
steps = (len(tr) // a.bs + 1) * a.epochs
sch = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
rng = np.random.RandomState(0)
for ep in range(a.epochs):
    model.train(); tot = 0.0; nb = 0
    idx = rng.permutation(len(tr))
    for i in range(0, len(idx), a.bs):
        b = idx[i:i + a.bs]
        enc = tok(tr["text"].iloc[b].fillna("").tolist(), padding=True, truncation=True,
                  max_length=a.max_tokens, return_tensors="pt").to("cuda")
        h = model(**enc).last_hidden_state
        last = enc["attention_mask"].sum(1) - 1
        e = h[torch.arange(len(b), device="cuda"), last].float()
        loss = F.cross_entropy(head(e / e.norm(dim=-1, keepdim=True)), torch.tensor(y[b], device="cuda"))
        opt.zero_grad(); loss.backward(); opt.step(); sch.step()
        tot += float(loss); nb += 1
    print(f"ep {ep} loss {tot/max(nb,1):.4f}", flush=True)

model.eval()
with torch.no_grad():
    e = embed(model, sc["text"].fillna("").tolist()); e = e / e.norm(dim=-1, keepdim=True)
    P = torch.softmax(head(e), -1).float().cpu().numpy()
np.savez(a.out, probs=P, ids=sc["id"].astype(str).values, labels=np.array(labels))
print("saved", a.out, P.shape, flush=True)
