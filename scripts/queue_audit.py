"""Approximate audit of GLM queue coverage: original (mass-ordered) vs proposed
(ambiguous-version priority) selection, using cached eval_v2 logits."""
import json
import numpy as np
import pandas as pd

fl = np.load("runs/diag1/final-logits.npz", allow_pickle=True)
ids = [str(x) for x in fl["ids"]]
labels = [str(x) for x in fl["labels"]]
z = np.asarray(fl["logits"], float)
P = np.exp(z - z.max(1, keepdims=True))
P /= P.sum(1, keepdims=True)

ia, ib = labels.index("glm-5.3"), labels.index("glm-5.3-flash")
mass = P[:, ia] + P[:, ib]
gap = np.abs(P[:, ia] - P[:, ib])
rel = gap / np.maximum(mass, 1e-9)          # 1.0 = genuinely undecidable between the two
amb = 1.0 - rel                              # higher = more ambiguous

ev = pd.read_csv("data/eval_v2.csv", dtype={"id": str}).set_index("id").loc[ids]
nchar = ev["text"].astype(str).str.len().values
tok = 5 + np.minimum(nchar * 0.235 * 1.05, 160) + 1

n = len(ids)
res = {"n_rows": n, "token_total": float(tok.sum())}
for frac in (0.15, 0.25, 0.30, 0.45):
    k = int(round(frac * n))
    old = np.argsort(-mass, kind="stable")[:k]
    new = np.argsort(-(mass * amb), kind="stable")[:k]
    so, sn = set(old.tolist()), set(new.tolist())
    inter, only_old, only_new = so & sn, so - sn, sn - so
    res[f"top{int(frac*100)}"] = {
        "k": k,
        "overlap": len(inter),
        "old_only": len(only_old),
        "new_only": len(only_new),
        "jaccard": len(inter) / len(so | sn),
        "token_cov_old": float(tok[old].sum() / tok.sum()),
        "token_cov_new": float(tok[new].sum() / tok.sum()),
        "old_only_token_share": float(tok[sorted(only_old)].sum() / max(1.0, tok[old].sum())),
        "old_only_mean_mass": float(mass[sorted(only_old)].mean()) if only_old else None,
        "old_only_mean_amb": float(amb[sorted(only_old)].mean()) if only_old else None,
        "new_only_mean_mass": float(mass[sorted(only_new)].mean()) if only_new else None,
        "new_only_mean_amb": float(amb[sorted(only_new)].mean()) if only_new else None,
        "mass_rank_of_new_only_max": int(np.sort(np.argsort(-mass)).tolist().index(min(only_new))) if only_new else None,
    }
res["mass"] = {"p50": float(np.percentile(mass, 50)), "p90": float(np.percentile(mass, 90)),
               "max": float(mass.max())}
res["amb"] = {"p50": float(np.percentile(amb, 50)), "p90": float(np.percentile(amb, 90))}
json.dump(res, open("results/queue_audit.json", "w"), indent=1)
print(json.dumps(res, indent=1)[:2600])
