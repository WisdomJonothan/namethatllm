import json, numpy as np
d = np.load("results/fam_gpt.npz", allow_pickle=True)
S, labels, gold, ids = d["scores"], list(d["labels"]), d["gold"], d["ids"]
inn = np.isin(gold, labels)
Sc = S - S.mean(0, keepdims=True)

def auc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    allv = np.concatenate([pos, neg]); r = allv.argsort().argsort().astype(float) + 1
    return float((r[:len(pos)].sum() - len(pos)*(len(pos)+1)/2) / (len(pos)*len(neg)))

top, ctop = S.max(1), Sc.max(1)
print("rows", len(gold), "in", int(inn.sum()), "out", int((~inn).sum()))
print("AUC(in vs out) max raw %.3f centred %.3f ; median in %.2f out %.2f" % (
    auc(top[inn], top[~inn]), auc(ctop[inn], ctop[~inn]), np.median(ctop[inn]), np.median(ctop[~inn])))
y = np.array([labels.index(g) for g in gold[inn]])
print("argmax acc in-family: raw %.4f centred %.4f" % ((S[inn].argmax(1) == y).mean(), (Sc[inn].argmax(1) == y).mean()))

pred = np.load("runs/diag1/final-logits.npz", allow_pickle=True)
pl = [str(x) for x in pred["labels"]]; pz = pred["logits"]; pids = [str(x) for x in pred["ids"]]
order = {i: k for k, i in enumerate(pids)}
base = np.array([pl[int(np.argmax(pz[order[str(i)]]))] if str(i) in order else "" for i in ids])
base_in = np.isin(base, labels)
sel = ~base_in
print("base says gpt-family on %d rows (%d truly gpt); on the other %d rows (%d truly gpt) AUC=%.3f" % (
    int(base_in.sum()), int((base_in & inn).sum()), int(sel.sum()), int((sel & inn).sum()),
    auc(ctop[sel][inn[sel]], ctop[sel][~inn[sel]])))
# 第二层重排相关性：底座已判对家族、但成员可能错的行
both = base_in & inn
ba = np.array([labels.index(g) for g in gold[both]])
bb = np.array([labels.index(g) for g in base[both]])
pa = Sc[both].argmax(1)
print("subset base-in-family & truly-in: n=%d  base member acc %.4f  probe acc %.4f  probe fixes %d breaks %d" % (
    both.sum(), (bb == ba).mean(), (pa == ba).mean(), int(((pa == ba) & (bb != ba)).sum()), int(((pa != ba) & (bb == ba)).sum())))
json.dump({"n_in": int(inn.sum()), "auc_raw_max": auc(top[inn], top[~inn]), "auc_centred_max": auc(ctop[inn], ctop[~inn]),
           "centred_acc_in_family": float((Sc[inn].argmax(1) == y).mean()),
           "n_base_in_family": int(both.sum()), "base_member_acc": float((bb == ba).mean()),
           "probe_member_acc": float((pa == ba).mean())}, open("results/fam_probe.json", "w"), indent=1)
print("saved results/fam_probe.json")
