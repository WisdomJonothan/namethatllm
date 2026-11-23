"""Does the GLM pair scorer separate the two GLM versions, and can a
mass-preserving in-pair re-blend with a stricter gate beat the shipped blend?

Reads runs/diag3 (v1 scores + member LP) and data/eval_v2.csv.
"""
import json, os
import numpy as np, pandas as pd

RUN = 'runs/diag3'
W = {'q14v5': 3.0, 'n14b_clean': 2.5, 'q8v5': 2.0, 'll8v5': 1.0, 'q25_14': 1.0, 'deb': 1.0}
A, B = 'glm-5.3', 'glm-5.3-flash'


def softmax(z):
    z = z - z.max(1, keepdims=True); e = np.exp(z); return e / e.sum(1, keepdims=True)


def ba(pred, truth):
    return float(np.mean([float((pred[truth == c] == c).mean()) for c in sorted(set(truth))]))


def text_logp(path):
    z = np.load(path, allow_pickle=True)
    ids = [str(x) for x in z['ids']]; sc, off = z['scores'], z['offsets']
    out = {}
    for i, t in enumerate(ids):
        s = sc[off[i]:off[i + 1]].astype(np.float64)
        if len(s):
            out[t] = (float(s[:, 0].sum()), int(len(s)))
    return out


# --- 1. member reconciliation -------------------------------------------------
m = np.load(os.path.join(RUN, 'diag_lp_members.npz'), allow_pickle=True)
ref = np.load(os.path.join(RUN, 'diag_lp.npy'))
raw = sum(m[k].astype(np.float64) for k in m.files if k in W)
wgt = sum(m[k].astype(np.float64) * W[k] for k in m.files if k in W)
print('member diff  unweighted %.4g   weighted %.4g' % (np.abs(raw - ref).max(), np.abs(wgt - ref).max()))

# --- 2. two-way scorer quality ------------------------------------------------
gl = np.load(os.path.join(RUN, 'final-logits.npz'), allow_pickle=True)
ids = [str(x) for x in gl['ids']]; labels = [str(x) for x in gl['labels']]
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
truth = dict(zip(ev.id, ev.model))
y = np.array([truth[i] for i in ids])
P0 = softmax(ref)                      # readers only, before GSQ/native/v1/apex
L = labels
ia, ib = L.index(A), L.index(B)

sa = text_logp(os.path.join(RUN, 'v1', 'glm53_scores.npz'))
sb = text_logp(os.path.join(RUN, 'v1', 'flash_scores.npz'))
common = [i for i in ids if i in sa and i in sb]
print('scored rows', len(common))
d = np.array([sa[i][0] / sa[i][1] - sb[i][0] / sb[i][1] for i in common])
pair = np.array([truth[i] in (A, B) for i in common])
acc = float(((d > 0) == (y[[ids.index(i) for i in common]] == A))[pair].mean())
print('in-pair two-way accuracy %.4f on %d rows' % (acc, int(pair.sum())))
print('scored rows that are really GLM: %d / %d' % (int(pair.sum()), len(common)))

# --- 3. gate sweep with mass-preserving in-pair re-blend ----------------------
mass = P0[:, ia] + P0[:, ib]
rows_q = {i: j for j, i in enumerate(common)}
Qz = np.zeros((len(ids), 2))
for i, j in rows_q.items():
    Qz[ids.index(i)] = [d[j], 0.0]      # higher self-likelihood = more likely A
Qp = softmax(1.715 * Qz)

base_pred = np.array(L)[P0.argmax(1)]
base_ba = ba(base_pred, y)
oracle = base_pred.copy()
for i, j in rows_q.items():
    if truth[i] == A:
        oracle[ids.index(i)] = A
    elif truth[i] == B:
        oracle[ids.index(i)] = B
res = {'scored': len(common), 'in_pair': int(pair.sum()), 'two_way_acc': acc,
       'base_ba': base_ba, 'oracle_ba': ba(oracle, y), 'gates': {}}
for t in (0.10, 0.20, 0.30, 0.36, 0.50, 0.60, 0.70, 0.80):
    P = P0.copy()
    sel = mass >= t
    for i, j in rows_q.items():
        k = ids.index(i)
        if not sel[k]:
            continue
        tot = P[k, ia] + P[k, ib]
        P[k, ia] = tot * Qp[k, 0]
        P[k, ib] = tot * Qp[k, 1]
    pred = np.array(L)[P.argmax(1)]
    res['gates'][f'{t:.2f}'] = {'selected': int(sel.sum()), 'ba': ba(pred, y),
                                'delta': ba(pred, y) - base_ba}
print(json.dumps(res, indent=2))
os.makedirs('results', exist_ok=True)
json.dump(res, open('results/glm_pair_reblend.json', 'w'), indent=2)
