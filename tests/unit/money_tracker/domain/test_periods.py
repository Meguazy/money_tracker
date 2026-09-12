"""Tests for resolving reporting periods against the Rome calendar."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from money_tracker.domain import (
    MAX_RANGE_DAYS,
    DateRange,
    DomainError,
    PeriodGranularity,
    ResolvedPeriod,
    YearMonth,
    month_range,
    resolve_custom,
    resolve_day,
    resolve_iso_week,
    resolve_month,
    resolve_year,
)


def _elapsed(interval: DateRange) -> timedelta:
    """Return how much real time an interval covers, not how many wall hours."""

    start, end = interval.bounds()
    return end.astimezone(UTC) - start.astimezone(UTC)


def test_a_day_is_the_rome_calendar_day_and_not_twenty_four_hours() -> None:
    """Keep a movement in its own day when the clocks move that night."""

    autumn = resolve_day(date(2026, 10, 25))
    spring = resolve_day(date(2026, 3, 29))

    assert _elapsed(autumn.interval) == timedelta(hours=25)
    assert _elapsed(spring.interval) == timedelta(hours=23)
    assert autumn.interval.day_count == 1
    assert autumn.granularity is PeriodGranularity.DAY


def test_day_bounds_are_half_open_around_rome_midnight() -> None:
    """Include the first instant of the day and exclude the next day's."""

    start, end = resolve_day(date(2026, 9, 12)).interval.bounds()

    assert start.astimezone(UTC) == datetime(2026, 9, 11, 22, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026, 9, 12, 22, tzinfo=UTC)


def test_an_iso_week_runs_monday_to_sunday_in_its_numbering_year() -> None:
    """Report the week the owner means rather than the calendar year's week."""

    week = resolve_iso_week(date(2025, 12, 31))

    assert week.interval == DateRange(start=date(2025, 12, 29), end=date(2026, 1, 4))
    assert (week.iso_year, week.iso_week) == (2026, 1)
    assert week.interval.day_count == 7


def test_every_day_of_one_iso_week_resolves_to_the_same_week() -> None:
    """Select a week by any day inside it, including across the year change."""

    resolved = {
        resolve_iso_week(date(2025, 12, 29) + timedelta(days=offset)).interval
        for offset in range(7)
    }

    assert resolved == {DateRange(start=date(2025, 12, 29), end=date(2026, 1, 4))}
    assert resolve_iso_week(date(2025, 12, 28)).interval.end == date(2025, 12, 28)
    assert resolve_iso_week(date(2026, 1, 5)).interval.start == date(2026, 1, 5)


def test_an_iso_week_covering_a_clock_change_keeps_seven_calendar_days() -> None:
    """Measure a week in days so an hour is neither lost nor duplicated."""

    week = resolve_iso_week(date(2026, 10, 25))

    assert week.interval.day_count == 7
    assert _elapsed(week.interval) == timedelta(days=7, hours=1)


def test_a_month_covers_its_own_length_including_february() -> None:
    """Resolve a month from the calendar rather than from a fixed width."""

    assert resolve_month(YearMonth(2026, 2)).interval == DateRange(
        start=date(2026, 2, 1), end=date(2026, 2, 28)
    )
    assert resolve_month(YearMonth(2024, 2)).interval == DateRange(
        start=date(2024, 2, 1), end=date(2024, 2, 29)
    )
    assert month_range(YearMonth(2026, 9)).day_count == 30


def test_a_month_interval_matches_the_instants_the_budget_calendar_uses() -> None:
    """Keep the range read and the existing monthly read over the same rows."""

    period = YearMonth(2026, 10)

    assert month_range(period).bounds() == period.bounds()


def test_a_year_runs_from_january_through_december() -> None:
    """Report a year as a calendar year, leap day included."""

    assert resolve_year(2024).interval == DateRange(
        start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    assert resolve_year(2024).interval.day_count == 366
    assert resolve_year(2026).interval.day_count == 365


def test_a_year_outside_the_supported_calendar_is_refused() -> None:
    """Refuse an unrepresentable year instead of raising a bare ValueError."""

    with pytest.raises(DomainError):
        resolve_year(0)
    with pytest.raises(DomainError):
        resolve_year(10000)
    with pytest.raises(DomainError):
        resolve_year(9999)


def test_a_custom_interval_must_be_ordered() -> None:
    """Refuse a reversed interval as a domain value, not as a query."""

    with pytest.raises(DomainError):
        resolve_custom(start=date(2026, 9, 30), end=date(2026, 9, 1))


def test_a_custom_interval_is_bounded_by_one_leap_year() -> None:
    """Keep one untrusted request from asking for the owner's whole history."""

    start = date(2026, 1, 1)
    longest = resolve_custom(
        start=start, end=start + timedelta(days=MAX_RANGE_DAYS - 1)
    )

    assert longest.interval.day_count == MAX_RANGE_DAYS
    with pytest.raises(DomainError):
        resolve_custom(start=start, end=start + timedelta(days=MAX_RANGE_DAYS))


def test_a_single_day_custom_interval_is_accepted() -> None:
    """Treat a closed interval as inclusive of both of its ends."""

    interval = resolve_custom(start=date(2026, 9, 12), end=date(2026, 9, 12)).interval

    assert interval.day_count == 1
    assert interval.bounds() == resolve_day(date(2026, 9, 12)).interval.bounds()


def test_an_interval_cannot_end_on_the_last_representable_date() -> None:
    """Refuse a period whose exclusive upper bound does not exist."""

    with pytest.raises(DomainError):
        DateRange(start=date.max - timedelta(days=1), end=date.max)


def test_an_interval_refuses_an_instant_in_place_of_a_calendar_date() -> None:
    """Keep a period a calendar notion rather than a timestamp comparison."""

    with pytest.raises(DomainError):
        DateRange(
            start=datetime(2026, 9, 12, tzinfo=UTC),
            end=datetime(2026, 9, 12, tzinfo=UTC),
        )


def test_only_a_whole_calendar_month_reports_as_one_month() -> None:
    """Define the one period against which a monthly budget has a position."""

    assert resolve_month(YearMonth(2026, 9)).calendar_month == YearMonth(2026, 9)
    assert resolve_custom(
        start=date(2026, 9, 1), end=date(2026, 9, 30)
    ).calendar_month == YearMonth(2026, 9)
    assert resolve_day(date(2026, 9, 12)).calendar_month is None
    assert resolve_iso_week(date(2026, 9, 12)).calendar_month is None
    assert resolve_year(2026).calendar_month is None
    assert (
        resolve_custom(start=date(2026, 9, 1), end=date(2026, 9, 29)).calendar_month
        is None
    )
    assert (
        resolve_custom(start=date(2026, 9, 2), end=date(2026, 9, 30)).calendar_month
        is None
    )
    assert (
        resolve_custom(start=date(2024, 2, 1), end=date(2024, 2, 28)).calendar_month
        is None
    )


def test_iso_week_numbering_belongs_only_to_a_week() -> None:
    """Refuse a period that claims a week number it does not have."""

    with pytest.raises(DomainError):
        ResolvedPeriod(
            granularity=PeriodGranularity.DAY,
            interval=DateRange(start=date(2026, 9, 12), end=date(2026, 9, 12)),
            iso_year=2026,
            iso_week=38,
        )
    with pytest.raises(DomainError):
        ResolvedPeriod(
            granularity=PeriodGranularity.WEEK,
            interval=DateRange(start=date(2026, 9, 7), end=date(2026, 9, 13)),
        )
