"""三兄弟专家 + 先验校正扫描：专家在 eval_v2 上会误杀 luna，试试按训练先验收缩能否纠正。

结果写 results/gpt_trio_prior.json
用法：python scripts/gpt_trio_prior.py
"""
import json
import os

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIO = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
PAIRS = [("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol")]


def main():
    p = pd.read_parquet(os.path.join(ROOT, "data", "pool", "ntl_open_pool.parquet"))
    q = p[p.model.isin(TRIO)].copy()
    cnt = q.groupby("prompt_group").model.nunique()
    q = q[q.prompt_group.isin(set(cnt[cnt == 3].index))]

    feats = FeatureUnion([
        ("ch", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                               max_features=200000, sublinear_tf=True)),
        ("wd", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                               max_features=200000, sublinear_tf=True)),
    ])
    clf = LogisticRegression(C=4.0, max_iter=2000)
    clf.fit(feats.fit_transform(q.text.fillna("").astype(str)), q.model.values)
    prior = q.model.value_counts(normalize=True).reindex(TRIO).values

    fl = np.load(os.path.join(ROOT, "runs", "diag1", "final-logits.npz"), allow_pickle=True)
    ids = [str(x) for x in fl["ids"].tolist()]
    labels = [str(x) for x in fl["labels"].tolist()]
    z = np.asarray(fl["logits"], dtype=np.float64)
    ev = pd.read_csv(os.path.join(ROOT, "data", "eval_v2.csv"), dtype={"id": str})
    shipped = pd.read_parquet(os.path.join(ROOT, "runs", "diag1", "diag_pred.parquet"))
    smap = dict(zip(shipped.id.astype(str), shipped.model))
    ev = ev.set_index("id").loc[ids].reset_index()
    y = ev.model.values
    pred = np.array([smap[i] for i in ids], dtype=object)

    def ba(preds):
        return float(np.mean([(preds[y == l] == l).mean() for l in labels if (y == l).sum()]))

    base = ba(pred)
    probs = clf.predict_proba(feats.transform(ev.text.fillna("").astype(str)))
    cls = list(clf.classes_)
    pr = probs[:, [cls.index(t) for t in TRIO]]
    top2 = np.argsort(-z, axis=1)[:, :2]
    pair = [frozenset((labels[a], labels[b])) for a, b in top2]
    col = {lab: k for k, lab in enumerate(TRIO)}

    rows = []
    for alpha in [0.0, 0.3, 0.5, 0.7, 1.0]:
        adj = pr / (prior ** alpha)
        adj = adj / adj.sum(1, keepdims=True)
        for tau in [0.0, 0.5, 0.6, 0.7]:
            newp = pred.copy()
            for a, b in PAIRS:
                key = frozenset([a, b])
                for i, pr_i in enumerate(pair):
                    if pr_i != key or pred[i] != "gpt-5.6-luna":
                        continue
                    pa, pb = adj[i, col[a]], adj[i, col[b]]
                    cand, conf = (a, pa) if pa >= pb else (b, pb)
                    if cand == pred[i] or conf < tau:
                        continue
                    newp[i] = cand
            g = ba(newp) - base
            rows.append({"alpha": alpha, "tau": tau, "gain": round(g, 5),
                         "n_changed": int((newp != pred).sum()),
                         "luna": round(float((newp[y == "gpt-5.6-luna"] == "gpt-5.6-luna").mean()), 4),
                         "terra": round(float((newp[y == "gpt-5.6-terra"] == "gpt-5.6-terra").mean()), 4),
                         "sol": round(float((newp[y == "gpt-5.6-sol"] == "gpt-5.6-sol").mean()), 4)})

    out = {"base_ba": round(base, 4), "prior": dict(zip(TRIO, [round(float(v), 4) for v in prior])),
           "sweep": rows}
    with open(os.path.join(ROOT, "results", "gpt_trio_prior.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    for r in rows:
        print(f"a={r['alpha']:.1f} tau={r['tau']:.2f} gain={r['gain']:+.5f} ch={r['n_changed']:>3} "
              f"luna={r['luna']:.3f} terra={r['terra']:.3f} sol={r['sol']:.3f}")


if __name__ == "__main__":
    main()
