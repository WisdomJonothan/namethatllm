"""LM 惊讶度特征的边际价值：折内堆叠 LR，比较 2 源 vs 3 源。"""
import numpy as np, pandas as pd, json
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

f1 = np.load("results/oof_style_fine.npz", allow_pickle=True)
f2 = np.load("results/oof_ft.npz", allow_pickle=True)
y = f2["y"]; labels = [str(x) for x in f2["labels"]]; folds = f2["folds"]
P1, P2 = f1["probs"], f2["probs"]
df = pd.read_csv("data/train.csv")
ft = pd.read_csv("results/feats_train.csv", dtype={"id": str})
ids = f2.get("prompt_id") if "prompt_id" in f2 else None
ev = pd.read_csv("data/eval_v2.csv", dtype={"id": str}) if False else None
# 对齐 feat 行到 train 行：train.csv 与 feats_train.csv 同为官方 train 顺序
assert len(ft) == len(df), (len(ft), len(df))
assert (ft["id"].astype(str).values == df["id"].astype(str).values).all(), "id 顺序不一致"
cols = [c for c in ft.columns if c != "id"]
F = ft[cols].values.astype(np.float64)
F = np.nan_to_num(F, nan=0.0, posinf=0.0, neginf=0.0)
print("lm features", F.shape, "| base BA %.4f | enc BA %.4f" % (
    np.mean([(P1.argmax(1)[y == c] == c).mean() for c in range(len(labels))]),
    np.mean([(P2.argmax(1)[y == c] == c).mean() for c in range(len(labels))])))

def ba(p): return float(np.mean([(p[y == c] == c).mean() for c in range(len(labels))]))
def fold_lr(X, tr_mask=None):
    P = np.zeros((len(y), len(labels)))
    for k in range(5):
        tr, te = folds != k, folds == k
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=1.0, max_iter=3000).fit(sc.transform(X[tr]), y[tr])
        P[te] = clf.predict_proba(sc.transform(X[te]))
    return P
P3 = fold_lr(F)
print("LM-only BA %.4f" % ba(P3.argmax(1)))
lg = lambda P: np.log(np.maximum(P, 1e-9))
X2 = np.hstack([lg(P1), lg(P2)]); X3 = np.hstack([lg(P1), lg(P2), lg(P3)])
b2, b3 = ba(fold_lr(X2).argmax(1)), ba(fold_lr(X3).argmax(1))
print("stack2 %.4f | stack3 %.4f | delta %+.4f" % (b2, b3, b3 - b2))
# 仅长度/标点廉价对照：LM 特征里只保留 n_tok 与 A_mean/B_mean 之外的朴素项
cheap = ["n_tok", "A_n_tok" if "A_n_tok" in cols else "n_tok"]
Xc = np.hstack([lg(P1), lg(P2), F[:, [cols.index("n_tok")]]])
bc = ba(fold_lr(Xc).argmax(1))
print("cheap control (n_tok only) %.4f" % bc)
json.dump({"lm_only": ba(P3.argmax(1)), "stack2": b2, "stack3": b3, "delta": b3 - b2,
           "cheap_control": bc, "n_feats": F.shape[1], "cols": cols},
          open("results/lm_feat_eval.json", "w"), ensure_ascii=False, indent=1)
