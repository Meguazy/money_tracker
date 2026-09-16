#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${MONEY_TRACKER_ENV_FILE:-${repo_root}/.env}"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing ${env_file}; run scripts/generate_local_env.sh first." >&2
  exit 1
fi

if [[ "$(stat -c '%a' "${env_file}")" != "600" ]]; then
  echo "Refusing to update ${env_file}: expected mode 600." >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate local secrets." >&2
  exit 1
fi

has_setting() {
  local setting_name="$1"
  grep -q "^${setting_name}=" "${env_file}"
}

database_settings=(
  MONEY_TRACKER_POSTGRES_ADMIN_PASSWORD
  MONEY_TRACKER_POSTGRES_PASSWORD
  MONEY_TRACKER_DATABASE_URL
  MONEY_TRACKER_TEST_DATABASE_URL
)
present_count=0
for setting_name in "${database_settings[@]}"; do
  if has_setting "${setting_name}"; then
    present_count=$((present_count + 1))
  fi
done

if (( present_count != 0 && present_count != ${#database_settings[@]} )); then
  echo "Refusing a partial database configuration in ${env_file}." >&2
  exit 1
fi

updated=false
if (( present_count == 0 )); then
  admin_password="$(openssl rand -hex 24)"
  app_password="$(openssl rand -hex 24)"
  {
    printf '\n# Dedicated application PostgreSQL.\n'
    printf 'MONEY_TRACKER_POSTGRES_ADMIN_PASSWORD=%s\n' "${admin_password}"
    printf 'MONEY_TRACKER_POSTGRES_PASSWORD=%s\n' "${app_password}"
    printf 'MONEY_TRACKER_DATABASE_URL=postgresql://money_tracker:%s@127.0.0.1:5433/money_tracker\n' "${app_password}"
    printf 'MONEY_TRACKER_TEST_DATABASE_URL=postgresql://money_tracker:%s@127.0.0.1:5433/money_tracker_test\n' "${app_password}"
  } >>"${env_file}"
  updated=true
fi

if ! has_setting LANGGRAPH_STRICT_MSGPACK; then
  printf 'LANGGRAPH_STRICT_MSGPACK=true\n' >>"${env_file}"
  updated=true
fi

if ! has_setting LOG_LEVEL; then
  printf 'LOG_LEVEL=INFO\n' >>"${env_file}"
  updated=true
fi

if ! has_setting LOG_FORMAT; then
  printf 'LOG_FORMAT=json\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_TRACING_ENABLED; then
  printf 'MONEY_TRACKER_TRACING_ENABLED=false\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS; then
  printf 'MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS=2\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT; then
  receipt_archive_root="${XDG_DATA_HOME:-${HOME}/.local/share}/money-tracker/receipts"
  printf 'MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT=%s\n' "${receipt_archive_root}" >>"${env_file}"
  updated=true
fi

telegram_config_root="${XDG_CONFIG_HOME:-${HOME}/.config}/money-tracker"
if ! has_setting MONEY_TRACKER_TELEGRAM_ENVIRONMENT; then
  printf 'MONEY_TRACKER_TELEGRAM_ENVIRONMENT=test\n' >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE; then
  printf 'MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE=%s/telegram-production.token\n' "${telegram_config_root}" >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE; then
  printf 'MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE=%s/telegram-test.token\n' "${telegram_config_root}" >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_WEB_HOST; then
  printf 'MONEY_TRACKER_WEB_HOST=127.0.0.1\n' >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_WEB_PORT; then
  printf 'MONEY_TRACKER_WEB_PORT=8000\n' >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_WEB_ALLOWED_HOSTS; then
  printf 'MONEY_TRACKER_WEB_ALLOWED_HOSTS=127.0.0.1,localhost\n' >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_WEB_ALLOWED_ORIGINS; then
  printf '%s\n' \
    'MONEY_TRACKER_WEB_ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000' \
    >>"${env_file}"
  updated=true
fi
if ! has_setting MONEY_TRACKER_WEB_STATIC_DIR; then
  printf 'MONEY_TRACKER_WEB_STATIC_DIR=%s/frontend/dist\n' "${repo_root}" \
    >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_BACKUP_EXPECTED_DATABASE; then
  printf 'MONEY_TRACKER_BACKUP_EXPECTED_DATABASE=money_tracker\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_BACKUP_CONFIG_FILE; then
  printf 'MONEY_TRACKER_BACKUP_CONFIG_FILE=%s\n' "${env_file}" >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_RESTIC_REPOSITORY; then
  printf 'MONEY_TRACKER_RESTIC_REPOSITORY=/absolute/path/to/external/money-tracker-restic\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_RESTIC_PASSWORD_FILE; then
  backup_password_file="${XDG_CONFIG_HOME:-${HOME}/.config}/money-tracker/restic-password"
  printf 'MONEY_TRACKER_RESTIC_PASSWORD_FILE=%s\n' "${backup_password_file}" >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_BACKUP_LOCK_FILE; then
  backup_lock_file="${XDG_STATE_HOME:-${HOME}/.local/state}/money-tracker/backup.lock"
  printf 'MONEY_TRACKER_BACKUP_LOCK_FILE=%s\n' "${backup_lock_file}" >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_BACKUP_STAGING_PARENT; then
  backup_staging_parent="${XDG_CACHE_HOME:-${HOME}/.cache}/money-tracker/backup-staging"
  printf 'MONEY_TRACKER_BACKUP_STAGING_PARENT=%s\n' "${backup_staging_parent}" >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_RESTIC_BINARY; then
  printf 'MONEY_TRACKER_RESTIC_BINARY=/usr/bin/restic\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_PG_DUMP_BINARY; then
  printf 'MONEY_TRACKER_PG_DUMP_BINARY=/usr/lib/postgresql/17/bin/pg_dump\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MONEY_TRACKER_PG_RESTORE_BINARY; then
  printf 'MONEY_TRACKER_PG_RESTORE_BINARY=/usr/lib/postgresql/17/bin/pg_restore\n' >>"${env_file}"
  updated=true
fi

if ! has_setting MMPROJ_PATH; then
  printf 'MMPROJ_PATH=\n' >>"${env_file}"
  updated=true
fi

if ! has_setting LLAMA_PARALLEL; then
  printf 'LLAMA_PARALLEL=1\n' >>"${env_file}"
  updated=true
fi

if ! has_setting LLAMA_REASONING; then
  printf 'LLAMA_REASONING=off\n' >>"${env_file}"
  updated=true
fi

chmod 600 "${env_file}"
if [[ "${updated}" == "true" ]]; then
  echo "Updated ${env_file} without changing existing values."
else
  echo "No update required for ${env_file}."
fi
