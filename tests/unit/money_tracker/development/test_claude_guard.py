"""Unit tests for the Claude Code Bash policy guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from money_tracker.development.claude_guard import bash_policy_violation


@pytest.mark.parametrize(
    ("command", "branch", "reason"),
    (
        ("git commit -m 'ci: unsafe'", "main", "Commits on main"),
        ("git push origin main", "ci/topic", "destination is main"),
        ("git push --force origin HEAD:main", "ci/topic", "destination is main"),
        ("git push -f origin HEAD:refs/heads/main", "ci/topic", "destination is main"),
        ("bash -c 'git push origin main'", "ci/topic", "destination is main"),
        ("gh pr merge 12 --admin", "ci/topic", "owner may merge"),
        ("sh -c 'gh pr merge 12'", "ci/topic", "owner may merge"),
        ("git commit --no-verify -m bad", "ci/topic", "--no-verify"),
        ("git push -n origin topic", "ci/topic", "-n hook-bypass"),
        ("git config core.hooksPath /tmp/hooks", "ci/topic", "core.hooksPath"),
        ("head -n 1 .env.acceptance", "ci/topic", "Printing .env"),
        ("cd frontend && cat ../.env", "ci/topic", "Printing .env"),
        ("bash -c 'cat .env'", "ci/topic", "Printing .env"),
    ),
)
def test_forbidden_bash_commands_are_rejected(
    command: str, branch: str, reason: str
) -> None:
    """Return a stable reason for every protected operation."""

    assert reason in (bash_policy_violation(command, branch) or "")


@pytest.mark.parametrize(
    "command",
    (
        "uv run --env-file .env pytest -q",
        "git push origin ci/agent-workflow-guardrails",
        "gh pr create --fill",
        "git commit -m 'ci: safe'",
    ),
)
def test_allowed_bash_commands_pass(command: str) -> None:
    """Allow ordinary slice work and non-printing environment-file use."""

    assert bash_policy_violation(command, "ci/agent-workflow-guardrails") is None


def test_guard_fails_open_when_hook_input_is_invalid() -> None:
    """Treat an internal hook input error as non-blocking and reveal no payload."""

    result = subprocess.run(
        (sys.executable, "-m", "money_tracker.development.claude_guard"),
        input="{invalid",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == "Claude Bash policy guard failed open.\n"


def test_guard_uses_exit_two_with_a_reason_for_a_block() -> None:
    """Use Claude Code's documented blocking exit status for forbidden Bash."""

    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 7"}}
    result = subprocess.run(
        (sys.executable, "-m", "money_tracker.development.claude_guard"),
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "owner may merge" in result.stderr


def test_shared_claude_settings_disable_attribution_and_call_guard() -> None:
    """Validate the committed hook and attribution settings."""

    repository_root = Path(__file__).resolve().parents[4]
    settings = json.loads(
        (repository_root / ".claude/settings.json").read_text(encoding="utf-8")
    )

    assert settings["attribution"] == {"commit": "", "pr": "", "sessionUrl": False}
    pre_tool = settings["hooks"]["PreToolUse"]
    assert pre_tool[0]["matcher"] == "Bash"
    assert "money-tracker-guard-claude-bash" in pre_tool[0]["hooks"][0]["command"]
    assert "bypassPermissions" not in json.dumps(settings)
    assert "includeCoAuthoredBy" not in settings
