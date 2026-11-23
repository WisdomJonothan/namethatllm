"""六读者按骨干消融：移除某骨干的主输出+twin+gauss，按原管线回放，量最终 BA 损失。"""
import json, sys
import numpy as np, pandas as pd
from scipy.special import log_softmax
sys.path.insert(0, 'runs/diag3')
from pngy_residual import Residual

RUN='runs/diag3'; GLM=['glm-5.3','glm-5.3-flash']; W=0.8743927164018473
train=pd.read_csv('data/train.csv'); labels=sorted(train.model.unique())
ev=pd.read_csv('data/eval_v2.csv',dtype={'id':str})
z=np.load(RUN+'/final-logits.npz',allow_pickle=True)
npz_labels=[str(x) for x in z['labels']]; ids=[str(x) for x in z['ids']]
base_lp=np.asarray(z['logits'],np.float64)[:,[npz_labels.index(l) for l in labels]]
truth=dict(zip(ev.id,ev.model)); texts=ev.set_index('id').loc[ids,'text'].fillna('').astype(str).values
y=np.array([truth[i] for i in ids]); ia,ib=labels.index(GLM[0]),labels.index(GLM[1])
model=json.load(open('build/v1_src/ll_model.json'))
feats={t:pd.read_csv(RUN+'/v1/'+t+'_features.csv',dtype={'id':str}).set_index('id') for t in ('flash','glm53')}
sel_ids=[str(i) for i in feats['flash'].index.tolist()]; sel_idx=np.array([ids.index(i) for i in sel_ids])
blocks=[feats[t].add_prefix(t+':') for t in ('flash','glm53')]
F=pd.concat(blocks,axis=1).reindex(sel_ids)
for c in model['diff_features']: F['d:'+c]=F['flash:'+c]-F['glm53:'+c]
X=F[model['features']].to_numpy(np.float64)
zs_=((X-np.asarray(model['mean']))/np.asarray(model['scale']))@np.asarray(model['coef']).T+np.asarray(model['intercept'])
ll=log_softmax(zs_,1)[:,[model['classes'].index(c) for c in labels]]
M=np.load(RUN+'/diag_lp_members.npz'); total=sum(M[k] for k in M.files).astype(np.float64)
def ba(p): return float(np.mean([(p[y==labels[c]]==labels[c]).mean() for c in range(len(labels))]))
def apex(pred):
    residual=Residual(labels).fit(train.text.fillna('').astype(str),train.model)
    rlp=residual.predict_log_probability(base_lp,texts); rp=np.asarray(labels)[rlp.argmax(1)]
    cur=pred.copy(); nonglm=~np.isin(cur,GLM); cur[nonglm]=rp[nonglm]
    P=np.exp(rlp-rlp.max(1,keepdims=True)); P/=P.sum(1,keepdims=True)
    allowed={'claude-opus-4-6','claude-opus-5','glm-5.3','glm-5.3-flash'}
    forbidden=np.array([l not in allowed for l in labels])
    no_curly=np.array([l.startswith('claude-') or l.startswith('glm-') for l in labels])
    no_gem=np.array([l.startswith('gemini-') for l in labels])
    for i,t in enumerate(texts):
        if ' \u2014 ' in t and cur[i] not in allowed:
            P[i,forbidden]=0.0
            if P[i].sum()>0: P[i]/=P[i].sum(); cur[i]=labels[P[i].argmax()]
        if '\u2019' in t and (cur[i].startswith('claude-') or cur[i].startswith('glm-')):
            P[i,no_curly]=0.0
            if P[i].sum()>0: P[i]/=P[i].sum(); cur[i]=labels[P[i].argmax()]
        if '\u2014' in t and cur[i].startswith('gemini-'):
            P[i,no_gem]=0.0
            if P[i].sum()>0: P[i]/=P[i].sum(); cur[i]=labels[P[i].argmax()]
    return cur
# 1) 标定 active：让 softmax(total/a) 复现 base_lp
ACT=16  # infer.py: lpn = total / active，active = LP 字典里幅值非零的项数（本次 16 项全非零）
P0=np.exp(base_lp); P0/=P0.sum(1,keepdims=True)
def pipeline(lp,act):
    lp=lp/act; lp=lp-lp.max(1,keepdims=True); P=np.exp(lp); P/=P.sum(1,keepdims=True)
    zz=np.log(np.maximum(P,1e-15))
    zz[sel_idx,ia]+=W*ll[:,ia]; zz[sel_idx,ib]+=W*ll[:,ib]
    return ba(apex(np.asarray(labels)[zz.argmax(1)]))
for aa in (6,16,26):
    print(f'[sanity] active={aa} readers-stage argmax BA={ba(np.asarray(labels)[(total/aa).argmax(1)]):.5f}',flush=True)
full=pipeline(total,ACT); print(f'FULL BA={full:.5f} (shipped final = 0.49941)',flush=True)
groups={'q14v5':['q14v5','q14v5_twin','q14v5_gauss'],'q8v5':['q8v5','q8v5_twin','q8v5_gauss'],
 'll8v5':['ll8v5','ll8v5_twin'],'n14b_clean':['n14b_clean','n14b_clean_twin'],
 'q25_14':['q25_14','q25_14_twin'],'deb':['deb'],'extras':['tfidf','svc4','distort']}
out={'active':ACT,'full_ba':full,'groups':{}}
for g,ks in groups.items():
    lp=total-sum(M[k] for k in ks)
    b=pipeline(lp,ACT+0)  # 同一缩放，只换内容
    out['groups'][g]={'ba':b,'delta':b-full}
    print(f"{g:12s} BA={b:.5f}  delta={b-full:+.5f}",flush=True)
json.dump(out,open('results/reader_ablation.json','w'),indent=1)
