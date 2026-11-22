"""服务器侧推理脚本（与 notebooks/submit_qlora.py 同一套逻辑，便于本地先验证）。

    python scripts/predict.py --model /homeB/Public/ntllm/models/qwen3-8b \
        --adapter runs/qlora/fold0 --csv data/train.csv --out results/infer_fold0.npz
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from peft import PeftModel
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          BitsAndBytesConfig)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constraint import apply_constraint, rebuild_groups  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bs", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=320)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--no-constraint", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    labels_path = Path(args.adapter) / "labels.json"
    labels = (json.loads(labels_path.read_text()) if labels_path.exists()
              else sorted(pd.read_csv(args.csv).model.unique()))
    print(f"labels={len(labels)}")

    cap = torch.cuda.get_device_capability(0)
    dtype = torch.float16 if cap[0] < 8 else torch.bfloat16
    tok = AutoTokenizer.from_pretrained(args.model)
    tok.padding_side = "right"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    base = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=len(labels), quantization_config=bnb,
        dtype=dtype, device_map={"": 0})
    base.config.pad_token_id = tok.pad_token_id
    model = PeftModel.from_pretrained(base, args.adapter, is_trainable=False).eval()
    print(f"loaded in {time.time() - t0:.0f}s")

    df = pd.read_csv(args.csv)
    texts = [t if isinstance(t, str) and t.strip() else " " for t in df.text.tolist()]
    probs = np.zeros((len(texts), len(labels)), dtype=np.float32)
    order = np.argsort([len(t) for t in texts])
    i, cur = 0, args.bs
    while i < len(order):
        idx = order[i:i + cur]
        enc = tok([texts[j] for j in idx], truncation=True, max_length=args.max_len,
                  padding=True, return_tensors="pt").to("cuda")
        try:
            with torch.inference_mode():
                logits = model(**enc).logits.float()
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if cur == 1:
                raise
            cur = max(1, cur // 2)
            print(f"OOM -> bs {cur}")
            continue
        probs[idx] = torch.softmax(logits, -1).cpu().numpy()
        i += len(idx)
    print(f"inference {time.time() - t0:.0f}s")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    payload = {"probs": probs, "labels": np.array(labels),
               "ids": df.id.astype(str).to_numpy()}
    if "model" in df.columns:
        payload["y"] = df.model.map({m: i for i, m in enumerate(labels)}).to_numpy()
        from sklearn.metrics import balanced_accuracy_score
        payload["ba"] = balanced_accuracy_score(payload["y"], probs.argmax(1))
        print(f"raw BA={payload['ba']:.4f}")

    if not args.no_constraint:
        groups = rebuild_groups(texts)
        probs_c = apply_constraint(probs, groups, temperature=args.temperature)
        payload["groups_n"] = len(groups)
        print(f"groups={len(groups)}  changed={(probs_c.argmax(1) != probs.argmax(1)).sum()}")
        if "y" in payload:
            from sklearn.metrics import balanced_accuracy_score
            payload["ba_constrained"] = balanced_accuracy_score(payload["y"], probs_c.argmax(1))
            print(f"constrained BA={payload['ba_constrained']:.4f} "
                  f"({payload['ba_constrained'] - payload['ba']:+.4f})")
        payload["probs_constrained"] = probs_c

    np.savez_compressed(args.out, **payload)
    print("saved", args.out)


if __name__ == "__main__":
    main()
