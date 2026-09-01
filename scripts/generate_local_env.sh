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

MODEL_PATH=/absolute/path/to/model.gguf
LLAMA_SERVER_BIN=llama-server
LLAMA_MODEL_ALIAS=local-model
LLAMA_CTX_SIZE=8192
EOF

chmod 600 "${env_file}"
echo "Created ${env_file}"
