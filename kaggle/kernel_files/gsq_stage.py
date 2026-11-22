import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

BASE_FILES = {'config.json': 5038, 'merges.txt': 3353259, 'model.safetensors.index.json': 198539, 'tokenizer.json': 12809320,
              'tokenizer_config.json': 17928, 'vocab.json': 6722759, 'model-00001-of-00003.safetensors': 2272369784,
              'model-00002-of-00003.safetensors': 4777704120, 'model-00003-of-00003.safetensors': 4777704376}


@contextmanager
def staged_tree(source, files, label, temp_root='/tmp'):
    source, temp_root = Path(source), Path(temp_root)
    assert source.is_dir()
    for name, size in files.items():
        item = source / name
        assert item.is_file() and item.stat().st_size == size, (label, name, size)
    total = sum(files.values())
    free = shutil.disk_usage(temp_root).free
    assert free > total + 2 * 2 ** 30, ('not enough local disk to stage', label, total, free)
    destination = Path(tempfile.mkdtemp(prefix='ntl-stage-' + label + '-', dir=temp_root))
    start = time.monotonic()
    try:
        for name, size in files.items():
            with (source / name).open('rb') as reader, (destination / name).open('wb') as writer:
                while block := reader.read(16 * 1024 * 1024):
                    writer.write(block)
            assert (destination / name).stat().st_size == size
        print(json.dumps({'stage': label, 'staged_bytes': total, 'seconds': round(time.monotonic() - start)}), flush=True)
        yield destination
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def run(assets, ids, texts, groups, work, labels, log=print, timeout=None):
    assets, work = Path(assets), Path(work)
    inference = assets / 'gsq120k-trained' / 'gsq120k-inference' / 'gsq'
    final, metadata, complete = inference / 'final.safetensors', inference / 'final.json', inference / 'COMPLETE.json'
    for path in (final, metadata, complete):
        assert path.is_file(), path
    wheels = sorted((assets / 'name-that-llm-artifacts' / 'qwen38-offline-linux').glob('*.whl'))
    assert len(wheels) >= 6, wheels
    work.mkdir(parents=True, exist_ok=True)
    target = work / 'python'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--disable-pip-version-check', '--target', str(target)] + [str(w) for w in wheels],
                   check=True, capture_output=True)
    query = work / 'query.parquet'
    pd.DataFrame({'id': list(ids), 'text': list(texts)}).to_parquet(query, index=False)
    group_file = work / 'groups.npy'
    np.save(group_file, np.asarray(groups, dtype=np.int64), allow_pickle=False)
    output = work / 'gsq.npz'
    if output.exists():
        output.unlink()
    started = time.monotonic()
    with staged_tree(assets / 'gsq3-base' / 'qwen38-27b-gsq3', BASE_FILES, 'gsq3-base') as base:
        env = dict(os.environ, PYTHONPATH=str(target), CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
        command = [sys.executable, '-u', str(Path(__file__).resolve().parent / 'gsq_infer.py'), '--base', str(base), '--final', str(final), '--metadata', str(metadata),
                   '--complete', str(complete), '--input', str(query), '--groups', str(group_file), '--output', str(output), '--work-dir', str(work / 'workers'), '--batch-size', '1']
        process = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise TimeoutError(f'GSQ stage exceeded {timeout:.0f} s')
        if code != 0:
            raise RuntimeError(f'GSQ stage exited with code {code}')
    with np.load(output, allow_pickle=False) as z:
        saved = [str(x) for x in z['labels'].tolist()]
        assert sorted(saved) == sorted(labels) and len(saved) == 18
        logits = z['plain_logits'][:, [saved.index(l) for l in labels]].astype(np.float64)
        assert len(logits) == len(ids) and np.isfinite(logits).all()
    logits -= logits.max(1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(1, keepdims=True)
    log(f'GSQ 27B scored {len(ids)} rows in {time.monotonic() - started:.0f} s')
    return probs
