"""Deterministic bounded retry scheduling for messaging workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from money_tracker.messaging.contracts import require_aware


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Calculate bounded exponential delays and exhaustion.

    Attributes:
        maximum_attempts: Total automatic attempts before dead letter.
        base_delay_seconds: Delay following the first failed attempt.
        maximum_delay_seconds: Upper bound applied to later attempts.
    """

    maximum_attempts: int = 5
    base_delay_seconds: int = 2
    maximum_delay_seconds: int = 60

    def __post_init__(self) -> None:
        """Reject a policy that cannot make deterministic progress."""

        if self.maximum_attempts <= 0:
            raise ValueError("maximum_attempts must be positive")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive")
        if self.maximum_delay_seconds < self.base_delay_seconds:
            raise ValueError("maximum delay cannot be below base delay")

    def exhausted(self, attempt_count: int) -> bool:
        """Return whether another automatic attempt is forbidden.

        Args:
            attempt_count: Count including the attempt that just failed.

        Returns:
            True once the configured attempt budget is consumed.
        """

        if attempt_count <= 0:
            raise ValueError("attempt_count must be positive")
        return attempt_count >= self.maximum_attempts

    def retry_at(self, now: datetime, *, attempt_count: int) -> datetime:
        """Return the deterministic eligibility instant after a failure.

        Args:
            now: Aware failure timestamp.
            attempt_count: Count including the attempt that just failed.

        Returns:
            Aware timestamp after a capped exponential delay.

        Raises:
            ValueError: If the attempt is exhausted or invalid.
        """

        require_aware(now, field="now")
        if self.exhausted(attempt_count):
            raise ValueError("exhausted work cannot be scheduled automatically")
        delay = min(
            self.base_delay_seconds * (2 ** (attempt_count - 1)),
            self.maximum_delay_seconds,
        )
        return now + timedelta(seconds=delay)
