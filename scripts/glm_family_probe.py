"""No-training feasibility probe for a separate 'is this GLM?' decision.

Question: inside the texts the GLM stage actually processed, is there any evidence that
separates true GLM passages from hard negatives once the base GLM mass is held fixed?
If the within-stratum AUC stays near 0.5, a family discriminator has nothing extra to
learn from these features and the project should not spend a week training one.

Signals: best/mean GLM self-likelihood (v1 features), token entropy, token surprisal,
reader disagreement on the GLM mass, and the base mass itself as the reference.
"""
import json

import numpy as np
import pandas as pd
from scipy.special import log_softmax
from sklearn.metrics import roc_auc_score

RUN = 'runs/diag3'
GLMS = ['glm-5.3', 'glm-5.3-flash']

train = pd.read_csv('data/train.csv')
labels = sorted(train.model.unique())
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
z = np.load(RUN + '/final-logits.npz', allow_pickle=True)
npz_labels = [str(x) for x in z['labels']]
ids = [str(x) for x in z['ids']]
base = np.asarray(z['logits'], np.float64)[:, [npz_labels.index(l) for l in labels]]
P = np.exp(log_softmax(base, 1))
truth = dict(zip(ev.id, ev.model))
y = np.array([truth[i] for i in ids])
is_glm = np.isin(y, GLMS)
gcols = [labels.index(c) for c in GLMS]
mass = P[:, gcols].sum(1)

feats = {t: pd.read_csv(f'{RUN}/v1/{t}_features.csv', dtype={'id': str}).set_index('id')
         for t in ('flash', 'glm53')}
sel_ids = [str(i) for i in feats['flash'].index.tolist()]
sel = np.array([ids.index(i) for i in sel_ids])
F = pd.concat([feats[t].add_prefix(t + ':') for t in ('flash', 'glm53')], axis=1).reindex(sel_ids)

sig = pd.DataFrame(index=sel_ids)
sig['mass'] = mass[sel]
sig['best_lp'] = np.maximum(F['flash:lp_mean'], F['glm53:lp_mean'])       # higher = better explained
sig['mean_lp'] = (F['flash:lp_mean'] + F['glm53:lp_mean']) / 2
sig['best_top1'] = np.maximum(F['flash:top1'], F['glm53:top1'])
sig['ent'] = -np.minimum(F['flash:ent_mean'], F['glm53:ent_mean'])          # lower entropy = better
sig['surprise'] = -np.minimum(F['flash:surprise_mean'], F['glm53:surprise_mean'])
sig['fdg'] = np.maximum(F['flash:fdg'], F['glm53:fdg'])

m = np.load(RUN + '/diag_lp_members.npz', allow_pickle=True)
readers = [k for k in m.files if not k.endswith(('_twin', '_gauss')) and k in
           ('q14v5', 'q8v5', 'll8v5', 'n14b_clean', 'q25_14', 'deb')]
if readers:
    R = np.stack([np.exp(m[k].astype(np.float64))[:, gcols].sum(1) for k in readers])
    sig['reader_std'] = R[:, sel].std(0)
    sig['reader_min'] = R[:, sel].min(0)
    sig['reader_max'] = R[:, sel].max(0)
    sig['reader_spread'] = sig['reader_max'] - sig['reader_min']

lab = is_glm[sel]
print('selected', len(sel_ids), 'true GLM', int(lab.sum()), 'non-GLM', int((~lab).sum()))
print('readers found:', len(readers))

out = {'n_selected': int(len(sel_ids)), 'n_true_glm': int(lab.sum()),
       'base_mass_mean_glm': float(sig['mass'][lab].mean()),
       'base_mass_mean_other': float(sig['mass'][~lab].mean()),
       'pooled_auc': {}, 'strata': []}

for c in sig.columns:
    out['pooled_auc'][c] = float(roc_auc_score(lab, sig[c]))

# hard negatives: base already thinks GLM is likely, truth is another family
hard = (~lab) & (sig['mass'] >= np.percentile(sig['mass'], 50))
out['hard_negatives'] = int(hard.sum())

qs = np.quantile(sig['mass'], np.linspace(0, 1, 6))
for lo, hi in zip(qs[:-1], qs[1:]):
    inbin = (sig['mass'] >= lo) & (sig['mass'] <= hi)
    rec = {'lo': float(lo), 'hi': float(hi), 'n': int(inbin.sum()), 'n_glm': int((lab & inbin).sum()),
           'auc': {}}
    if rec['n_glm'] >= 5 and (~lab & inbin).sum() >= 5:
        for c in sig.columns:
            if c == 'mass':
                continue
            rec['auc'][c] = float(roc_auc_score(lab[inbin], sig[c][inbin]))
        rec['auc']['mass'] = float(roc_auc_score(lab[inbin], sig['mass'][inbin]))
    out['strata'].append(rec)

print(json.dumps(out, indent=2))
json.dump(out, open('results/glm_family_probe.json', 'w'), indent=2)
