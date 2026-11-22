"""Can we rebuild prompt groups at test time using text alone?

Why this matters (the transductive lever): test.csv ships (id, text) with no
prompt_id, but passages answering the same prompt are about the same topic. If
we can rebuild the groups, we get a free constraint -- one prompt should not
produce two answers from the same model -- which is exactly what balanced
accuracy punishes. The 0.711 public solution does this ("Reciprocal Prompt-Pair
Balancing: TF-IDF cosine similarity neighbor clustering with duplicate author
probability mass dissipation").

This script answers "is the clustering good enough" using the training set,
where prompt_id is known and can therefore be used as ground truth.
Cost: a couple of minutes on CPU. Decision: if pairwise F1 of the recovered
groups is high, implement the dedup/assignment step; otherwise drop it.

Usage:
    python scripts/reconstruct_prompts.py --train data/train.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import adjusted_rand_score

try:
    from scipy.sparse.csgraph import connected_components
except ImportError:  # pragma: no cover
    connected_components = None


def pairwise_scores(pred: np.ndarray, true: np.ndarray) -> dict:
    """Pairwise precision/recall/F1 of a clustering vs the true grouping."""
    same_true = true[:, None] == true[None, :]
    same_pred = pred[:, None] == pred[None, :]
    iu = np.triu_indices(len(true), k=1)
    st, sp = same_true[iu], same_pred[iu]
    tp = int((st & sp).sum())
    fp = int((~st & sp).sum())
    fn = int((st & ~sp).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def union_find_clusters(adj: csr_matrix) -> np.ndarray:
    n = adj.shape[0]
    if connected_components is None:
        raise SystemExit("scipy is required for the threshold sweep")
    n_comp, labels = connected_components(csgraph=adj, directed=False)
    return labels, n_comp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/train.csv")
    ap.add_argument("--out", default="results/prompt_reconstruction.json")
    ap.add_argument("--max-features", type=int, default=200_000)
    args = ap.parse_args()

    df = pd.read_csv(args.train)
    texts = df.text.fillna(" ").astype(str)
    true = pd.factorize(df.prompt_id)[0]

    vec = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=2,
        max_features=args.max_features,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    X = vec.fit_transform(texts)
    print(f"tf-idf: {X.shape[0]} docs x {X.shape[1]} features")

    S = (X @ X.T).toarray()
    np.fill_diagonal(S, 0.0)

    results = {"n_docs": int(len(df)), "n_true_prompts": int(len(set(true))), "thresholds": []}

    # --- threshold sweep with connected components -------------------------
    for thr in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        adj = csr_matrix((S >= thr).astype(np.int8))
        labels, n_comp = union_find_clusters(adj)
        m = pairwise_scores(labels, true)
        m.update({"threshold": thr, "n_clusters": int(n_comp), "ari": float(adjusted_rand_score(true, labels))})
        results["thresholds"].append(m)
        print(
            f"thr={thr:.2f} clusters={n_comp:>5}  P={m['precision']:.3f} "
            f"R={m['recall']:.3f} F1={m['f1']:.3f} ARI={m['ari']:.3f}"
        )

    # --- agglomerative clustering at the known group count ------------------
    for linkage in ("average", "complete"):
        model = AgglomerativeClustering(
            n_clusters=len(set(true)), metric="cosine", linkage=linkage
        )
        labels = model.fit_predict(S)
        m = pairwise_scores(labels, true)
        m.update({"linkage": linkage, "n_clusters": len(set(true)), "ari": float(adjusted_rand_score(true, labels))})
        results.setdefault("agglomerative", []).append(m)
        print(
            f"agglo/{linkage:<8} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} "
            f"ARI={m['ari']:.3f}"
        )

    # --- oracle ceiling: how many same-prompt pairs exist at all ------------
    iu = np.triu_indices(len(true), k=1)
    results["oracle"] = {"n_pairs": int(len(iu[0])), "n_same_prompt_pairs": int((true[:, None] == true[None, :])[iu].sum())}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    print("Decision rule: F1 >= ~0.85 on the best threshold -> implement dedup/assignment.")


if __name__ == "__main__":
    main()
