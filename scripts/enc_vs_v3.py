"""在 eval_v2 上对齐 v3 最终预测与编码器预测：净账、逐类可救空间、融合扫描。"""
import numpy as np, pandas as pd, json
ev = pd.read_csv("data/eval_v2.csv", dtype={"id": str})
truth = dict(zip(ev.id, ev.model))
v3 = pd.read_parquet("runs/diag3/diag_pred.parquet"); v3["id"] = v3.id.astype(str)
v3p = dict(zip(v3.id, v3.model))
z = np.load("runs/ft_evalv2_probs.npz", allow_pickle=True)
labels = [str(x) for x in z["labels"]]; ids = [str(x) for x in z["ids"]]; Pe = z["probs"]
L = {l: i for i, l in enumerate(labels)}
enc = {i: labels[int(Pe[k].argmax())] for k, i in enumerate(ids)}
ids = [i for i in ids if i in truth and i in v3p]
y = np.array([L[truth[i]] for i in ids]); p3 = np.array([L[v3p[i]] for i in ids]); pe = np.array([L[enc[i]] for i in ids])
def ba(p):
    return float(np.mean([(p[y == c] == c).mean() for c in range(len(labels))]))
def rec(p):
    return [float((p[y == c] == c).mean()) for c in range(len(labels))]
print("n", len(ids), "| v3 %.4f | enc %.4f" % (ba(p3), ba(pe)))
save = (p3 != y) & (pe == y); kill = (p3 == y) & (pe != y); both_ok = (p3 == y) & (pe == y)
print("v3错&enc对 %d | v3对&enc错 %d | 都对 %d | 都错 %d" % (save.sum(), kill.sum(), both_ok.sum(), ((p3 != y) & (pe != y)).sum()))
r3, re_ = rec(p3), rec(pe)
rows = []
for c, l in enumerate(labels):
    n = int((y == c).sum())
    if n == 0: continue
    rows.append((l, n, round(r3[c], 3), round(re_[c], 3), round(re_[c] - r3[c], 3),
                 int(save[y == c].sum()), int(kill[y == c].sum())))
rows.sort(key=lambda r: r[4])
print("%-22s %4s %6s %6s %7s %5s %5s" % ("class", "n", "v3", "enc", "delta", "save", "kill"))
for r in rows:
    print("%-22s %4d %6.3f %6.3f %+7.3f %5d %5d" % r)
# 融合：v3 的最终 logits（已是概率）与编码器概率
fl = np.load("runs/diag3/final-logits.npz", allow_pickle=True)
fid = [str(x) for x in fl["ids"]]; flab = [str(x) for x in fl["labels"]]
z3 = {i: np.exp(fl["logits"][k]) for k, i in enumerate(fid)}
P3 = np.array([z3[i] / z3[i].sum() for i in ids])
best = None
for w in np.arange(0.0, 0.55, 0.05):
    P = (1 - w) * P3 + w * Pe[[ids.index(i) for i in ids]]
    b = ba(P.argmax(1)); print("w=%.2f BA %.4f" % (w, b))
    if best is None or b > best[1]: best = (w, b)
print("best w %.2f BA %.4f" % best)
json.dump({"n": len(ids), "ba_v3": ba(p3), "ba_enc": ba(pe), "save": int(save.sum()), "kill": int(kill.sum()),
           "best_w": float(best[0]), "best_ba": float(best[1]), "per_class": rows},
          open("results/enc_vs_v3.json", "w"), ensure_ascii=False, indent=1)
