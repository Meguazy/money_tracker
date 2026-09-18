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
# What CUDA needs is /dev/nvidia-uvm, the unified-memory device, and that is
# not what the obvious readiness signals report. Two reboots measured it: the
# first probed CUDA at 10:22:28.040 with /dev/nvidiactl arriving at .432, and
# the second waited for `nvidia-smi` to answer and still failed, because
# nvidia-smi never touches the uvm device and it appeared 7.4 seconds later.
#
# So create the node rather than wait for something correlated with it.
# nvidia-modprobe is setuid root precisely to do this on demand, which is what
# a CUDA runtime would have done itself. The bounded wait remains only for
# hosts without that helper. Set LLAMA_REQUIRE_GPU=0 for a CPU-only host.
require_gpu="${LLAMA_REQUIRE_GPU:-1}"
gpu_wait_seconds="${LLAMA_GPU_WAIT_SECONDS:-60}"
if [[ "${require_gpu}" != "0" ]]; then
  if command -v nvidia-modprobe >/dev/null 2>&1; then
    nvidia-modprobe -u -c 0 >/dev/null 2>&1 || true
  fi
  waited=0
  until [[ -e /dev/nvidia-uvm ]] && nvidia-smi --list-gpus >/dev/null 2>&1; do
    if (( waited >= gpu_wait_seconds )); then
      echo "No usable GPU after ${gpu_wait_seconds}s; refusing to start." >&2
      echo "/dev/nvidia-uvm is what CUDA needs and it never appeared." >&2
      echo "llama.cpp would run on CPU without failing, roughly thirty times" >&2
      echo "slower, and nothing downstream would report it." >&2
      echo "Fix the driver, or set LLAMA_REQUIRE_GPU=0 to accept CPU speed." >&2
      exit 2
    fi
    sleep 1
    waited=$((waited + 1))
    if command -v nvidia-modprobe >/dev/null 2>&1; then
      nvidia-modprobe -u -c 0 >/dev/null 2>&1 || true
    fi
  done
  if (( waited > 0 )); then
    echo "Waited ${waited}s for the CUDA unified-memory device." >&2
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
)
if [[ -n "${MMPROJ_PATH:-}" ]]; then
  server_args+=(--mmproj "${MMPROJ_PATH}")
fi

# The key is handed over through a pipe rather than an argument, because any
# process of the same user can read another's command line. The substitution
# sits on the exec line itself: one made inside the array assignment above
# would be closed before the server could read it.
exec "${llama_server_bin}" "${server_args[@]}" \
  --api-key-file <(printf '%s\n' "${LLM_API_KEY:-local}")
