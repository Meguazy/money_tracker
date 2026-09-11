#!/usr/bin/env bash
set -euo pipefail

# This launcher reads the shell environment rather than .env, because the
# server is started outside the UV entry points that load .env themselves.
# Every failure below names the exact fix, since an unset variable and a
# placeholder path are otherwise indistinguishable at the llama.cpp boundary.
if [[ -z "${MODEL_PATH:-}" ]]; then
  echo "MODEL_PATH is not set. Load the local environment first:" >&2
  echo "  set -a; source .env; set +a" >&2
  exit 2
fi
if [[ ! -f "${MODEL_PATH}" ]] || [[ ! -r "${MODEL_PATH}" ]]; then
  echo "MODEL_PATH does not name a readable file:" >&2
  echo "  ${MODEL_PATH}" >&2
  echo "Install a GGUF model and set MODEL_PATH in .env." >&2
  exit 2
fi
if [[ -n "${MMPROJ_PATH:-}" ]] && { [[ ! -f "${MMPROJ_PATH}" ]] || [[ ! -r "${MMPROJ_PATH}" ]]; }; then
  echo "MMPROJ_PATH is set but does not name a readable file:" >&2
  echo "  ${MMPROJ_PATH}" >&2
  echo "Leave MMPROJ_PATH empty for a text-only run." >&2
  exit 2
fi

llama_server_bin="${LLAMA_SERVER_BIN:-llama-server}"
if ! command -v "${llama_server_bin}" >/dev/null 2>&1; then
  echo "LLAMA_SERVER_BIN is not executable: ${llama_server_bin}" >&2
  echo "Build llama.cpp and set LLAMA_SERVER_BIN to its llama-server." >&2
  exit 2
fi
# llama.cpp falls back to CPU when CUDA cannot initialize, and says so only in
# a warning it then keeps running past. At boot that fallback is silent and
# catastrophic: the unit is active, the health endpoint answers, no queue grows,
# and every inference takes roughly seventy seconds instead of two, which no
# amount of retrying fixes and which NFR-PER-001 cannot absorb.
#
# The first real reboot lost that race by 400 milliseconds: CUDA was probed at
# 10:22:28.040 and /dev/nvidiactl appeared at 10:22:28.432. So wait for the
# driver rather than assume a boot ordering that no user unit can express, and
# refuse to start rather than degrade where nothing would notice. Set
# LLAMA_REQUIRE_GPU=0 for a deliberate CPU-only host.
require_gpu="${LLAMA_REQUIRE_GPU:-1}"
gpu_wait_seconds="${LLAMA_GPU_WAIT_SECONDS:-60}"
if [[ "${require_gpu}" != "0" ]]; then
  waited=0
  until [[ -e /dev/nvidiactl ]] && nvidia-smi --list-gpus >/dev/null 2>&1; do
    if (( waited >= gpu_wait_seconds )); then
      echo "No usable GPU after ${gpu_wait_seconds}s; refusing to start." >&2
      echo "llama.cpp would run on CPU without failing, roughly thirty times" >&2
      echo "slower, and nothing downstream would report it." >&2
      echo "Fix the driver, or set LLAMA_REQUIRE_GPU=0 to accept CPU speed." >&2
      exit 2
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if (( waited > 0 )); then
    echo "Waited ${waited}s for the NVIDIA driver before starting." >&2
  fi
fi
model_alias="${LLAMA_MODEL_ALIAS:-local-model}"
context_size="${LLAMA_CTX_SIZE:-8192}"
parallel="${LLAMA_PARALLEL:-1}"
reasoning="${LLAMA_REASONING:-off}"

server_args=(
  --model "${MODEL_PATH}"
  --alias "${model_alias}"
  --host 127.0.0.1
  --port 8080
  --n-gpu-layers 99
  --ctx-size "${context_size}"
  --parallel "${parallel}"
  --reasoning "${reasoning}"
  --flash-attn on
  --jinja
  --api-key "${LLM_API_KEY:-local}"
)
if [[ -n "${MMPROJ_PATH:-}" ]]; then
  server_args+=(--mmproj "${MMPROJ_PATH}")
fi

exec "${llama_server_bin}" "${server_args[@]}"
