"""用两个公开 LM 给每篇文本算逐 token 惊讶度，压成可解释特征（含跨模型分歧）。"""
import argparse, numpy as np, pandas as pd, torch
from transformers import AutoTokenizer, AutoModelForCausalLM

ap = argparse.ArgumentParser()
ap.add_argument("--texts", required=True); ap.add_argument("--text_col", default="text")
ap.add_argument("--modelA", required=True); ap.add_argument("--modelB", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--max_tokens", type=int, default=320)
ap.add_argument("--bs", type=int, default=8); ap.add_argument("--bins", type=int, default=20)
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()

df = pd.read_csv(a.texts)
if a.limit: df = df.head(a.limit)
texts = df[a.text_col].fillna("").astype(str).tolist()
print("texts", len(texts), flush=True)

def profile(path):
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    m = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, trust_remote_code=True).to("cuda").eval()
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    prof = [None] * len(texts)
    for i in range(0, len(texts), a.bs):
        batch = texts[i:i + a.bs]
        enc = tok(batch, padding=True, truncation=True, max_length=a.max_tokens, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = m(**enc).logits.float()
        logp = torch.log_softmax(out[:, :-1], -1)
        tgt = enc["input_ids"][:, 1:]
        lp = logp.gather(-1, tgt.unsqueeze(-1)).squeeze(-1)
        mask = enc["attention_mask"][:, 1:].bool()
        for j in range(len(batch)):
            v = lp[j][mask[j]].cpu().numpy().astype(np.float64)
            prof[i + j] = v
        if i % 400 == 0: print("processed", i, flush=True)
    del m; torch.cuda.empty_cache()
    return prof

def feats(v, bins):
    f = {}
    if len(v) < 5:
        return {k: 0.0 for k in ["mean", "std", "q10", "q90", "frac3", "frac5", "maxrun3", "first", "last", "fl", "maxbin", "argmax", "minbin", "argmin"]}
    s = -v  # surprisal 正数
    f["mean"] = float(s.mean()); f["std"] = float(s.std())
    f["q10"], f["q90"] = float(np.quantile(s, .10)), float(np.quantile(s, .90))
    f["frac3"] = float((s > 3).mean()); f["frac5"] = float((s > 5).mean())
    run = mx = 0
    for x in (s > 3):
        run = run + 1 if x else 0; mx = max(mx, run)
    f["maxrun3"] = float(mx) / max(len(s), 1)
    edges = np.linspace(0, len(s), bins + 1).astype(int)
    bm = np.array([s[edges[k]:max(edges[k] + 1, edges[k + 1])].mean() for k in range(bins)])
    f["first"], f["last"] = float(bm[0]), float(bm[-1]); f["fl"] = f["last"] - f["first"]
    f["maxbin"], f["argmax"] = float(bm.max()), float(bm.argmax()) / bins
    f["minbin"], f["argmin"] = float(bm.min()), float(bm.argmin()) / bins
    return f, bm

print("model A", flush=True); pa = profile(a.modelA)
print("model B", flush=True); pb = profile(a.modelB)
rows = []
for k in range(len(texts)):
    fa, ba = feats(pa[k], a.bins); fb, bb = feats(pb[k], a.bins)
    r = {f"A_{x}": fa[x] for x in fa}; r.update({f"B_{x}": fb[x] for x in fb})
    if ba is not None and bb is not None:
        d = np.abs(ba - bb)
        r["X_corr"] = float(np.corrcoef(ba, bb)[0, 1]) if ba.std() > 1e-6 and bb.std() > 1e-6 else 0.0
        r["X_madiff"] = float(d.mean()); r["X_maxdiff"] = float(d.max()); r["X_stddiff"] = float((ba - bb).std())
        r["X_grp"] = r["A_mean"] - r["B_mean"]
    else:
        r["X_corr"] = r["X_madiff"] = r["X_maxdiff"] = r["X_stddiff"] = r["X_grp"] = 0.0
    r["n_tok"] = float(len(pa[k]) if pa[k] is not None else 0)
    rows.append(r)
out = pd.DataFrame(rows); out.insert(0, "id", df["id"].values)
out.to_csv(a.out, index=False)
print("saved", a.out, out.shape, flush=True)
