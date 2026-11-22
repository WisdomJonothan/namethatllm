"""Generate prompt-level 5-fold splits for Name That LLM.

The whole point of this script is the leakage trap: the 2,699 training rows are
150 prompts x 18 models, so a random row split puts the *same prompt* in train
and validation and inflates CV by a lot. Every split here is grouped by
prompt_id, and the script hard-fails if a prompt ever leaks across folds.

Output: data/folds.csv  (id, prompt_id, model, text, fold)
        results/folds_report.json

Usage:
    python scripts/make_folds.py --train data/train.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

N_CLASSES = 18


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/train.csv")
    ap.add_argument("--out", default="data/folds.csv")
    ap.add_argument("--report", default="results/folds_report.json")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261121)
    args = ap.parse_args()

    df = pd.read_csv(args.train)
    need = {"id", "prompt_id", "text", "model"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"train.csv missing columns: {sorted(missing)}")
    if df.prompt_id.isna().any():
        raise SystemExit("prompt_id has NaNs")

    prompts = np.array(sorted(df.prompt_id.unique()))
    rng = np.random.default_rng(args.seed)
    rng.shuffle(prompts)
    fold_of = {p: int(i % args.n_splits) for i, p in enumerate(prompts)}
    df["fold"] = df.prompt_id.map(fold_of).astype(int)

    # --- hard checks -------------------------------------------------------
    for f in range(args.n_splits):
        va = df[df.fold == f]
        tr = df[df.fold != f]
        leak = set(va.prompt_id) & set(tr.prompt_id)
        if leak:
            raise SystemExit(f"fold {f}: {len(leak)} prompts leaked across the split")
        n_cls = va.model.nunique()
        if n_cls != N_CLASSES:
            raise SystemExit(f"fold {f}: only {n_cls}/{N_CLASSES} classes in validation")
        if va.empty or tr.empty:
            raise SystemExit(f"fold {f}: empty split")

    # --- report ------------------------------------------------------------
    report = {
        "seed": args.seed,
        "n_splits": args.n_splits,
        "n_rows": int(len(df)),
        "n_prompts": int(df.prompt_id.nunique()),
        "n_classes": int(df.model.nunique()),
        "folds": [],
    }
    print(f"{'fold':>4} {'val_rows':>9} {'val_prompts':>12} {'classes':>8} {'min_cls':>8}")
    for f in range(args.n_splits):
        va = df[df.fold == f]
        counts = va.model.value_counts()
        report["folds"].append(
            {
                "fold": f,
                "val_rows": int(len(va)),
                "val_prompts": int(va.prompt_id.nunique()),
                "classes": int(va.model.nunique()),
                "min_class": int(counts.min()),
                "max_class": int(counts.max()),
            }
        )
        print(
            f"{f:>4} {len(va):>9} {va.prompt_id.nunique():>12} "
            f"{va.model.nunique():>8} {counts.min():>8}"
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.out} ({len(df)} rows) and {args.report}")


if __name__ == "__main__":
    main()
