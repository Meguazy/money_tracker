"""Validate repository branch, commit-message, and pull-request history policy."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from money_tracker.structured_logging import configure_logging

ALLOWED_TYPES = (
    "feat",
    "fix",
    "docs",
    "test",
    "refactor",
    "perf",
    "build",
    "ci",
    "chore",
)
_TYPE_PATTERN = "|".join(ALLOWED_TYPES)
_HEADER = re.compile(rf"^(?:{_TYPE_PATTERN})(?:\([a-z0-9][a-z0-9./-]*\))?!?: [^\s].*$")
_CO_AUTHOR = re.compile(r"^co-authored-by\s*:", re.IGNORECASE | re.MULTILINE)
_BRANCH = re.compile(
    rf"^(?:{_TYPE_PATTERN})/(?:step-\d+(?:-\d+)*-)?"
    r"[a-z0-9]+(?:-[a-z0-9]+)*$"
)


def validate_commit_message(message: str) -> list[str]:
    """Return violations in one proposed commit message.

    Args:
        message: Complete commit message, including any body and trailers.

    Returns:
        Stable human-readable violations. An empty list means the message is valid.
    """

    lines = message.splitlines()
    header = lines[0] if lines else ""
    issues: list[str] = []
    if header.startswith(("fixup!", "squash!")):
        issues.append("fixup! and squash! commits are not allowed")
    if not _HEADER.fullmatch(header):
        allowed = ", ".join(ALLOWED_TYPES)
        issues.append(
            "commit header must use Conventional Commits with an allowed type "
            f"({allowed})"
        )
    if _CO_AUTHOR.search(message):
        issues.append("Co-authored-by trailers are not allowed")
    return issues


def validate_branch_name(branch: str) -> list[str]:
    """Return violations in one pull-request branch name.

    Args:
        branch: Short branch name without a remote prefix.

    Returns:
        Stable human-readable violations. Dependabot branches are exempt.
    """

    if branch.startswith("dependabot/") or _BRANCH.fullmatch(branch):
        return []
    return ["branch must be <type>/<step>-<topic> or <type>/<topic> in kebab-case"]


def _git(*arguments: str) -> str:
    """Run Git and return decoded standard output.

    Args:
        arguments: Git arguments after the executable name.

    Returns:
        Standard output with surrounding whitespace removed.

    Raises:
        subprocess.CalledProcessError: If Git rejects the command.
    """

    result = subprocess.run(
        ("git", *arguments),
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def current_branch() -> str | None:
    """Return the current short branch, or None for a detached HEAD."""

    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    return branch or None


def validate_working_tree_branch() -> list[str]:
    """Reject commits on main while allowing detached HEAD during a rebase."""

    try:
        branch = current_branch()
    except subprocess.CalledProcessError:
        return []
    if branch == "main":
        return ["commits on main are not allowed; create a slice branch first"]
    return []


def _commit_issues(commit: str) -> list[str]:
    """Return message, author-count, and merge violations for one commit."""

    raw = _git("cat-file", "-p", commit)
    headers = raw.split("\n\n", 1)[0].splitlines()
    author_count = sum(line.startswith("author ") for line in headers)
    parent_count = sum(line.startswith("parent ") for line in headers)
    message = _git("show", "-s", "--format=%B", commit)
    issues = [f"{commit}: {issue}" for issue in validate_commit_message(message)]
    if author_count != 1:
        issues.append(f"{commit}: commit must have exactly one author")
    if parent_count > 1:
        issues.append(f"{commit}: merge commits are not allowed in a PR range")
    return issues


def validate_commit_range(base: str, head: str) -> list[str]:
    """Return violations for every commit reachable in `base..head`.

    Args:
        base: Base revision excluded from validation.
        head: Head revision included in validation.

    Returns:
        Stable human-readable violations for the pull-request range.
    """

    commits = _git("rev-list", "--reverse", f"{base}..{head}").splitlines()
    issues: list[str] = []
    for commit in commits:
        issues.extend(_commit_issues(commit))
    return issues


def validate_pre_push_updates(lines: str) -> list[str]:
    """Validate branch names and reject every remote update of main.

    Args:
        lines: Git pre-push records read from standard input.

    Returns:
        Stable human-readable violations.
    """

    issues: list[str] = []
    for line in lines.splitlines():
        fields = line.split()
        if len(fields) != 4:
            issues.append("invalid pre-push input")
            continue
        local_ref, _local_oid, remote_ref, _remote_oid = fields
        if remote_ref == "refs/heads/main":
            issues.append("pushes that update refs/heads/main are not allowed")
        prefix = "refs/heads/"
        if local_ref.startswith(prefix):
            issues.extend(validate_branch_name(local_ref.removeprefix(prefix)))
    return issues


def _parser() -> argparse.ArgumentParser:
    """Build the Git-policy command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    message = subparsers.add_parser("message", help="validate a commit message file")
    message.add_argument("path", type=Path)
    title = subparsers.add_parser("title", help="validate a PR title")
    title.add_argument("text")
    branch = subparsers.add_parser("branch", help="validate a branch name")
    branch.add_argument("name")
    commit_range = subparsers.add_parser("range", help="validate base..head commits")
    commit_range.add_argument("base")
    commit_range.add_argument("head")
    subparsers.add_parser("working-tree", help="reject a commit on main")
    subparsers.add_parser("pre-push", help="validate pre-push records on stdin")
    return parser


def _run(args: argparse.Namespace) -> list[str]:
    """Execute one parsed policy command and return its violations."""

    if args.command == "message":
        return validate_commit_message(args.path.read_text(encoding="utf-8"))
    if args.command == "title":
        return validate_commit_message(args.text)
    if args.command == "branch":
        return validate_branch_name(args.name)
    if args.command == "range":
        return validate_commit_range(args.base, args.head)
    if args.command == "working-tree":
        return validate_working_tree_branch()
    if args.command == "pre-push":
        return validate_pre_push_updates(sys.stdin.read())
    return ["unsupported Git policy command"]


def main(argv: Sequence[str] | None = None) -> None:
    """Validate one Git policy surface and exit non-zero on violations.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: If policy violations are found.
    """

    configure_logging()
    issues = _run(_parser().parse_args(argv))
    if issues:
        raise SystemExit("\n".join(issues))


if __name__ == "__main__":
    main()
