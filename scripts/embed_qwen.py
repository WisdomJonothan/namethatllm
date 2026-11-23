import argparse, numpy as np, pandas as pd, torch
from transformers import AutoTokenizer, AutoModel

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--csv", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--max_tokens", type=int, default=320)
a = ap.parse_args()
df = pd.read_csv(a.csv)
texts = [str(t) if isinstance(t, str) else "" for t in df["text"]]
tok = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True)
model = AutoModel.from_pretrained(a.model, dtype=torch.bfloat16, device_map="cuda", trust_remote_code=True).eval()

def run(batch):
    enc = tok(batch, padding=True, truncation=True, max_length=a.max_tokens, return_tensors="pt").to("cuda")
    with torch.no_grad():
        out = model(**enc).last_hidden_state
    last = enc["attention_mask"].sum(1) - 1
    e = out[torch.arange(out.shape[0], device=out.device), last].float()
    return torch.nn.functional.normalize(e, dim=-1).cpu().numpy()

X = []
for i in range(0, len(texts), 16):
    X.append(run(texts[i:i + 16]))
    if (i // 16) % 20 == 0: print(i, "/", len(texts), flush=True)
X = np.concatenate(X, 0).astype(np.float32)
np.savez(a.out, X=X, ids=df["id"].astype(str).values,
         y=df["model"].astype(str).values,
         prompt=df["prompt_id"].astype(str).values if "prompt_id" in df else np.array([""] * len(df)))
print("saved", a.out, X.shape, flush=True)
