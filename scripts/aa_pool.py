"""作者适配器：120 篇/作者（官方 train）vs 3000 篇/作者（公开 pool）等折对照。"""
import json, time, numpy as np, pandas as pd, torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

BASE = "/homeC/wujiaheng/models-qwen3-emb06"; DEV = "cuda:0"; MAXLEN = 256
SIX = ['gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol','claude-opus-4-6','glm-5.3','kimi-k3']
tok = AutoTokenizer.from_pretrained(BASE)
if tok.pad_token is None: tok.pad_token = tok.eos_token

class DS(Dataset):
    def __init__(self, texts): self.t = texts
    def __len__(self): return len(self.t)
    def __getitem__(self, i):
        e = tok(self.t[i], truncation=True, max_length=MAXLEN, padding="max_length", return_attention_mask=True)
        ids = np.asarray(e["input_ids"]); am = np.asarray(e["attention_mask"])
        return torch.tensor(ids), torch.tensor(am)

def train_adapter(texts, steps_max=None, bs=16, epochs=1):
    base = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16, device_map={"": 0})
    cfg = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj","k_proj","v_proj","o_proj"], lora_dropout=0.0, task_type="CAUSAL_LM")
    m = get_peft_model(base, cfg); m.train()
    opt = torch.optim.AdamW([p for p in m.parameters() if p.requires_grad], lr=2e-4)
    dl = DataLoader(DS(texts), batch_size=bs, shuffle=True, num_workers=2, drop_last=True)
    n = 0
    for _ in range(epochs):
        for ids, am in dl:
            ids, am = ids.to(DEV), am.to(DEV)
            out = m(input_ids=ids, attention_mask=am, labels=ids)
            (out.loss).backward(); opt.step(); opt.zero_grad(set_to_none=True); n += 1
            if steps_max and n >= steps_max: break
        if steps_max and n >= steps_max: break
    m.eval(); return m, n

@torch.no_grad()
def score(m, texts, bs=16):
    res = []
    for i in range(0, len(texts), bs):
        chunk = texts[i:i+bs]
        e = tok(chunk, return_tensors="pt", truncation=True, max_length=MAXLEN, padding=True, return_attention_mask=True)
        ids, am = e["input_ids"].to(DEV), e["attention_mask"].to(DEV)
        lg = m(input_ids=ids, attention_mask=am).logits
        lp = torch.log_softmax(lg[:, :-1].float(), -1)
        ll = lp.gather(-1, ids[:, 1:].unsqueeze(-1)).squeeze(-1) * am[:, 1:].float()
        res.append((ll.sum(1) / am[:, 1:].float().sum(1).clamp(min=1)).float().cpu().numpy())
    return np.concatenate(res)

df = pd.read_csv("folds.csv"); df["text"] = df.text.fillna(" ").astype(str)
va = df[df.fold == 0].reset_index(drop=True)
mask = va.model.isin(SIX).values
vtext, vy = va.text[mask].tolist(), va.model[mask].values
print("eval subset", len(vtext), "of", len(va), flush=True)
pool = pd.read_csv("pool_trio3k.csv"); pool["text"] = pool.text.fillna(" ").astype(str)

out = {}
for tag, src in (("train120", None), ("pool3000", pool)):
    t0 = time.time(); mat = []
    for a in SIX:
        texts = (df[(df.model == a) & (df.fold != 0)].text.tolist() if src is None else pool[pool.model == a].text.tolist())
        steps = 15 if src is None else 1000
        m, n = train_adapter(texts, steps_max=steps)
        mat.append(score(m, vtext)); del m; torch.cuda.empty_cache()
        print(f"[{tag}] {a} texts={len(texts)} steps={n} {time.time()-t0:.0f}s", flush=True)
    M = np.array(mat).T; A = np.array(SIX)
    raw = float((A[M.argmax(1)] == vy).mean())
    C = M - M.mean(0, keepdims=True)
    cen = float((A[C.argmax(1)] == vy).mean())
    rec = {a: round(float((A[C.argmax(1)][vy == a] == a).mean()), 3) for a in SIX}
    out[tag] = dict(raw=raw, centered=cen, recall=rec, n=len(vy), secs=round(time.time()-t0))
    print(f"[{tag}] raw {raw:.4f} centered {cen:.4f} recall {rec}", flush=True)
json.dump(out, open("aa_pool.json", "w"), indent=1)
print("RANDOM 6-way 0.1667")
