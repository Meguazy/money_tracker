#!/usr/bin/env bash
set -euo pipefail

# Store one Telegram credential without placing it in argv, shell history, or
# process environment. Configuration is parsed as data rather than sourced.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${MONEY_TRACKER_ENV_FILE:-${repo_root}/.env}"
environment="${1:-}"

case "${environment}" in
  production)
    selected_name="MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE"
    other_name="MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE"
    ;;
  test)
    selected_name="MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE"
    other_name="MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE"
    ;;
  *)
    echo "Usage: scripts/store_telegram_token.sh production|test" >&2
    exit 1
    ;;
esac

if [[ ! -f "${env_file}" ]] || [[ -L "${env_file}" ]]; then
  echo "The configured environment file must be a regular file." >&2
  exit 1
fi
if [[ "$(stat -c '%a' "${env_file}")" != "600" ]] ||
   [[ "$(stat -c '%u' "${env_file}")" != "$(id -u)" ]]; then
  echo "The configured environment file must be owner-owned with mode 600." >&2
  exit 1
fi

setting_value() {
  local name="$1"
  local matches
  matches="$(grep -c "^${name}=" "${env_file}" || true)"
  if [[ "${matches}" != "1" ]]; then
    echo "The environment file must define ${name} exactly once." >&2
    exit 1
  fi
  grep -m1 "^${name}=" "${env_file}" | cut -d= -f2-
}

token_file="$(setting_value "${selected_name}")"
other_file="$(setting_value "${other_name}")"
if [[ "${token_file}" != /* ]] || [[ "${other_file}" != /* ]]; then
  echo "Telegram credential paths must be absolute." >&2
  exit 1
fi
if [[ "${token_file}" == "${other_file}" ]]; then
  echo "Production and test credentials must use different files." >&2
  exit 1
fi

token_directory="$(dirname -- "${token_file}")"
install -d -m 700 "${token_directory}"
if [[ -L "${token_directory}" ]] ||
   [[ "$(stat -c '%u' "${token_directory}")" != "$(id -u)" ]]; then
  echo "The Telegram credential directory must be owner-owned and not a symlink." >&2
  exit 1
fi
chmod 700 "${token_directory}"
if [[ -e "${token_file}" ]] &&
   { [[ ! -f "${token_file}" ]] || [[ -L "${token_file}" ]] ||
     [[ "$(stat -c '%u' "${token_file}")" != "$(id -u)" ]]; }; then
  echo "Refusing to replace an unsafe Telegram credential file." >&2
  exit 1
fi

printf 'Enter the %s Telegram bot token (input hidden): ' "${environment}" >&2
IFS= read -r -s token
printf '\n' >&2
if (( ${#token} < 20 || ${#token} > 256 )) ||
   [[ "${token}" != *:* ]] || [[ "${token}" == */* ]] ||
   [[ "${token}" =~ [[:space:]] ]]; then
  unset token
  echo "The supplied Telegram credential is invalid." >&2
  exit 1
fi

temporary_file="$(mktemp "${token_file}.tmp.XXXXXX")"
cleanup() {
  if [[ -n "${temporary_file:-}" ]] && [[ -f "${temporary_file}" ]]; then
    rm -- "${temporary_file}"
  fi
}
trap cleanup EXIT
chmod 600 "${temporary_file}"
printf '%s' "${token}" >"${temporary_file}"
unset token
mv -f -- "${temporary_file}" "${token_file}"
temporary_file=""
trap - EXIT

echo "Stored the ${environment} Telegram credential in its owner-only file."
