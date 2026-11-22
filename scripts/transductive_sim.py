"""同 prompt 去重到底值多少分？——用 OOF 概率直接量化。

三种情形都算一遍 balanced accuracy：
  naive       argmax，什么都不做
  clustered   用 TF-IDF + complete-linkage 重建的组做组内唯一分配（现实情形）
  oracle      用真实 prompt_id 分组做组内唯一分配（这个杠杆的天花板）

clustered 和 oracle 的差距 = 重建质量带来的损失；oracle 和 naive 的差距 = 这个杠杆的
上限。如果 oracle 只值 +0.005，直接放弃，别在提交 notebook 里加这段复杂度。

Usage:
    python scripts/transductive_sim.py --probs results/oof_tfidf.npz --folds data/folds.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import balanced_accuracy_score


def dedup_group(probs: np.ndarray, n_labels: int) -> np.ndarray:
    """Assign labels inside one group so that no label repeats (greedy by confidence)."""
    order = np.argsort(-probs.max(1))
    used: set[int] = set()
    out = np.full(len(probs), -1)
    for i in order:
        ranked = np.argsort(-probs[i])
        pick = next((c for c in ranked if c not in used), int(ranked[0]))
        used.add(pick)
        out[i] = pick
    return out


def dedup_group_hungarian(probs: np.ndarray, n_labels: int) -> np.ndarray:
    if len(probs) != n_labels:
        return dedup_group(probs, n_labels)
    cost = -np.log(np.clip(probs, 1e-9, 1.0))
    rows, cols = linear_sum_assignment(cost)
    out = np.empty(len(probs), dtype=int)
    out[rows] = cols
    return out


def apply_groups(probs: np.ndarray, groups: np.ndarray, n_labels: int, use_hungarian: bool) -> np.ndarray:
    pred = probs.argmax(1)
    out = pred.copy()
    for g in np.unique(groups):
        idx = np.where(groups == g)[0]
        if len(idx) < 2:
            continue
        sub = probs[idx]
        out[idx] = dedup_group_hungarian(sub, n_labels) if use_hungarian else dedup_group(sub, n_labels)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="results/oof_tfidf.npz")
    ap.add_argument("--folds", default="data/folds.csv")
    ap.add_argument("--out", default="results/transductive_sim.json")
    args = ap.parse_args()

    d = np.load(args.probs, allow_pickle=True)
    probs, y, labels = d["probs"], d["y"], list(d["labels"])
    n_labels = len(labels)

    df = pd.read_csv(args.folds)
    if len(df) != len(y):
        raise SystemExit("folds.csv and probs are not aligned")

    # --- rebuild groups from text alone ------------------------------------
    vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                          max_features=200_000, sublinear_tf=True, strip_accents="unicode")
    X = vec.fit_transform(df.text.fillna(" ").astype(str))
    S = (X @ X.T).toarray()
    np.fill_diagonal(S, 0.0)
    dist = np.clip(1.0 - S, 0.0, 2.0)
    n_prompts = df.prompt_id.nunique()
    clustered = AgglomerativeClustering(n_clusters=n_prompts, metric="precomputed",
                                        linkage="complete").fit_predict(dist)
    true_groups = pd.factorize(df.prompt_id)[0]

    sizes = pd.Series(clustered).value_counts()
    print(f"clusters={len(sizes)}  size min/median/max = {sizes.min()}/{int(sizes.median())}/{sizes.max()}")

    results = {"naive": None}
    naive = probs.argmax(1)
    results["naive"] = round(float(balanced_accuracy_score(y, naive)), 4)

    for name, groups, hung in [
        ("clustered_greedy", clustered, False),
        ("clustered_hungarian", clustered, True),
        ("oracle_greedy", true_groups, False),
        ("oracle_hungarian", true_groups, True),
    ]:
        pred = apply_groups(probs, groups, n_labels, hung)
        ba = float(balanced_accuracy_score(y, pred))
        changed = int((pred != naive).sum())
        results[name] = {"ba": round(ba, 4), "delta": round(ba - results["naive"], 4), "changed_rows": changed}
        print(f"{name:22s} BA={ba:.4f}  Δ={ba - results['naive']:+.4f}  changed={changed}")

    from sklearn.metrics import recall_score
    naive_pred = probs.argmax(1)
    dedup_pred = apply_groups(probs, clustered, n_labels, True)
    r0 = recall_score(y, naive_pred, labels=np.arange(n_labels), average=None, zero_division=0)
    r1 = recall_score(y, dedup_pred, labels=np.arange(n_labels), average=None, zero_division=0)
    rescued = sorted(
        ((labels[i], round(float(r0[i]), 3), round(float(r1[i]), 3)) for i in range(n_labels)),
        key=lambda t: t[2] - t[1], reverse=True,
    )[:6]
    results["rescued_classes"] = [{"class": c, "recall_naive": a, "recall_dedup": b} for c, a, b in rescued]
    print("rescued classes (recall naive -> dedup):")
    for c, a, b in rescued:
        print(f"  {c:24s} {a:.3f} -> {b:.3f}  ({b - a:+.3f})")

    results["cluster_sizes"] = {"min": int(sizes.min()), "median": int(sizes.median()), "max": int(sizes.max())}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    print("读法：oracle_* 与 naive 的差 = 这个杠杆的上限；clustered_* 与 oracle_* 的差 = 重建质量的代价。")


if __name__ == "__main__":
    main()
