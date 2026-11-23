"""Per-prompt win/loss between the binary and the paired ranking heads (same folds)."""
import json
import numpy as np
import pandas as pd
from math import comb
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
out = {}
for a, b in PAIRS:
    d = df[df["model"].isin([a, b])]
    wins = {"paired_only": 0, "binary_only": 0, "both": 0, "neither": 0}
    for f in range(FOLDS):
        tr, te = d[d["fold"] != f], d[d["fold"] == f]
        v = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200_000)
        v.fit(pd.concat([tr["text"], te["text"]]))
        piv = tr.pivot_table(index="prompt_id", columns="model", values="text", aggfunc="first").dropna()
        A, B = v.transform(piv[a].values), v.transform(piv[b].values)
        Xd = vstack([(B - A).tocsr(), (A - B).tocsr()])
        yd = np.r_[np.ones(A.shape[0]), np.zeros(A.shape[0])]
        mp = LogisticRegression(C=4, max_iter=2000, fit_intercept=False).fit(Xd, yd)
        w = mp.coef_.ravel()
        mb = LogisticRegression(C=4, max_iter=2000).fit(
            v.transform(tr["text"]), (tr["model"].values == b).astype(int))
        for pid, g in te.groupby("prompt_id"):
            if g["model"].nunique() < 2:
                continue
            ta = g[g["model"] == a]["text"].iloc[0]; tb = g[g["model"] == b]["text"].iloc[0]
            Xa, Xb = v.transform([ta]), v.transform([tb])
            ok_pair = float(Xb @ w) > float(Xa @ w)
            ok_bin = mb.predict_proba(Xb)[0, 1] > mb.predict_proba(Xa)[0, 1]
            key = ("both" if (ok_pair and ok_bin) else "paired_only" if ok_pair
                   else "binary_only" if ok_bin else "neither")
            wins[key] += 1
    n = sum(wins.values()); k = wins["paired_only"] + wins["binary_only"]
    p = sum(comb(k, i) for i in range(min(wins["paired_only"], wins["binary_only"]) + 1)) / 2 ** k if k else 1.0
    out[f"{a}__vs__{b}"] = {**wins, "n_prompts": n,
                            "acc_binary": round((wins["both"] + wins["binary_only"]) / n, 3),
                            "acc_paired": round((wins["both"] + wins["paired_only"]) / n, 3),
                            "mcnemar_p": round(min(1.0, 2 * p), 3)}
json.dump(out, open("results/paired_trio_mcnemar.json", "w"), indent=1)
for k, v in out.items():
    print(f"{k:<45} bin={v['acc_binary']:.3f} pair={v['acc_paired']:.3f} "
          f"pairOnly={v['paired_only']:>3} binOnly={v['binary_only']:>3} p={v['mcnemar_p']}")
