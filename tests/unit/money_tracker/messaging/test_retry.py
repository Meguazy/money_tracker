"""Unit tests for deterministic messaging retry scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from money_tracker.messaging import RetryPolicy


def test_retry_policy_uses_bounded_exponential_delays() -> None:
    """Schedule stable delays and stop after the fifth total attempt."""

    policy = RetryPolicy()
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)

    assert policy.retry_at(now, attempt_count=1) == now + timedelta(seconds=2)
    assert policy.retry_at(now, attempt_count=2) == now + timedelta(seconds=4)
    assert policy.retry_at(now, attempt_count=4) == now + timedelta(seconds=16)
    assert not policy.exhausted(4)
    assert policy.exhausted(5)
    with pytest.raises(ValueError, match="exhausted"):
        policy.retry_at(now, attempt_count=5)


@pytest.mark.parametrize(
    "values",
    [
        {"maximum_attempts": 0},
        {"base_delay_seconds": 0},
        {"base_delay_seconds": 4, "maximum_delay_seconds": 2},
    ],
)
def test_retry_policy_rejects_non_progressing_configuration(
    values: dict[str, int],
) -> None:
    """Refuse retry policies that cannot make bounded progress."""

    with pytest.raises(ValueError):
        RetryPolicy(**values)
