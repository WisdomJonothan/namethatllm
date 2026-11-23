"""固定 token 预算下的 GLM 队列顺序回放：原顺序 vs 高家族概率×版本歧义 vs 价值上界。"""
import json, sys
import numpy as np, pandas as pd
from scipy.special import log_softmax
sys.path.insert(0, 'runs/diag3')
from pngy_residual import Residual

RUN = 'runs/diag3'; GLM = ['glm-5.3', 'glm-5.3-flash']; W = 0.8743927164018473
train = pd.read_csv('data/train.csv'); labels = sorted(train.model.unique())
ev = pd.read_csv('data/eval_v2.csv', dtype={'id': str})
z = np.load(RUN + '/final-logits.npz', allow_pickle=True)
npz_labels = [str(x) for x in z['labels']]; ids = [str(x) for x in z['ids']]
base_lp = np.asarray(z['logits'], np.float64)[:, [npz_labels.index(l) for l in labels]]
truth = dict(zip(ev.id, ev.model)); texts = ev.set_index('id').loc[ids, 'text'].fillna('').astype(str).values
y = np.array([truth[i] for i in ids]); ia, ib = labels.index(GLM[0]), labels.index(GLM[1])
model = json.load(open('build/v1_src/ll_model.json'))
feats = {t: pd.read_csv(RUN + '/v1/' + t + '_features.csv', dtype={'id': str}).set_index('id') for t in ('flash', 'glm53')}
sel_ids = [str(i) for i in feats['flash'].index.tolist()]; sel_idx = np.array([ids.index(i) for i in sel_ids])
blocks = [feats[t].add_prefix(t + ':') for t in ('flash', 'glm53')]
F = pd.concat(blocks, axis=1).reindex(sel_ids)
for c in model['diff_features']: F['d:' + c] = F['flash:' + c] - F['glm53:' + c]
X = F[model['features']].to_numpy(np.float64)
zs_ = ((X - np.asarray(model['mean'])) / np.asarray(model['scale'])) @ np.asarray(model['coef']).T + np.asarray(model['intercept'])
ll = log_softmax(zs_, 1)[:, [model['classes'].index(c) for c in labels]]
P0 = np.exp(base_lp - base_lp.max(1, keepdims=True)); P0 /= P0.sum(1, keepdims=True)
mass = P0[:, ia] + P0[:, ib]; rel = np.abs(P0[:, ia] - P0[:, ib]) / np.maximum(mass, 1e-12)
def ba(p): return float(np.mean([(p[y == labels[c]] == labels[c]).mean() for c in range(len(labels))]))
def apex(pred):
    residual = Residual(labels).fit(train.text.fillna('').astype(str), train.model)
    rlp = residual.predict_log_probability(base_lp, texts); rp = np.asarray(labels)[rlp.argmax(1)]
    cur = pred.copy(); nonglm = ~np.isin(cur, GLM); cur[nonglm] = rp[nonglm]
    P = np.exp(rlp - rlp.max(1, keepdims=True)); P /= P.sum(1, keepdims=True)
    allowed = {'claude-opus-4-6', 'claude-opus-5', 'glm-5.3', 'glm-5.3-flash'}
    forbidden = np.array([l not in allowed for l in labels])
    no_curly = np.array([l.startswith('claude-') or l.startswith('glm-') for l in labels])
    no_gem = np.array([l.startswith('gemini-') for l in labels])
    for i, t in enumerate(texts):
        if ' \u2014 ' in t and cur[i] not in allowed:
            P[i, forbidden] = 0.0
            if P[i].sum() > 0: P[i] /= P[i].sum(); cur[i] = labels[P[i].argmax()]
        if '\u2019' in t and (cur[i].startswith('claude-') or cur[i].startswith('glm-')):
            P[i, no_curly] = 0.0
            if P[i].sum() > 0: P[i] /= P[i].sum(); cur[i] = labels[P[i].argmax()]
        if '\u2014' in t and cur[i].startswith('gemini-'):
            P[i, no_gem] = 0.0
            if P[i].sum() > 0: P[i] /= P[i].sum(); cur[i] = labels[P[i].argmax()]
    return cur
q2_all = ll[:, [ia, ib]]
# 逐篇价值：加上 W*ll 后是否从错变对
base_pred = np.asarray(labels)[base_lp.argmax(1)]
vals = []
for k, i in enumerate(sel_idx):
    zz = base_lp[i].copy(); zz[ia] += W * ll[k, ia]; zz[ib] += W * ll[k, ib]
    newp = labels[zz.argmax()]; old = base_pred[i]
    if (old != y[i]) and (newp == y[i]): vals.append(1)     # 救
    elif (old == y[i]) and (newp != y[i]): vals.append(-1)  # 杀
    else: vals.append(0)
vals = np.array(vals)
orig_order = np.argsort(-mass[sel_idx]); alt_order = np.argsort(-(mass[sel_idx] * (1 - rel[sel_idx])))
val_order = np.argsort(-vals)
rows = []
for k in (50, 100, 200, 300, 400, 553):
    res = {}
    for name, order in (('orig', orig_order), ('alt', alt_order), ('oracle', val_order)):
        keep = order[:k]; zs = base_lp.copy()
        for j in keep:
            zs[sel_idx[j]] += W * ll[j]
        res[name] = ba(apex(np.asarray(labels)[zs.argmax(1)]))
    rows.append((k, res['orig'], res['alt'], res['oracle']))
    print("k=%3d  orig %.4f  alt %.4f  oracle-best-order %.4f" % (k, res['orig'], res['alt'], res['oracle']), flush=True)
json.dump(rows, open('results/queue_replay.json', 'w'))
