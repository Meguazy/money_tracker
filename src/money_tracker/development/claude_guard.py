"""Guard sensitive Git, GitHub, and environment-file operations requested through Bash.

Claude Code runs this module as a `PreToolUse` hook for every Bash command. The
declarative deny rules in `.claude/settings.json` are glob patterns over the
command text; this guard is the precise layer behind them. It tokenizes the
command like a shell, so quoted commit messages and heredoc bodies are data
rather than arguments, and it follows `bash -c`, `sh -c`, and `eval`.

Git commands are judged by what they do: `git commit` and `git push` must not
carry `-n` or `--no-verify`, no commit may land on `main`, and no push may name
`main` as its destination, whether through a refspec, `HEAD` while on `main`, a
bare push from `main`, or `--all`/`--mirror`. GitHub API calls that write
rulesets, branch protection, or repository settings are reserved for the owner.
Commands that cannot be tokenized fall back to conservative text patterns.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator, Sequence

from money_tracker.development.git_policy import current_branch
from money_tracker.structured_logging import configure_logging

MERGE_REASON = "Only the repository owner may merge pull requests."
NO_VERIFY_REASON = "--no-verify is forbidden for commits and pushes."
N_OPTION_REASON = "The -n option is forbidden for git commit and git push."
HOOKS_PATH_REASON = "Changing core.hooksPath is forbidden."
ENV_PRINT_REASON = "Printing .env or .env.acceptance is forbidden."
COMMIT_ON_MAIN_REASON = "Commits on main are forbidden; create a slice branch first."
PUSH_TO_MAIN_REASON = "Pushes whose destination is main are forbidden."
PROTECTION_REASON = "Changing rulesets or branch protection is reserved for the owner."
REPOSITORY_SETTINGS_REASON = "Changing repository settings is reserved for the owner."

_GH_PR_MERGE = re.compile(r"\bgh\s+pr\s+merge(?:\s|$)")
_HOOKS_PATH = re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+config\b[^;&|\n]*core\.hooksPath",
    re.IGNORECASE,
)
_ENV_PRINT = re.compile(
    r"\b(?:cat|head|tail|less|more|sed|awk|grep|rg|nl|bat)\b"
    r"[^;&|\n]*(?:^|[\s'\"/])\.env(?:\.acceptance)?(?:[\s'\";&|]|$)"
)

# Shell structure.
_SEPARATORS = ";&|()\n"
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_FD_REDIRECTION = re.compile(r"\d*[<>]&\d*-?|&>>?")
_REDIRECTION = re.compile(r"^\d*(?:<<?|>>?)")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset({"command", "exec", "nohup", "time", "env", "sudo"})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_MAX_NESTING = 3

# Git option grammar needed to find subcommands, clusters, and refspecs.
_GIT_GLOBAL_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
_COMMIT_SHORT_VALUE = frozenset("mFCct")
_COMMIT_SHORT_ATTACHED = frozenset("Su")
_COMMIT_LONG_VALUE = frozenset(
    {
        "--message",
        "--file",
        "--reuse-message",
        "--reedit-message",
        "--template",
        "--author",
        "--date",
        "--cleanup",
        "--fixup",
        "--squash",
        "--trailer",
        "--pathspec-from-file",
    }
)
_PUSH_LONG_VALUE = frozenset({"--push-option", "--repo", "--receive-pack", "--exec"})
_PUSH_ALL_BRANCHES = frozenset({"--all", "--mirror", "--branches"})

# GitHub API grammar.
_GH_API_BODY_OPTIONS = frozenset({"-f", "--raw-field", "-F", "--field", "--input"})
_GH_API_VALUE_OPTIONS = frozenset(
    {
        "-H",
        "--header",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--hostname",
        "-p",
        "--preview",
        "--cache",
    }
)
_PROTECTION_ENDPOINT = re.compile(
    r"(?:^|/)(?:rulesets|branches/[^/]+/protection)(?:/|$)"
)
_REPOSITORY_ENDPOINT = re.compile(r"^/?repos/[^/]+/[^/]+/?$")
_PROTECTION_MUTATION = re.compile(
    r"\b(?:create|update|delete)(?:RepositoryRuleset|BranchProtectionRule)\b"
)

# Conservative fallbacks for text that does not tokenize.
_FALLBACK_N_OPTION = re.compile(
    r"\bgit(?:\s+-C\s+\S+)?\s+(?:commit|push)\b[^;&|\n]*\s-n(?:\s|$)"
)
_FALLBACK_PUSH = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+push(?:\s|$)")
_FALLBACK_GH_API_PROTECTED = re.compile(
    r"\bgh\s+api\b[^;&|\n]*(?:rulesets|branches/\S+/protection)"
)
_FALLBACK_GH_API_WRITE_OPTION = re.compile(
    r"\s(?:-X\S*|--method|-f|-F|--field|--raw-field|--input)(?:[\s=]|$)"
)


def _without_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies, which are data such as commit messages."""

    kept: list[str] = []
    terminators: list[str] = []
    for line in command.split("\n"):
        if terminators:
            if line.strip() == terminators[0]:
                terminators.pop(0)
            continue
        kept.append(line)
        terminators.extend(match.group(2) for match in _HEREDOC.finditer(line))
    return "\n".join(kept)


def _tokens(command: str) -> list[str]:
    """Split a command like a POSIX shell, keeping operators as tokens.

    Raises:
        ValueError: If quoting is unbalanced.
    """

    text = _FD_REDIRECTION.sub(" ", _without_heredoc_bodies(command))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_SEPARATORS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    return list(lexer)


def _simple_commands(command: str, depth: int = 0) -> Iterator[list[str]]:
    """Yield every simple command, including those nested in a shell or eval.

    Raises:
        ValueError: If quoting is unbalanced.
    """

    current: list[str] = []
    for token in [*_tokens(command), ";"]:
        if token and all(character in _SEPARATORS for character in token):
            yield from _expanded(current, depth)
            current = []
        else:
            current.append(token)


def _expanded(tokens: list[str], depth: int) -> Iterator[list[str]]:
    """Normalize one simple command and descend into nested command text."""

    words = _without_redirections(tokens)
    while words and (_ASSIGNMENT.match(words[0]) or words[0] in _WRAPPERS):
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
    if not words:
        return
    program = words[0].rsplit("/", 1)[-1]
    if depth < _MAX_NESTING:
        if program in _SHELLS:
            for index, word in enumerate(words[1:-1], start=1):
                if word.startswith("-") and not word.startswith("--") and "c" in word:
                    yield from _simple_commands(words[index + 1], depth + 1)
                    break
        elif program == "eval":
            yield from _simple_commands(" ".join(words[1:]), depth + 1)
    yield [program, *words[1:]]


def _without_redirections(tokens: list[str]) -> list[str]:
    """Remove redirection operators and their targets from a word list."""

    words: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        redirection = _REDIRECTION.match(token)
        if redirection:
            skip_target = redirection.end() == len(token)
            continue
        words.append(token)
    return words


def _git_subcommand(arguments: Sequence[str]) -> tuple[str | None, list[str], bool]:
    """Return the Git subcommand, its arguments, and a hooks-path override flag."""

    index = 0
    overrides_hooks = False
    while index < len(arguments):
        argument = arguments[index]
        if argument in _GIT_GLOBAL_VALUE_OPTIONS:
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
            overrides_hooks |= value.lower().startswith("core.hookspath")
            index += 2
            continue
        if argument.startswith("--config-env="):
            overrides_hooks |= argument.lower().startswith(
                "--config-env=core.hookspath"
            )
        if argument.startswith("-"):
            index += 1
            continue
        return argument, list(arguments[index + 1 :]), overrides_hooks
    return None, [], overrides_hooks


def _commit_uses_n(arguments: Sequence[str]) -> bool:
    """Return whether commit options, not message text, include `-n`."""

    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        if argument.startswith("--"):
            if "=" not in argument and argument in _COMMIT_LONG_VALUE:
                index += 1
        elif argument.startswith("-") and len(argument) > 1:
            cluster = argument[1:]
            for position, option in enumerate(cluster):
                if option == "n":
                    return True
                if option in _COMMIT_SHORT_ATTACHED:
                    break
                if option in _COMMIT_SHORT_VALUE:
                    if position == len(cluster) - 1:
                        index += 1
                    break
        index += 1
    return False


def _push_violation(arguments: Sequence[str], branch: str | None) -> str | None:
    """Return why a push is forbidden, judging its options and destinations."""

    positionals: list[str] = []
    pushes_all = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            positionals.extend(arguments[index + 1 :])
            break
        if argument in _PUSH_LONG_VALUE or argument == "-o":
            index += 2
            continue
        if argument.startswith("--"):
            pushes_all |= argument.split("=", 1)[0] in _PUSH_ALL_BRANCHES
        elif argument.startswith("-") and len(argument) > 1:
            cluster = argument[1:]
            for position, option in enumerate(cluster):
                if option == "n":
                    return N_OPTION_REASON
                if option == "o":
                    if position == len(cluster) - 1:
                        index += 1
                    break
        else:
            positionals.append(argument)
        index += 1
    if pushes_all:
        return PUSH_TO_MAIN_REASON
    refspecs = positionals[1:]
    if not refspecs:
        return PUSH_TO_MAIN_REASON if branch == "main" else None
    for refspec in refspecs:
        source, _, destination = refspec.removeprefix("+").partition(":")
        target = destination if destination else source
        if target in {"HEAD", "@"}:
            target = branch or ""
        target = target.removeprefix("refs/heads/")
        if target == "main" or ("*" in target and not target.startswith("refs/tags/")):
            return PUSH_TO_MAIN_REASON
    return None


def _git_violation(arguments: Sequence[str], branch: str | None) -> str | None:
    """Return why one Git invocation is forbidden."""

    subcommand, rest, overrides_hooks = _git_subcommand(arguments)
    if overrides_hooks:
        return HOOKS_PATH_REASON
    if subcommand == "commit":
        if _commit_uses_n(rest):
            return N_OPTION_REASON
        if branch == "main":
            return COMMIT_ON_MAIN_REASON
    if subcommand == "push":
        return _push_violation(rest, branch)
    return None


def _gh_api_violation(arguments: Sequence[str]) -> str | None:
    """Return why a GitHub API call is forbidden; reads are always allowed."""

    method: str | None = None
    has_body = False
    bodies: list[str] = []
    endpoint: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        name, separator, attached = argument.partition("=")
        following = arguments[index + 1] if index + 1 < len(arguments) else ""
        if argument.startswith("-X") and len(argument) > 2:
            method = argument[2:]
        elif name in {"-X", "--method"}:
            method = attached if separator else following
            index += 0 if separator else 1
        elif name in _GH_API_BODY_OPTIONS:
            has_body = True
            bodies.append(attached if separator else following)
            index += 0 if separator else 1
        elif argument[:2] in {"-f", "-F"} and len(argument) > 2:
            has_body = True
            bodies.append(argument[2:])
        elif name in _GH_API_VALUE_OPTIONS:
            index += 0 if separator else 1
        elif not argument.startswith("-") and endpoint is None:
            endpoint = argument.split("?", 1)[0]
        index += 1
    if endpoint is None:
        return None
    if endpoint == "graphql":
        if any(_PROTECTION_MUTATION.search(body) for body in bodies):
            return PROTECTION_REASON
        return None
    if (method or ("POST" if has_body else "GET")).upper() == "GET":
        return None
    if _PROTECTION_ENDPOINT.search(endpoint):
        return PROTECTION_REASON
    if _REPOSITORY_ENDPOINT.match(endpoint):
        return REPOSITORY_SETTINGS_REASON
    return None


def _parsed_violation(command: str, branch: str | None) -> str | None:
    """Judge every simple command of a tokenizable command line.

    Raises:
        ValueError: If quoting is unbalanced.
    """

    for words in _simple_commands(command):
        program, arguments = words[0], words[1:]
        reason: str | None = None
        if program == "git":
            reason = _git_violation(arguments, branch)
        elif program == "gh" and arguments[:1] == ["api"]:
            reason = _gh_api_violation(arguments[1:])
        if reason is not None:
            return reason
    return None


def _fallback_violation(command: str, branch: str | None) -> str | None:
    """Apply conservative text patterns to a command that does not tokenize."""

    if _FALLBACK_N_OPTION.search(command):
        return N_OPTION_REASON
    for match in _FALLBACK_PUSH.finditer(command):
        segment = re.split(r"[;&|\n]", command[match.start() :], maxsplit=1)[0]
        if re.search(r"(?:^|[\s:/+])main(?:[\s'\"]|$)", segment):
            return PUSH_TO_MAIN_REASON
    if branch == "main" and re.search(r"\bgit\b[^;&|\n]*\scommit(?:\s|$)", command):
        return COMMIT_ON_MAIN_REASON
    if _FALLBACK_GH_API_PROTECTED.search(
        command
    ) and _FALLBACK_GH_API_WRITE_OPTION.search(command):
        return PROTECTION_REASON
    return None


def bash_policy_violation(command: str, branch: str | None) -> str | None:
    """Return the first blocking reason for a proposed Bash command.

    Args:
        command: Complete textual command supplied to Claude Code's Bash tool.
        branch: Current short Git branch, or None outside a branch.

    Returns:
        A stable reason when the command is forbidden, otherwise None.
    """

    if _GH_PR_MERGE.search(command):
        return MERGE_REASON
    if "--no-verify" in command:
        return NO_VERIFY_REASON
    if _HOOKS_PATH.search(command):
        return HOOKS_PATH_REASON
    if _ENV_PRINT.search(command):
        return ENV_PRINT_REASON
    try:
        return _parsed_violation(command, branch)
    except ValueError:
        return _fallback_violation(command, branch)


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
