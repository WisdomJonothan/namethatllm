"""Replay the Apex Grandmaster V2 post-processing stage (diag notebook cell 48)
offline on eval_v2, and attribute its per-rule effect.

Usage: python scripts/replay_apex_v2.py
Outputs: results/apex_v2_replay.json
"""
import json
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "repro"))

# pngy_residual.py is stored as a %%writefile cell; materialise the module body.
_src = os.path.join(ROOT, "repro", "pngy_residual_ref.py")
_text = open(_src, encoding="utf-8").read()
_text = re.sub(r"^%%writefile[^\n]*\n", "", _text)
_mod_path = os.path.join(ROOT, "repro", "pngy_residual.py")
open(_mod_path, "w", encoding="utf-8").write(_text)
sys.path.insert(0, os.path.join(ROOT, "repro"))

from pngy_residual import Residual  # noqa: E402


def balanced_accuracy(y_true, y_pred, labels):
    recalls = []
    for lab in labels:
        m = y_true == lab
        if m.sum():
            recalls.append(float((y_pred[m] == y_true[m]).mean()))
    return float(np.mean(recalls)), recalls


def main():
    data = os.path.join(ROOT, "data")
    train = pd.read_csv(os.path.join(data, "train.csv"), dtype={"id": str})
    ev = pd.read_csv(os.path.join(data, "eval_v2.csv"), dtype={"id": str})
    labels = sorted(train.model.unique())

    npz = np.load(os.path.join(ROOT, "runs", "diag1", "final-logits.npz"), allow_pickle=True)
    ids = [str(x) for x in npz["ids"].tolist()]
    z_labels = [str(x) for x in npz["labels"].tolist()]
    base_lp = np.asarray(npz["logits"], dtype=np.float64)
    base_lp = base_lp[:, [z_labels.index(l) for l in labels]]

    ev = ev.set_index("id").loc[ids].reset_index()
    assert ev.text.notna().all()

    base_pred = np.asarray(labels)[base_lp.argmax(1)]
    post = Residual(labels).fit(train.text.fillna("").astype(str), train.model)
    refined_lp = post.predict_log_probability(base_lp, ev.text.fillna("").astype(str))
    refined_pred = np.asarray(labels)[refined_lp.argmax(1)]

    cur = base_pred.copy()
    steps = []

    # step 1: overwrite every non-GLM row with the sibling-residual prediction
    glm_mask = np.isin(cur, ["glm-5.3", "glm-5.3-flash"])
    changed_sib = (~glm_mask) & (cur != refined_pred)
    cur[changed_sib] = refined_pred[changed_sib]
    steps.append({"rule": "sibling_residual_masked_non_glm", "changed": int(changed_sib.sum())})

    P = np.exp(refined_lp - refined_lp.max(1, keepdims=True))
    P /= P.sum(1, keepdims=True)

    ALLOWED = {"claude-opus-4-6", "claude-opus-5", "glm-5.3", "glm-5.3-flash"}
    forb1 = np.array([l not in ALLOWED for l in labels])
    nobrace = np.array([l.startswith("claude-") or l.startswith("glm-") for l in labels])
    nogem = np.array([l.startswith("gemini-") for l in labels])

    texts = ev.text.fillna("").astype(str).tolist()
    rule_hits = {"spaced_em_dash": 0, "curly_apostrophe": 0, "em_dash_gemini": 0}
    for i, t in enumerate(texts):
        if " — " in t and cur[i] not in ALLOWED:
            P[i, forb1] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
                rule_hits["spaced_em_dash"] += 1
        if "’" in t and (cur[i].startswith("claude-") or cur[i].startswith("glm-")):
            P[i, nobrace] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
                rule_hits["curly_apostrophe"] += 1
        if "—" in t and cur[i].startswith("gemini-"):
            P[i, nogem] = 0.0
            if P[i].sum() > 0:
                P[i] /= P[i].sum()
                cur[i] = labels[P[i].argmax()]
                rule_hits["em_dash_gemini"] += 1

    steps.append(rule_hits)

    shipped = pd.read_parquet(os.path.join(ROOT, "runs", "diag1", "diag_pred.parquet"))
    shipped = dict(zip(shipped.id.astype(str), shipped.model))
    shipped_pred = np.array([shipped[i] for i in ids], dtype=object)

    y = ev.model.values
    out = {
        "n": len(ev),
        "reproduced_mismatch_vs_shipped": int((cur != shipped_pred).sum()),
        "ba_base": balanced_accuracy(y, base_pred, labels)[0],
        "ba_refined_all_rows": balanced_accuracy(y, refined_pred, labels)[0],
        "ba_shipped": balanced_accuracy(y, shipped_pred, labels)[0],
        "ba_replay": balanced_accuracy(y, cur, labels)[0],
        "ba_residual_masked_only": balanced_accuracy(y, np.where(glm_mask, shipped_pred, refined_pred), labels)[0],
        "steps": steps,
        "flips": {},
    }

    # attribution: who moved base -> shipped, and did it help?
    flip_mask = base_pred != shipped_pred
    rows = []
    for i in np.where(flip_mask)[0]:
        rows.append({
            "id": ids[i],
            "true": y[i],
            "base": base_pred[i],
            "shipped": shipped_pred[i],
            "delta": int(shipped_pred[i] == y[i]) - int(base_pred[i] == y[i]),
        })
    df = pd.DataFrame(rows)
    df["pair"] = df.base + " -> " + df.shipped
    agg = (df.groupby("pair")
             .agg(n=("delta", "size"), net=("delta", "sum"))
             .sort_values("net", ascending=False))
    out["flip_pairs"] = agg.reset_index().to_dict("records")
    out["flips_total"] = int(len(df))
    out["flips_net"] = int(df.delta.sum())

    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    with open(os.path.join(ROOT, "results", "apex_v2_replay.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in out.items() if k != "flip_pairs"}, ensure_ascii=False, indent=2))
    print(agg.head(12).to_string())


if __name__ == "__main__":
    main()
