#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MONEY_TRACKER_POSTGRES_PASSWORD:-}" ]]; then
  echo "MONEY_TRACKER_POSTGRES_PASSWORD is required." >&2
  exit 1
fi

psql \
  --set=ON_ERROR_STOP=1 \
  --set=app_password="${MONEY_TRACKER_POSTGRES_PASSWORD}" \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" <<'SQL'
CREATE ROLE money_tracker
    LOGIN
    PASSWORD :'app_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;
ALTER DATABASE money_tracker OWNER TO money_tracker;
CREATE DATABASE money_tracker_test OWNER money_tracker;
SQL
