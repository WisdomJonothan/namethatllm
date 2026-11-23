"""官方 train 上的“同题配对排序”小实验 + 主题词遮蔽对照（纯 CPU）。

问题：用官方 train（150 prompt × 18 模型）训一个只区分兄弟模型的打分函数，
      配对目标（同题内 A 的分数要高于 B）是否比普通二分类学到更多跨题信号？
      再把主题词替换成占位符，看是否只用功能词/标点/结构就够。

评估：按 prompt 分 5 折，留出折上做“同题二选一”判别准确率（每折 30 prompt）。
产物：results/paired_trio_cpu.json
"""
import json
import os
import re

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = [("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol"),
         ("gemini-3.7-flash", "gemini-3.8-flash"), ("glm-5.3", "glm-5.3-flash")]

FUNC = set("""a an the this that these those of in on at to for with from by as is are was were be been being
it its it's their there here we you they he she i not no nor but and or so if then than too very can could may
might must shall should will would do does did have has had also just only more most much many some any each
every other such what which who whom whose when where why how all both few own same s t don now""".split())
WORD = re.compile(r"[A-Za-z']+|[^A-Za-z\s]+")


def mask(text: str) -> str:
    parts = []
    for w in WORD.findall(text):
        if w.isalpha() or "'" in w:
            parts.append(w.lower() if w.lower() in FUNC else "<W>")
        else:
            parts.append(w)
    return " ".join(parts)


def featurizer(masked: bool):
    def prep(t):
        return mask(t) if masked else t
    return FeatureUnion([
        ("ch", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                               max_features=150000, sublinear_tf=True, preprocessor=prep)),
        ("wd", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                               max_features=150000, sublinear_tf=True, preprocessor=prep)),
    ])


def folds(df, k=5):
    prompts = np.array(sorted(df.prompt_id.unique()))
    rng = np.random.RandomState(0)
    rng.shuffle(prompts)
    return [set(prompts[i::k]) for i in range(k)]


def main():
    df = pd.read_csv(os.path.join(ROOT, "data", "train.csv"), dtype={"id": str})
    df["text"] = df.text.fillna("").astype(str)
    fl = folds(df)
    out = {}

    for a, b in PAIRS:
        sub = df[df.model.isin([a, b])].copy()
        if sub.empty:
            continue
        res = {}
        for masked in (False, True):
            for mode in ("binary", "paired"):
                accs = []
                for held in fl:
                    tr = sub[~sub.prompt_id.isin(held)]
                    te = sub[sub.prompt_id.isin(held)]
                    feats = featurizer(masked)
                    Xtr = feats.fit_transform(tr.text)
                    if mode == "binary":
                        ytr = (tr.model == a).astype(int).values
                        clf = LogisticRegression(C=2.0, max_iter=3000)
                        clf.fit(Xtr, ytr)
                        scorer = lambda X: clf.decision_function(X)  # noqa: E731
                    else:
                        # Bradley-Terry on same-prompt differences; inference is w.phi(text)
                        rows, ys, idx = [], [], {}
                        Xtr = Xtr.tocsr()
                        tr = tr.reset_index(drop=True)
                        for pid, g in tr.groupby("prompt_id"):
                            ia = g.index[g.model == a].tolist()
                            ib = g.index[g.model == b].tolist()
                            if not ia or not ib:
                                continue
                            for i in ia:
                                for j in ib:
                                    rows.append(Xtr[i] - Xtr[j]); ys.append(1)
                                    rows.append(Xtr[j] - Xtr[i]); ys.append(0)
                        from scipy.sparse import vstack
                        clf = LogisticRegression(C=0.5, max_iter=3000)
                        clf.fit(vstack(rows), np.array(ys))
                        scorer = lambda X: clf.decision_function(X)  # noqa: E731
                    Xte = feats.transform(te.text)
                    s = scorer(Xte)
                    te = te.reset_index(drop=True)
                    s = pd.Series(s, index=te.index)
                    ok = 0; n = 0
                    for pid, g in te.groupby("prompt_id"):
                        sv = s.loc[g.index]
                        sa = sv[(g.model == a).values]
                        sb = sv[(g.model == b).values]
                        if sa.empty or sb.empty:
                            continue
                        ok += int(sa.mean() > sb.mean()); n += 1
                    accs.append(ok / max(n, 1))
                res[f"{'masked' if masked else 'raw'}_{mode}"] = {
                    "acc": round(float(np.mean(accs)), 4),
                    "per_fold": [round(x, 3) for x in accs],
                }
        out[f"{a}__vs__{b}"] = res
        print(a, "vs", b, json.dumps(res, ensure_ascii=False))

    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    with open(os.path.join(ROOT, "results", "paired_trio_cpu.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
