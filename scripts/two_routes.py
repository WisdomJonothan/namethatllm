"""思路一：独立 18 类作者分类器（prompt 分组 OOF）；思路二：成对比较器 + 等改判数对照。"""
import json, numpy as np
from collections import Counter
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix

d = np.load("results/emb_train.npz", allow_pickle=True)
X, y_raw, prompt = d["X"].astype(np.float64), d["y"], d["prompt"]
labels = sorted(set(y_raw.tolist())); L = {l: i for i, l in enumerate(labels)}
y = np.array([L[v] for v in y_raw])
prompts = sorted(set(prompt.tolist()))
rng = np.random.RandomState(0); rng.shuffle(prompts)
fold_of = {p: i % 5 for i, p in enumerate(prompts)}
fold = np.array([fold_of[p] for p in prompt])

def macro_recall(yt, yp, n=len(labels)):
    rec = [float((yp[yt == c] == c).mean()) if (yt == c).any() else 0.0 for c in range(n)]
    return float(np.mean(rec)), rec

# ---------- 思路一 ----------
P = np.zeros((len(y), len(labels)))
for f in range(5):
    tr, te = fold != f, fold == f
    clf = LogisticRegression(C=1.0, max_iter=3000, n_jobs=-1).fit(X[tr], y[tr])
    P[te] = clf.predict_proba(X[te])
pred = P.argmax(1); ba1, rec1 = macro_recall(y, pred)
top2 = np.argsort(-P, axis=1)[:, :2]
print("思路一 OOF: BA %.4f acc %.4f" % (ba1, float((pred == y).mean())), flush=True)

# ---------- 思路二 ----------
def pair_experiment(la, lb, tag):
    a, b = L[la], L[lb]
    rows_ab = np.isin(y, [a, b])
    # 训练比较器：同题配对，fold 与上面一致
    pair_pred = np.full(len(y), -1)
    for f in range(5):
        tr = rows_ab & (fold != f); te = rows_ab & (fold == f)
        feats_tr = []
        for i in np.where(tr)[0]:
            other = b if y[i] == a else a
            feats_tr.append(np.concatenate([X[i], X[a], X[b]]))
        lab_tr = np.array([0 if y[i] == a else 1 for i in np.where(tr)[0]])
        clf = LogisticRegression(C=1.0, max_iter=3000, n_jobs=-1).fit(np.array(feats_tr), lab_tr)
        for i in np.where(te)[0]:
            s = clf.predict_proba(np.concatenate([X[i], X[a], X[b]]).reshape(1, -1))[0]
            pair_pred[i] = a if s[0] >= s[1] else b
    # 触发：base top2 恰为 {a,b}
    trig = (np.sort(top2, 1)[:, 0] == min(a, b)) & (np.sort(top2, 1)[:, 1] == max(a, b))
    base = pred.copy(); new = pred.copy(); ctrl = pred.copy()
    fixed = broke = 0
    for i in np.where(trig)[0]:
        cur = base[i]
        if pair_pred[i] >= 0 and pair_pred[i] != cur:
            if pair_pred[i] == y[i]: fixed += 1
            elif cur == y[i]: broke += 1
            new[i] = pair_pred[i]
    # 等改判数对照：按 |p_a-p_b| 相对 margin 从小到大挑同样多条改判
    n_flip = int((new != base).sum())
    tw = np.where(trig)[0]
    margin = np.abs(P[tw, a] - P[tw, b]) / np.maximum(P[tw, a] + P[tw, b], 1e-9)
    order = tw[np.argsort(margin)]
    for i in order[:n_flip]:
        other = b if base[i] == a else a
        ctrl[i] = other
    ba2, _ = macro_recall(y, new); baC, _ = macro_recall(y, ctrl)
    cfix = int(((ctrl == y) & (base != y)).sum()); cbrk = int(((ctrl != y) & (base == y)).sum())
    print("%s 触发 %d 行 | 比较器 BA %.4f (Δ%+.4f, 救 %d 改错 %d, 改判 %d) | 对照 BA %.4f (Δ%+.4f, 救 %d 改错 %d)" % (
        tag, int(trig.sum()), ba2, ba2 - ba1, fixed, broke, n_flip, baC, baC - ba1, cfix, cbrk), flush=True)
    return dict(tag=tag, triggers=int(trig.sum()), ba=ba2, delta=ba2 - ba1, fixed=fixed, broke=broke,
                flips=n_flip, ctrl_ba=baC, ctrl_delta=baC - ba1, ctrl_fixed=cfix, ctrl_broke=cbrk)

out = {"route1": {"ba": ba1, "acc": float((pred == y).mean()),
                  "recall": dict(zip(labels, [round(r, 4) for r in rec1]))},
       "route2": [pair_experiment("gpt-5.6-luna", "gpt-5.6-terra", "luna/terra"),
                  pair_experiment("gpt-5.6-luna", "gpt-5.6-sol", "luna/sol")]}
json.dump(out, open("results/two_routes.json", "w"), indent=1)
print("saved results/two_routes.json")
