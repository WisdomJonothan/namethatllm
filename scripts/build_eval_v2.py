#!/usr/bin/env python
"""构建"流水线没见过"的独立验证集 v2（用于诊断版 GPU 跑批 + knobs 重放）。

为什么不用 open pool：areebbb v24 的 comp_stack 把 **整份 ntl_open_pool.parquet** 当
EXTRA 训练语料（cell 34 load_extra_passages），pool 的每一行对 comp 组件都是"见过"的，
在 pool 上调 comp_w / sinkhorn 会系统性偏高。所以 v2 取两个**未被挂载、也不匹配 EXTRA
glob**（ntl_generations / gen_passages / ntl_proxy_generations / ntl_extra_passages_claude /
passages_clean / ntl_generated*）的来源：

  1) olegzholobov/name-that-llm-response-corpus 的 external_short_wide.parquet
     —— 13,642 个 prompt × 18 个模型列的完整网格，带 prompt 文本。
  2) willrice/name-that-llm-openrouter-harvest 的 passages.csv
     —— 1,000 prompt、13 个模型（无 Claude 家族），OpenRouter 官方 provider 直采。

再加两道去重：
  * 文本级：与 open pool / 官方 train 的规范化文本哈希完全相同的行剔除；
  * prompt 级：与官方 train 的 prompt 文本相同的 prompt 整组剔除。

产物：data/eval_v2.csv（列 id,prompt_id,text,model,src）、results/eval_v2.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re

import numpy as np
import pandas as pd

LABELS = [
    "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
    "gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "claude-fable-5", "claude-opus-4-6", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
    "qwen3.8-max", "glm-5.3", "glm-5.3-flash", "kimi-k3",
]


def norm(t: str) -> str:
    t = str(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def h(t: str) -> str:
    return hashlib.sha1(norm(t).encode("utf-8")).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-wide", default="data/indep/external_short_wide.parquet")
    ap.add_argument("--harvest", default="data/indep/passages.csv")
    ap.add_argument("--pool", default="data/pool/ntl_open_pool.parquet")
    ap.add_argument("--train", default="data/train.csv")
    ap.add_argument("--per-class", type=int, default=300)
    ap.add_argument("--min-words", type=int, default=40)
    ap.add_argument("--max-words", type=int, default=250)
    ap.add_argument("--max-prompts", type=int, default=420, help="harvest 最多取多少个 prompt 组")
    ap.add_argument("--corpus-prompts", type=int, default=140, help="再用 corpus 补多少个组")
    ap.add_argument("--out", default="data/eval_v2.csv")
    ap.add_argument("--report", default="results/eval_v2.json")
    ap.add_argument("--seed", type=int, default=20261122)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    stats: dict = {}

    recs = []
    # ---- 来源 1：Response Corpus 宽表 ----
    w = pd.read_parquet(args.corpus_wide)
    stats["corpus_rows"] = int(len(w))
    stats["corpus_models"] = [c for c in w.columns if c in LABELS]
    for m in stats["corpus_models"]:
        for pid, txt in zip(w["id"].astype(str), w[m]):
            if isinstance(txt, str) and txt.strip():
                recs.append(("corpus", pid, m, txt))
    # ---- 来源 2：willrice harvest ----
    hv = pd.read_csv(args.harvest)
    hv = hv[hv["label"].isin(LABELS)]
    stats["harvest_rows"] = int(len(hv))
    for pid, m, txt in zip(hv["prompt_id"].astype(str), hv["label"], hv["text"]):
        if isinstance(txt, str) and txt.strip():
            recs.append(("harvest", "wr_" + pid, m, txt))

    df = pd.DataFrame(recs, columns=["src", "prompt_id", "model", "text"])
    df["text"] = df["text"].map(norm)
    df["n_words"] = df["text"].str.split().str.len()
    stats["rows_raw"] = int(len(df))
    df = df[(df.n_words >= args.min_words) & (df.n_words <= args.max_words)]
    stats["rows_words"] = int(len(df))
    df = df.drop_duplicates(subset=["model", "text"])
    stats["rows_dedup_modeltext"] = int(len(df))

    # ---- 文本级去重：pool / 官方 train ----
    seen = set()
    pool = pd.read_parquet(args.pool, columns=["text"])
    seen |= {h(t) for t in pool["text"].tolist()}
    tr = pd.read_csv(args.train)
    seen |= {h(t) for t in tr["text"].tolist()}
    stats["hash_set"] = int(len(seen))
    before = len(df)
    df = df[~df["text"].map(h).isin(seen)]
    stats["dropped_text_seen"] = int(before - len(df))

    # ---- prompt 级去重：与官方 train 的 prompt 文本重合的整组剔除 ----
    train_prompts = set(tr["text"].map(h))
    pmap = df.groupby("prompt_id")["text"].first()
    bad_prompts = {pid for pid, t in pmap.items() if h(t) in train_prompts}
    stats["dropped_prompts_overlap_train"] = int(len(bad_prompts))
    df = df[~df["prompt_id"].isin(bad_prompts)]

    # ---- 组大小：按 prompt 整组抽，优先组内模型多的 ----
    g = df.groupby("prompt_id")["model"].nunique()
    stats["prompts_available"] = int(len(g))
    stats["group_size_quantiles"] = {
        str(q): int(np.quantile(g.values, q)) for q in (0.5, 0.75, 0.9)
    }
    grp = df.groupby("prompt_id")
    src_of = df.groupby("prompt_id")["src"].first().to_dict()
    # 两阶段：先抽 harvest（组密、prompt 真实、13 个非 Claude 类），再用 corpus 补 Claude 类
    order_by_src = {}
    for s in ("harvest", "corpus"):
        ps = [p for p in g.index if src_of.get(p) == s]
        rng.shuffle(ps)
        ps.sort(key=lambda p: -g[p])
        order_by_src[s] = ps
    picked_prompts, counts = [], {m: 0 for m in LABELS}
    for p in order_by_src["harvest"]:
        if len(picked_prompts) >= args.max_prompts:
            break
        sub = grp.get_group(p)
        need = [m for m in sub["model"].unique() if counts.get(m, 0) < args.per_class]
        if not need:
            continue
        picked_prompts.append(p)
        for m in sub["model"].unique():
            counts[m] = counts.get(m, 0) + 1
    for p in order_by_src["corpus"]:
        if len(picked_prompts) >= args.max_prompts + args.corpus_prompts:
            break
        sub = grp.get_group(p)
        need = [m for m in sub["model"].unique() if counts.get(m, 0) < args.per_class]
        if not need:
            continue
        picked_prompts.append(p)
        for m in sub["model"].unique():
            counts[m] = counts.get(m, 0) + 1

    out = df[df["prompt_id"].isin(picked_prompts)].copy()
    out["id"] = ["ev2_%06d" % i for i in range(len(out))]
    out = out[["id", "prompt_id", "text", "model", "src"]]
    stats["selected_rows"] = int(len(out))
    stats["selected_prompts"] = int(out["prompt_id"].nunique())
    stats["per_class"] = {m: int((out["model"] == m).sum()) for m in LABELS}
    stats["by_src"] = out["src"].value_counts().to_dict()
    stats["rows_per_prompt"] = round(len(out) / max(1, out["prompt_id"].nunique()), 2)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8")
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: stats[k] for k in ("selected_rows", "selected_prompts", "rows_per_prompt",
                                            "dropped_text_seen", "by_src")}, ensure_ascii=False))
    print("min per class", min(stats["per_class"].values()), "max", max(stats["per_class"].values()))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
