"""How stable is the veto gain? Re-shuffle the prompt folds 40 times and re-run the
cross-fitted threshold rule for every signal, and count how many rows actually get vetoed.
"""
import json

import numpy as np

d = np.load('results/glm_flip_rows.npz', allow_pickle=True)
good, bad, weight, pids = d['good'], d['bad'], d['weight'], d['pids']
signals = [k for k in d.files if k not in ('good', 'bad', 'neutral', 'weight', 'pids')]
uniq = np.unique(pids)
n = len(good)
base = float((np.where(good, weight, 0) - np.where(bad, weight, 0)).sum() / 18)

out = {'n_rows': int(n), 'accept_all': base, 'stability': {}}
for name in signals:
    s = d[name]
    grid = np.quantile(s, np.linspace(0.02, 0.98, 25))
    deltas, vetoes = [], []
    for seed in range(40):
        rng = np.random.default_rng(seed)
        perm = rng.permutation(uniq)
        fold_of = {p: k % 5 for k, p in enumerate(perm)}
        fold = np.array([fold_of[p] for p in pids])
        total, veto = 0.0, 0
        for k in range(5):
            tr, te = fold != k, fold == k
            if not tr.any() or not te.any():
                continue
            best_t, best_v = -np.inf, -1e9
            for cand in grid:
                keep = s[tr] >= cand
                v = float((np.where(keep & good[tr], weight[tr], 0)
                           - np.where(keep & bad[tr], weight[tr], 0)).sum())
                if v > best_v:
                    best_v, best_t = v, cand
            keep_te = s[te] >= best_t
            veto += int((~keep_te).sum())
            total += float((np.where(keep_te & good[te], weight[te], 0)
                            - np.where(keep_te & bad[te], weight[te], 0)).sum())
        deltas.append(total / 18)
        vetoes.append(veto)
    arr = np.array(deltas)
    out['stability'][name] = {'mean': float(arr.mean()), 'p2.5': float(np.percentile(arr, 2.5)),
                              'p97.5': float(np.percentile(arr, 97.5)),
                              'share_better_than_accept_all': float((arr > base + 1e-12).mean()),
                              'mean_vetoed_rows': float(np.mean(vetoes))}

print(json.dumps(out, indent=2))
json.dump(out, open('results/glm_flip_veto_stability.json', 'w'), indent=2)
