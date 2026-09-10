#!/usr/bin/env bash
set -euo pipefail

# Render the unit templates for this checkout and install them as user
# services. User services are deliberate: the application owns no system-wide
# resource, and lingering is what makes it survive logout without granting it
# root. Every failure below names the exact fix, because a unit that installs
# but cannot start is harder to diagnose than one that refuses to install.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_source="${repo_root}/deploy/systemd"
unit_target="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is not available; these units require systemd." >&2
  exit 2
fi

if [[ ! -x "${repo_root}/.venv/bin/money-tracker-telegram" ]] ||
   [[ ! -x "${repo_root}/.venv/bin/money-tracker-worker" ]]; then
  echo "The locked virtual environment is missing production entry points." >&2
  echo "Run: uv sync --locked --all-groups" >&2
  exit 2
fi

if [[ ! -f "${repo_root}/.env" ]]; then
  echo "No .env in ${repo_root}." >&2
  echo "Create it first: scripts/generate_local_env.sh" >&2
  exit 2
fi

mkdir -p "${unit_target}"
installed=()
for template in "${unit_source}"/*.service; do
  unit_name="$(basename "${template}")"
  sed \
    -e "s|__MONEY_TRACKER_ROOT__|${repo_root}|g" \
    "${template}" >"${unit_target}/${unit_name}"
  installed+=("${unit_name}")
done

systemctl --user daemon-reload

echo "Installed into ${unit_target}:"
printf '  %s\n' "${installed[@]}"
cat <<'NEXT'

The units are installed but not started. Before enabling them:

  1. Pair the owner, otherwise the gateway discards every update:
       uv run --env-file .env money-tracker-telegram pair
  2. Allow the services to run without an active login session:
       loginctl enable-linger "$USER"
  3. Enable and start what you need:
       systemctl --user enable --now money-tracker-llama.service
       systemctl --user enable --now money-tracker-telegram.service
       systemctl --user enable --now money-tracker-worker.service

Exit code 1 means an operator must change something, and the units stop
instead of restarting. Exit code 2 means a dependency was unreachable and the
unit restarts on its own. Check which one happened with:

  systemctl --user status money-tracker-telegram.service
NEXT
