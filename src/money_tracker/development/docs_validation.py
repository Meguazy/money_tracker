"""Validate documentation links, identifiers, fences, YAML, and privacy rules."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import unquote

import yaml

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
REQUIREMENT_DEFINITION = re.compile(r"\*\*((?:FR|NFR)-[A-Z]+-\d{3})\s+—")


def _markdown_files(repository_root: Path) -> Iterable[Path]:
    """Yield canonical repository Markdown while excluding generated areas."""

    for root_file in ("README.md", "AGENTS.md"):
        path = repository_root / root_file
        if path.is_file():
            yield path
    yield from sorted((repository_root / "docs").rglob("*.md"))
    eval_root = repository_root / "evals"
    if eval_root.is_dir():
        yield from sorted(eval_root.rglob("*.md"))


def _relative_link_issues(path: Path, text: str) -> list[str]:
    """Return missing local-link diagnostics for one Markdown file."""

    issues: list[str] = []
    for match in MARKDOWN_LINK.finditer(text):
        raw_target = match.group(1).strip().strip("<>")
        if not raw_target or raw_target.startswith(
            ("#", "http://", "https://", "mailto:")
        ):
            continue
        target_without_anchor = unquote(raw_target.split("#", 1)[0])
        if not target_without_anchor:
            continue
        resolved = (path.parent / target_without_anchor).resolve()
        if not resolved.exists():
            issues.append(f"{path}: missing link target {raw_target}")
    return issues


def _yaml_issues(repository_root: Path) -> list[str]:
    """Parse committed YAML documents and return stable diagnostics."""

    issues: list[str] = []
    yaml_files = sorted((repository_root / "docs").rglob("*.yaml"))
    evals_root = repository_root / "evals"
    for yaml_area in ("suites", "models"):
        area = evals_root / yaml_area
        if area.is_dir():
            yaml_files.extend(sorted(area.rglob("*.yaml")))
    for path in yaml_files:
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            issue = str(error).splitlines()[0]
            issues.append(f"{path}: invalid YAML: {issue}")
    return issues


def validate_repository(repository_root: Path) -> list[str]:
    """Validate the canonical documentation and evaluation layout.

    Args:
        repository_root: Repository directory containing ``docs`` and ``evals``.

    Returns:
        Sorted human-readable issues. An empty list means every check passed.
    """

    issues: list[str] = []
    requirement_ids: list[str] = []
    for path in _markdown_files(repository_root):
        text = path.read_text(encoding="utf-8")
        issues.extend(_relative_link_issues(path, text))
        if sum(line.lstrip().startswith("```") for line in text.splitlines()) % 2:
            issues.append(f"{path}: unbalanced fenced code block")
        if path.name == "vision-and-requirements.md":
            requirement_ids.extend(REQUIREMENT_DEFINITION.findall(text))

    for requirement_id, count in Counter(requirement_ids).items():
        if count > 1:
            issues.append(f"duplicate requirement definition: {requirement_id}")

    issues.extend(_yaml_issues(repository_root))
    gitignore = (repository_root / ".gitignore").read_text(encoding="utf-8")
    for required_rule in (
        "/artifacts/evals/",
        "/evals/private/*",
        "!/evals/private/README.md",
    ):
        if required_rule not in gitignore.splitlines():
            issues.append(f".gitignore: missing privacy rule {required_rule}")
    return sorted(issues)


def _parser() -> argparse.ArgumentParser:
    """Build the repository-validation parser."""

    parser = argparse.ArgumentParser(description="Validate project documentation")
    parser.add_argument(
        "repository_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run documentation validation and fail with actionable diagnostics.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: If one or more validation issues are found.
    """

    repository_root = _parser().parse_args(argv).repository_root.resolve()
    issues = validate_repository(repository_root)
    if issues:
        raise SystemExit("\n".join(issues))
    print("Documentation validation passed.")


if __name__ == "__main__":
    main()
