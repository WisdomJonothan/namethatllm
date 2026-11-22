import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def read_rows(path, text_column):
    frame = pd.read_parquet(path).reset_index(drop=True)
    assert text_column in frame and frame[text_column].notna().all()
    texts = frame[text_column].astype(str).tolist()
    assert all(texts)
    return frame, texts


def worker(args):
    assert os.environ.get("CUDA_VISIBLE_DEVICES") in ("0", "1")
    import torch
    from adapter import load_final
    from gsq_infer_model import GSQClassifier

    assert torch.cuda.device_count() == 1, "GSQ worker must see one isolated GPU"
    assert torch.cuda.get_device_capability(0) >= (7, 5), "GSQ T4 path requires SM 7.5 or newer"
    torch.set_grad_enabled(False)
    frame, texts = read_rows(args.input, args.text_column)
    indices = np.arange(len(texts), dtype=np.int64)[args.worker_id::2]
    assert len(indices)
    model = GSQClassifier(args.base)
    metadata = load_final(model, args.final, args.metadata, args.complete)
    tokenizer = model.tokenizer
    features = np.empty((len(indices), 40960), dtype=np.float32)
    plain = np.empty((len(indices), 18), dtype=np.float32)
    for begin in range(0, len(indices), args.batch_size):
        batch_ids = indices[begin:begin + args.batch_size]
        token_rows = tokenizer([texts[int(i)] for i in batch_ids], add_special_tokens=False,
                               truncation=False)["input_ids"]
        token_rows = [ids + [248044] for ids in token_rows]
        assert all(len(ids) <= model.cfg.max_position_embeddings for ids in token_rows), "Full text exceeds model context"
        with torch.inference_mode():
            value = model(token_rows)
            score = model.plain(value)
        stop = begin + len(batch_ids)
        features[begin:stop] = value.float().cpu().numpy()
        plain[begin:stop] = score.float().cpu().numpy()
        del value, score
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, row_index=indices, features=features, plain_logits=plain)
    print(json.dumps(dict(worker=args.worker_id, rows=len(indices), labels=metadata["labels"],
                          load_report=model.load_report)), flush=True)


def group_codes(args, frame):
    assert (args.groups is None) != (args.prompt_column is None), "Provide exactly one group source"
    if args.groups is not None:
        codes = np.load(args.groups, allow_pickle=False)
        assert codes.ndim == 1 and len(codes) == len(frame) and np.issubdtype(codes.dtype, np.integer)
        assert np.all(codes >= 0), "Explicit group codes must be nonnegative"
        return codes
    assert args.prompt_column in frame, "Requested prompt column is missing"
    assert frame[args.prompt_column].notna().all(), "Missing prompt group is ambiguous"
    return pd.factorize(frame[args.prompt_column], sort=False)[0]


def combine(args, outputs):
    from safetensors import safe_open

    frame, _ = read_rows(args.input, args.text_column)
    n = len(frame)
    features = np.empty((n, 40960), dtype=np.float32)
    plain = np.empty((n, 18), dtype=np.float32)
    seen = np.zeros(n, dtype=bool)
    for path in outputs:
        with np.load(path, allow_pickle=False) as item:
            ids = item["row_index"]
            assert ids.ndim == 1 and np.issubdtype(ids.dtype, np.integer)
            assert len(np.unique(ids)) == len(ids)
            assert np.all((0 <= ids) & (ids < n)) and not np.any(seen[ids])
            assert item["features"].shape == (len(ids), 40960)
            assert item["plain_logits"].shape == (len(ids), 18)
            features[ids] = item["features"]
            plain[ids] = item["plain_logits"]
            seen[ids] = True
    assert seen.all() and np.isfinite(features).all() and np.isfinite(plain).all()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    with safe_open(str(args.final), framework="pt", device="cpu") as weights:
        kernel = weights.get_tensor("centered.weight").numpy().astype(np.float32)
        bias = weights.get_tensor("centered.bias").numpy().astype(np.float32)
    assert kernel.shape == (18, 40960) and bias.shape == (18,)
    groups = group_codes(args, frame)
    centered = plain.copy()
    valid_centered = np.zeros(n, dtype=bool)
    for group in np.unique(groups):
        ids = np.flatnonzero(groups == group)
        if len(ids) == 1:
            continue
        centered[ids] = (features[ids] - features[ids].mean(0, keepdims=True)) @ kernel.T + bias
        valid_centered[ids] = True
    assert np.isfinite(centered).all() and len(groups) == n
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, row_index=np.arange(n, dtype=np.int64), features=features,
             plain_logits=plain, centered_logits=centered, valid_centered=valid_centered,
             group_code=groups, labels=np.asarray(metadata["labels"]))


def parent(args):
    _, texts = read_rows(args.input, args.text_column)
    worker_count = min(2, len(texts))
    outputs = [args.work_dir / f"gsq-worker-{i}.npz" for i in range(worker_count)]
    args.work_dir.mkdir(parents=True, exist_ok=True)
    children = []
    for device in range(worker_count):
        command = [sys.executable, str(Path(__file__).resolve()), "--worker-id", str(device),
                   "--base", str(args.base), "--final", str(args.final), "--metadata", str(args.metadata),
                   "--complete", str(args.complete), "--input", str(args.input), "--output", str(outputs[device]),
                   "--text-column", args.text_column,
                   "--batch-size", str(args.batch_size)]
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=str(device), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   TOKENIZERS_PARALLELISM="false")
        children.append(subprocess.Popen(command, env=env))
    codes = [child.wait() for child in children]
    if any(code != 0 for code in codes):
        raise RuntimeError(f"GSQ workers failed: exit codes {codes}; outputs were not combined")
    combine(args, outputs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "final", "metadata", "complete", "input", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--work-dir", type=Path)
    p.add_argument("--text-column", default="text")
    p.add_argument("--prompt-column")
    p.add_argument("--groups", type=Path)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--worker-id", type=int, choices=(0, 1))
    args = p.parse_args()
    assert args.batch_size >= 1
    if args.worker_id is None:
        assert args.work_dir is not None and os.environ.get("CUDA_VISIBLE_DEVICES") in (None, "", "0,1")
        assert (args.groups is None) != (args.prompt_column is None), "Provide --groups or --prompt-column"
        parent(args)
    else:
        worker(args)


if __name__ == "__main__":
    main()
