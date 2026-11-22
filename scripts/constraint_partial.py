"""测试"组内去重"在**不完整组**下的表现——模拟测试集的真实情况。

训练集是每组 18 条（149/150），但评价页明说测试集"每类条数不等"，所以提交时
每组可能只有 k 条（k<18）。本脚本对每个重建出来的组随机抽 k 条，只在这 k 条上
施加约束，然后只看这 k 条的召回，回答两个问题：

1. 组不完整时，硬分配（Hungarian）还成立吗？
2. 软约束（Sinkhorn，目标列和 = k/18）在什么温度下最稳？

Usage:
    python scripts/constraint_partial.py --probs results/oof_style_fine.npz
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

EPS = 1e-12


def sinkhorn(logits: np.ndarray, n_cols: int = 18, iters: int = 30) -> np.ndarray:
    """行和 = 1，列和 = len(rows)/n_cols（矩形时列容量不足 1，允许有的列空着）。

    返回已归一化的行概率（约束后的分布，用于 argmax）。
    """
    n = logits.shape[0]
    cap = n / n_cols
    P = np.exp(logits - logits.max(axis=1, keepdims=True))
    P /= P.sum(axis=1, keepdims=True)
    for _ in range(iters):
        P /= (P.sum(axis=0, keepdims=True) / cap + EPS)   # 列和 -> cap
        P /= (P.sum(axis=1, keepdims=True) + EPS)          # 行和 -> 1
    return P


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="results/oof_style_fine.npz")
    ap.add_argument("--train", default="data/train.csv")
    ap.add_argument("--ks", default="18,15,12,9")
    ap.add_argument("--temps", default="0.5,1,2,4,8")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default="results/constraint_partial.json")
    args = ap.parse_args()

    d = np.load(args.probs, allow_pickle=True)
    probs, y, labels = d["probs"], d["y"], [str(s) for s in d["labels"]]
    n_labels = len(labels)
    df = pd.read_csv(args.train)

    # 重建分组（与 reconstruct_prompts.py 一致）
    tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                         max_features=200_000, sublinear_tf=True)
    X = tf.fit_transform(df.text.fillna(" ").astype(str))
    n_clusters = int(round(len(df) / 18))
    cl = AgglomerativeClustering(n_clusters=n_clusters, metric="cosine",
                                 linkage="complete").fit(X.toarray())
    groups = [np.where(cl.labels_ == g)[0] for g in range(n_clusters)]
    groups = [g for g in groups if len(g) >= 3]
    print(f"{n_clusters} clusters, {len(df)} rows")

    logp = np.log(np.clip(probs, 1e-9, 1))
    naive_pred = probs.argmax(1)
    base_ba = balanced_accuracy_score(y, naive_pred)
    print(f"naive BA = {base_ba:.4f}")

    rng = np.random.default_rng(0)
    out = {"naive_ba": round(float(base_ba), 4), "n_groups": len(groups), "results": {}}

    for k in [int(v) for v in args.ks.split(",")]:
        acc = {f"T={t}": [] for t in args.temps.split(",")}
        acc["hungarian"] = []
        acc["greedy"] = []
        for seed in range(args.seeds):
            pred_h = naive_pred.copy()
            pred_g = naive_pred.copy()
            pred_s = {f"T={t}": naive_pred.copy() for t in args.temps.split(",")}
            for g in groups:
                if len(g) < k:
                    continue
                idx = rng.choice(g, size=k, replace=False)
                sub = logp[idx]

                # 硬分配：k x 18 的矩形指派（挑 k 个标签，互不重复）
                r, c = linear_sum_assignment(-sub)
                for ri, ci in zip(r, c):
                    pred_h[idx[ri]] = ci

                # greedy：按置信度从高到低，取还没被本组用掉的最高分标签
                order = np.argsort(-probs[idx].max(1))
                used = set()
                for ri in order:
                    row = probs[idx[ri]].copy()
                    for u in used:
                        row[u] = -1
                    pick = int(row.argmax())
                    pred_g[idx[ri]] = pick
                    used.add(pick)

                # Sinkhorn：温度缩放后再投影
                for t in args.temps.split(","):
                    T = float(t)
                    P = sinkhorn(sub / T, n_cols=n_labels)
                    for ri in range(k):
                        pred_s[f"T={t}"][idx[ri]] = int(P[ri].argmax())

            acc["hungarian"].append(balanced_accuracy_score(y, pred_h))
            acc["greedy"].append(balanced_accuracy_score(y, pred_g))
            for t in args.temps.split(","):
                acc[f"T={t}"].append(balanced_accuracy_score(y, pred_s[f"T={t}"]))
        out["results"][f"k={k}"] = {m: round(float(np.mean(v)), 4) for m, v in acc.items()}
        print(f"\nk={k}  (每组随机抽 {k} 条)")
        for m, v in sorted(out["results"][f"k={k}"].items(), key=lambda kv: -kv[1]):
            print(f"  {m:10s} {v:.4f}  ({v - base_ba:+.4f})")

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
