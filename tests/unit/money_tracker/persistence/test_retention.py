"""Unit tests for the guard rails of runtime retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from money_tracker.persistence.retention import (
    MINIMUM_RETENTION,
    RETENTION_DAYS,
    DeletedRuntimeRows,
    PostgresRuntimeRetention,
    RetainedRuntimeRows,
    RuntimeRetentionError,
    RuntimeRetentionResult,
    _count,
    _facts,
)

_UNREACHABLE = "postgresql://retention@127.0.0.1:1/money_tracker_unreachable"


def test_the_owner_window_is_thirty_days_and_never_shorter_than_one() -> None:
    """Pin the 2026-09-19 decision and Telegram's 24-hour backlog floor."""

    assert RETENTION_DAYS == 30
    assert MINIMUM_RETENTION == timedelta(days=1)


def test_a_window_shorter_than_a_day_is_refused_before_connecting() -> None:
    """Reject the window itself rather than reporting an unreachable database."""

    retention = PostgresRuntimeRetention(_UNREACHABLE)

    with pytest.raises(RuntimeRetentionError) as refused:
        retention.run(retention=timedelta(hours=23), confirm=True)

    assert refused.value.reason_code == "retention_too_short"
    assert not refused.value.transient


def test_a_naive_clock_is_refused() -> None:
    """Count the window only from an unambiguous instant."""

    retention = PostgresRuntimeRetention(
        _UNREACHABLE, clock=lambda: datetime(2026, 9, 19, 12)
    )

    with pytest.raises(RuntimeRetentionError) as refused:
        retention.run(retention=timedelta(days=30), confirm=False)

    assert refused.value.reason_code == "clock_not_aware"


def test_an_unreachable_database_is_a_transient_refusal() -> None:
    """Let the scheduled unit fail visibly and the next occurrence retry."""

    retention = PostgresRuntimeRetention(
        _UNREACHABLE, clock=lambda: datetime(2026, 9, 19, 12, tzinfo=UTC)
    )

    with pytest.raises(RuntimeRetentionError) as refused:
        retention.run(retention=timedelta(days=30), confirm=True)

    assert refused.value.reason_code == "database_unavailable"
    assert refused.value.transient


def test_only_postgresql_urls_are_accepted() -> None:
    """Refuse a URL the statements were never written for."""

    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresRuntimeRetention("sqlite:///retention.db")


def test_detached_links_are_not_counted_as_deleted_rows() -> None:
    """Report a cleared link separately from content that was removed."""

    rows = DeletedRuntimeRows(
        pending_actions=1,
        outbox_messages=2,
        inbox_items=3,
        detached_outbox_messages=4,
        checkpoint_threads=5,
        checkpoints=6,
        checkpoint_blobs=7,
        checkpoint_writes=8,
    )

    assert rows.total == 1 + 2 + 3 + 6 + 7 + 8


@pytest.mark.parametrize("value", [-1, "3", None, 2.0])
def test_an_aggregate_that_is_not_a_non_negative_integer_is_refused(
    value: object,
) -> None:
    """Never report a count PostgreSQL did not produce as one."""

    with pytest.raises(RuntimeRetentionError) as refused:
        _count(value)

    assert refused.value.reason_code == "database_count_invalid"
    assert _count(3) == 3


def test_journal_fields_are_counts_and_flags_only() -> None:
    """Keep every field the completion record carries free of row values."""

    facts = _facts(
        RuntimeRetentionResult(
            cutoff=datetime(2026, 8, 20, tzinfo=UTC),
            dry_run=True,
            deleted=DeletedRuntimeRows(1, 2, 3, 4, 5, 6, 7, 8),
            retained=RetainedRuntimeRows(9, 10, 11, 12),
            checkpoint_store_present=True,
            vacuumed=False,
        )
    )

    assert facts == {
        "pending_actions": 1,
        "outbox_messages": 2,
        "inbox_items": 3,
        "detached_outbox_messages": 4,
        "checkpoint_threads": 5,
        "deleted_rows": 1 + 2 + 3 + 6 + 7 + 8,
        "retained_inbox_items": 9,
        "retained_outbox_messages": 10,
        "retained_pending_actions": 11,
        "retained_checkpoint_threads": 12,
        "checkpoint_store_present": True,
        "vacuumed": False,
    }
