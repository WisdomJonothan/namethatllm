"""Replay the shipped V1 blend (zl + w*ll) against two mass-preserving
alternatives, all on the same readers-only base P0, on eval_v2."""
import json
import numpy as np, pandas as pd
from scipy.special import log_softmax

RUN = 'runs/diag3'
A, B = 'glm-5.3', 'glm-5.3-flash'


def ba(pred, truth):
    return float(np.mean([float((pred[truth == c] == c).mean()) for c in sorted(set(truth))]))


model = json.load(open('build/v1_src/ll_model.json'))
feats = {t: pd.read_csv(f'{RUN}/v1/{t}_features.csv', dtype={'id': str}).set_index('id')
         for t in ('flash', 'glm53')}
ids_sel = feats['flash'].index.tolist()

gl = np.load(f'{RUN}/final-logits.npz', allow_pickle=True)
ids = [str(x) for x in gl['ids']]; labels = [str(x) for x in gl['labels']]
ref = np.load(f'{RUN}/diag_lp.npy')
P0 = np.exp(ref - ref.max(1, keepdims=True)); P0 /= P0.sum(1, keepdims=True)
zl = np.log(np.maximum(P0, 1e-30))

ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
truth = dict(zip(ev.id, ev.model))
y = np.array([truth[i] for i in ids])


def member_logp(model, feats, ids):
    blocks = [feats[t].add_prefix(f'{t}:') for t in ('flash', 'glm53')]
    F = pd.concat(blocks, axis=1).reindex(ids)
    for c in model['diff_features']:
        F[f'd:{c}'] = F[f'flash:{c}'] - F[f'glm53:{c}']
    X = F[model['features']].to_numpy(np.float64)
    z = ((X - np.asarray(model['mean'])) / np.asarray(model['scale'])) @ np.asarray(model['coef']).T + np.asarray(model['intercept'])
    return log_softmax(z, 1)


ll = member_logp(model, feats, ids_sel)[:, [model['classes'].index(c) for c in labels]]
sel_idx = [ids.index(i) for i in ids_sel]
sel_mask = np.zeros(len(ids), bool); sel_mask[sel_idx] = True
w = 0.874
ia, ib = labels.index(A), labels.index(B)

out = {'n': len(ids), 'selected': int(sel_mask.sum()), 'variants': {}}
in_pair = np.array([truth[i] in (A, B) for i in ids])

def record(name, P):
    pred = np.array(labels)[P.argmax(1)]
    acc_pair = float((pred[in_pair] == y[in_pair]).mean())
    out['variants'][name] = {'ba': ba(pred, y), 'in_pair_acc': acc_pair,
                             'glm_pred': int(np.isin(pred, [A, B]).sum())}

record('P0_readers_only', P0)

# shipped: add w * log-opinion on selected rows
z2 = zl.copy(); z2[sel_mask] += w * ll
record('shipped_additive_opinion', np.exp(z2 - z2.max(1, keepdims=True)) / np.exp(z2 - z2.max(1, keepdims=True)).sum(1, keepdims=True))

# mass-preserving: keep the pair's total mass, split it with the GLM opinion
Po = P0.copy()
for k, j in zip(sel_idx, range(len(sel_idx))):
    tot = Po[k, ia] + Po[k, ib]
    q = np.exp(ll[j] - ll[j].max()); q /= q.sum()
    Po[k, ia] = tot * q[ia]; Po[k, ib] = tot * q[ib]
record('mass_preserving_opinion', Po)

# mass-preserving with the self-likelihood two-way comparator
sa = {}
for t in ('glm53', 'flash'):
    d = feats[t]
    sa[t] = (d['lp_mean'].values, d['n_tok'].values)
n = len(ids_sel)
dl = sa['glm53'][0] - sa['flash'][0]
Ps = P0.copy()
q2 = np.exp(1.715 * np.stack([dl, np.zeros(n)], 1) / 2); q2 /= q2.sum(1, keepdims=True)
for k, j in zip(sel_idx, range(n)):
    tot = Ps[k, ia] + Ps[k, ib]
    Ps[k, ia] = tot * q2[j, 0]; Ps[k, ib] = tot * q2[j, 1]
record('mass_preserving_selflik', Ps)

# oracle inside the pair
Py = P0.copy()
for k in sel_idx:
    tot = Py[k, ia] + Py[k, ib]
    t = y[k]
    Py[k, ia] = tot if t == A else 0.0
    Py[k, ib] = tot if t == B else 0.0
record('oracle_in_pair', Py)

print(json.dumps(out, indent=2))
json.dump(out, open('results/v1_blend_replay.json', 'w'), indent=2)
