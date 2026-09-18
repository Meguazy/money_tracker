"""Guard sensitive Git and environment-file operations requested through Bash."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from money_tracker.development.git_policy import current_branch
from money_tracker.structured_logging import configure_logging

_GIT_COMMIT = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+commit(?:\s|$)")
_GIT_PUSH = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+push(?:\s|$)")
_GH_PR_MERGE = re.compile(r"\bgh\s+pr\s+merge(?:\s|$)")
_HOOKS_PATH = re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+config\b[^;&|\n]*core\.hooksPath",
    re.IGNORECASE,
)
_ENV_PRINT = re.compile(
    r"\b(?:cat|head|tail|less|more|sed|awk|grep|rg|nl|bat)\b"
    r"[^;&|\n]*(?:^|[\s'\"/])\.env(?:\.acceptance)?(?:[\s'\";&|]|$)"
)


def _pushes_main(command: str) -> bool:
    """Return whether a Git push command names main as its destination."""

    for match in _GIT_PUSH.finditer(command):
        segment = re.split(r"[;&|\n]", command[match.start() :], maxsplit=1)[0]
        if re.search(r"(?:^|[\s:/])main(?:[\s'\"]|$)", segment):
            return True
    return False


def bash_policy_violation(command: str, branch: str | None) -> str | None:
    """Return the first blocking reason for a proposed Bash command.

    Args:
        command: Complete textual command supplied to Claude Code's Bash tool.
        branch: Current short Git branch, or None outside a branch.

    Returns:
        A stable reason when the command is forbidden, otherwise None.
    """

    if _GH_PR_MERGE.search(command):
        return "Only the repository owner may merge pull requests."
    if "--no-verify" in command:
        return "--no-verify is forbidden for commits and pushes."
    if re.search(
        r"\bgit(?:\s+-C\s+\S+)?\s+(?:commit|push)\b[^;&|\n]*\s-n(?:\s|$)",
        command,
    ):
        return "The -n hook-bypass option is forbidden for commits and pushes."
    if _HOOKS_PATH.search(command):
        return "Changing core.hooksPath is forbidden."
    if _ENV_PRINT.search(command):
        return "Printing .env or .env.acceptance is forbidden."
    if branch == "main" and _GIT_COMMIT.search(command):
        return "Commits on main are forbidden; create a slice branch first."
    if _pushes_main(command):
        return "Pushes whose destination is main are forbidden."
    return None


def _command_from_stdin() -> str:
    """Read and validate the Claude Code PreToolUse payload."""

    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def main() -> None:
    """Block forbidden Bash operations and fail open on internal errors."""

    try:
        configure_logging()
        command = _command_from_stdin()
        try:
            branch = current_branch()
        except subprocess.CalledProcessError:
            branch = None
        reason = bash_policy_violation(command, branch)
    except Exception:
        # Hook failures are non-blocking by contract. Keep the diagnostic stable so
        # the payload or command can never leak into the transcript.
        print("Claude Bash policy guard failed open.", file=sys.stderr)
        return
    if reason is not None:
        print(reason, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
