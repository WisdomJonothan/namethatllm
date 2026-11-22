"""C 阶段：TF-IDF + 逻辑回归基线（CPU，几分钟）。

作用有三：
1. 给出一个必须被 8B 模型显著超越的下限（门槛 BA >= 0.33）。
2. 产出 OOF 概率，供 transductive_sim.py 量化"同 prompt 去重"到底值多少分。
3. 用混淆矩阵确认"同族兄弟混淆"这个先验在真实数据上成不成立。

Usage:
    python scripts/baseline_tfidf.py --folds data/folds.csv
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default="data/folds.csv")
    ap.add_argument("--out-probs", default="results/oof_tfidf.npz")
    ap.add_argument("--out-report", default="results/baseline_tfidf.json")
    ap.add_argument("--max-iter", type=int, default=2000)
    args = ap.parse_args()

    df = pd.read_csv(args.folds)
    labels = sorted(df.model.unique())
    lab2idx = {m: i for i, m in enumerate(labels)}
    y = df.model.map(lab2idx).to_numpy()
    texts = df.text.fillna(" ").astype(str)
    folds = df.fold.to_numpy()

    oof = np.zeros((len(df), len(labels)), dtype=np.float32)
    per_fold = []
    t0 = time.time()

    for f in sorted(np.unique(folds)):
        tr, va = folds != f, folds == f
        char = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=2,
            max_features=200_000, sublinear_tf=True,
        )
        word = TfidfVectorizer(
            analyzer="word", ngram_range=(1, 2), min_df=2,
            max_features=50_000, sublinear_tf=True, strip_accents="unicode",
        )
        Xc, Xw = char.fit_transform(texts[tr]), word.fit_transform(texts[tr])
        X = hstack([Xc, Xw]).tocsr()
        Xva = hstack([char.transform(texts[va]), word.transform(texts[va])]).tocsr()

        clf = LogisticRegression(C=2.0, max_iter=args.max_iter, n_jobs=-1)
        clf.fit(X, y[tr])
        proba = np.zeros((va.sum(), len(labels)), dtype=np.float32)
        proba[:, clf.classes_] = clf.predict_proba(Xva)
        oof[va] = proba

        ba = balanced_accuracy_score(y[va], proba.argmax(1))
        per_fold.append(round(float(ba), 4))
        print(f"fold {f}: BA={ba:.4f}  ({X.shape[1]:,} features, {time.time() - t0:.0f}s)")

    ba_all = balanced_accuracy_score(y, oof.argmax(1))
    cm = confusion_matrix(y, oof.argmax(1), labels=np.arange(len(labels)))
    recalls = {labels[i]: round(float(cm[i, i] / cm[i].sum()), 4) for i in range(len(labels))}

    # top confusions: (true, predicted, count)
    off = cm.copy()
    np.fill_diagonal(off, 0)
    pairs = [
        (labels[i], labels[j], int(off[i, j]))
        for i, j in np.dstack(np.unravel_index(np.argsort(off.ravel())[::-1], off.shape))[0][:10]
        if off[i, j] > 0
    ]

    report = {
        "oof_balanced_accuracy": round(float(ba_all), 4),
        "per_fold": per_fold,
        "fold_mean": round(float(np.mean(per_fold)), 4),
        "fold_sd": round(float(np.std(per_fold)), 4),
        "per_class_recall": recalls,
        "top_confusions": pairs,
        "n_features_last_fold": int(X.shape[1]),
        "seconds": round(time.time() - t0, 1),
    }
    Path(args.out_probs).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out_probs, probs=oof, y=y, labels=np.array(labels), folds=folds,
                        prompt_id=df.prompt_id.to_numpy())
    Path(args.out_report).write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nOOF balanced accuracy = {ba_all:.4f}   (folds {np.mean(per_fold):.4f} ± {np.std(per_fold):.4f})")
    print("lowest-recall classes:", sorted(recalls.items(), key=lambda kv: kv[1])[:5])
    print("top confusions:")
    for a, b, n in pairs[:8]:
        print(f"  {a:24s} -> {b:24s} {n}")
    print(f"\nwrote {args.out_probs} and {args.out_report}")


if __name__ == "__main__":
    main()
