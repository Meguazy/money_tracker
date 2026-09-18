"""Unit tests for the executable Git policy."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from money_tracker.development.git_policy import (
    validate_branch_name,
    validate_commit_message,
    validate_commit_range,
    validate_pre_push_updates,
    validate_pull_request_body,
    validate_working_tree_branch,
)


@pytest.mark.parametrize(
    "message",
    (
        "ci: add repository guardrails",
        "fix(hooks)!: reject direct pushes\n\nBREAKING CHANGE: install hooks",
        "docs(roadmap): document pull-request delivery",
        # The shape `.github/dependabot.yml` configures, with its trailer.
        (
            "build(deps-dev): bump the python group with 2 updates\n\n"
            "---\nupdated-dependencies:\n- dependency-name: ruff\n...\n\n"
            "Signed-off-by: dependabot[bot] <support@github.com>"
        ),
        "ci(deps): bump the github-actions group with 3 updates",
    ),
)
def test_valid_conventional_commit_messages_pass(message: str) -> None:
    """Accept allowed types, optional scopes, and breaking-change markers."""

    assert validate_commit_message(message) == []


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("feature: add guardrail", "allowed type"),
        ("fixup! ci: add guardrail", "fixup!"),
        ("squash! ci: add guardrail", "squash!"),
        (
            "ci: add guardrail\n\nco-AUTHORED-by: Helper <helper@example.test>",
            "Co-authored-by",
        ),
    ),
)
def test_invalid_commit_messages_report_the_policy(message: str, expected: str) -> None:
    """Reject unsupported headers, autosquash commits, and co-author trailers."""

    assert any(expected in issue for issue in validate_commit_message(message))


@pytest.mark.parametrize(
    "branch",
    (
        "ci/agent-workflow-guardrails",
        "feat/step-6-receipt-extraction",
        "fix/step-5-5-flush-timeout",
        "dependabot/pip/pytest-9.1",
    ),
)
def test_valid_branch_names_pass(branch: str) -> None:
    """Accept roadmap, non-roadmap, and Dependabot branch conventions."""

    assert validate_branch_name(branch) == []


@pytest.mark.parametrize(
    "branch",
    ("main", "feature/new-thing", "ci/Step-6-bad", "ci/topic_with_underscore"),
)
def test_invalid_branch_names_are_rejected(branch: str) -> None:
    """Reject untyped or non-kebab-case pull-request branch names."""

    assert validate_branch_name(branch)


def test_pull_request_body_requires_every_template_section() -> None:
    """Keep metadata validation aligned with the checked-in PR template."""

    repository_root = Path(__file__).resolve().parents[4]
    body = (repository_root / ".github/pull_request_template.md").read_text(
        encoding="utf-8"
    )

    assert validate_pull_request_body(body) == []
    assert validate_pull_request_body(
        body.replace("## Validation", "### Validation")
    ) == ["pull-request body is missing required section: Validation"]


def test_pre_push_rejects_every_update_of_remote_main() -> None:
    """Reject main whether it is sourced from main or another local branch."""

    zeros = "0" * 40
    lines = (
        f"refs/heads/ci/guardrails {zeros} refs/heads/main {zeros}\n"
        f"refs/heads/ci/guardrails {zeros} refs/heads/ci/guardrails {zeros}\n"
    )

    issues = validate_pre_push_updates(lines)

    assert issues == ["pushes that update refs/heads/main are not allowed"]


def _git(repository: Path, *arguments: str) -> str:
    """Run Git in an isolated repository and return standard output."""

    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Single Author",
            "GIT_AUTHOR_EMAIL": "author@example.test",
            "GIT_COMMITTER_NAME": "Single Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.test",
        }
    )
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def test_working_tree_rejects_main_but_allows_detached_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protect main without preventing the detached state used by rebase."""

    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "commit", "--allow-empty", "-m", "ci: create base")
    monkeypatch.chdir(tmp_path)

    assert validate_working_tree_branch()

    _git(tmp_path, "switch", "--detach")
    assert validate_working_tree_branch() == []


def test_commit_range_rejects_merge_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspect real Git objects and reject a merge in the pull-request range."""

    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "commit", "--allow-empty", "-m", "ci: create base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "switch", "-c", "ci/left")
    _git(tmp_path, "commit", "--allow-empty", "-m", "ci: add left")
    _git(tmp_path, "switch", "-c", "ci/right", base)
    _git(tmp_path, "commit", "--allow-empty", "-m", "ci: add right")
    _git(tmp_path, "switch", "ci/left")
    _git(tmp_path, "merge", "--no-ff", "ci/right", "-m", "ci: merge branches")
    monkeypatch.chdir(tmp_path)

    issues = validate_commit_range(base, "HEAD")

    assert any("merge commits are not allowed" in issue for issue in issues)


def test_versioned_hooks_are_executable_and_call_the_checker() -> None:
    """Keep each hook executable and wired to the single policy implementation."""

    repository_root = Path(__file__).resolve().parents[4]
    hooks = repository_root / ".githooks"
    for name in ("pre-commit", "commit-msg", "pre-push"):
        hook = hooks / name
        assert hook.stat().st_mode & stat.S_IXUSR
        assert "money-tracker-validate-git-policy" in hook.read_text(encoding="utf-8")
