"""思路二复测：底座换成 TF-IDF+微调编码器 融合（OOF），比较器 vs 等改判对照。"""
import numpy as np
from sklearn.linear_model import LogisticRegression
emb = np.load("results/emb_train.npz", allow_pickle=True)
Xe = emb["X"].astype(np.float64)
f = np.load("results/oof_ft.npz", allow_pickle=True)
Pf, y, labels, folds = f["probs"], f["y"], [str(x) for x in f["labels"]], f["folds"]
Pb = np.load("results/oof_style_fine.npz", allow_pickle=True)["probs"]
P = 0.6 * Pb + 0.4 * Pf
L = {l: i for i, l in enumerate(labels)}
def rec(p): return [float((p[y == c] == c).mean()) for c in range(len(labels))]
def ba(p): return float(np.mean(rec(p)))
pred = P.argmax(1); top2 = np.argsort(-P, 1)[:, :2]
print("blended base BA %.4f" % ba(pred))
for la, lb in (("gpt-5.6-luna", "gpt-5.6-terra"), ("gpt-5.6-luna", "gpt-5.6-sol")):
    a, bb = L[la], L[lb]
    rows = np.isin(y, [a, bb]); pp = np.full(len(y), -1)
    for k in range(5):
        tr, te = rows & (folds != k), rows & (folds == k)
        F = np.array([np.concatenate([Xe[i], Xe[a], Xe[bb]]) for i in np.where(tr)[0]])
        lab = np.array([0 if y[i] == a else 1 for i in np.where(tr)[0]])
        clf = LogisticRegression(C=1.0, max_iter=3000).fit(F, lab)
        for i in np.where(te)[0]:
            s = clf.predict_proba(np.concatenate([Xe[i], Xe[a], Xe[bb]]).reshape(1, -1))[0]
            pp[i] = a if s[0] >= s[1] else bb
    lo, hi = min(a, bb), max(a, bb)
    trig = (np.sort(top2, 1)[:, 0] == lo) & (np.sort(top2, 1)[:, 1] == hi)
    new, ctrl = pred.copy(), pred.copy(); fx = br = 0
    for i in np.where(trig)[0]:
        if pp[i] >= 0 and pp[i] != pred[i]:
            if pp[i] == y[i]: fx += 1
            elif pred[i] == y[i]: br += 1
            new[i] = pp[i]
    n = int((new != pred).sum()); tw = np.where(trig)[0]
    m = np.abs(P[tw, a] - P[tw, bb]) / np.maximum(P[tw, a] + P[tw, bb], 1e-9)
    for i in tw[np.argsort(m)][:n]: ctrl[i] = bb if pred[i] == a else a
    cfx = int(((ctrl == y) & (pred != y)).sum()); cbr = int(((ctrl != y) & (pred == y)).sum())
    print("%s: 触发 %d | 比较器 BA %.4f (Δ%+.4f, 救%d 错%d, 改%d) | 对照 BA %.4f (Δ%+.4f, 救%d 错%d)" % (
        f"{la[-4:]}/{lb[-4:]}", int(trig.sum()), ba(new), ba(new) - ba(pred), fx, br, n,
        ba(ctrl), ba(ctrl) - ba(pred), cfx, cbr))
