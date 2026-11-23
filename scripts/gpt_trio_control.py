"""等改判预算对照：GPT 专家的 169 次改判 vs “把 luna 最不确定的那些篇改走”。

回答的问题：专家究竟是在识别 terra/sol，还是仅仅把过多 luna 拨回去了一部分？
两者改判数相同，直接比精度。

结果写 results/gpt_trio_control.json
用法：python scripts/gpt_trio_control.py
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
    # ---- specialist -------------------------------------------------------
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

    # ---- eval side --------------------------------------------------------
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
    L2I = {l: k for k, l in enumerate(labels)}

    def ba(preds):
        return float(np.mean([(preds[y == l] == l).mean() for l in labels if (y == l).sum()]))

    base = ba(pred)
    probs = clf.predict_proba(feats.transform(ev.text.fillna("").astype(str)))
    cls = list(clf.classes_)
    pr = probs[:, [cls.index(t) for t in TRIO]]
    top2 = np.argsort(-z, axis=1)[:, :2]
    pair = [frozenset((labels[a], labels[b])) for a, b in top2]

    rows = np.arange(len(ids))
    spec_flips, margin_flips = {}, {}
    for a, b in PAIRS:
        key = frozenset([a, b])
        in_pair = np.array([p_ == key for p_ in pair])
        is_luna = in_pair & (pred == "gpt-5.6-luna")
        li, bi = L2I[a], L2I[b]
        # specialist says the sibling
        says_sib = pr[:, 0 + list(cls).index(b)] if False else None
        pa = probs[:, cls.index(a)]
        pb = probs[:, cls.index(b)]
        spec_flips[key] = set(rows[is_luna & (pb > pa)].tolist())
        margin = z[:, li] - z[:, bi]          # >0 means luna already ahead
        order = rows[is_luna][np.argsort(margin[is_luna])]
        margin_flips[key] = order

    S = sorted(set().union(*[v for v in spec_flips.values()]))
    k = len(S)
    # control: take the k globally least-confident luna rows (smallest margin within their own pair)
    all_margin = []
    for key, order in margin_flips.items():
        a, b = sorted(key)
        marg = z[order, L2I[a]] - z[order, L2I[b]]
        all_margin.extend(zip(marg.tolist(), order.tolist()))
    all_margin.sort(key=lambda t: t[0])
    C = [i for _, i in all_margin[:k]]

    def evaluate(flip_idx):
        newp = pred.copy()
        for i in flip_idx:
            key = pair[i]
            a, b = sorted(key)
            newp[i] = b if pred[i] == a else a
        flipped = [i for i in flip_idx if newp[i] != pred[i]]
        gain = ba(newp) - base
        hits = [i for i in flipped if y[i] == newp[i]]
        return {"n_flipped": len(flipped), "gain": round(gain, 5),
                "precision": round(len(hits) / max(1, len(flipped)), 4),
                "rescued": len(hits), "broken": len(flipped) - len(hits)}

    out = {"base_ba": round(base, 4), "specialist": evaluate(S), "margin_control": evaluate(C),
           "overlap": len(set(S) & set(C))}

    # disagreement rows
    only_s = sorted(set(S) - set(C))
    only_c = sorted(set(C) - set(S))
    out["disagreement"] = {
        "specialist_only": evaluate(only_s),
        "control_only": evaluate(only_c),
    }

    # control best case over k
    sweep = []
    for kk in [40, 80, 120, 169, 220, 300]:
        idx = [i for _, i in all_margin[:kk]]
        e = evaluate(idx)
        e["k"] = kk
        sweep.append(e)
    out["control_sweep"] = sweep

    with open(os.path.join(ROOT, "results", "gpt_trio_control.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "control_sweep"}, ensure_ascii=False, indent=2))
    print("\ncontrol sweep:")
    for e in sweep:
        print(f"  k={e['k']:>3} flipped={e['n_flipped']:>3} gain={e['gain']:+.5f} prec={e['precision']:.3f}")


if __name__ == "__main__":
    main()
