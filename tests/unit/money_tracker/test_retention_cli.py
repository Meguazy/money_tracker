"""Unit tests for the runtime retention operator command."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from money_tracker.persistence.retention import (
    DeletedRuntimeRows,
    RetainedRuntimeRows,
    RuntimeRetentionResult,
)
from money_tracker.retention_cli import _parser, main, render


def test_the_default_window_is_the_owner_decision() -> None:
    """Run the accepted policy when no window is given."""

    assert _parser().parse_args(["plan"]).older_than_days == 30
    delete = _parser().parse_args(["delete"])
    assert delete.older_than_days == 30
    assert delete.confirm is False


@pytest.mark.parametrize("value", ["0", "-3", "1.5", "trenta"])
def test_a_window_below_one_whole_day_is_rejected(value: str) -> None:
    """Keep every run outside Telegram's 24-hour backlog."""

    with pytest.raises(SystemExit) as exited:
        _parser().parse_args(["delete", "--older-than-days", value])

    assert exited.value.code == 2


def test_only_delete_accepts_a_confirmation() -> None:
    """Keep the planning command unable to delete anything."""

    with pytest.raises(SystemExit):
        _parser().parse_args(["plan", "--confirm"])


def test_rendering_carries_counts_and_flags_only() -> None:
    """Expose what was removed and kept without any value from a row."""

    result = RuntimeRetentionResult(
        cutoff=datetime(2026, 8, 20, 4, 45, tzinfo=UTC),
        dry_run=False,
        deleted=DeletedRuntimeRows(
            pending_actions=1,
            outbox_messages=2,
            inbox_items=3,
            detached_outbox_messages=1,
            checkpoint_threads=4,
            checkpoints=5,
            checkpoint_blobs=6,
            checkpoint_writes=7,
        ),
        retained=RetainedRuntimeRows(
            inbox_items=1, outbox_messages=2, pending_actions=3, checkpoint_threads=4
        ),
        checkpoint_store_present=True,
        vacuumed=True,
    )

    rendered = render("delete", 30, result)

    assert json.loads(json.dumps(rendered)) == {
        "command": "delete",
        "dry_run": False,
        "retention_days": 30,
        "cutoff": "2026-08-20T04:45:00+00:00",
        "deleted": {
            "pending_actions": 1,
            "outbox_messages": 2,
            "inbox_items": 3,
            "checkpoint_threads": 4,
            "checkpoints": 5,
            "checkpoint_blobs": 6,
            "checkpoint_writes": 7,
        },
        "detached_outbox_messages": 1,
        "retained_past_cutoff": {
            "inbox_items": 1,
            "outbox_messages": 2,
            "pending_actions": 3,
            "checkpoint_threads": 4,
        },
        "checkpoint_store_present": True,
        "vacuumed": True,
    }


def test_a_missing_database_url_is_an_operator_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stop the unit instead of restarting when configuration is absent."""

    monkeypatch.delenv("MONEY_TRACKER_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as exited:
        main(["delete", "--confirm"])

    assert exited.value.code == 1
    assert "database_configuration_invalid" in capsys.readouterr().err


def test_an_unreachable_database_exits_for_a_later_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report a transient refusal without the credential-bearing URL."""

    url = "postgresql://retention:secret-password@127.0.0.1:1/unreachable"
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", url)

    with pytest.raises(SystemExit) as exited:
        main(["plan"])

    captured = capsys.readouterr()
    assert exited.value.code == 2
    assert captured.out == ""
    assert "database_unavailable" in captured.err
    assert "secret-password" not in captured.err
