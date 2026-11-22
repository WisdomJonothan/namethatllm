"""从 open pool 里抽一份平衡的外部训练补充集。

pool 里 23.1 万条、18 类，prompt 与官方训练集几乎不重叠（max_sim_train_prompt
中位数 0.084、p99 0.318），所以按类别平衡抽一小份掺进去是安全的。

    .venv/bin/python scripts/make_extra.py --pool data/pool/ntl_open_pool.parquet \
        --per-class 30 --out data/extra_540.csv
"""
from __future__ import annotations

import argparse

import pandas as pd

OFFICIAL = ["gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
            "gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.7-flash",
            "gemini-3.8-flash", "claude-fable-5", "claude-opus-4-6", "claude-opus-4-8",
            "claude-opus-5", "claude-sonnet-5", "qwen3.8-max", "glm-5.3",
            "glm-5.3-flash", "kimi-k3"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--per-class", type=int, default=30)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-sim", type=float, default=0.5)
    args = ap.parse_args()

    d = pd.read_parquet(args.pool)
    d = d[d.model.isin(OFFICIAL)].copy()
    d["n_words"] = pd.to_numeric(d.n_words, errors="coerce")
    d = d[d.n_words.between(40, 250)]
    sim = pd.to_numeric(d.max_sim_train_prompt, errors="coerce").fillna(0)
    d = d[sim <= args.max_sim]
    d["text"] = d.text.fillna("").astype(str)
    d = d[d.text.str.split().str.len().between(40, 250)]
    d = d.drop_duplicates(subset=["text"])

    parts = []
    for i, m in enumerate(OFFICIAL):
        sub = d[d.model == m]
        if len(sub) > args.per_class:
            # 每个 prompt 只取一条，避免同 prompt 多条挤占配额
            sub = sub.drop_duplicates(subset=["prompt_canon"]).sample(
                n=args.per_class, random_state=args.seed + i)
        parts.append(sub)
    out = pd.concat(parts)
    out = out.assign(prompt_id="ext_" + out.prompt_group.astype(str),
                     id="ext_" + out.id.astype(str))
    out = out[["id", "prompt_id", "text", "model"]]
    out.to_csv(args.out, index=False)
    print(out.shape, out.model.value_counts().min(), out.model.value_counts().max())
    print("prompts:", out.prompt_id.nunique())


if __name__ == "__main__":
    main()
