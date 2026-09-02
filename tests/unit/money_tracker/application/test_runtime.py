"""Tests for production clock and identifier adapters."""

from datetime import UTC, datetime
from uuid import UUID

from money_tracker.application.runtime import SystemClock, UUID4Generator


def test_system_clock_returns_an_aware_utc_instant() -> None:
    """Provide audit timestamps that can cross persistence boundaries safely."""

    before = datetime.now(UTC)
    actual = SystemClock().now()
    after = datetime.now(UTC)

    assert before <= actual <= after
    assert actual.tzinfo is UTC


def test_uuid_generator_returns_distinct_uuid4_values() -> None:
    """Generate stable identifiers without sharing mutable generator state."""

    generator = UUID4Generator()
    first = generator.new()
    second = generator.new()

    assert isinstance(first, UUID)
    assert first.version == 4
    assert second.version == 4
    assert first != second
