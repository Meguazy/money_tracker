"""Unit tests for the Claude Code Bash policy guard."""

from __future__ import annotations

import fnmatch
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
        ("git -C . commit -m 'ci: unsafe'", "main", "Commits on main"),
        ("git push origin main", "ci/topic", "destination is main"),
        ("git push -u origin main", "ci/topic", "destination is main"),
        ("git push --force origin HEAD:main", "ci/topic", "destination is main"),
        ("git push -f origin HEAD:refs/heads/main", "ci/topic", "destination is main"),
        ("git push origin +topic:main", "ci/topic", "destination is main"),
        ("git push origin :main", "ci/topic", "destination is main"),
        ("git push origin --delete main", "ci/topic", "destination is main"),
        (
            "git push origin 'refs/heads/*:refs/heads/*'",
            "ci/topic",
            "destination is main",
        ),
        ("git push --all origin", "ci/topic", "destination is main"),
        ("git push --mirror", "ci/topic", "destination is main"),
        ("git push", "main", "destination is main"),
        ("git push -u origin HEAD", "main", "destination is main"),
        ("git push origin main 2>&1 | tail -3", "ci/topic", "destination is main"),
        ("(cd frontend && git push origin main)", "ci/topic", "destination is main"),
        ("bash -c 'git push origin main'", "ci/topic", "destination is main"),
        ("FOO=1 env git push origin main", "ci/topic", "destination is main"),
        ("gh pr merge 12 --admin", "ci/topic", "owner may merge"),
        ("sh -c 'gh pr merge 12'", "ci/topic", "owner may merge"),
        ("git commit --no-verify -m bad", "ci/topic", "--no-verify"),
        ("git commit -n -m bad", "ci/topic", "-n option"),
        ("git commit -nm bad", "ci/topic", "-n option"),
        ("git commit -anm bad", "ci/topic", "-n option"),
        ("git commit -m bad -n", "ci/topic", "-n option"),
        ("git push -n origin topic", "ci/topic", "-n option"),
        ("git push -un origin topic", "ci/topic", "-n option"),
        ("git config core.hooksPath /tmp/hooks", "ci/topic", "core.hooksPath"),
        ("git -c core.hooksPath=/dev/null commit -m x", "ci/topic", "core.hooksPath"),
        ("head -n 1 .env.acceptance", "ci/topic", "Printing .env"),
        ("cd frontend && cat ../.env", "ci/topic", "Printing .env"),
        ("bash -c 'cat .env'", "ci/topic", "Printing .env"),
        (
            "gh api --method POST repos/o/r/rulesets --input rulesets/main.json",
            "ci/topic",
            "rulesets or branch protection",
        ),
        ("gh api -X PUT repos/o/r/rulesets/7 --input r.json", "ci/topic", "rulesets"),
        ("gh api repos/o/r/rulesets/7 -XDELETE", "ci/topic", "rulesets"),
        ("gh api repos/o/r/rulesets -f name=open", "ci/topic", "rulesets"),
        (
            "gh api -X PUT repos/o/r/branches/main/protection --input p.json",
            "ci/topic",
            "branch protection",
        ),
        (
            "gh api graphql -f query='mutation { createRepositoryRuleset(input: {}) "
            "{ clientMutationId } }'",
            "ci/topic",
            "branch protection",
        ),
        (
            "gh api -X PATCH repos/o/r -F delete_branch_on_merge=true",
            "ci/topic",
            "repository settings",
        ),
        ("gh api --method DELETE repos/o/r", "ci/topic", "repository settings"),
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
        "git push -u origin ci/protect-main",
        "git push -u origin feat/step-6-domain-model",
        "git push origin fix/remaining-checks",
        "git push origin fix/main",
        "git push origin main:ci/topic",
        "git push -u origin HEAD",
        "git push",
        "git push --force-with-lease origin ci/topic",
        "gh pr create --fill",
        "git commit -m 'ci: safe'",
        "git commit -m 'docs: explain the -n flag and main'",
        "git commit -am 'fix(web): accept -nightly builds'",
        "git commit -F - <<'EOF'\nci: safe\n\nNever pass -n or push to main.\nEOF",
        "git commit -S -m 'ci: signed'",
        "git log --oneline main..HEAD",
        "git switch main && git pull --ff-only",
        "git rev-parse --git-path hooks",
        "gh api repos/o/r/rulesets",
        "gh api repos/o/r/rules/branches/main --jq '.[].type'",
        "gh api -X GET repos/o/r/rulesets/7",
        "gh api --method PATCH repos/o/r/pulls/3 -F body=@body.md",
        'gh api graphql -f query=\'{ repository(owner: "o", name: "r") { id } }\'',
    ),
)
def test_allowed_bash_commands_pass(command: str) -> None:
    """Allow slice work, messages mentioning flags, and branch names with main."""

    assert bash_policy_violation(command, "ci/agent-workflow-guardrails") is None


def test_unbalanced_quoting_falls_back_to_conservative_patterns() -> None:
    """Keep blocking protected operations when the shell text cannot parse."""

    assert bash_policy_violation("git push origin main 'open", "ci/topic")
    assert bash_policy_violation("git commit -n -m 'open", "ci/topic")
    assert bash_policy_violation("gh api -X PUT repos/o/r/rulesets/7 'open", "ci/topic")
    assert bash_policy_violation("git push origin ci/topic 'open", "ci/topic") is None


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
    assert "Bash(git push *main*)" not in settings["permissions"]["deny"]
    pre_tool = settings["hooks"]["PreToolUse"]
    assert pre_tool[0]["matcher"] == "Bash"
    assert "money-tracker-guard-claude-bash" in pre_tool[0]["hooks"][0]["command"]
    assert "bypassPermissions" not in json.dumps(settings)
    assert "includeCoAuthoredBy" not in settings


def _declaratively_denied(command: str) -> bool:
    """Match one simple command against the committed Bash deny patterns.

    Claude Code matches each simple command of a compound line against the
    rule's glob, where `*` stands for any text; `fnmatchcase` reproduces that
    for patterns without bracket expressions, which the settings do not use.
    """

    repository_root = Path(__file__).resolve().parents[4]
    settings = json.loads(
        (repository_root / ".claude/settings.json").read_text(encoding="utf-8")
    )
    patterns = [
        rule.removeprefix("Bash(").removesuffix(")")
        for rule in settings["permissions"]["deny"]
        if rule.startswith("Bash(")
    ]
    assert not any("[" in pattern for pattern in patterns)
    return any(fnmatch.fnmatchcase(command, pattern) for pattern in patterns)


@pytest.mark.parametrize(
    "command",
    (
        "git push origin main",
        "git push -u origin main",
        "git push origin main --force",
        "git push origin HEAD:main",
        "git push --force origin HEAD:refs/heads/main",
        "git push origin +main",
        "git commit -n -m bad",
        "git commit -nm bad",
        "git commit -m bad -n",
        "git push -n origin topic",
        "git push origin topic -n",
        "gh pr merge 12",
        "gh api --method POST repos/o/r/rulesets --input main.json",
        "gh api repos/o/r/rulesets/7 -X PUT --input main.json",
        "gh api -X PUT repos/o/r/branches/main/protection --input p.json",
        "gh api graphql -f query=mutation{createRepositoryRuleset}",
        "gh api graphql -f query=mutation{updateBranchProtectionRule}",
    ),
)
def test_declarative_rules_deny_protected_operations(command: str) -> None:
    """Keep a second, hook-independent barrier for the protected operations."""

    assert _declaratively_denied(command)


@pytest.mark.parametrize(
    "command",
    (
        "git push -u origin ci/protect-main",
        "git push -u origin feat/step-6-domain-model",
        "git push origin fix/remaining-checks",
        "git push origin fix/step-6-nightly",
        "git push origin main:ci/topic",
        "git commit -m 'fix(web): reject -negative amounts'",
        "git commit -am 'docs: document the domain model'",
        "gh api repos/o/r/rulesets",
        "gh api repos/o/r/rules/branches/main --jq .",
        "gh api --method PATCH repos/o/r/pulls/3 -F body=@body.md",
    ),
)
def test_declarative_rules_allow_names_that_only_contain_main_or_n(
    command: str,
) -> None:
    """Do not refuse branch names or messages that merely contain main or -n."""

    assert not _declaratively_denied(command)
