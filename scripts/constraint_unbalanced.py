"""在"组内各类条数不等"的情形下比较约束策略。

官方说明测试集 per-model 条数不等、public/private 混合比例不同，所以提交时
不能假设每组恰好每类一条。本脚本用训练集 OOF 概率做仿真：

  1. 按真实 prompt 分组（folds.csv 的 prompt_id），每组 17-18 条；
  2. 用 Dirichlet 重采样模拟"组内条数不等"：对每类给权重 w_c ~ Dirichlet(alpha)，
     每组按多项式分布抽 m 条（m 同原组大小），允许重复类别；
  3. 把抽到的行按预测概率重新分配真值标签（即保留该条文本的真实模型标签）；
  4. 比较 naive / Sinkhorn(uniform 列目标) / Sinkhorn(预测先验列目标) / Hungarian。

注意：重采样只改变"组内标签构成"，不改变文本与真实标签的对应关系，
因此 BA 仍是可比的。

用法：python scripts/constraint_unbalanced.py --probs results/oof_style_fine.npz --alpha 1.0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sinkhorn_probs(p: np.ndarray, target_col: np.ndarray, T: float = 1.0,
                   iters: int = 200) -> np.ndarray:
    """行硬和为 1；列目标为 target_col（长度等于类别数），按组内行数缩放。

    p: (m, C) 概率。返回 (m, C) 投影后概率。
    """
    m, C = p.shape
    q = np.power(np.maximum(p, 1e-12), 1.0 / T)
    col = target_col * m
    col = col / col.sum() * m
    for _ in range(iters):
        q /= q.sum(axis=1, keepdims=True)
        q *= (col / np.maximum(q.sum(axis=0), 1e-12))[None, :]
        q /= q.sum(axis=1, keepdims=True)
    return q


def hungarian_assign(p: np.ndarray, target_col: np.ndarray) -> np.ndarray:
    from scipy.optimize import linear_sum_assignment
    m, C = p.shape
    cols = []
    for c in range(C):
        cols.extend([c] * max(int(round(target_col[c] * m / target_col.sum())), 0))
    if len(cols) < m:
        cols.extend([int(np.argmax(target_col))] * (m - len(cols)))
    big = np.log(np.maximum(p, 1e-12))
    cost = -big[:, cols]
    r, cidx = linear_sum_assignment(cost)
    out = np.zeros(m, dtype=int)
    out[r] = np.array(cols)[cidx]
    return out


def balanced_accuracy(y: np.ndarray, pred: np.ndarray, C: int = 18) -> float:
    recalls = []
    for c in range(C):
        mask = y == c
        if mask.sum() == 0:
            continue
        recalls.append((pred[mask] == c).mean())
    return float(np.mean(recalls))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="results/oof_style_fine.npz")
    ap.add_argument("--alpha", type=float, default=1.0, help="Dirichlet 集中度，越小越不均匀")
    ap.add_argument("--drop-rate", type=float, default=0.2,
                    help="每组按此概率丢掉某类（模拟类别缺失）")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default="results/constraint_unbalanced.json")
    args = ap.parse_args()

    d = np.load(ROOT / args.probs, allow_pickle=True)
    P = d["probs"]
    y = d["y"]
    groups = d["prompt_id"]
    C = P.shape[1]

    uniq = sorted(set(groups.tolist()))
    rng = np.random.default_rng(0)

    res = {"alpha": args.alpha, "drop_rate": args.drop_rate, "seeds": []}
    for seed in range(args.seeds):
        rng = np.random.default_rng(1000 + seed)
        idx_all = []
        for g in uniq:
            rows = np.where(groups == g)[0]
            m = len(rows)
            # 每类的可用行
            pool = {c: rows[y[rows] == c] for c in range(C)}
            w = rng.dirichlet([args.alpha] * C)
            if args.drop_rate > 0:
                alive = rng.random(C) > args.drop_rate
                if alive.sum() < 3:
                    alive[rng.choice(C, 3, replace=False)] = True
                w = w * alive
                w = w / w.sum()
            counts = rng.multinomial(m, w)
            chosen = []
            for c in range(C):
                n = counts[c]
                if n == 0 or len(pool[c]) == 0:
                    continue
                # 该类行不够就允许重复抽取
                take = rng.choice(pool[c], size=n, replace=(n > len(pool[c])))
                chosen.extend(take.tolist())
            if len(chosen) < m:
                extra = rng.choice(rows, size=m - len(chosen), replace=True)
                chosen.extend(extra.tolist())
            chosen = chosen[:m]
            idx_all.extend(chosen)
        idx = np.array(idx_all)

        preds = {}
        base = P[idx].argmax(1)
        preds["naive"] = base

        s_uni, s_pri, s_hun = base.copy(), base.copy(), base.copy()
        for g in uniq:
            rows = np.where(groups[idx] == g)[0]
            if len(rows) < 3:
                continue
            p = P[idx[rows]]
            uni = np.full(C, 1.0 / C)
            prior = p.mean(0)
            prior = 0.5 * uni + 0.5 * prior
            q1 = sinkhorn_probs(p, uni)
            q2 = sinkhorn_probs(p, prior)
            s_uni[rows] = q1.argmax(1)
            s_pri[rows] = q2.argmax(1)
            s_hun[rows] = hungarian_assign(p, uni)
        preds["sinkhorn_uniform"] = s_uni
        preds["sinkhorn_prior"] = s_pri
        preds["hungarian"] = s_hun

        row = {k: round(balanced_accuracy(y[idx], v, C), 4) for k, v in preds.items()}
        row["seed"] = seed
        row["mean_group_size"] = round(float(np.mean([np.sum(groups[idx] == g) for g in uniq])), 2)
        res["seeds"].append(row)
        print(row)

    keys = ["naive", "sinkhorn_uniform", "sinkhorn_prior", "hungarian"]
    res["mean"] = {k: round(float(np.mean([s[k] for s in res["seeds"]])), 4) for k in keys}
    res["std"] = {k: round(float(np.std([s[k] for s in res["seeds"]])), 4) for k in keys}
    print("mean:", res["mean"])
    (ROOT / args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
