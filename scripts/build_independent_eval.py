#!/usr/bin/env python
"""构造"独立验证集"：版本可核实 + prompt 独立 + 与已知训练来源去重。

适配 open pool v16 (ntl_open_pool.parquet) 的实际列名：
  行级：id, text, model, prompt_canon, n_words, max_sim_train_prompt,
        gen_route, gen_served_model, gen_date, flag_kb_proxy,
        flag_near_train_prompt, source_dataset, source_license

判据：
1. prompt 独立：prompt_canon 不在 --extra 用过的 prompt 集合里，
   且 max_sim_train_prompt <= --max-sim（默认 0.5）。
2. 版本可核实：gen_route 有记录（--require-route 默认开），可选要求
   gen_served_model == model（--require-served-match）。
3. 标签平衡：每类最多 --per-class 行；按 prompt 整组抽，同 prompt 不拆。
4. 可选：--min-words / --max-words 与赛题一致（40–250）。

产物：data/eval_independent.csv + results/eval_independent.json
"""
import argparse, json, os
import numpy as np
import pandas as pd

LABELS = [
    "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
    "gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "claude-fable-5", "claude-opus-4-6", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
    "qwen3.8-max", "glm-5.3", "glm-5.3-flash", "kimi-k3",
]
PROMPT_COL = "prompt_canon"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-pool", default="data/pool/ntl_open_pool.parquet")
    ap.add_argument("--extra", default="data/extra_45000.csv", help="已用于训练的 csv（只取 prompt 列）")
    ap.add_argument("--out", default="data/eval_independent.csv")
    ap.add_argument("--report", default="results/eval_independent.json")
    ap.add_argument("--per-class", type=int, default=200)
    ap.add_argument("--max-sim", type=float, default=0.5)
    ap.add_argument("--min-words", type=int, default=40)
    ap.add_argument("--max-words", type=int, default=250)
    ap.add_argument("--require-route", action="store_true", default=True)
    ap.add_argument("--no-require-route", dest="require_route", action="store_false")
    ap.add_argument("--require-served-match", action="store_true")
    ap.add_argument("--row-level", action="store_true",
                    help="只按行去重（不整组剔除 prompt），用于 18 类覆盖不足时；会在报告中标注 prompt 重叠")
    ap.add_argument("--seed", type=int, default=20261122)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    pool = pd.read_parquet(args.open_pool)
    stats = {"pool_rows": int(len(pool))}
    pool = pool[pool["model"].isin(LABELS)].copy()
    stats["after_label_filter"] = int(len(pool))

    # 词数窗口与赛题一致
    if "n_words" in pool.columns:
        w = pool["n_words"]
    else:
        w = pool["text"].astype(str).str.split().str.len()
    pool = pool[(w >= args.min_words) & (w <= args.max_words)].copy()
    stats["after_words_filter"] = int(len(pool))

    # 1) 与已训练 prompt 去重（整组）
    if args.extra and os.path.exists(args.extra):
        extra = pd.read_csv(args.extra)
        used_prompts = set()
        # ext45 的 id 形如 ext_ntlop_145769，对应 pool.id 去掉前缀；据此映射回 prompt_canon
        if "id" in extra.columns and "id" in pool.columns:
            eid = extra["id"].astype(str).str.replace(r"^ext_", "", regex=True)
            used_prompts |= set(pool.loc[pool["id"].astype(str).isin(set(eid)), PROMPT_COL].astype(str))
            stats["extra_ids_mapped"] = int(len(set(eid)))
        for col in (PROMPT_COL, "prompt_id"):
            if col in extra.columns and col in pool.columns:
                used_prompts |= set(extra[col].astype(str))
        before = len(pool)
        if args.row_level and "id" in pool.columns:
            pool = pool[~pool["id"].astype(str).str.replace(r"^ext_", "", regex=True).isin(set(eid))].copy()
            stats["dedupe_mode"] = "row"
        else:
            pool = pool[~pool[PROMPT_COL].astype(str).isin(used_prompts)].copy()
            stats["dedupe_mode"] = "prompt"
        stats["used_prompts"] = int(len(used_prompts))
        stats["prompt_overlap_rows"] = int(pool[PROMPT_COL].astype(str).isin(used_prompts).sum()) if len(pool) else 0
        stats["dropped_prompt_in_extra"] = int(before - len(pool))

    # 2) prompt 独立
    if "max_sim_train_prompt" in pool.columns:
        before = len(pool)
        pool = pool[pool["max_sim_train_prompt"].astype(float) <= args.max_sim].copy()
        stats["dropped_high_sim"] = int(before - len(pool))

    # 3) 版本可核实
    if args.require_route and "gen_route" in pool.columns:
        before = len(pool)
        route = pool["gen_route"].astype(str).str.strip().str.lower()
        pool = pool[~route.isin(["", "nan", "none", "unknown"])].copy()
        stats["dropped_no_route"] = int(before - len(pool))
    if args.require_served_match and "gen_served_model" in pool.columns:
        before = len(pool)
        same = pool["gen_served_model"].astype(str).str.strip().str.lower() == pool["model"].astype(str).str.lower()
        pool = pool[same].copy()
        stats["dropped_served_mismatch"] = int(before - len(pool))

    # 4) 按 prompt 整组、每类配额抽样
    picked = []
    for lab in LABELS:
        sub = pool[pool["model"] == lab]
        if len(sub) == 0:
            continue
        groups = list(sub.groupby(PROMPT_COL).indices.items())
        rng.shuffle(groups)
        got, rows = 0, []
        for _pid, idx in groups:
            if got >= args.per_class:
                break
            take = idx[: max(1, args.per_class - got)]
            rows.extend(sub.index[take].tolist())
            got += len(take)
        picked.extend(rows)
    out = pool.loc[sorted(set(picked))].copy()

    stats["selected_rows"] = int(len(out))
    stats["selected_prompts"] = int(out[PROMPT_COL].nunique())
    stats["per_class"] = {lab: int((out["model"] == lab).sum()) for lab in LABELS}
    if len(out) and "max_sim_train_prompt" in out.columns:
        v = out["max_sim_train_prompt"].astype(float)
        stats["sim_quantiles"] = {q: round(float(np.quantile(v, q)), 4) for q in (0.5, 0.9, 0.99)}
    if "gen_route" in out.columns:
        stats["routes"] = out["gen_route"].astype(str).value_counts().head(10).to_dict()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    keep = [c for c in ["id", PROMPT_COL, "text", "model", "max_sim_train_prompt",
                        "gen_route", "gen_served_model", "gen_date", "source_dataset"] if c in out.columns]
    out[keep].to_csv(args.out, index=False, encoding="utf-8")
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: stats.get(k) for k in ["selected_rows", "selected_prompts", "dropped_high_sim"]},
                     ensure_ascii=False))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
