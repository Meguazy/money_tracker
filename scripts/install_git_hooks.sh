#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

git config core.hooksPath .githooks
configured_path="$(git config --get core.hooksPath)"
if [[ "${configured_path}" != ".githooks" ]]; then
  echo "Failed to configure the versioned Git hooks." >&2
  exit 1
fi

echo "Configured core.hooksPath=.githooks"
