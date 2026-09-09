#!/usr/bin/env bash
set -euo pipefail

# Measure the same suites across several catalogued candidates, one at a time.
#
# A candidate switch is two steps that must agree: the weights the evaluation
# runner loads and hashes, and the weights the inference server actually has in
# memory. Doing it by hand and forgetting the restart produces a complete,
# plausible run that attributes one candidate's answers to another, so this
# script owns both steps and lets `--model-profile` verify them on every suite.
#
# A server already serving the requested candidate is reused as is. One serving
# different weights is taken over: the sweep stops it, measures every candidate,
# and puts the original weights back before it exits, including when it is
# interrupted. Pass --keep-running-server to refuse instead, which is worth
# doing when something other than this project answers on that port.
#
# Usage:
#   scripts/sweep_models.sh [options] <profile> [<profile>...]
#
# Options:
#   --suites "a b c"          suites to run for each candidate
#                             (default: routing ingestion categorization tools
#                              advice category-review)
#   --minimum-confidence N    required when the routing suite is included; no
#                             value is promoted, so there is no default
#   --output-root DIR         sweep output root (default: artifacts/sweeps)
#   --selection-policy ID     policy for tools and advice
#                             (default: explicit-finance-scope-v1)
#   --keep-running-server     refuse to take over a server serving different
#                             weights instead of restoring it afterwards
#   --dry-run                 print the plan and resolve every candidate
#                             without starting a server or running a suite

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repository_root}"

suites="routing ingestion categorization tools advice category-review"
minimum_confidence=""
output_root="artifacts/sweeps"
selection_policy="explicit-finance-scope-v1"
dry_run=false
keep_running_server=false
profiles=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --suites) suites="${2:?--suites needs a value}"; shift 2 ;;
    --minimum-confidence) minimum_confidence="${2:?--minimum-confidence needs a value}"; shift 2 ;;
    --output-root) output_root="${2:?--output-root needs a value}"; shift 2 ;;
    --selection-policy) selection_policy="${2:?--selection-policy needs a value}"; shift 2 ;;
    --keep-running-server) keep_running_server=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help)
      awk '/^# Measure the same suites/,0 {if (!/^#/) exit; sub(/^# ?/, ""); print}' \
        "${BASH_SOURCE[0]}"
      exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) profiles+=("$1"); shift ;;
  esac
done

if [[ ${#profiles[@]} -eq 0 ]]; then
  echo "name at least one candidate; see: money-tracker-eval catalog" >&2
  exit 2
fi
if [[ " ${suites} " == *" routing "* && -z "${minimum_confidence}" ]]; then
  echo "the routing suite needs --minimum-confidence: no bar is promoted," >&2
  echo "so a run must state the one it is measuring." >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo ".env not found; generate it with scripts/generate_local_env.sh" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

base_url="${LLM_BASE_URL:-http://localhost:8080/v1}"
server_root="${base_url%/v1}"
server_root="${server_root%/}"
server_port="${server_root##*:}"
started_server_pid=""
# Full weights path of a server this sweep took over, so the machine can be left
# as it was found. The configured pair is kept because a projector cannot be
# read back from a running server.
adopted_weights=""
configured_model_path="${MODEL_PATH:-}"
configured_mmproj_path="${MMPROJ_PATH:-}"

# The suites that read back postconditions need the isolated test database. It
# is checked before a candidate is loaded, because discovering it after a ten
# gigabyte model has been read into VRAM wastes the most expensive step.
for suite in ${suites}; do
  case "${suite}" in
    tools|advice|category-review)
      test_url="${MONEY_TRACKER_TEST_DATABASE_URL:-}"
      host_port="${test_url##*@}"
      host_port="${host_port%%/*}"
      host="${host_port%%:*}"
      port="${host_port##*:}"
      if ! (exec 3<>"/dev/tcp/${host:-127.0.0.1}/${port:-5433}") 2>/dev/null; then
        echo "${suite} needs the isolated test database at ${host:-127.0.0.1}:${port:-5433}," >&2
        echo "which is not accepting connections. Start it with:" >&2
        echo "  docker compose up -d application-postgres" >&2
        exit 2
      fi
      break
      ;;
  esac
done

stop_started_server() {
  if [[ -n "${started_server_pid}" ]] && kill -0 "${started_server_pid}" 2>/dev/null; then
    kill "${started_server_pid}" 2>/dev/null || true
    wait "${started_server_pid}" 2>/dev/null || true
  fi
  started_server_pid=""
}

# Echo the pid listening on the inference port, if any.
listening_pid() {
  ss -ltnp 2>/dev/null | awk -v port=":${server_port}$" '
    $4 ~ port {
      if (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART + 4, RLENGTH - 4)
        exit
      }
    }
  '
}

stop_foreign_server() {
  local pid waited=0
  pid="$(listening_pid)"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  kill "${pid}" 2>/dev/null || true
  while (( waited < 60 )); do
    if [[ -z "$(loaded_filename)" ]]; then
      return 0
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  echo "  the server on port ${server_port} did not stop" >&2
  return 1
}

# Put back the server the sweep took over. This runs on every exit path,
# including an interrupt, because leaving the machine without the server it had
# is a worse outcome than any sweep result.
restore_adopted_server() {
  if [[ -z "${adopted_weights}" ]]; then
    return 0
  fi
  local weights="${adopted_weights}" projector=""
  if [[ "${weights}" == "${configured_model_path}" ]]; then
    projector="${configured_mmproj_path}"
  fi
  adopted_weights=""
  echo "restoring the server that was running before the sweep: $(basename "${weights}")"
  MODEL_PATH="${weights}" MMPROJ_PATH="${projector}" \
    setsid scripts/run_llama_server.sh >"${sweep_root}/restored-server.log" 2>&1 &
  disown 2>/dev/null || true
}

finish() {
  stop_started_server
  restore_adopted_server
}
trap finish EXIT INT TERM

# Echo the full weights path the running server reports, or nothing when no
# server answers.
loaded_weights_path() {
  local properties
  properties="$(curl -fsS -m 5 -H "Authorization: Bearer ${LLM_API_KEY:-local}" \
    "${server_root}/props" 2>/dev/null)" || return 0
  printf '%s' "${properties}" | python3 -c '
import json, sys
try:
    loaded = json.load(sys.stdin).get("model_path")
except (json.JSONDecodeError, AttributeError):
    loaded = None
print(loaded or "")
'
}

# Echo the weights filename the running server reports, or nothing when no
# server answers. A server that is up but not ready answers this too.
loaded_filename() {
  local properties
  properties="$(curl -fsS -m 5 -H "Authorization: Bearer ${LLM_API_KEY:-local}" \
    "${server_root}/props" 2>/dev/null)" || return 0
  printf '%s' "${properties}" | python3 -c '
import json, sys
from pathlib import Path
try:
    loaded = json.load(sys.stdin).get("model_path")
except (json.JSONDecodeError, AttributeError):
    loaded = None
print(Path(loaded).name if loaded else "")
'
}

wait_until_loaded() {
  local expected="$1" waited=0
  while (( waited < 600 )); do
    if [[ "$(loaded_filename)" == "${expected}" ]]; then
      return 0
    fi
    if [[ -n "${started_server_pid}" ]] && ! kill -0 "${started_server_pid}" 2>/dev/null; then
      echo "  the inference server exited before it finished loading" >&2
      return 1
    fi
    sleep 2
    waited=$(( waited + 2 ))
  done
  echo "  the inference server did not load ${expected} within 600s" >&2
  return 1
}

run_id="$(date +%Y%m%d-%H%M%S)"
sweep_root="${output_root}/${run_id}"
echo "sweep ${run_id}"
echo "  suites:  ${suites}"
echo "  output:  ${sweep_root}/<candidate>/"
echo "  read policy: ${selection_policy}"
echo

failures=0
for profile in "${profiles[@]}"; do
  echo "== ${profile}"
  if ! weights="$(uv run money-tracker-eval catalog --profile "${profile}" --field path 2>&1)"; then
    echo "  ${weights}" >&2
    failures=$(( failures + 1 ))
    continue
  fi
  projector="$(uv run money-tracker-eval catalog --profile "${profile}" --field projector 2>/dev/null || true)"
  filename="$(basename "${weights}")"
  candidate_dir="${sweep_root}/${profile}"

  echo "  weights: ${weights}"
  if [[ ! -r "${weights}" ]]; then
    echo "  the declared weights are not readable; download them first" >&2
    failures=$(( failures + 1 ))
    continue
  fi

  if ${dry_run}; then
    echo "  would write ${candidate_dir}/"
    for suite in ${suites}; do
      if [[ "${suite}" == "tools" || "${suite}" == "advice" ]]; then
        echo "    would run: money-tracker-eval ${suite} --model-profile ${profile} --selection-policy ${selection_policy}"
      else
        echo "    would run: money-tracker-eval ${suite} --model-profile ${profile}"
      fi
    done
    echo
    continue
  fi

  mkdir -p "${candidate_dir}"
  export MODEL_PATH="${weights}"
  export MMPROJ_PATH="${projector}"

  already_loaded="$(loaded_filename)"
  if [[ "${already_loaded}" == "${filename}" ]]; then
    echo "  reusing the running server, which already serves ${filename}"
  elif [[ -n "${already_loaded}" ]]; then
    if ${keep_running_server}; then
      echo "  a server this sweep did not start is serving ${already_loaded}," >&2
      echo "  and --keep-running-server forbids taking it over." >&2
      exit 3
    fi
    if [[ -z "${adopted_weights}" ]]; then
      adopted_weights="$(loaded_weights_path)"
      echo "  taking over the server serving ${already_loaded}, which is restored"
      echo "  when the sweep ends"
    fi
    stop_foreign_server || exit 3
    echo "  starting the inference server"
    scripts/run_llama_server.sh >"${candidate_dir}/server.log" 2>&1 &
    started_server_pid=$!
    if ! wait_until_loaded "${filename}"; then
      echo "  see ${candidate_dir}/server.log" >&2
      stop_started_server
      failures=$(( failures + 1 ))
      continue
    fi
  else
    echo "  starting the inference server"
    scripts/run_llama_server.sh >"${candidate_dir}/server.log" 2>&1 &
    started_server_pid=$!
    if ! wait_until_loaded "${filename}"; then
      echo "  see ${candidate_dir}/server.log" >&2
      stop_started_server
      failures=$(( failures + 1 ))
      continue
    fi
  fi

  for suite in ${suites}; do
    arguments=("${suite}" --model-profile "${profile}")
    if [[ "${suite}" == "routing" ]]; then
      arguments+=(--minimum-confidence "${minimum_confidence}")
    elif [[ "${suite}" == "tools" || "${suite}" == "advice" ]]; then
      arguments+=(--selection-policy "${selection_policy}")
    fi
    printf '  %-16s' "${suite}"
    if uv run money-tracker-eval "${arguments[@]}" >"${candidate_dir}/${suite}.json" 2>"${candidate_dir}/${suite}.log"; then
      echo "gates passed"
    else
      status=$?
      # Exit 1 is a failing blocking gate, which is a measurement rather than a
      # broken run, so the sweep records it and carries on to the next suite.
      if [[ ${status} -eq 1 ]]; then
        echo "gates failed"
      else
        echo "did not run (exit ${status}); see ${candidate_dir}/${suite}.log"
        failures=$(( failures + 1 ))
      fi
    fi
  done
  stop_started_server
  echo
done

echo "raw runs:   artifacts/evals/<candidate>/"
echo "scores:     out/evaluations/<suite>/<candidate>/"
echo "sweep logs: ${sweep_root}/"
if (( failures > 0 )); then
  echo "${failures} suite run(s) could not be executed" >&2
  exit 1
fi
