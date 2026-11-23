"""Analyse the stage3 diag run: member-LP reconciliation, Apex retention,
and (the actual question) whether per-token GLM evidence separates
glm-5.3 from glm-5.3-flash better than the blended probabilities did.

Run: python scripts/analyze_stage3.py [--run runs/diag3]
Everything it needs is expected under --run (downloaded from the kernel output).
"""
import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

W = {'q14v5': 3.0, 'n14b_clean': 2.5, 'q8v5': 2.0, 'll8v5': 1.0, 'q25_14': 1.0, 'deb': 1.0}


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def ba(pred, truth):
    labels = sorted(set(truth) | set(pred))
    rec = []
    for c in labels:
        m = truth == c
        if m.sum():
            rec.append(float((pred[m] == c).mean()))
    return float(np.mean(rec)), rec


def load_run(run):
    out = {}
    p = os.path.join(run, 'final-logits.npz')
    if os.path.exists(p):
        z = np.load(p, allow_pickle=True)
        out['ids'] = [str(x) for x in z['ids']]
        out['labels'] = [str(x) for x in z['labels']]
        out['logits'] = np.asarray(z['logits'], np.float64)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default='runs/diag3')
    ap.add_argument('--eval', default='data/eval_v2.csv')
    a = ap.parse_args()
    rep = {}

    d = load_run(a.run)
    if d:
        ev = pd.read_csv(a.eval, dtype={'id': str})
        truth = dict(zip(ev.id, ev.model))
        L = d['labels']
        y = np.array([truth[i] for i in d['ids']])
        P = softmax(d['logits'])
        pred = np.array(L)[P.argmax(1)]
        rep['final_ba'] = ba(pred, y)[0]

        # member-LP reconciliation: sum the six readers with the shipped weights
        mp = os.path.join(a.run, 'diag_lp_members.npz')
        if os.path.exists(mp):
            m = np.load(mp, allow_pickle=True)
            keys = list(m.files)
            tot = None
            for k in keys:
                t = m[k].astype(np.float64)
                tot = t if tot is None else tot + t
            ref = np.load(os.path.join(a.run, 'diag_lp.npy'))
            rep['member_sum_maxabs_diff'] = float(np.abs(tot - ref).max())
            rep['member_keys_found'] = len(keys)

        # Apex retention: prediction before vs after cell 48
        pp = os.path.join(a.run, 'pred_pre_apex.parquet')
        if os.path.exists(pp):
            pre = pd.read_parquet(pp).set_index('id').loc[d['ids'], 'model'].values
            rep['ba_pre_apex'] = ba(pre, y)[0]
            rep['apex_flips'] = int((pre != pred).sum())
            rep['apex_flips_fixed'] = int(((pre != y) & (pred == y)).sum())
            rep['apex_flips_broken'] = int(((pre == y) & (pred != y)).sum())

    # ---- per-token GLM evidence ----
    files = sorted(glob.glob(os.path.join(a.run, 'v1', '**', '*_scores.npz'), recursive=True))
    rep['score_files'] = [os.path.basename(f) for f in files]
    tags = {}
    for f in files:
        tag = os.path.basename(f).replace('_scores.npz', '')
        tags.setdefault(tag, f)
    rep['tags'] = sorted(tags)
    pair = None
    for cand in (('glm53', 'flash'), ('glm53', 'glm53flash'), ('glm-5.3', 'glm-5.3-flash')):
        if cand[0] in tags and cand[1] in tags:
            pair = cand
            break
    if pair:
        def per_text(path):
            z = np.load(path, allow_pickle=True)
            ids = [str(x) for x in z['ids']]
            sc, off = z['scores'], z['offsets']
            rows = {}
            for i, tid in enumerate(ids):
                s = sc[off[i]:off[i + 1]].astype(np.float64)
                if not len(s):
                    continue
                lp = s[:, 0]
                rows[tid] = dict(
                    n=len(lp), lp_sum=lp.sum(), lp_mean=lp.mean(),
                    lp_minus_max=lp.sum() - lp.max(),
                    h_mean=s[:, 1].mean(), top1=float((s[:, 3] == 0).mean()),
                    m2_mean=s[:, 2].mean(),
                )
            return rows

        A, B = per_text(tags[pair[0]]), per_text(tags[pair[1]])
        if d:
            ev = pd.read_csv(a.eval, dtype={'id': str})
            truth = dict(zip(ev.id, ev.model))
            rows = []
            for tid in d['ids']:
                if tid in A and tid in B and truth.get(tid) in ('glm-5.3', 'glm-5.3-flash'):
                    rows.append(dict(id=tid, y=truth[tid],
                                     d_lp_mean=A[tid]['lp_mean'] - B[tid]['lp_mean'],
                                     d_lp_sum_n=(A[tid]['lp_sum'] / A[tid]['n']) - (B[tid]['lp_sum'] / B[tid]['n']),
                                     d_lp_minus_max=(A[tid]['lp_minus_max'] / A[tid]['n']) - (B[tid]['lp_minus_max'] / B[tid]['n']),
                                     d_h_mean=A[tid]['h_mean'] - B[tid]['h_mean'],
                                     d_top1=A[tid]['top1'] - B[tid]['top1'],
                                     d_m2=A[tid]['m2_mean'] - B[tid]['m2_mean']))
            df = pd.DataFrame(rows)
            rep['pair_rows'] = int(len(df))
            if len(df) > 10:
                # AUC of each delta for "the text really is A", no sklearn dependency
                def auc(x, pos):
                    order = np.argsort(x)
                    r = np.empty(len(x), float)
                    r[order] = np.arange(1, len(x) + 1)
                    n1, n0 = pos.sum(), (~pos).sum()
                    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
                pos = (df.y.values == 'glm-5.3')
                rep['auc'] = {c: auc(df[c].values, pos) for c in df.columns if c.startswith('d_')}
                rep['pair_balance'] = {str(k): int(v) for k, v in df.y.value_counts().items()}
    print(json.dumps(rep, indent=2, default=float))
    os.makedirs('results', exist_ok=True)
    with open('results/stage3_analysis.json', 'w') as f:
        json.dump(rep, f, indent=2, default=float)


if __name__ == '__main__':
    main()
