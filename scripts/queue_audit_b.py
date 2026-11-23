"""Variant B: within the top-2k mass pool, order by ambiguity (version confusion)."""
import json
import numpy as np
import pandas as pd

fl = np.load("runs/diag1/final-logits.npz", allow_pickle=True)
ids = [str(x) for x in fl["ids"]]; labels = [str(x) for x in fl["labels"]]
z = np.asarray(fl["logits"], float)
P = np.exp(z - z.max(1, keepdims=True)); P /= P.sum(1, keepdims=True)
ia, ib = labels.index("glm-5.3"), labels.index("glm-5.3-flash")
mass = P[:, ia] + P[:, ib]; rel = np.abs(P[:, ia] - P[:, ib]) / np.maximum(mass, 1e-9)
amb = 1.0 - rel
ev = pd.read_csv("data/eval_v2.csv", dtype={"id": str}).set_index("id").loc[ids]
tok = 5 + np.minimum(ev["text"].astype(str).str.len().values * 0.235 * 1.05, 160) + 1
n = len(ids); out = {}
for frac in (0.15, 0.25, 0.30, 0.45):
    k = int(round(frac * n))
    old = np.argsort(-mass, kind="stable")[:k]; so = set(old.tolist())
    pool = np.argsort(-mass, kind="stable")[: min(n, 2 * k)]
    newb = pool[np.argsort(-amb[pool], kind="stable")][:k]; sb = set(newb.tolist())
    extra = sorted(sb - so); lost = sorted(so - sb)
    out[f"top{int(frac*100)}"] = {
        "k": k, "overlap": len(so & sb), "newB_added": len(extra), "newB_dropped": len(lost),
        "added_token_share_of_old": float(tok[extra].sum() / max(1.0, tok[old].sum())),
        "added_mean_mass": float(mass[extra].mean()) if extra else None,
        "added_mean_amb": float(amb[extra].mean()) if extra else None,
        "dropped_mean_mass": float(mass[lost].mean()) if lost else None,
        "dropped_mean_amb": float(amb[lost].mean()) if lost else None,
        # how many of the added rows are actually GLM-true (only available offline)
        "added_glm_true_share": float(ev["model"].isin(["glm-5.3", "glm-5.3-flash"]).values[extra].mean()) if extra else None,
        "old_glm_true_share": float(ev["model"].isin(["glm-5.3", "glm-5.3-flash"]).values[old].mean()),
    }
json.dump(out, open("results/queue_audit_b.json", "w"), indent=1)
print(json.dumps(out, indent=1))
