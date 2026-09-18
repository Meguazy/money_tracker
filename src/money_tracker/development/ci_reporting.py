"""Report JUnit outcomes and enforce non-empty real-service CI suites.

Every CI job that runs a pytest, Vitest, or Playwright suite passes its JUnit
report through this command. It appends the passed, skipped, failed, and error
counts to the GitHub job summary. For a job that starts a real service, it also
fails when the suite executed no test or skipped tests because the enabling
flag the job promises to set was absent, so a mis-wired job cannot turn green
by skipping everything.
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from money_tracker.structured_logging import configure_logging, get_logger

_LOGGER = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JUnitSummary:
    """Aggregate observable outcomes from one or more JUnit documents.

    Attributes:
        passed: Test cases that have no failure, error, or skip outcome.
        skipped: Test cases represented by a ``skipped`` element.
        failed: Test cases represented by a ``failure`` element.
        errors: Test cases represented by an ``error`` element.
        skip_reasons: Non-empty skip messages exposed by the reporters.
    """

    passed: int
    skipped: int
    failed: int
    errors: int
    skip_reasons: tuple[str, ...]

    @property
    def total(self) -> int:
        """Return the number of test cases represented in the documents."""

        return self.passed + self.skipped + self.failed + self.errors

    @property
    def executed(self) -> int:
        """Return test cases that ran instead of being skipped."""

        return self.total - self.skipped


def summarize_junit(paths: Sequence[Path]) -> JUnitSummary:
    """Aggregate test-case outcomes from JUnit XML files.

    Args:
        paths: JUnit XML documents produced by one logical suite.

    Returns:
        Counts and skip reasons derived from individual test cases.

    Raises:
        ValueError: If no JUnit document contains a test case.
        xml.etree.ElementTree.ParseError: If a document is malformed.
    """

    passed = skipped = failed = errors = 0
    reasons: list[str] = []
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            skip = case.find("skipped")
            failure = case.find("failure")
            error = case.find("error")
            if skip is not None:
                skipped += 1
                reason = (skip.get("message") or skip.text or "").strip()
                if reason:
                    reasons.append(reason)
            elif failure is not None:
                failed += 1
            elif error is not None:
                errors += 1
            else:
                passed += 1
    summary = JUnitSummary(passed, skipped, failed, errors, tuple(reasons))
    if summary.total == 0:
        raise ValueError("JUnit suite contains no test cases")
    return summary


def validate_service_summary(
    summary: JUnitSummary, forbidden_skip_reasons: Sequence[str]
) -> list[str]:
    """Reject an empty service run or a skip caused by its enabled flag.

    Args:
        summary: Parsed JUnit outcomes.
        forbidden_skip_reasons: Fragments naming flags the job promises to set.

    Returns:
        Stable violations; an empty list means the service gate really ran.
    """

    issues = []
    if summary.executed == 0:
        issues.append("service suite executed zero tests")
    for fragment in forbidden_skip_reasons:
        if any(
            fragment.casefold() in reason.casefold() for reason in summary.skip_reasons
        ):
            issues.append(f"service suite skipped tests because {fragment} was absent")
    return issues


def append_job_summary(path: Path, label: str, summary: JUnitSummary) -> None:
    """Append one test-count row to a GitHub job summary.

    Args:
        path: GitHub summary Markdown file.
        label: Human-readable suite name.
        summary: Counts to render.
    """

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"| {label} | {summary.passed} | {summary.skipped} | "
            f"{summary.failed} | {summary.errors} |\n"
        )


def _parser() -> argparse.ArgumentParser:
    """Build the CI-report command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--forbid-skip-reason", action="append", default=[])
    parser.add_argument("junit", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Report a JUnit suite and fail when a promised service gate did not run.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: If a report is unreadable or contains no test case, or if
            service validation finds an empty run or a missing execution flag.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    try:
        summary = summarize_junit(args.junit)
    except (OSError, ValueError, ET.ParseError) as error:
        _LOGGER.exception(
            "ci_report.junit_rejected",
            suite=args.label,
            report_count=len(args.junit),
            error_type=type(error).__name__,
        )
        raise SystemExit(
            f"{args.label}: JUnit evidence is unreadable or empty"
        ) from error
    if args.summary_file:
        append_job_summary(args.summary_file, args.label, summary)
    print(
        f"{args.label}: {summary.passed} passed, {summary.skipped} skipped, "
        f"{summary.failed} failed, {summary.errors} errors"
    )
    if args.service:
        issues = validate_service_summary(summary, args.forbid_skip_reason)
        if issues:
            _LOGGER.error(
                "ci_report.service_gate_rejected",
                suite=args.label,
                executed=summary.executed,
                skipped=summary.skipped,
                issue_count=len(issues),
            )
            raise SystemExit("\n".join(issues))


if __name__ == "__main__":
    main()
