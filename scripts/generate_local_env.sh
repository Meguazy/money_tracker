#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${MONEY_TRACKER_ENV_FILE:-${repo_root}/.env}"

if [[ -e "${env_file}" ]]; then
  echo "Refusing to overwrite existing file: ${env_file}" >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate local secrets." >&2
  exit 1
fi

random_hex() {
  openssl rand -hex "$1"
}

app_postgres_password="$(random_hex 24)"
app_postgres_admin_password="$(random_hex 24)"
receipt_archive_root="${XDG_DATA_HOME:-${HOME}/.local/share}/money-tracker/receipts"
model_root="${XDG_DATA_HOME:-${HOME}/.local/share}/money-tracker/models"
backup_password_file="${XDG_CONFIG_HOME:-${HOME}/.config}/money-tracker/restic-password"
backup_lock_file="${XDG_STATE_HOME:-${HOME}/.local/state}/money-tracker/backup.lock"
backup_staging_parent="${XDG_CACHE_HOME:-${HOME}/.cache}/money-tracker/backup-staging"
telegram_config_root="${XDG_CONFIG_HOME:-${HOME}/.config}/money-tracker"
telegram_production_token_file="${telegram_config_root}/telegram-production.token"
telegram_test_token_file="${telegram_config_root}/telegram-test.token"

umask 077
cat >"${env_file}" <<EOF
# Generated local development configuration. Do not commit this file.

LLM_BASE_URL=http://localhost:8080/v1
LLM_API_KEY=local
LLM_MODEL=local-model
LLM_TEMPERATURE=0.1
LLM_TIMEOUT_SECONDS=120

LANGFUSE_BASE_URL=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-$(random_hex 24)
LANGFUSE_SECRET_KEY=sk-lf-$(random_hex 24)
LANGFUSE_TRACING_ENVIRONMENT=development

LANGFUSE_INIT_USER_EMAIL=admin@money-tracker.local
LANGFUSE_INIT_USER_NAME=Local-Admin
LANGFUSE_INIT_USER_PASSWORD=$(random_hex 16)

LANGFUSE_NEXTAUTH_SECRET=$(random_hex 32)
LANGFUSE_SALT=$(random_hex 32)
LANGFUSE_ENCRYPTION_KEY=$(random_hex 32)
LANGFUSE_POSTGRES_PASSWORD=$(random_hex 24)
LANGFUSE_CLICKHOUSE_PASSWORD=$(random_hex 24)
LANGFUSE_REDIS_PASSWORD=$(random_hex 24)
LANGFUSE_MINIO_PASSWORD=$(random_hex 24)

MONEY_TRACKER_POSTGRES_ADMIN_PASSWORD=${app_postgres_admin_password}
MONEY_TRACKER_POSTGRES_PASSWORD=${app_postgres_password}
MONEY_TRACKER_DATABASE_URL=postgresql://money_tracker:${app_postgres_password}@127.0.0.1:5433/money_tracker
MONEY_TRACKER_TEST_DATABASE_URL=postgresql://money_tracker:${app_postgres_password}@127.0.0.1:5433/money_tracker_test
MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT=${receipt_archive_root}

MONEY_TRACKER_TELEGRAM_ENVIRONMENT=test
MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE=${telegram_production_token_file}
MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE=${telegram_test_token_file}

MONEY_TRACKER_BACKUP_EXPECTED_DATABASE=money_tracker
MONEY_TRACKER_BACKUP_CONFIG_FILE=${env_file}
MONEY_TRACKER_RESTIC_REPOSITORY=/absolute/path/to/external/money-tracker-restic
MONEY_TRACKER_RESTIC_PASSWORD_FILE=${backup_password_file}
MONEY_TRACKER_BACKUP_LOCK_FILE=${backup_lock_file}
MONEY_TRACKER_BACKUP_STAGING_PARENT=${backup_staging_parent}
MONEY_TRACKER_RESTIC_BINARY=/usr/bin/restic
MONEY_TRACKER_PG_DUMP_BINARY=/usr/lib/postgresql/17/bin/pg_dump
MONEY_TRACKER_PG_RESTORE_BINARY=/usr/lib/postgresql/17/bin/pg_restore

LANGGRAPH_STRICT_MSGPACK=true

MODEL_PATH=${model_root}/text/<profile>/<weights>.gguf
MMPROJ_PATH=
LLAMA_SERVER_BIN=llama-server
LLAMA_MODEL_ALIAS=local-model
LLAMA_CTX_SIZE=8192
LLAMA_PARALLEL=1
LLAMA_REASONING=off
EOF

chmod 600 "${env_file}"
echo "Created ${env_file}"
