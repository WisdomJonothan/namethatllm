"""按作者训练 LoRA 适配器，用"该作者真实文本下的平均对数概率"判别作者。"""
import argparse, json, time
import numpy as np, pandas as pd, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model

p = argparse.ArgumentParser()
p.add_argument('--model', required=True); p.add_argument('--csv', required=True)
p.add_argument('--fold', type=int, default=0); p.add_argument('--authors', default='all')
p.add_argument('--rank', type=int, default=8); p.add_argument('--epochs', type=int, default=1)
p.add_argument('--maxlen', type=int, default=256); p.add_argument('--bs', type=int, default=8)
p.add_argument('--sbs', type=int, default=16)
p.add_argument('--out', default='adapter_fold0.json')
a = p.parse_args()

df = pd.read_csv(a.csv); df['text'] = df.text.fillna(' ').astype(str)
auth = sorted(df.model.unique()) if a.authors == 'all' else a.authors.split(',')
tr = df[df.fold != a.fold]; va = df[df.fold == a.fold]
tok = AutoTokenizer.from_pretrained(a.model)
if tok.pad_token is None: tok.pad_token = tok.eos_token
base = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map={'': 0}).eval()
dev = 'cuda'

def enc(batch):
    e = tok(batch, return_tensors='pt', truncation=True, max_length=a.maxlen, padding=True)
    return {k: v.to(dev) for k, v in e.items()}

@torch.no_grad()
def score(model, texts):
    res = np.zeros(len(texts))
    for i in range(0, len(texts), a.sbs):
        b = enc(texts[i:i + a.sbs]); ids = b['input_ids']
        logits = model(**b).logits
        lp = torch.log_softmax(logits[:, :-1].float(), -1)
        msk = b['attention_mask'][:, 1:].float()
        ll = lp.gather(-1, ids[:, 1:].unsqueeze(-1)).squeeze(-1) * msk
        res[i:i + len(ids)] = (ll.sum(1) / msk.sum(1).clamp(min=1)).float().cpu().numpy()
    return res

t0 = time.time(); va_texts = va.text.tolist(); y = va.model.values
mat = np.zeros((len(va), len(auth)))
for j, an in enumerate(auth):
    sub = tr[tr.model == an]; texts = sub.text.tolist()
    m = get_peft_model(base, LoraConfig(r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.05,
                                        task_type='CAUSAL_LM',
                                        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj']))
    m.print_trainable_parameters() if j == 0 else None
    m.train()
    opt = torch.optim.AdamW([q for q in m.parameters() if q.requires_grad], lr=2e-4)
    steps = 0
    for ep in range(a.epochs):
        idx = np.random.RandomState(ep).permutation(len(texts))
        for i in range(0, len(texts), a.bs):
            b = enc([texts[k] for k in idx[i:i + a.bs]])
            loss = m(**b, labels=b['input_ids']).loss
            opt.zero_grad(); loss.backward(); opt.step(); steps += 1
    m.eval(); mat[:, j] = score(m, va_texts)
    print(f'[{time.time()-t0:7.1f}s] {j+1}/{len(auth)} {an} rows={len(sub)} steps={steps}', flush=True)
    m.delete_adapter('default')
pred = np.array(auth)[mat.argmax(1)]
acc = float((pred == y).mean())
rec = {an: float((pred[y == an] == an).mean()) for an in auth if (y == an).any()}
json.dump({'fold': a.fold, 'authors': auth, 'acc': acc, 'recall': rec, 'n': int(len(va)),
           'secs': time.time() - t0, 'matrix': mat.tolist(), 'ids': va.id.tolist()},
          open(a.out, 'w'))
print('ACC', round(acc, 4), 'random', round(1 / len(auth), 4), f'{time.time()-t0:.0f}s', flush=True)
