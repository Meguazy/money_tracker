#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MODEL_PATH:-}" ]]; then
  echo "MODEL_PATH must point to a GGUF model." >&2
  exit 2
fi

llama_server_bin="${LLAMA_SERVER_BIN:-llama-server}"
model_alias="${LLAMA_MODEL_ALIAS:-local-model}"
context_size="${LLAMA_CTX_SIZE:-8192}"

exec "${llama_server_bin}" \
  --model "${MODEL_PATH}" \
  --alias "${model_alias}" \
  --host 127.0.0.1 \
  --port 8080 \
  --n-gpu-layers 99 \
  --ctx-size "${context_size}" \
  --flash-attn on \
  --jinja
