"""Tests for Rome-calendar monthly budget behavior."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from money_tracker.domain import (
    DomainError,
    Money,
    MonthlyBudget,
    YearMonth,
    budget_status,
)


def test_year_month_uses_rome_occurrence_time() -> None:
    """Resolve UTC instants at a Rome month boundary using local occurrence time."""

    instant = datetime(2026, 9, 30, 22, 30, tzinfo=UTC)
    period = YearMonth.from_datetime(instant)
    start, end = period.bounds()

    assert period == YearMonth(2026, 10)
    assert (start.year, start.month, start.day, start.utcoffset().total_seconds()) == (
        2026,
        10,
        1,
        7200,
    )
    assert end == datetime(2026, 11, 1, tzinfo=end.tzinfo)


def test_budget_override_replaces_target_without_rollover() -> None:
    """Use only the selected month's override and compute signed remaining cents."""

    october = YearMonth(2026, 10)
    budget = MonthlyBudget(
        id=uuid4(),
        profile_id=uuid4(),
        category_id=uuid4(),
        recurring_target=Money(20_000),
        overrides=((october, Money(25_000)),),
    )

    assert budget.target_for(YearMonth(2026, 9)) == Money(20_000)
    assert budget_status(
        budget, period=october, posted_spend=Money(26_500)
    ).remaining == Money(-1500)


def test_budget_targets_must_be_positive_and_months_unique() -> None:
    """Reject non-positive targets and conflicting month overrides."""

    with pytest.raises(DomainError, match="positive"):
        MonthlyBudget(uuid4(), uuid4(), uuid4(), Money(0))
    month = YearMonth(2026, 9)
    with pytest.raises(DomainError, match="unique"):
        MonthlyBudget(
            uuid4(),
            uuid4(),
            uuid4(),
            Money(100),
            ((month, Money(100)), (month, Money(200))),
        )


@pytest.mark.parametrize("year,month", [(0, 1), (10_000, 1), (2026, 0), (2026, 13)])
def test_invalid_calendar_period_is_rejected(year: int, month: int) -> None:
    """Reject unrepresentable budget calendar values."""

    with pytest.raises(DomainError):
        YearMonth(year, month)


def test_calendar_conversion_rejects_a_naive_datetime() -> None:
    """Require an instant before selecting its Rome-local month."""

    with pytest.raises(DomainError, match="timezone"):
        YearMonth.from_datetime(datetime(2026, 9, 2, 10))
