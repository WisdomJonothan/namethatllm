"""Mine *deterministic* typographic artifacts (A3 from the scouting report).

Idea: 18 frontier models differ in house style (em dash, curly quotes, bullet
marker, Oxford comma, heading style...). A handful of such habits may be
near-zero-variance per model: the model basically always / never uses them.
If a habit is (a) very common for exactly one model and (b) rare for the other
17, then "feature present" is almost a hard rule.

Public write-ups only hard-code ~4 such rules and apply them as a *model-level
mask*, which is risky: a false positive nukes a whole class. We instead mine
rules, then measure each rule's precision on an INDEPENDENT set (eval_v2, built
from corpora that never entered any training step).

Outputs results/artifact_rules.json with per-rule train stats / eval precision /
coverage, so we can decide whether a soft version is worth wiring into the
notebook.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\NameThatLLM")
TRAIN = ROOT / "data" / "train.csv"
EVAL = ROOT / "data" / "eval_v2.csv"
OUT = ROOT / "results" / "artifact_rules.json"

# --- candidate typographic / formatting habits -------------------------------
PATTERNS: dict[str, str] = {
    "em_dash": r"—",
    "en_dash": r"–",
    "spaced_em_dash": r"\s—\s",
    "curly_apos": r"’",
    "curly_dquote": r"[“”]",
    "straight_dquote": r'"',
    "ellipsis_char": r"…",
    "ellipsis_dots": r"\.\.\.",
    "semicolon": r";",
    "colon_space": r":\s",
    "oxford_comma": r",\s+and\s",
    "double_space": r"[^\n]  +[^\n]",
    "markdown_bold": r"\*\*[^*\n]+\*\*",
    "markdown_heading": r"(?m)^#{1,6} ",
    "markdown_bullet": r"(?m)^\s*[-*]\s+",
    "numbered_list": r"(?m)^\s*\d+\.\s+",
    "backtick": r"`",
    "paren_eg": r"\(e\.g\.",
    "comma_eg": r"e\.g\.,",
    "ie_comma": r"i\.e\.,",
    "em_dash_pair": r"—[^—\n]{3,80}—",
    "single_quote_word": r"(?<![\w])'[a-zA-Z]{2,}(?![\w])",
    "exclaim": r"!",
    "question": r"\?",
    "pipe": r"\|",
    "arrow": r"→|->",
    "tilde": r"~",
    "ampersand": r"&",
    "slash_word": r"\b\w+/\w+\b",
    "unclosed_quote_start": r'^"',
    "space_before_punct": r"\s+[,.;:!?]",
    "no_space_after_comma": r",[A-Za-z]",
    "capital_after_colon": r":\s+[A-Z]",
    "british_ise": r"\b\w+isation\b|\b\w+ise\b",
    "american_ize": r"\b\w+ization\b|\b\w+ize\b",
    "first_person_we": r"\b[Ww]e\b",
    "second_person_you": r"\b[Yy]ou\b",
    "hedge_may": r"\bmay\b",
    "hedge_can": r"\bcan\b",
    "therefore": r"\btherefore\b",
    "however": r"\b[Hh]owever\b",
    "moreover": r"\b[Mm]oreover\b",
    "furthermore": r"\b[Ff]urthermore\b",
    "in_conclusion": r"[Ii]n conclusion",
    "overall_comma": r"^Overall,",
    "newline_para": r"\n\n",
}


def featurize(texts) -> pd.DataFrame:
    rows = []
    for t in texts:
        t = "" if not isinstance(t, str) else t
        rows.append({k: bool(re.search(p, t)) for k, p in PATTERNS.items()})
    return pd.DataFrame(rows)


def main() -> None:
    tr = pd.read_csv(TRAIN)
    ev = pd.read_csv(EVAL)
    tr = tr.dropna(subset=["text", "model"])
    ev = ev.dropna(subset=["text", "model"])

    ftr = featurize(tr["text"].tolist())
    ftr["model"] = tr["model"].values
    fev = featurize(ev["text"].tolist())
    fev["model"] = ev["model"].values

    models = sorted(tr["model"].unique())
    n_tr = len(tr)
    base_rate = tr["model"].value_counts(normalize=True)

    # per (feature, model) presence rate
    rate = {f: {} for f in PATTERNS}
    for f in PATTERNS:
        s = ftr.groupby("model")[f].mean()
        rate[f] = s.to_dict()

    rules = []
    for f in PATTERNS:
        r = pd.Series(rate[f]).reindex(models).fillna(0.0)
        for m in models:
            p_own = float(r[m])
            others = r.drop(index=m)
            p_max_other = float(others.max())
            # near-zero-variance: model almost always does it, others almost never
            if p_own >= 0.50 and p_max_other <= 0.10 and (p_own - p_max_other) >= 0.40:
                # ---- validate on the independent set ------------------------
                sub = fev[fev[f] == True]  # noqa: E712
                n_fire = len(sub)
                n_hit = int((sub["model"] == m).sum())
                prec = (n_hit / n_fire) if n_fire else float("nan")
                rules.append({
                    "feature": f,
                    "model": m,
                    "train_presence": round(p_own, 4),
                    "max_other_presence": round(p_max_other, 4),
                    "train_support": int(round(p_own * int((tr['model'] == m).sum()))),
                    "eval_fires": n_fire,
                    "eval_hits": n_hit,
                    "eval_precision": None if not n_fire else round(prec, 4),
                    "eval_coverage": round(n_fire / len(fev), 4),
                    "eval_model_n": int((fev["model"] == m).sum()),
                })

    # the reverse rule shape: model NEVER does it (generic habit), still weak for
    # 18-way classification, so we only record it for information.
    rules.sort(key=lambda d: (-(d["eval_precision"] or 0), -d["eval_coverage"]))

    good = [d for d in rules if d["eval_precision"] is not None and d["eval_precision"] >= 0.5 and d["eval_coverage"] >= 0.01]
    usable = [d for d in rules if d["eval_precision"] is not None and d["eval_precision"] >= 0.8 and d["eval_fires"] >= 30]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "n_train": n_tr,
        "n_eval": len(ev),
        "n_models": len(models),
        "n_candidate_rules": len(rules),
        "n_precision_ge_0.5": len(good),
        "n_precision_ge_0.8_fires_ge_30": len(usable),
        "rules": rules,
    }, indent=2), encoding="utf-8")

    print(f"train={n_tr} eval={len(ev)} models={len(models)}")
    print(f"candidate (mining) rules: {len(rules)}   prec>=0.5: {len(good)}   prec>=0.8 & fires>=30: {len(usable)}")
    print("\n top rules (by eval precision, coverage):")
    for d in rules[:15]:
        print(f"  {d['feature']:>22s} -> {d['model']:<24s} train={d['train_presence']:.2f} "
              f"other={d['max_other_presence']:.2f} eval_fire={d['eval_fires']:>4d} "
              f"prec={d['eval_precision']} cov={d['eval_coverage']}")
    if usable:
        print("\n STANDOUT rules (precision>=0.8, >=30 firings on independent data):")
        for d in usable:
            print(f"  {d['feature']} -> {d['model']} prec={d['eval_precision']} cov={d['eval_coverage']} n={d['eval_fires']}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
