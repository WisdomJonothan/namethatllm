"""Final, ID-aligned replay of the GLM stage and Apex on the stage3 run.

Fixes over replay_v1_blend.py:
  * rows are aligned by ID (sel_idx), never by a boolean mask on another order
  * the two-class re-split uses softmax over the two GLM classes only
  * no oracle that peeks at truth for non-GLM rows
Gates: the shipped reconstruction must equal pred_pre_apex.parquet and the Apex
replay must equal diag_pred.parquet, both row for row.
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy.special import log_softmax

sys.path.insert(0, 'runs/diag3')
from pngy_residual import Residual  # noqa: E402

RUN = 'runs/diag3'
GLM = ['glm-5.3', 'glm-5.3-flash']
W = 0.8743927164018473


def ba(pred, truth, labels):
    return float(np.mean([float((pred[truth == c] == c).mean()) for c in labels]))


def recall(pred, truth, c):
    m = truth == c
    return float((pred[m] == c).mean()) if m.any() else float('nan')


def member_logp(model, feats, ids):
    blocks = [feats[t].add_prefix(t + ':') for t in ('flash', 'glm53')]
    F = pd.concat(blocks, axis=1).reindex(ids)
    for c in model['diff_features']:
        F['d:' + c] = F['flash:' + c] - F['glm53:' + c]
    X = F[model['features']].to_numpy(np.float64)
    z = ((X - np.asarray(model['mean'])) / np.asarray(model['scale'])) @ np.asarray(model['coef']).T
    z = z + np.asarray(model['intercept'])
    return log_softmax(z, 1)


train = pd.read_csv('data/train.csv')
labels = sorted(train.model.unique())
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
z = np.load(RUN + '/final-logits.npz', allow_pickle=True)
npz_labels = [str(x) for x in z['labels']]
ids = [str(x) for x in z['ids']]
base_lp = np.asarray(z['logits'], np.float64)[:, [npz_labels.index(l) for l in labels]]
truth = dict(zip(ev.id, ev.model))
src = dict(zip(ev.id, ev.src))
prompt = dict(zip(ev.id, ev.prompt_id))
y = np.array([truth[i] for i in ids])
texts = ev.set_index('id').loc[ids, 'text'].fillna('').astype(str).values
ia, ib = labels.index(GLM[0]), labels.index(GLM[1])

model = json.load(open('build/v1_src/ll_model.json'))
feats = {t: pd.read_csv(RUN + '/v1/' + t + '_features.csv', dtype={'id': str}).set_index('id')
         for t in ('flash', 'glm53')}
sel_ids = [str(i) for i in feats['flash'].index.tolist()]
sel_idx = np.array([ids.index(i) for i in sel_ids])
ll = member_logp(model, feats, sel_ids)[:, [model['classes'].index(c) for c in labels]]
print('selected rows', len(sel_idx), 'npz label order == sorted(train):', npz_labels == labels)

base_pred = np.asarray(labels)[base_lp.argmax(1)]

zs = base_lp.copy()
zs[sel_idx] += W * ll
shipped_pred = np.asarray(labels)[zs.argmax(1)]
pre = pd.read_parquet(RUN + '/pred_pre_apex.parquet').set_index('id').loc[ids, 'model'].values
print('shipped == pred_pre_apex:', int((shipped_pred == pre).sum()), '/', len(ids))


def apex(pred):
    residual = Residual(labels).fit(train.text.fillna('').astype(str), train.model)
    rlp = residual.predict_log_probability(base_lp, texts)
    rp = np.asarray(labels)[rlp.argmax(1)]
    cur = pred.copy()
    nonglm = ~np.isin(cur, GLM)
    cur[nonglm] = rp[nonglm]
    P = np.exp(rlp - rlp.max(1, keepdims=True))
    P /= P.sum(1, keepdims=True)
    allowed = {'claude-opus-4-6', 'claude-opus-5', 'glm-5.3', 'glm-5.3-flash'}
    forbidden = np.array([l not in allowed for l in labels])
    no_curly = np.array([l.startswith('claude-') or l.startswith('glm-') for l in labels])
    no_gem = np.array([l.startswith('gemini-') for l in labels])
    for i, t in enumerate(texts):
        if ' \u2014 ' in t and cur[i] not in allowed:
            P[i, forbidden] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
        if '\u2019' in t and (cur[i].startswith('claude-') or cur[i].startswith('glm-')):
            P[i, no_curly] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
        if '\u2014' in t and cur[i].startswith('gemini-'):
            P[i, no_gem] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
    return cur


Shipped_final = apex(shipped_pred)
ref_final = pd.read_parquet(RUN + '/diag_pred.parquet').set_index('id').loc[ids, 'model'].values
print('apex replay == diag_pred:', int((Shipped_final == ref_final).sum()), '/', len(ids))

P0 = np.exp(base_lp - base_lp.max(1, keepdims=True))
P0 /= P0.sum(1, keepdims=True)
zq = ll[:, [ia, ib]]
q2 = np.exp(zq - zq.max(1, keepdims=True))
q2 /= q2.sum(1, keepdims=True)

Pm = P0.copy()
tot = Pm[sel_idx, ia] + Pm[sel_idx, ib]
Pm[sel_idx, ia] = tot * q2[:, 0]
Pm[sel_idx, ib] = tot * q2[:, 1]
mass_pred = np.asarray(labels)[Pm.argmax(1)]

strict = np.isin(base_pred[sel_idx], GLM)
Pr = P0.copy()
rows = sel_idx[strict]
totr = Pr[rows, ia] + Pr[rows, ib]
Pr[rows, ia] = totr * q2[strict, 0]
Pr[rows, ib] = totr * q2[strict, 1]
restricted_pred = np.asarray(labels)[Pr.argmax(1)]

Mass_final = apex(mass_pred)
Restr_final = apex(restricted_pred)

out = {'n': len(ids), 'selected': int(len(sel_idx)), 'strict_rows': int(strict.sum()),
       'variants': {}}
for name, p, pf in [('base_v1_input', base_pred, None),
                    ('shipped_additive', shipped_pred, Shipped_final),
                    ('mass_preserving', mass_pred, Mass_final),
                    ('restricted_glm_winner', restricted_pred, Restr_final)]:
    rec = {'ba_pre_apex': ba(p, y, labels)}
    if pf is not None:
        rec['ba_final'] = ba(pf, y, labels)
        rec['recall_glm53'] = recall(pf, y, GLM[0])
        rec['recall_glm53flash'] = recall(pf, y, GLM[1])
        rec['n_glm53'] = int((pf == GLM[0]).sum())
        rec['n_glm53flash'] = int((pf == GLM[1]).sum())
    out['variants'][name] = rec

sh_ba = out['variants']['shipped_additive']['ba_final']
for name in ('mass_preserving', 'restricted_glm_winner'):
    out['variants'][name]['delta_final_vs_shipped'] = out['variants'][name]['ba_final'] - sh_ba

for name, p in [('shipped_additive', shipped_pred), ('mass_preserving', mass_pred),
                ('restricted_glm_winner', restricted_pred)]:
    out['variants'][name]['into_glm'] = int(np.sum(~np.isin(base_pred, GLM) & np.isin(p, GLM)))
    out['variants'][name]['away_from_glm'] = int(np.sum(np.isin(base_pred, GLM) & ~np.isin(p, GLM)))

srcs = np.array([src[i] for i in ids])
for s in sorted(set(srcs.tolist())):
    m = srcs == s
    lab = sorted(set(y[m].tolist()))
    out.setdefault('by_source', {})[s] = {
        'n': int(m.sum()),
        'ba_shipped': ba(Shipped_final[m], y[m], lab),
        'ba_mass': ba(Mass_final[m], y[m], lab),
        'recall_glm53_shipped': recall(Shipped_final[m], y[m], GLM[0]),
        'recall_glm53_mass': recall(Mass_final[m], y[m], GLM[0]),
        'recall_flash_shipped': recall(Shipped_final[m], y[m], GLM[1]),
        'recall_flash_mass': recall(Mass_final[m], y[m], GLM[1]),
    }

rng = np.random.default_rng(0)
pids = np.array([prompt[i] for i in ids])
uniq = np.unique(pids)
deltas = []
for _ in range(400):
    pick = rng.choice(uniq, size=len(uniq), replace=True)
    idx = np.concatenate([np.where(pids == p)[0] for p in pick])
    deltas.append(ba(Mass_final[idx], y[idx], labels) - ba(Shipped_final[idx], y[idx], labels))
out['bootstrap_final_delta'] = {'mean': float(np.mean(deltas)),
                                'p2.5': float(np.percentile(deltas, 2.5)),
                                'p97.5': float(np.percentile(deltas, 97.5)),
                                'share_pos': float(np.mean(np.array(deltas) > 0))}

print(json.dumps(out, indent=2, default=float))
json.dump(out, open('results/glm_blend_final.json', 'w'), indent=2, default=float)
