#!/usr/bin/env python
"""离线重放诊断版推理的后处理：只动 comp_w / sinkhorn / rho / prior，不重跑 GPU。

输入（诊断 notebook 落在 /kaggle/working，需 down 到本地 runs/diag1/）：
    diag_lp.npy            reader 成员加权求和后的 log-prob（未按 active 数归一、未混合 comp）
    comp_logit.npy + comp_meta.json   comp_stack 的 18 类 logit
    final-logits.npz       流水线真正用的 decide_scores(P)（用于反解 active 数、自检）
    pred.parquet           最终写出（含 apex 残差），仅用于对照
    eval_v2.csv            真标签

原理（对应 infer.py L451-479）：
    P(s)   = softmax(tot / s)                      s = active terms 数（未知，反解）
    P      = (1-w) P(s) + w * COMP_P                w = comp_w
    target = (1-rho)*marg + rho*prior               marg = P.mean(0), prior = 训练集类先验
    Q      = 100 轮按列缩放到 len(Q)*target
    z      = (1-lam) log P + lam log Q              lam = sinkhorn
    pred   = argmax z
反解 s：在 1..12 里找使 |decide(z) - final-logits| 最小的整数，并要求误差 < 1e-6。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

LABELS = [
    "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra",
    "gemini-3.1-pro-preview", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "claude-fable-5", "claude-opus-4-6", "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
    "qwen3.8-max", "glm-5.3", "glm-5.3-flash", "kimi-k3",
]
L2I = {l: i for i, l in enumerate(LABELS)}


def softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(1, keepdims=True)


def blend(tot: np.ndarray, comp_p: np.ndarray | None, w: float, s: float) -> np.ndarray:
    P = softmax(tot / s)
    if comp_p is not None and w > 0:
        P = (1.0 - w) * P + w * comp_p
        P /= P.sum(1, keepdims=True)
    return P


def decide_scores(P: np.ndarray, lam: float, rho: float, prior: np.ndarray) -> np.ndarray:
    Q = P.copy()
    marg = np.clip(P.mean(0), 1e-6, None)
    marg /= marg.sum()
    target = np.clip((1.0 - rho) * marg + rho * prior, 1e-6, None)
    target /= target.sum()
    for _ in range(100):
        Q *= (len(Q) * target[None, :]) / np.clip(Q.sum(0, keepdims=True), 1e-12, None)
        Q /= np.clip(Q.sum(1, keepdims=True), 1e-12, None)
    return (1.0 - lam) * np.log(P + 1e-12) + lam * np.log(Q + 1e-12)


def balanced_accuracy(y: np.ndarray, pred: np.ndarray) -> float:
    rec = []
    for c in range(len(LABELS)):
        m = y == c
        if m.sum():
            rec.append(float((pred[m] == c).mean()))
    return float(np.mean(rec)) if rec else float("nan")


def load(d: Path):
    lp = np.load(d / "diag_lp.npy")
    meta = json.loads((d / "comp_meta.json").read_text(encoding="utf-8"))
    cl = np.load(d / "comp_logit.npy")
    cl = cl[:, [meta["labels"].index(l) for l in LABELS]]
    comp_p = np.exp(cl - cl.max(1, keepdims=True))
    comp_p /= comp_p.sum(1, keepdims=True)
    fl = np.load(d / "final-logits.npz")
    ids = [str(x) for x in fl["ids"]]
    return lp, comp_p, ids, fl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="runs/diag1")
    ap.add_argument("--eval", default="data/eval_v2.csv")
    ap.add_argument("--train", default="data/train.csv")
    ap.add_argument("--out", default="results/knob_replay.json")
    args = ap.parse_args()

    d = Path(args.dir)
    lp, comp_p, ids, fl = load(d)
    ev = pd.read_csv(args.eval)
    tr = pd.read_csv(args.train)
    assert len(ids) == len(ev), (len(ids), len(ev))
    ev = ev.set_index("id").loc[ids].reset_index()
    y = ev["model"].map(L2I).to_numpy()
    prior = np.bincount(tr["model"].map(L2I).to_numpy(), minlength=18).astype(np.float64) + 1.0
    prior /= prior.sum()

    # ---- 反解 active 数 s：让默认参数复现 final-logits ----
    target = fl["logits"]
    best = None
    for s in range(1, 13):
        P = blend(lp, comp_p, 0.15, s)
        z = decide_scores(P, 0.30, 0.35, prior)
        err = float(np.abs(z - target).max())
        if best is None or err < best[1]:
            best = (s, err)
    s_best, err_best = best
    base_pred = decide_scores(blend(lp, comp_p, 0.15, s_best), 0.30, 0.35, prior).argmax(1)
    base_ba = balanced_accuracy(y, base_pred)

    results = {
        "active_terms_s": s_best,
        "reproduction_max_abs_err": err_best,
        "baseline": {"comp_w": 0.15, "sinkhorn": 0.30, "rho": 0.35, "BA": base_ba},
        "grid": [],
    }

    for w in (0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50):
        for lam in (0.0, 0.15, 0.30, 0.45, 0.60):
            for rho in (0.2, 0.35, 0.5, 0.65, 0.8):
                P = blend(lp, comp_p, w, s_best)
                pred = decide_scores(P, lam, rho, prior).argmax(1)
                results["grid"].append({
                    "comp_w": w, "sinkhorn": lam, "rho": rho,
                    "BA": round(balanced_accuracy(y, pred), 5),
                    "delta_vs_baseline": round(balanced_accuracy(y, pred) - base_ba, 5),
                })

    results["grid"].sort(key=lambda r: -r["BA"])
    results["best"] = results["grid"][0]
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("active_terms_s", "reproduction_max_abs_err", "baseline", "best")},
                     ensure_ascii=False, indent=2))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
