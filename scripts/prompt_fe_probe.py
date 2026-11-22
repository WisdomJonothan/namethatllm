"""A1 probe: do prompt fixed effects make a linear style model generalise better
to *unseen prompts*?

Mechanism under test
--------------------
The 150 training prompts each have 18 answers (one per model), so a linear model
can raise its training accuracy by memorising prompt/topic words instead of the
model's writing style. Within each prompt group, the topic words are constant,
so subtracting the group mean removes exactly that component and leaves the
"how this model writes" part.

Inference stays ordinary (raw features, no grouping), because on the hidden test
we do not know the groups. The claim is only about what the *weights* learn.

Protocol that is honest for this task
-------------------------------------
* fit the vectoriser and the weights on train.csv only;
* evaluate on eval_v2.csv (3,350 rows, 550 prompts, never seen, de-duplicated
  against both the official train and the open pool).

    python scripts/prompt_fe_probe.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score

ROOT = Path(r"D:\NameThatLLM")


def centre_by_group(X: sp.csr_matrix, groups: np.ndarray) -> sp.csr_matrix:
    blocks = []
    for p in pd.unique(groups):
        idx = np.where(groups == p)[0]
        blk = X[idx]
        mu = np.asarray(blk.mean(0)).ravel()
        n = blk.shape[0]
        correction = sp.csr_matrix(np.ones((n, 1)) @ mu[None, :])
        blocks.append((blk - correction).tocsr())
    order = np.concatenate([np.where(groups == p)[0] for p in pd.unique(groups)])
    out = sp.vstack(blocks).tocsr()
    inv = np.empty_like(order)
    inv[order] = np.arange(len(order))
    return out[inv]


def run(tag: str, Xtr, ytr, Xev, yev, C: float) -> dict:
    clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced")
    clf.fit(Xtr, ytr)
    p_tr = clf.predict(Xtr)
    p_ev = clf.predict(Xev)
    return {
        "arm": tag,
        "C": C,
        "train_ba": round(float(balanced_accuracy_score(ytr, p_tr)), 4),
        "eval_ba": round(float(balanced_accuracy_score(yev, p_ev)), 4),
        "eval_acc": round(float((p_ev == yev).mean()), 4),
    }


def main() -> None:
    tr = pd.read_csv(ROOT / "data" / "train.csv").dropna(subset=["text", "model", "prompt_id"])
    ev = pd.read_csv(ROOT / "data" / "eval_v2.csv").dropna(subset=["text", "model"])

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                          max_features=200_000, sublinear_tf=True)
    X = vec.fit_transform(tr["text"].tolist())
    Xev = vec.transform(ev["text"].tolist())
    Xc = centre_by_group(X, tr["prompt_id"].values)

    ytr = tr["model"].values
    yev = ev["model"].values

    res = []
    for C in (0.5, 2.0, 8.0):
        res.append(run("raw", X, ytr, Xev, yev, C))
        res.append(run("prompt_centred", Xc, ytr, Xev, yev, C))

    out = {"n_train": len(tr), "n_eval": len(ev), "n_features": int(X.shape[1]), "results": res}
    (ROOT / "results" / "prompt_fe_probe.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"train={len(tr)} eval={len(ev)} feats={X.shape[1]}")
    print(f"{'arm':>16s} {'C':>5s} {'trainBA':>8s} {'evalBA':>8s} {'evalAcc':>8s}")
    for r in res:
        print(f"{r['arm']:>16s} {r['C']:>5} {r['train_ba']:>8.4f} {r['eval_ba']:>8.4f} {r['eval_acc']:>8.4f}")

    by_c = {}
    for r in res:
        by_c.setdefault(r["C"], {})[r["arm"]] = r["eval_ba"]
    print("\ndelta(eval BA, centred - raw) per C:")
    for C, d in by_c.items():
        print(f"  C={C:<4} {d['prompt_centred'] - d['raw']:+.4f}")


if __name__ == "__main__":
    main()
