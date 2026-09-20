"""Unit tests for the authoritative backup administration CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from money_tracker.backup_cli import _parser, _required_environment, main


def test_parser_exposes_only_explicit_backup_operations() -> None:
    """Require a bounded command and an explicit snapshot for restore drills."""

    assert _parser().parse_args(["init"]).command == "init"
    assert _parser().parse_args(["create"]).command == "create"
    assert _parser().parse_args(["status"]).command == "status"
    assert _parser().parse_args(["verify-restore"]).command == "verify-restore"
    restore = _parser().parse_args(["restore-drill", "--snapshot-id", "abc123"])
    assert restore.command == "restore-drill"
    assert restore.snapshot_id == "abc123"
    with pytest.raises(SystemExit):
        _parser().parse_args(["restore-drill"])


def test_required_restore_environment_rejects_blank_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not infer destructive restore targets from defaults."""

    monkeypatch.setenv("RESTORE_TARGET", "  ")
    with pytest.raises(SystemExit, match="RESTORE_TARGET is required"):
        _required_environment("RESTORE_TARGET")

    monkeypatch.setenv("RESTORE_TARGET", "/isolated/target")
    assert _required_environment("RESTORE_TARGET") == "/isolated/target"


def _backup_environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Configure every backup setting against private test paths."""

    values = {
        "MONEY_TRACKER_DATABASE_URL": "postgresql://user:secret@127.0.0.1:1/test",
        "MONEY_TRACKER_BACKUP_EXPECTED_DATABASE": "test",
        "MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT": str(root / "receipts"),
        "MONEY_TRACKER_BACKUP_CONFIG_FILE": str(root / "config.env"),
        "MONEY_TRACKER_RESTIC_REPOSITORY": str(root / "repository"),
        "MONEY_TRACKER_RESTIC_PASSWORD_FILE": str(root / "restic-password"),
        "MONEY_TRACKER_BACKUP_LOCK_FILE": str(root / "backup.lock"),
        "MONEY_TRACKER_BACKUP_STAGING_PARENT": str(root / "staging"),
        "MONEY_TRACKER_RESTIC_BINARY": str(root / "missing" / "restic"),
        "MONEY_TRACKER_PG_DUMP_BINARY": str(root / "missing" / "pg_dump"),
        "MONEY_TRACKER_PG_RESTORE_BINARY": str(root / "missing" / "pg_restore"),
        "LOG_FORMAT": "json",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _events(stderr: str) -> list[dict[str, object]]:
    """Parse the JSON journal records a command wrote to standard error."""

    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def test_a_missing_executable_is_a_refusal_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop the scheduled unit with exit 1 and one record the owner can act on."""

    _backup_environment(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as exited:
        main(["status"])

    captured = capsys.readouterr()
    assert exited.value.code == 1
    assert captured.out == ""
    assert "Backup command refused: required executable is unavailable" in (
        captured.err
    )
    events = _events(captured.err)
    refused = [event for event in events if event["event"] == "backup.command.refused"]
    assert len(refused) == 1 and refused[0]["level"] == "ERROR"
    assert not [event for event in events if event["level"] == "CRITICAL"]
    assert "secret" not in captured.err


def test_a_missing_private_input_does_not_disclose_its_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Describe a missing credential without writing its location to the journal."""

    _backup_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("MONEY_TRACKER_RESTIC_BINARY", "/bin/true")
    monkeypatch.setenv("MONEY_TRACKER_PG_DUMP_BINARY", "/bin/true")
    monkeypatch.setenv("MONEY_TRACKER_PG_RESTORE_BINARY", "/bin/true")

    with pytest.raises(SystemExit) as exited:
        main(["status"])

    captured = capsys.readouterr()
    assert exited.value.code == 1
    assert "Backup command refused: Restic password is not readable" in captured.err
    assert str(tmp_path) not in captured.err
    assert "Traceback" not in captured.err


def test_an_unconfigured_restore_verification_is_refused_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Name the missing restore setting's reason without a traceback escaping."""

    monkeypatch.setenv("LOG_FORMAT", "json")
    for name in (
        "MONEY_TRACKER_RESTORE_DATABASE_URL",
        "MONEY_TRACKER_RESTORE_EXPECTED_DATABASE",
        "MONEY_TRACKER_RESTORE_RECEIPT_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exited:
        main(["verify-restore"])

    captured = capsys.readouterr()
    assert exited.value.code == 1
    (refused,) = [
        event
        for event in _events(captured.err)
        if event["event"] == "backup.command.refused"
    ]
    assert refused["reason"] == "restore_setting_missing"


def test_an_unreachable_restore_database_is_a_transient_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let the supervisor retry when an isolated PostgreSQL target is down."""

    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(mode=0o700)
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv(
        "MONEY_TRACKER_RESTORE_DATABASE_URL",
        "postgresql://user:secret@127.0.0.1:1/restore_test",
    )
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_EXPECTED_DATABASE", "restore_test")
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_RECEIPT_ROOT", str(receipt_root))

    with pytest.raises(SystemExit) as exited:
        main(["verify-restore"])

    captured = capsys.readouterr()
    assert exited.value.code == 2
    assert captured.out == ""
    assert "Backup command failed: database_unavailable" in captured.err
    failed = [
        event
        for event in _events(captured.err)
        if event["event"] == "backup.command.failed"
    ]
    assert len(failed) == 1 and failed[0]["reason"] == "database_unavailable"
    assert not [
        event for event in _events(captured.err) if event["level"] == "CRITICAL"
    ]
    assert "secret" not in captured.err
