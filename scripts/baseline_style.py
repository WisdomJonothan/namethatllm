"""C 阶段加强版：TF-IDF + 手工风格特征 + 逻辑回归。

与 baseline_tfidf.py 的唯一区别是加入一层"法证风格特征"（标点指纹、行文结构、
Markdown 习惯、大小写/数字密度、长度）。这些特征不吃语义，只吃"这个模型怎么写"，
对同族兄弟区分应该有效。

Usage:
    python scripts/baseline_style.py --folds data/folds.csv --cs 1,2,4
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix
from sklearn.preprocessing import StandardScaler

PUNCT = {
    "em_dash": "—", "en_dash": "–", "hyphen": "-", "curly_apos": "\u2019",
    "curly_quote": "\u201c", "semicolon": ";", "colon": ":", "paren": "(",
    "excl": "!", "quest": "?", "comma": ",", "period": ".", "dquote": '"',
    "squote": "'", "star": "*", "hash": "#", "backtick": "`", "slash": "/",
    "percent": "%", "pipe": "|", "ellipsis": "…", "tilde": "~",
}
MARKERS = {
    "md_bold": "**", "md_heading": "\n#", "md_bullet": "\n- ", "md_num": "\n1.",
    "md_code": "```", "md_quote": "\n> ", "md_table": "|", "bullet_inline": " - ",
    "double_space": "  ", "line_break": "\n",
}
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])")
WORD = re.compile(r"[A-Za-z']+")


def style_features(texts) -> np.ndarray:
    rows = []
    for t in texts:
        t = str(t)
        n_chars = max(len(t), 1)
        words = WORD.findall(t)
        n_words = max(len(words), 1)
        sents = [s for s in SENT_SPLIT.split(t.strip()) if s]
        sent_lens = [len(WORD.findall(s)) for s in sents] or [0]
        lines = t.split("\n")
        feats = [
            np.log1p(n_chars), np.log1p(n_words), np.log1p(len(lines)),
            np.log1p(len(sents)),
            float(np.mean([len(w) for w in words])) if words else 0.0,
            float(np.std([len(w) for w in words])) if len(words) > 1 else 0.0,
            float(np.mean(sent_lens)), float(np.std(sent_lens)),
            len(set(w.lower() for w in words)) / n_words,
            sum(c.isupper() for c in t) / n_chars,
            sum(c.isdigit() for c in t) / n_chars,
            sum(ord(c) > 127 for c in t) / n_chars,
            sum(1 for l in lines if l.strip() == "") / max(len(lines), 1),
            sum(1 for l in lines if l.startswith(("- ", "* "))) / max(len(lines), 1),
            sum(1 for l in lines if re.match(r"^\d+[.)]\s", l)) / max(len(lines), 1),
            sum(1 for l in lines if l.rstrip().endswith("  ")) / max(len(lines), 1),
        ]
        for name, ch in PUNCT.items():
            feats.append(t.count(ch) / n_chars * 100.0)
        for name, ch in MARKERS.items():
            feats.append(t.count(ch) / n_chars * 100.0)
        rows.append(feats)
    return np.asarray(rows, dtype=np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default="data/folds.csv")
    ap.add_argument("--cs", default="1,2,4")
    ap.add_argument("--out-probs", default="results/oof_style.npz")
    ap.add_argument("--out-report", default="results/baseline_style.json")
    args = ap.parse_args()

    df = pd.read_csv(args.folds)
    labels = sorted(df.model.unique())
    lab2idx = {m: i for i, m in enumerate(labels)}
    y = df.model.map(lab2idx).to_numpy()
    texts = df.text.fillna(" ").astype(str)
    folds = df.fold.to_numpy()

    S_all = style_features(texts)
    print(f"style features: {S_all.shape[1]} dims, {S_all.shape[0]} rows")

    results = {}
    best = None
    for C in [float(c) for c in args.cs.split(",")]:
        t0 = time.time()
        oof = np.zeros((len(df), len(labels)), dtype=np.float32)
        for f in sorted(np.unique(folds)):
            tr, va = folds != f, folds == f
            char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                                   max_features=200_000, sublinear_tf=True)
            word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                                   max_features=50_000, sublinear_tf=True,
                                   strip_accents="unicode")
            Xc, Xw = char.fit_transform(texts[tr]), word.fit_transform(texts[tr])
            sc = StandardScaler().fit(S_all[tr])
            Xs = csr_matrix(sc.transform(S_all[tr]))
            X = hstack([Xc, Xw, Xs]).tocsr()
            Xva = hstack([char.transform(texts[va]), word.transform(texts[va]),
                          csr_matrix(sc.transform(S_all[va]))]).tocsr()
            clf = LogisticRegression(C=C, max_iter=3000, n_jobs=-1)
            clf.fit(X, y[tr])
            proba = np.zeros((va.sum(), len(labels)), dtype=np.float32)
            proba[:, clf.classes_] = clf.predict_proba(Xva)
            oof[va] = proba
        ba = balanced_accuracy_score(y, oof.argmax(1))
        results[f"C={C}"] = {"oof_balanced_accuracy": round(float(ba), 4),
                             "seconds": round(time.time() - t0, 1)}
        print(f"C={C}: OOF BA={ba:.4f}  ({time.time() - t0:.0f}s)")
        if best is None or ba > best[0]:
            best = (ba, C, oof)

    ba, C, oof = best
    cm = confusion_matrix(y, oof.argmax(1), labels=np.arange(len(labels)))
    recalls = {labels[i]: round(float(cm[i, i] / cm[i].sum()), 4) for i in range(len(labels))}

    base = json.loads(Path("results/baseline_tfidf.json").read_text(encoding="utf-8"))
    report = {
        "best_C": C,
        "oof_balanced_accuracy": round(float(ba), 4),
        "delta_vs_plain_tfidf": round(float(ba) - base["oof_balanced_accuracy"], 4),
        "sweep": results,
        "per_class_recall": recalls,
        "worst_recall": sorted(recalls.items(), key=lambda kv: kv[1])[:6],
    }
    Path(args.out_report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    np.savez_compressed(args.out_probs, probs=oof, y=y, labels=np.array(labels), folds=folds,
                        prompt_id=df.prompt_id.to_numpy())

    print(f"\nplain tfidf = {base['oof_balanced_accuracy']:.4f}  ->  +style = {ba:.4f} "
          f"({ba - base['oof_balanced_accuracy']:+.4f}), best C={C}")
    print("worst recall:", report["worst_recall"])
    print(f"wrote {args.out_report} and {args.out_probs}")


if __name__ == "__main__":
    main()
