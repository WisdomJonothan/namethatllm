"""Decompose the mass-preserving gain: where do the +0.0067 final-stage points come from?"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, 'runs/diag3')
from pngy_residual import Residual  # noqa: E402
from scipy.special import log_softmax  # noqa: E402

RUN = 'runs/diag3'
GLM = ['glm-5.3', 'glm-5.3-flash']
W = 0.8743927164018473

train = pd.read_csv('data/train.csv')
labels = sorted(train.model.unique())
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
z = np.load(RUN + '/final-logits.npz', allow_pickle=True)
npz_labels = [str(x) for x in z['labels']]
ids = [str(x) for x in z['ids']]
base_lp = np.asarray(z['logits'], np.float64)[:, [npz_labels.index(l) for l in labels]]
truth = dict(zip(ev.id, ev.model))
src = dict(zip(ev.id, ev.src))
y = np.array([truth[i] for i in ids])
texts = ev.set_index('id').loc[ids, 'text'].fillna('').astype(str).values
ia, ib = labels.index(GLM[0]), labels.index(GLM[1])

model = json.load(open('build/v1_src/ll_model.json'))
feats = {t: pd.read_csv(RUN + '/v1/' + t + '_features.csv', dtype={'id': str}).set_index('id')
         for t in ('flash', 'glm53')}
sel_ids = [str(i) for i in feats['flash'].index.tolist()]
sel_idx = np.array([ids.index(i) for i in sel_ids])
F = pd.concat([feats[t].add_prefix(t + ':') for t in ('flash', 'glm53')], axis=1).reindex(sel_ids)
for c in model['diff_features']:
    F['d:' + c] = F['flash:' + c] - F['glm53:' + c]
X = F[model['features']].to_numpy(np.float64)
zc = ((X - np.asarray(model['mean'])) / np.asarray(model['scale'])) @ np.asarray(model['coef']).T
zc = zc + np.asarray(model['intercept'])
ll = log_softmax(zc, 1)[:, [model['classes'].index(c) for c in labels]]

zs = base_lp.copy()
zs[sel_idx] += W * ll
shipped = np.asarray(labels)[zs.argmax(1)]

P0 = np.exp(base_lp - base_lp.max(1, keepdims=True))
P0 /= P0.sum(1, keepdims=True)
zq = ll[:, [ia, ib]]
q2 = np.exp(zq - zq.max(1, keepdims=True))
q2 /= q2.sum(1, keepdims=True)
Pm = P0.copy()
tot = Pm[sel_idx, ia] + Pm[sel_idx, ib]
Pm[sel_idx, ia] = tot * q2[:, 0]
Pm[sel_idx, ib] = tot * q2[:, 1]
mass = np.asarray(labels)[Pm.argmax(1)]


def apex(pred):
    residual = Residual(labels).fit(train.text.fillna('').astype(str), train.model)
    rlp = residual.predict_log_probability(base_lp, texts)
    rp = np.asarray(labels)[rlp.argmax(1)]
    cur = pred.copy()
    cur[~np.isin(cur, GLM)] = rp[~np.isin(cur, GLM)]
    return cur


sh_f, ms_f = apex(shipped), apex(mass)
base_pred = np.asarray(labels)[base_lp.argmax(1)]
srcs = np.array([src[i] for i in ids])
glm_truth = np.isin(y, GLM)

# 1) composition of the 48 cross-family recaptures
into = ~np.isin(shipped, GLM) & np.isin(mass, GLM)
away = np.isin(shipped, GLM) & ~np.isin(mass, GLM)
comp = {
    'into_glm_n': int(into.sum()),
    'into_glm_truth_glm': int((into & glm_truth).sum()),
    'into_glm_truth_other': int((into & ~glm_truth).sum()),
    'away_glm_n': int(away.sum()),
    'away_glm_truth_glm': int((away & glm_truth).sum()),
    'away_glm_truth_other': int((away & ~glm_truth).sum()),
    'into_glm_by_src': {s: int((into & (srcs == s)).sum()) for s in sorted(set(srcs.tolist()))},
    'into_glm_correct_by_src': {s: int((into & glm_truth & (srcs == s)).sum())
                                for s in sorted(set(srcs.tolist()))},
}

# 2) per-class recall, shipped vs mass, final stage
rows = []
for c in labels:
    m = y == c
    r_s = float((sh_f[m] == c).mean()) if m.any() else float('nan')
    r_m = float((ms_f[m] == c).mean()) if m.any() else float('nan')
    rows.append({'class': c, 'n': int(m.sum()), 'recall_shipped': r_s, 'recall_mass': r_m,
                 'delta': r_m - r_s, 'glm': c in GLM})
tab = pd.DataFrame(rows).sort_values('delta')
gain_from_glm = float(tab[tab.glm]['delta'].sum() / len(labels))
gain_from_other = float(tab[~tab.glm]['delta'].sum() / len(labels))

out = {'composition': comp,
       'delta_total': float(np.mean(tab.delta)),
       'delta_from_glm_classes': gain_from_glm,
       'delta_from_other_classes': gain_from_other,
       'table': tab.to_dict(orient='records')}
print(tab.to_string(index=False))
print(json.dumps({k: v for k, v in out.items() if k != 'table'}, indent=2))
json.dump(out, open('results/glm_blend_decomp.json', 'w'), indent=2, default=float)
