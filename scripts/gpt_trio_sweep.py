"""三兄弟专家的门控扫描：专家有信息但会误杀 luna，试试加置信门限能否只收割 terra/sol。

输入：scripts/gpt_trio_expert.py 里同样的 pool 三兄弟训练集 + eval_v2 预测
输出：results/gpt_trio_sweep.json

用法：python scripts/gpt_trio_sweep.py
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

    rows = []
    for tau in [0.0, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
        for mode in ["both", "from_luna"]:
            newp = pred.copy()
            for a, b in PAIRS:
                key = frozenset([a, b])
                col = {lab: k for k, lab in enumerate(TRIO)}
                for i, pr_i in enumerate(pair):
                    if pr_i != key or pred[i] not in (a, b):
                        continue
                    pa, pb = pr[i, col[a]], pr[i, col[b]]
                    cand, conf = (a, pa) if pa >= pb else (b, pb)
                    if cand == pred[i] or conf < tau:
                        continue
                    if mode == "from_luna" and pred[i] != "gpt-5.6-luna":
                        continue
                    newp[i] = cand
            g = ba(newp) - base
            luna = y == "gpt-5.6-luna"
            rows.append({
                "tau": tau, "mode": mode, "gain": round(g, 5),
                "luna_acc": round(float((newp[luna] == "gpt-5.6-luna").mean()), 4),
                "terra_acc": round(float((newp[y == "gpt-5.6-terra"] == "gpt-5.6-terra").mean()), 4),
                "sol_acc": round(float((newp[y == "gpt-5.6-sol"] == "gpt-5.6-sol").mean()), 4),
                "n_changed": int((newp != pred).sum()),
            })

    luna0 = float((pred[y == "gpt-5.6-luna"] == "gpt-5.6-luna").mean())
    out = {"base_ba": round(base, 4), "luna_acc_baseline": round(luna0, 4),
           "terra_acc_baseline": round(float((pred[y == "gpt-5.6-terra"] == "gpt-5.6-terra").mean()), 4),
           "sol_acc_baseline": round(float((pred[y == "gpt-5.6-sol"] == "gpt-5.6-sol").mean()), 4),
           "sweep": rows}
    with open(os.path.join(ROOT, "results", "gpt_trio_sweep.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
