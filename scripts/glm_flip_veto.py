"""Veto test on the rows where the mass-preserving blend differs from the shipped one.

For the ~104 rows whose final (post-Apex) label changes, the only question that matters
is: keep the new label or fall back to the shipped one? Rows where both are wrong are
worth zero either way; rows where exactly one is right are worth +/- 1/n_class.

Signals compared (all computed without touching truth):
  base_diff      P0[v5] - P0[shipped] on the V1-input probs
  deb_diff       same difference from the DeBERTa member alone (18-class softmax)
  n14b_diff      same from n14b_clean
  reader_min_raw min over the six readers of exp(LP) summed over the two GLM classes
  reader_min_sm  same but each member passed through an 18-class softmax first

Thresholds are chosen on training folds only and applied to held-out folds, grouping by
prompt_id; "accept everything" is the control.
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy.special import log_softmax, softmax
from sklearn.metrics import roc_auc_score

sys.path.insert(0, 'runs/diag3')
from pngy_residual import Residual  # noqa: E402

RUN = 'runs/diag3'
GLM = ['glm-5.3', 'glm-5.3-flash']
W = 0.8743927164018473
READERS = ['q14v5', 'q8v5', 'll8v5', 'n14b_clean', 'q25_14', 'deb']

train = pd.read_csv('data/train.csv')
labels = sorted(train.model.unique())
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
z = np.load(RUN + '/final-logits.npz', allow_pickle=True)
npz_labels = [str(x) for x in z['labels']]
ids = [str(x) for x in z['ids']]
base_lp = np.asarray(z['logits'], np.float64)[:, [npz_labels.index(l) for l in labels]]
truth = dict(zip(ev.id, ev.model))
prompt = dict(zip(ev.id, ev.prompt_id))
y = np.array([truth[i] for i in ids])
texts = ev.set_index('id').loc[ids, 'text'].fillna('').astype(str).values
ia, ib = labels.index(GLM[0]), labels.index(GLM[1])
n_cls = np.array([(y == c).sum() for c in labels], dtype=float)

model = json.load(open('build/v1_src/ll_model.json'))
feats = {t: pd.read_csv(f'{RUN}/v1/{t}_features.csv', dtype={'id': str}).set_index('id')
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


sh_f, ms_f = apex(shipped), apex(mass)
diff = sh_f != ms_f
print('rows where the two finals differ:', int(diff.sum()))

idx = np.where(diff)[0]
v5 = ms_f[idx]
sh = sh_f[idx]
t = y[idx]
good = v5 == t
bad = (sh == t) & (v5 != t)
neutral = ~good & ~bad
weight = 1.0 / n_cls[[labels.index(c) for c in t]]
print('keep-v5 right', int(good.sum()), '| fallback right', int(bad.sum()), '| both wrong', int(neutral.sum()))
base_delta = float((np.where(good, weight, 0) - np.where(bad, weight, 0)).sum() / 18)

members = np.load(RUN + '/diag_lp_members.npz', allow_pickle=True)


def member_softmax(key):
    return softmax(members[key].astype(np.float64), 1)


sig = {}
sig['base_diff'] = P0[idx, [labels.index(c) for c in v5]] - P0[idx, [labels.index(c) for c in sh]]
sig['deb_diff'] = (member_softmax('deb')[idx, [labels.index(c) for c in v5]]
                   - member_softmax('deb')[idx, [labels.index(c) for c in sh]])
sig['n14b_diff'] = (member_softmax('n14b_clean')[idx, [labels.index(c) for c in v5]]
                    - member_softmax('n14b_clean')[idx, [labels.index(c) for c in sh]])
raw = np.stack([np.exp(members[k].astype(np.float64))[:, [ia, ib]].sum(1) for k in READERS])
sm = np.stack([softmax(members[k].astype(np.float64), 1)[:, [ia, ib]].sum(1) for k in READERS])
sig['reader_min_raw'] = raw.min(0)[idx]
sig['reader_min_sm'] = sm.min(0)[idx]

binary = good.astype(int)
out = {'n_rows': int(diff.sum()), 'good': int(good.sum()), 'bad': int(bad.sum()),
       'neutral': int(neutral.sum()), 'baseline_accept_all': base_delta, 'signals': {}}

pids = np.array([prompt[ids[i]] for i in idx])
uniq = np.unique(pids)
fold_of = {p: n % 5 for n, p in enumerate(uniq)}
fold = np.array([fold_of[p] for p in pids])

for name, s in sig.items():
    rec = {'auc_good_vs_bad': float(roc_auc_score(binary[~neutral], s[~neutral])) if (~neutral).sum() > 1 else None,
           'auc_all': float(roc_auc_score(binary, s)) if 0 < binary.sum() < len(binary) else None}
    grid = np.quantile(s, np.linspace(0.02, 0.98, 25))
    # cross-fitted: choose the threshold on the other four folds, apply to this fold
    total = 0.0
    picks = []
    for k in range(5):
        tr, te = fold != k, fold == k
        if tr.sum() == 0 or te.sum() == 0:
            continue
        best_t, best_v = -np.inf, -1e9
        for cand in grid:
            keep = s[tr] >= cand
            v = float((np.where(keep & good[tr], weight[tr], 0)
                       - np.where(keep & bad[tr], weight[tr], 0)).sum())
            if v > best_v:
                best_v, best_t = v, cand
        keep_te = s[te] >= best_t
        total += float((np.where(keep_te & good[te], weight[te], 0)
                        - np.where(keep_te & bad[te], weight[te], 0)).sum())
        picks.append(float(best_t))
    rec['crossfit_delta_ba'] = total / 18
    rec['crossfit_gain_vs_accept_all'] = total / 18 - base_delta
    rec['thresholds'] = picks
    out['signals'][name] = rec

print(json.dumps(out, indent=2))
json.dump(out, open('results/glm_flip_veto.json', 'w'), indent=2)
np.savez('results/glm_flip_rows.npz', good=good, bad=bad, neutral=neutral, weight=weight,
         pids=pids, **{k: v for k, v in sig.items()})
