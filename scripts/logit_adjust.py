"""A4: balanced-accuracy-optimal decision layer (logit adjustment).

Theory (Menon et al. 2020, https://arxiv.org/abs/2007.07314): for the *balanced*
error, the Bayes-optimal rule is

    argmax_c  p(x | y=c)  ==  argmax_c  p(y=c | x) / p_train(y=c)

i.e. subtract log(train prior) from the logits.  Balanced accuracy does **not**
depend on the test-time class mix, so re-balancing the predicted marginal on the
hidden test (what Sinkhorn does) is not the right target: it fits the test's own
marginal instead of the 18-class balanced objective.

Two ways to get the per-class bias b:
  prior : b_c = -log p_train(c)                       (no fitting, theory-exact)
  fit   : b_c chosen to maximise soft macro recall    (recall-style surrogate,
                                                       cross-fitted)

This module works on any (N,18) probability matrix, so it can be applied to the
replayed ensemble output as well as to any single channel.

    python scripts/logit_adjust.py --probs results/oof_style_probs.npz --labels data/eval_v2.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

LABELS = [
    "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
    "gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "claude-fable-5", "claude-opus-4-6", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
    "qwen3.8-max", "glm-5.3", "glm-5.3-flash", "kimi-k3",
]
L2I = {l: i for i, l in enumerate(LABELS)}


def balanced_accuracy(y: np.ndarray, pred: np.ndarray) -> float:
    rec = []
    for c in np.unique(y):
        rec.append(float((pred[y == c] == c).mean()))
    return float(np.mean(rec))


def soft_macro_recall(P: np.ndarray, y: np.ndarray) -> float:
    """Differentiable surrogate: mean over classes of mean p(c|x) on that class."""
    vals = []
    for c in range(P.shape[1]):
        m = y == c
        if m.any():
            vals.append(float(P[m, c].mean()))
    return float(np.mean(vals))


def apply_bias(P: np.ndarray, b: np.ndarray) -> np.ndarray:
    z = np.log(np.clip(P, 1e-30, None)) + b[None, :]
    z -= z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def fit_bias(P: np.ndarray, y: np.ndarray, l2: float = 1e-3, iters: int = 400) -> np.ndarray:
    """Coordinate ascent on soft macro recall; bias centred to sum 0."""
    from scipy.optimize import minimize

    def neg(q, P, y):
        b = q - q.mean()
        Q = apply_bias(P, b)
        loss = -soft_macro_recall(Q, y)
        return loss + l2 * float(np.sum(b ** 2))

    q0 = np.zeros(P.shape[1])
    res = minimize(neg, q0, args=(P, y), method="L-BFGS-B",
                   options={"maxiter": iters, "ftol": 1e-12})
    b = res.x - res.x.mean()
    return b


def evaluate(P: np.ndarray, y: np.ndarray, prior: np.ndarray, n_splits: int = 5,
             seed: int = 0) -> dict:
    base_pred = P.argmax(1)
    base = balanced_accuracy(y, base_pred)

    b_prior = -np.log(np.clip(prior, 1e-9, None))
    b_prior -= b_prior.mean()
    prior_ba = balanced_accuracy(y, apply_bias(P, b_prior).argmax(1))

    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    folds = np.array_split(idx, n_splits)
    cv_pred = np.zeros(len(y), dtype=int)
    for k in range(n_splits):
        te = folds[k]
        tr = np.concatenate([folds[j] for j in range(n_splits) if j != k])
        b = fit_bias(P[tr], y[tr])
        cv_pred[te] = apply_bias(P[te], b).argmax(1)
    cv_ba = balanced_accuracy(y, cv_pred)

    b_all = fit_bias(P, y)
    fit_ba = balanced_accuracy(y, apply_bias(P, b_all).argmax(1))

    return {
        "n": int(len(y)),
        "ba_base": round(base, 4),
        "ba_prior_adjust": round(prior_ba, 4),
        "ba_fit_cv": round(cv_ba, 4),
        "ba_fit_insample": round(fit_ba, 4),
        "delta_prior": round(prior_ba - base, 4),
        "delta_fit_cv": round(cv_ba - base, 4),
        "bias": [round(float(x), 3) for x in b_all],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", required=True, help="npz with 'probs' (N,18) and 'ids'")
    ap.add_argument("--labels", required=True, help="csv with id,model")
    ap.add_argument("--train", default=r"D:\NameThatLLM\data\train.csv")
    ap.add_argument("--out", default=r"D:\NameThatLLM\results\logit_adjust.json")
    a = ap.parse_args()

    d = np.load(a.probs, allow_pickle=True)
    P = d["probs"]
    ids = d["ids"]
    lab = pd.read_csv(a.labels)
    m = dict(zip(lab["id"].astype(str), lab["model"].astype(str)))
    keep = [i for i, x in enumerate(ids) if str(x) in m]
    P = P[keep]
    y = np.array([L2I[m[str(ids[i])]] for i in keep])

    tr = pd.read_csv(a.train)
    cnt = tr["model"].value_counts().reindex(LABELS).fillna(0).values + 1.0
    prior = cnt / cnt.sum()

    res = evaluate(P, y, prior)
    res["source"] = str(a.probs)
    Path(a.out).write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
