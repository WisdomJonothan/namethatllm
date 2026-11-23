import json, numpy as np
for f in ("results/probe_gpt.json", "results/probe_claude_glm.json"):
    d = json.load(open(f, encoding="utf-8")); s = d["summary"]
    print("==", f, s["labels"])
    print("  acc", round(s["acc"], 4), "macro_recall", round(s["macro_recall"], 4), "chance", round(s["chance"], 3))
    print("  recall", {k: round(v, 3) for k, v in s["recall"].items()})
    det = d["detail"]; labels = s["labels"]
    S = np.array([r["scores"] for r in det]); y = np.array([labels.index(r["gold"]) for r in det])
    print("  mean_score_per_author", {labels[i]: round(float(S[:, i].mean()), 3) for i in range(len(labels))})
    Sc = S - S.mean(0, keepdims=True)
    print("  centred_acc", round(float((Sc.argmax(1) == y).mean()), 4),
          {labels[c]: round(float((Sc.argmax(1)[y == c] == c).mean()), 3) for c in range(len(labels))})
    Sr = (S - S.mean(1, keepdims=True)) / (S.std(1, keepdims=True) + 1e-9)
    print("  rownorm_acc", round(float((Sr.argmax(1) == y).mean()), 4))
