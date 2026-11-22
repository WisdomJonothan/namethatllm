"""Variant sweep for the Apex V2 typographic invariants.

Answers three questions:
  1. which rule produced the flips that cost BA on eval_v2
  2. what happens if rule 1 is restricted (Claude-only / margin-gated / dropped)
  3. why rule 1 looks right in-domain but fires badly here (dash-row class mix)

Outputs: results/apex_v2_variants.json
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "repro"))
from pngy_residual import Residual  # noqa: E402

ALLOWED = {"claude-opus-4-6", "claude-opus-5", "glm-5.3", "glm-5.3-flash"}
SPACED = (" — ",)


def ba(y, p, labels):
    r = [float((p[y == l] == l).mean()) for l in labels if (y == l).sum()]
    return float(np.mean(r)), r


def apply_rules(base, refined_lp, texts, labels, use_sib=True, r1="full", r1_margin=0.0, r2r3=True):
    cur = base.copy()
    if use_sib:
        glm = np.isin(cur, ["glm-5.3", "glm-5.3-flash"])
        rp = np.asarray(labels)[refined_lp.argmax(1)]
        m = (~glm) & (cur != rp)
        cur[m] = rp[m]
    P = np.exp(refined_lp - refined_lp.max(1, keepdims=True))
    P /= P.sum(1, keepdims=True)
    forb = np.array([l not in ALLOWED for l in labels])
    nobrace = np.array([l.startswith("claude-") or l.startswith("glm-") for l in labels])
    nogem = np.array([l.startswith("gemini-") for l in labels])
    hits = {"spaced": 0, "curly": 0, "gem": 0}
    for i, t in enumerate(texts):
        if r1 != "off" and any(s in t for s in SPACED) and cur[i] not in ALLOWED:
            gate = True
            if r1 == "claude_only":
                gate = cur[i].startswith("claude-")
            elif r1 == "margin":
                p = P[i].copy()
                p[forb] = 0.0
                if p.sum() <= 0:
                    gate = False
                else:
                    p /= p.sum()
                    keep = p[labels.index(cur[i])]
                    best = p.max()
                    gate = (best - keep) >= r1_margin
            if gate:
                P[i, forb] = 0.0
                if P[i].sum() > 0:
                    P[i] /= P[i].sum()
                    cur[i] = labels[P[i].argmax()]
                    hits["spaced"] += 1
        if r2r3 and "’" in t and (cur[i].startswith("claude-") or cur[i].startswith("glm-")):
            P[i, nobrace] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
                hits["curly"] += 1
        if r2r3 and "—" in t and cur[i].startswith("gemini-"):
            P[i, nogem] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
                hits["gem"] += 1
    return cur, hits


def main():
    data = os.path.join(ROOT, "data")
    train = pd.read_csv(os.path.join(data, "train.csv"), dtype={"id": str})
    ev = pd.read_csv(os.path.join(data, "eval_v2.csv"), dtype={"id": str})
    labels = sorted(train.model.unique())

    npz = np.load(os.path.join(ROOT, "runs", "diag1", "final-logits.npz"), allow_pickle=True)
    ids = [str(x) for x in npz["ids"].tolist()]
    zl = [str(x) for x in npz["labels"].tolist()]
    base_lp = np.asarray(npz["logits"], dtype=np.float64)[:, [zl.index(l) for l in labels]]
    ev = ev.set_index("id").loc[ids].reset_index()
    texts = ev.text.fillna("").astype(str).tolist()
    y = ev.model.values
    base = np.asarray(labels)[base_lp.argmax(1)]
    refined_lp = Residual(labels).fit(
        train.text.fillna("").astype(str), train.model
    ).predict_log_probability(base_lp, np.asarray(texts))

    variants = {}
    for name, kw in [
        ("base", None),
        ("sib_plus_r23", dict(use_sib=True, r1="off")),
        ("ship_full", dict(use_sib=True, r1="full")),
        ("r1_claude_only", dict(use_sib=True, r1="claude_only")),
        ("r1_margin_0.30", dict(use_sib=True, r1="margin", r1_margin=0.30)),
        ("r1_margin_0.60", dict(use_sib=True, r1="margin", r1_margin=0.60)),
        ("residual_only", dict(use_sib=True, r1="off", r2r3=False)),
        ("r23_only", dict(use_sib=False, r1="off")),
    ]:
        if kw is None:
            pred, hits = base, {}
        else:
            pred, hits = apply_rules(base, refined_lp, texts, labels, **kw)
        score, per = ba(y, pred, labels)
        variants[name] = {
            "ba": round(score, 4),
            "delta_vs_base": round(score - float(ba(y, base, labels)[0]), 4),
            "hits": hits,
            "worst_recall": sorted(zip(labels, [round(v, 3) for v in per]), key=lambda t: t[1])[:5],
        }

    # class mix of spaced-em-dash rows: train vs eval_v2
    def mix(df, label_col):
        m = df.text.fillna("").astype(str).str.contains(" — ", regex=False)
        d = df[m][label_col].value_counts()
        tot = int(m.sum())
        return {
            "rows_with_spaced_dash": tot,
            "share_of_data": round(tot / len(df), 3),
            "allowed_hit_rate": round(float(d.reindex(list(ALLOWED)).fillna(0).sum() / max(tot, 1)), 3),
            "top_classes": d.head(6).to_dict(),
        }

    out = {
        "variants": variants,
        "dash_mix": {"train": mix(train, "model"), "eval_v2": mix(ev, "model")},
        "n": len(ev),
    }
    with open(os.path.join(ROOT, "results", "apex_v2_variants.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
