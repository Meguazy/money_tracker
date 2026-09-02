"""Unit tests for the authoritative backup administration CLI."""

from __future__ import annotations

import pytest

from money_tracker.backup_cli import _parser, _required_environment


def test_parser_exposes_only_explicit_backup_operations() -> None:
    """Require a bounded command and an explicit snapshot for restore drills."""

    assert _parser().parse_args(["init"]).command == "init"
    assert _parser().parse_args(["create"]).command == "create"
    assert _parser().parse_args(["status"]).command == "status"
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
