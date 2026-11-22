"""用生产版约束实现（constraint.py: TF-IDF→SVD128→complete/cosine + Sinkhorn T=1）
在 OOF 概率上复算增益，保证报告的数字与提交 notebook 完全一致。

    python scripts/sim_production.py --probs results/oof_style_fine.npz
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, recall_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constraint import apply_constraint, rebuild_groups  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="results/oof_style_fine.npz")
    ap.add_argument("--folds", default="data/folds.csv")
    ap.add_argument("--out", default="results/sim_production.json")
    ap.add_argument("--group-size", type=int, default=18)
    args = ap.parse_args()

    d = np.load(args.probs, allow_pickle=True)
    probs, y, labels = d["probs"], d["y"], list(d["labels"])
    df = pd.read_csv(args.folds)
    assert len(df) == len(y)

    naive = probs.argmax(1)
    ba0 = float(balanced_accuracy_score(y, naive))

    texts = df.text.fillna(" ").astype(str).tolist()
    groups = rebuild_groups(texts, expected_group_size=args.group_size)
    adj = apply_constraint(probs, groups, temperature=1.0)
    pred = adj.argmax(1)
    ba1 = float(balanced_accuracy_score(y, pred))

    # oracle：用真实 prompt_id 分组，看这个杠杆的上限
    true_groups = [np.where(df.prompt_id.values == g)[0] for g in df.prompt_id.unique()]
    ba2 = float(balanced_accuracy_score(y, apply_constraint(probs, true_groups).argmax(1)))

    r0 = recall_score(y, naive, labels=np.arange(len(labels)), average=None, zero_division=0)
    r1 = recall_score(y, pred, labels=np.arange(len(labels)), average=None, zero_division=0)
    rescued = sorted(((labels[i], round(float(r0[i]), 3), round(float(r1[i]), 3))
                      for i in range(len(labels))), key=lambda t: t[2] - t[1], reverse=True)[:8]

    out = {
        "probs": args.probs,
        "n": int(len(y)),
        "groups": len(groups),
        "group_size_mean": round(float(np.mean([len(g) for g in groups])), 2),
        "naive": round(ba0, 4),
        "production": {"ba": round(ba1, 4), "delta": round(ba1 - ba0, 4),
                       "changed": int((pred != naive).sum())},
        "oracle_groups": {"ba": round(ba2, 4), "delta": round(ba2 - ba0, 4)},
        "rescued": [{"class": c, "naive": a, "prod": b} for c, a, b in rescued],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
