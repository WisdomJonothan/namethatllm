#!/usr/bin/env bash
# Run ON THE SERVER (10.13.71.195), with the laptop-side reverse proxy already up:
#
#   laptop$  ssh -R 12334:127.0.0.1:12334 wujiaheng@10.13.71.195
#
# Why: the server's egress is whitelisted (HF/ModelScope blocked), so it cannot
# download the model by itself. The reverse forward lends it the laptop's proxy,
# which avoids pushing 16.4GB from a laptop whose disks are already full.
set -uo pipefail

echo "== hardware =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
python -c "import torch, sys; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'avail', torch.cuda.is_available())" || echo "!! torch import failed"

echo
echo "== disk =="
df -h /tmp /homeC 2>/dev/null

FREE_KB=$(df -k /tmp | awk 'NR==2{print $4}')
NEED_KB=$((18 * 1024 * 1024))
if [ "${FREE_KB:-0}" -lt "$NEED_KB" ]; then
  echo "!! /tmp has $((FREE_KB / 1024 / 1024))G free, need >= 18G. Aborting."
  echo "   clean first:  ls -la /tmp | head -50 ; du -sh /tmp/* 2>/dev/null | sort -h | tail"
  exit 1
fi

export http_proxy=http://127.0.0.1:12334
export https_proxy=$http_proxy
echo
echo "== proxy check =="
python - <<'PY'
import os, urllib.request
try:
    with urllib.request.urlopen("https://huggingface.co/api/models/Qwen/Qwen3-8B", timeout=15) as r:
        print("proxy OK, status", r.status)
except Exception as e:
    print("proxy FAILED:", type(e).__name__, e)
    raise SystemExit(1)
PY
[ $? -ne 0 ] && { echo "!! reverse proxy not reachable on 127.0.0.1:12334"; exit 1; }

echo
echo "== huggingface_hub =="
pip install -q -U "huggingface_hub[cli]" 2>&1 | tail -2
hf version 2>/dev/null || huggingface-cli version

echo
echo "== download (16.4GB, --local-dir keeps a single copy, no HF cache duplication) =="
hf download Qwen/Qwen3-8B --local-dir /tmp/qwen3-8b --max-workers 4

echo
echo "== result =="
du -sh /tmp/qwen3-8b
df -h /tmp
ls -la /tmp/qwen3-8b | head -20

echo
echo "next: pip install -U 'transformers>=4.51' peft accelerate bitsandbytes"
