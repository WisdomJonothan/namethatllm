#!/usr/bin/env bash
# 本机中继：hf-mirror（3.3MB/s）→ 服务器，中间不落本地磁盘（本机 D 盘只剩 0.7GB）。
# 服务器自己的出网是白名单，hf-mirror 在服务器上不通，所以只能这样转。
set -uo pipefail

HOST=${HOST:-wujiaheng@10.13.71.195}
DIR=${DIR:-/homeB/Public/ntllm/models/qwen3-8b}
BASE=${BASE:-https://hf-mirror.com/Qwen/Qwen3-8B/resolve/main}
PARALLEL=${PARALLEL:-3}

FILES=(
  config.json
  generation_config.json
  merges.txt
  tokenizer.json
  tokenizer_config.json
  vocab.json
  model.safetensors.index.json
  model-00001-of-00005.safetensors
  model-00002-of-00005.safetensors
  model-00003-of-00005.safetensors
  model-00004-of-00005.safetensors
  model-00005-of-00005.safetensors
)

ssh -o BatchMode=yes "$HOST" "mkdir -p $DIR" || exit 1

fetch_one() {
  local f=$1
  local want have
  want=$(curl -sIL --max-time 40 "$BASE/$f" | tr -d '\r' \
         | awk 'BEGIN{IGNORECASE=1}/^content-length:/{v=$2}END{print v}')
  have=$(ssh -o BatchMode=yes "$HOST" "stat -c %s '$DIR/$f' 2>/dev/null || echo 0")
  if [ -n "${want:-}" ] && [ "$want" = "$have" ]; then
    echo "[skip] $f ($have bytes)"
    return 0
  fi
  echo "[get ] $f want=$want have=$have"
  curl -sL --retry 5 --retry-all-errors --max-time 5400 "$BASE/$f" \
    | ssh -o BatchMode=yes -o ServerAliveInterval=20 "$HOST" "cat > '$DIR/$f.part' && mv '$DIR/$f.part' '$DIR/$f'"
  local got
  got=$(ssh -o BatchMode=yes "$HOST" "stat -c %s '$DIR/$f' 2>/dev/null || echo 0")
  if [ -n "${want:-}" ] && [ "$got" != "$want" ]; then
    echo "[FAIL] $f got=$got want=$want"
    return 1
  fi
  echo "[ ok ] $f ($got bytes)"
}

export -f fetch_one
export HOST DIR BASE

printf '%s\n' "${FILES[@]}" | xargs -I{} -P "$PARALLEL" bash -c 'fetch_one "$@"' _ {}

echo "=== final ==="
ssh -o BatchMode=yes "$HOST" "du -sh '$DIR'; ls -l '$DIR' | tail -6"
