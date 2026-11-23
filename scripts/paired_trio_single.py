"""Single-text discrimination: can a per-text scorer, thresholded on the train side,
separate the two siblings on held-out prompts? Complements the same-prompt ranking task."""
import json
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from scipy.sparse import vstack

PAIRS = [("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol"),
         ("gemini-3.7-flash", "gemini-3.8-flash"), ("glm-5.3", "glm-5.3-flash")]
FOLDS, SEED = 5, 0

df = pd.read_csv("data/train.csv", dtype={"id": str})
rng = np.random.default_rng(SEED)
prompts = df["prompt_id"].unique()
fold_of = {p: i % FOLDS for i, p in enumerate(rng.permutation(prompts))}
df["fold"] = df["prompt_id"].map(fold_of)

def vec():
    return TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True,
                           lowercase=True, max_features=200_000)

out = {}
for a, b in PAIRS:
    d = df[df["model"].isin([a, b])].sort_values(["prompt_id", "model"]).reset_index(drop=True)
    rec_bin, rec_pair = [], []
    per_fold = []
    for f in range(FOLDS):
        tr = d[d["fold"] != f]; te = d[d["fold"] == f]
        if te["model"].nunique() < 2:
            continue
        v = vec().fit(pd.concat([tr["text"], te["text"]]))
        Xtr, Xte = v.transform(tr["text"]), v.transform(te["text"])
        ytr = (tr["model"].values == b).astype(int)
        yte = (te["model"].values == b).astype(int)
        # binary: p(b) per text, threshold 0.5 on the train-fitted model
        m = LogisticRegression(C=4, max_iter=2000).fit(Xtr, ytr)
        pb = m.predict_proba(Xte)[:, 1]
        # paired: fit on within-prompt differences, score each text with the same w
        piv = tr.pivot_table(index="prompt_id", columns="model", values="text", aggfunc="first").dropna()
        ids_tr = tr[tr["prompt_id"].isin(piv.index)].copy()
        A = v.transform(piv[a].values); B = v.transform(piv[b].values)
        diff = (B - A).tocsr()
        Xd = vstack([diff, -diff]); yd = np.r_[np.ones(diff.shape[0]), np.zeros(diff.shape[0])]
        mp = LogisticRegression(C=4, max_iter=2000, fit_intercept=False).fit(Xd, yd)
        w = mp.coef_.ravel()
        sa, sb = A @ w, B @ w
        thr = 0.5 * (np.median(sa) + np.median(sb))
        ste = Xte @ w
        pred_bin = pb > 0.5
        pred_pair = ste > thr
        ra_bin = float(((~pred_bin) & (yte == 0)).sum() / max(1, (yte == 0).sum()))
        rb_bin = float((pred_bin & (yte == 1)).sum() / max(1, (yte == 1).sum()))
        ra_par = float(((~pred_pair) & (yte == 0)).sum() / max(1, (yte == 0).sum()))
        rb_par = float((pred_pair & (yte == 1)).sum() / max(1, (yte == 1).sum()))
        rec_bin.append((ra_bin, rb_bin)); rec_pair.append((ra_par, rb_par))
        per_fold.append({"fold": f, "n": int(len(te)),
                         "binary": [round(ra_bin, 3), round(rb_bin, 3)],
                         "paired": [round(ra_par, 3), round(rb_par, 3)]})
    def agg(rs):
        m = np.array(rs).mean(0)
        return {"recall_a": float(m[0]), "recall_b": float(m[1]), "balanced": float(m.mean())}
    out[f"{a}__vs__{b}"] = {"binary": agg(rec_bin), "paired": agg(rec_pair),
                            "per_fold": per_fold, "n_rows": int(len(d))}
json.dump(out, open("results/paired_trio_single.json", "w"), indent=1)
for k, v in out.items():
    print(f"{k:<45} bin a={v['binary']['recall_a']:.3f} b={v['binary']['recall_b']:.3f} bal={v['binary']['balanced']:.3f} | "
          f"pair a={v['paired']['recall_a']:.3f} b={v['paired']['recall_b']:.3f} bal={v['paired']['balanced']:.3f}")
