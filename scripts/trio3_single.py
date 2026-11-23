"""单篇三分类：官方 train 按 prompt 五折 + 外部 eval_v2 直推。
模型 A：char_wb TF-IDF + LR（用户对照用）。
模型 B：每位作者一个字符 5-gram 语言模型（stupid backoff），按平均 log 概率判别。
"""
import json, math, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline

TASKS = {
    "gpt": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
    "claude_glm": ["claude-fable-5", "glm-5.3", "glm-5.3-flash"],
}
ORDERS = (1, 2, 3, 4, 5)
BACKOFF = 0.4


class AuthorLM:
    def __init__(self, orders=ORDERS, alpha=0.5):
        self.orders, self.alpha, self.counts, self.totals, self.vocab = orders, alpha, {}, {}, set()

    def fit(self, texts):
        for o in self.orders:
            self.counts[o] = defaultdict(Counter)
        for t in texts:
            s = t
            self.vocab.update(s)
            for o in self.orders:
                for i in range(len(s) - o + 1):
                    self.counts[o][s[i:i + o - 1]][s[i + o - 1]] += 1
        V = len(self.vocab) + 1
        self.V = V
        return self

    def score(self, text):
        tot, n = 0.0, 0
        for o in self.orders:
            for i in range(len(text) - o + 1):
                ctx, ch = text[i:i + o - 1], text[i + o - 1]
                c = self.counts[o].get(ctx)
                n += 1
                if c and c.get(ch):
                    tot += math.log(c[ch] / sum(c.values()))
                else:
                    tot += math.log(self.alpha / (self.alpha * self.V)) * (BACKOFF ** (5 - o))
        return tot / max(1, n)


def fit_lm(train_df, labels):
    return {lab: AuthorLM().fit(train_df.loc[train_df.model == lab, "text"].tolist()) for lab in labels}


def predict_lm(lms, labels, texts):
    S = np.array([[lms[l].score(t) for l in labels] for t in texts])
    return S.argmax(1), S


def ba(y, p, k):
    rec = [((p[y == c] == c).mean() if (y == c).any() else 0.0) for c in range(k)]
    return float(np.mean(rec))


def fit_clf(train_df, labels):
    clf = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1, max_features=200000, sublinear_tf=True),
        LogisticRegression(C=4, max_iter=2000))
    clf.fit(train_df.text, train_df.model)
    return clf


def main():
    tr = pd.read_csv("data/train.csv")
    ev = pd.read_csv("data/eval_v2.csv")
    out = {}
    for name, labels in TASKS.items():
        l2i = {l: i for i, l in enumerate(labels)}
        t = tr[tr.model.isin(labels)].copy(); t["y"] = t.model.map(l2i)
        e = ev[ev.model.isin(labels)].copy(); e["y"] = e.model.map(l2i)
        # A) 官方五折（按 prompt 分组）
        oof_clf = np.array([None] * len(t), dtype=object); oof_lm = np.full(len(t), -1)
        gkf = GroupKFold(n_splits=5)
        for trn, val in gkf.split(t, t.y, groups=t.prompt_id):
            a, b = t.iloc[trn], t.iloc[val]
            oof_clf[val] = fit_clf(a, labels).predict(b.text).tolist()
            p_lm, _ = predict_lm(fit_lm(a, labels), labels, b.text.tolist())
            oof_lm[val] = p_lm
        oof_clf = np.array([l2i[v] if isinstance(v, str) else int(v) for v in oof_clf], dtype=int)
        # B) 官方全量训练 → 外部直推
        clf_full = fit_clf(t, labels); lms_full = fit_lm(t, labels)
        ext_clf = np.array([l2i[v] for v in clf_full.predict(e.text)])
        ext_lm, _ = predict_lm(lms_full, labels, e.text.tolist())
        out[name] = {
            "labels": labels,
            "n_official": int(len(t)), "n_external": int(len(e)),
            "official_clf": ba(t.y.values, oof_clf, 3), "official_lm": ba(t.y.values, oof_lm, 3),
            "external_clf": ba(e.y.values, ext_clf, 3), "external_lm": ba(e.y.values, ext_lm, 3),
            "chance": 1 / 3,
        }
        print(json.dumps({name: out[name]}, ensure_ascii=False, indent=1), flush=True)
    Path("results/trio3_single.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
