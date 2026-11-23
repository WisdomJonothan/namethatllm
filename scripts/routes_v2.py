"""思路二（相对自训底座）：用官方 train 的折外底座预测做触发，比较器 vs 等改判数分差对照。"""
import json, numpy as np
from sklearn.linear_model import LogisticRegression

emb = np.load("results/emb_train.npz", allow_pickle=True)
Xe, ids, prompt_e = emb["X"].astype(np.float64), emb["ids"], emb["prompt"]
for name in ("oof_style_fine", "oof_tfidf"):
    b = np.load(f"results/{name}.npz", allow_pickle=True)
    P, y, labels = b["probs"], b["y"], [str(x) for x in b["labels"]]
    L = {l: i for i, l in enumerate(labels)}
    assert (ids == b.get("ids", ids)).all() if "ids" in b else True
    pred = P.argmax(1); top2 = np.argsort(-P, 1)[:, :2]
    rec = [float((pred[y == c] == c).mean()) for c in range(len(labels))]
    ba0 = float(np.mean(rec))
    print(f"=== base {name}: BA {ba0:.4f} acc {float((pred==y).mean()):.4f}", flush=True)

    def macro(yp):
        return float(np.mean([float((yp[y == c] == c).mean()) for c in range(len(labels))]))

    for la, lb in (("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol")):
        a, bb = L[la], L[lb]
        pair_pred = np.full(len(y), -1)
        rows = np.isin(y, [a, bb])
        for f in range(5):
            tr = rows & (b["folds"] != f); te = rows & (b["folds"] == f)
            if tr.sum() < 20: continue
            F = np.array([np.concatenate([Xe[i], Xe[a], Xe[bb]]) for i in np.where(tr)[0]])
            lab = np.array([0 if y[i] == a else 1 for i in np.where(tr)[0]])
            clf = LogisticRegression(C=1.0, max_iter=3000).fit(F, lab)
            for i in np.where(te)[0]:
                s = clf.predict_proba(np.concatenate([Xe[i], Xe[a], Xe[bb]]).reshape(1, -1))[0]
                pair_pred[i] = a if s[0] >= s[1] else bb
        # 触发行
        lo, hi = min(a, bb), max(a, bb)
        trig = (np.sort(top2, 1)[:, 0] == lo) & (np.sort(top2, 1)[:, 1] == hi)
        new, ctrl = pred.copy(), pred.copy()
        fx = br = 0
        for i in np.where(trig)[0]:
            if pair_pred[i] >= 0 and pair_pred[i] != pred[i]:
                if pair_pred[i] == y[i]: fx += 1
                elif pred[i] == y[i]: br += 1
                new[i] = pair_pred[i]
        n_flip = int((new != pred).sum())
        tw = np.where(trig)[0]
        margin = np.abs(P[tw, a] - P[tw, bb]) / np.maximum(P[tw, a] + P[tw, bb], 1e-9)
        for i in tw[np.argsort(margin)][:n_flip]:
            ctrl[i] = bb if pred[i] == a else a
        cfx = int(((ctrl == y) & (pred != y)).sum()); cbr = int(((ctrl != y) & (pred == y)).sum())
        rec_new = [float((new[y == c] == c).mean()) for c in range(len(labels))]
        print(f"  {la[-4:]}/{lb[-4:]}: 触发 {int(trig.sum()):4d} 行 | 比较器 BA {macro(new):.4f} (Δ{macro(new)-ba0:+.4f}; 救 {fx} 错 {br}; 改 {n_flip}) | 对照 BA {macro(ctrl):.4f} (Δ{macro(ctrl)-ba0:+.4f}; 救 {cfx} 错 {cbr})", flush=True)
        print(f"      召回 {la}: {rec_new[a]:.4f} → 原 {rec[a]:.4f} | {lb}: {rec_new[bb]:.4f} → 原 {rec[bb]:.4f}", flush=True)
