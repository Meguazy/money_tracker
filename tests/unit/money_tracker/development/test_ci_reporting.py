"""Unit tests for CI JUnit reporting and real-service enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.development.ci_reporting import (
    append_job_summary,
    main,
    summarize_junit,
    validate_service_summary,
)


def _write_junit(path: Path, cases: str) -> None:
    """Write a compact synthetic JUnit document owned by the test."""

    path.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")


def test_junit_summary_counts_each_observable_outcome(tmp_path: Path) -> None:
    """Count individual cases without trusting reporter aggregate attributes."""

    report = tmp_path / "junit.xml"
    _write_junit(
        report,
        """
        <testcase name="pass" />
        <testcase name="skip"><skipped message="optional boundary" /></testcase>
        <testcase name="fail"><failure /></testcase>
        <testcase name="error"><error /></testcase>
        """,
    )

    summary = summarize_junit([report])

    assert (summary.passed, summary.skipped, summary.failed, summary.errors) == (
        1,
        1,
        1,
        1,
    )
    assert summary.total == 4
    assert summary.executed == 3


def test_service_summary_rejects_missing_flag_skip(tmp_path: Path) -> None:
    """Prevent an all-skipped service suite from becoming a green job."""

    report = tmp_path / "junit.xml"
    _write_junit(
        report,
        '<testcase name="skip"><skipped message="set RUN_POSTGRES=1" /></testcase>',
    )

    issues = validate_service_summary(summarize_junit([report]), ["RUN_POSTGRES=1"])

    assert issues == [
        "service suite executed zero tests",
        "service suite skipped tests because RUN_POSTGRES=1 was absent",
    ]


def test_empty_junit_is_rejected(tmp_path: Path) -> None:
    """Reject a reporter artifact that proves no test discovery."""

    report = tmp_path / "junit.xml"
    _write_junit(report, "")

    with pytest.raises(ValueError, match="no test cases"):
        summarize_junit([report])


def test_job_summary_appends_exact_counts(tmp_path: Path) -> None:
    """Write a concise Markdown row for the GitHub job summary."""

    report = tmp_path / "junit.xml"
    destination = tmp_path / "summary.md"
    _write_junit(report, '<testcase name="pass" />')

    append_job_summary(destination, "unit", summarize_junit([report]))

    assert destination.read_text(encoding="utf-8") == "| unit | 1 | 0 | 0 | 0 |\n"


def test_command_prints_and_summarizes_a_real_service_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accept a service suite whose only skips come from other flags."""

    report = tmp_path / "junit.xml"
    summary = tmp_path / "summary.md"
    _write_junit(
        report,
        """
        <testcase name="pass" />
        <testcase name="other"><skipped message="set RUN_BACKUP=1" /></testcase>
        """,
    )

    main(
        [
            "--label",
            "service",
            "--summary-file",
            str(summary),
            "--service",
            "--forbid-skip-reason",
            "RUN_POSTGRES=1",
            str(report),
        ]
    )

    assert capsys.readouterr().out == (
        "service: 1 passed, 1 skipped, 0 failed, 0 errors\n"
    )
    assert summary.read_text(encoding="utf-8") == "| service | 1 | 1 | 0 | 0 |\n"


def test_command_rejects_a_service_run_skipped_by_its_own_flag(
    tmp_path: Path,
) -> None:
    """Fail the job when the flag it promises to set was missing."""

    report = tmp_path / "junit.xml"
    _write_junit(
        report,
        '<testcase name="skip"><skipped message="set RUN_POSTGRES=1" /></testcase>',
    )

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--label",
                "service",
                "--service",
                "--forbid-skip-reason",
                "RUN_POSTGRES=1",
                str(report),
            ]
        )

    assert raised.value.code == (
        "service suite executed zero tests\n"
        "service suite skipped tests because RUN_POSTGRES=1 was absent"
    )


@pytest.mark.parametrize("contents", ["<testsuites>", "<testsuites />"])
def test_command_rejects_malformed_or_empty_reports(
    tmp_path: Path, contents: str
) -> None:
    """Turn unusable reporter output into a stable command failure."""

    report = tmp_path / "junit.xml"
    report.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        main(["--label", "broken", str(report)])

    assert raised.value.code == "broken: JUnit evidence is unreadable or empty"
