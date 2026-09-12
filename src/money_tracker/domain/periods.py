"""Rome calendar periods for deterministic reads wider than one month.

A reporting period is a calendar notion, not a timestamp arithmetic exercise.
"Yesterday", "this ISO week", and "2026" only mean something once a timezone
fixes where a day begins, and `AD-007` fixes that timezone at `Europe/Rome`
for the whole product. Resolving them here, in pure domain code, keeps that
rule in one tested place instead of spreading it across an HTTP adapter, a
SQL predicate, and a browser.

The resolved value is a closed interval of Rome-local dates. It is converted
to instants only at the persistence boundary, through `DateRange.bounds`,
which produces the half-open aware interval a range query needs. That
conversion is where daylight saving actually matters: the Rome day on which
the clocks go back is twenty-five hours long, and a period that counted hours
instead of calendar days would silently lose or duplicate an hour of
movements.

Nothing in this module reads or writes storage, and none of its values carry
an amount, so a period is safe to name in a log record.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from money_tracker.domain.budgets import YearMonth
from money_tracker.domain.errors import DomainError
from money_tracker.domain.profile import V1_TIMEZONE

MAX_RANGE_DAYS = 366
"""Longest reportable interval, which is one leap calendar year.

A year is the widest calendar granularity this release reports, so a custom
interval is held to the same bound. The limit exists because a custom
interval is untrusted input: without it, one request could ask the database
for the owner's entire history.
"""


class PeriodGranularity(StrEnum):
    """How an owner chose a reporting period.

    The value is carried through to the response so a caller can render the
    period the way it was selected rather than inferring a granularity from
    the width of an interval, which cannot distinguish a calendar month from
    a custom interval that happens to cover one.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True, order=True)
class DateRange:
    """A closed, ordered, bounded interval of Rome calendar dates.

    Both ends are inclusive, because that is how an owner names an interval.
    The half-open instant interval a query needs is derived in `bounds`.

    Attributes:
        start: First Rome-local date included.
        end: Last Rome-local date included.
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        """Validate ordering, representability, and the supported span.

        Raises:
            DomainError: If either end is an instant rather than a calendar
                date, if the interval is reversed, if its exclusive upper
                bound is not representable, or if it is longer than
                `MAX_RANGE_DAYS`.
        """

        if isinstance(self.start, datetime) or isinstance(self.end, datetime):
            raise DomainError("calendar range bounds must be dates, not instants")
        if not isinstance(self.start, date) or not isinstance(self.end, date):
            raise DomainError("calendar range bounds must be dates")
        if self.end < self.start:
            raise DomainError("calendar range must end on or after it starts")
        if self.end == date.max:
            # bounds() needs the day after the last one, so the final
            # representable date cannot close an interval.
            raise DomainError("calendar range cannot end on the last supported date")
        if self.day_count > MAX_RANGE_DAYS:
            raise DomainError(
                f"calendar range cannot exceed {MAX_RANGE_DAYS} days",
            )

    @property
    def day_count(self) -> int:
        """Return how many Rome calendar days the interval includes."""

        return (self.end - self.start).days + 1

    def bounds(self) -> tuple[datetime, datetime]:
        """Return the half-open aware Rome interval covering these dates.

        Midnight is used as the boundary instant because Rome moves its
        clocks at 02:00 and 03:00, so a local midnight is never skipped and
        never repeated. The width of the result therefore follows the
        calendar: the day the clocks go back spans twenty-five hours.

        Returns:
            Inclusive first instant and exclusive instant after the last day.
        """

        timezone = ZoneInfo(V1_TIMEZONE)
        following = self.end + timedelta(days=1)
        return (
            datetime(
                self.start.year, self.start.month, self.start.day, tzinfo=timezone
            ),
            datetime(following.year, following.month, following.day, tzinfo=timezone),
        )

    def as_calendar_month(self) -> YearMonth | None:
        """Return the single calendar month this interval covers exactly.

        A monthly budget has no rollover, so a target only has a position
        against a period that is one whole calendar month. This is the test
        for that, and it is deliberately about the dates rather than about
        how they were selected: a custom interval spanning exactly one month
        is one month, and a seven-day week never is.

        Returns:
            The covered month, or None when the interval is not exactly one.
        """

        if self.start.day != 1:
            return None
        if (self.start.year, self.start.month) != (self.end.year, self.end.month):
            return None
        if (self.end + timedelta(days=1)).month == self.end.month:
            return None
        return YearMonth(self.start.year, self.start.month)


@dataclass(frozen=True, slots=True)
class ResolvedPeriod:
    """One owner-selected period resolved against the Rome calendar.

    Attributes:
        granularity: How the period was selected.
        interval: Closed Rome-local interval the selection resolves to.
        iso_year: ISO 8601 week-numbering year, set only for a week. It is
            not always the calendar year of the interval's dates.
        iso_week: ISO 8601 week number from 1 through 53, set only for a week.
    """

    granularity: PeriodGranularity
    interval: DateRange
    iso_year: int | None = None
    iso_week: int | None = None

    def __post_init__(self) -> None:
        """Require ISO week numbering exactly on a week period.

        Raises:
            DomainError: If week numbering is present on another granularity
                or absent from a week.
        """

        numbered = self.iso_year is not None and self.iso_week is not None
        if numbered != (self.granularity is PeriodGranularity.WEEK):
            raise DomainError("ISO week numbering belongs to a week period only")

    @property
    def calendar_month(self) -> YearMonth | None:
        """Return the calendar month this period covers exactly, if any."""

        return self.interval.as_calendar_month()


def resolve_day(day: date) -> ResolvedPeriod:
    """Resolve one Rome calendar day.

    Args:
        day: Rome-local date the owner selected.

    Returns:
        A single-day period.

    Raises:
        DomainError: If the date cannot close an interval.
    """

    return ResolvedPeriod(
        granularity=PeriodGranularity.DAY,
        interval=DateRange(start=day, end=day),
    )


def resolve_iso_week(day: date) -> ResolvedPeriod:
    """Resolve the ISO 8601 week containing one Rome calendar day.

    The week runs Monday through Sunday and belongs to the ISO
    week-numbering year, which is not always the calendar year of its days:
    the week containing 2025-12-31 is 2026-W01 and starts on 2025-12-29.
    Selecting a week by a day inside it keeps that rule here rather than
    asking a caller to name a week-numbering year it would have to compute.

    Args:
        day: Any Rome-local date inside the wanted week.

    Returns:
        The Monday-to-Sunday period carrying its ISO numbering.

    Raises:
        DomainError: If the resulting interval is not representable.
    """

    iso = day.isocalendar()
    try:
        start = date.fromisocalendar(iso.year, iso.week, 1)
        end = date.fromisocalendar(iso.year, iso.week, 7)
    except ValueError as error:
        raise DomainError("ISO week is outside the supported calendar") from error
    return ResolvedPeriod(
        granularity=PeriodGranularity.WEEK,
        interval=DateRange(start=start, end=end),
        iso_year=iso.year,
        iso_week=iso.week,
    )


def month_range(period: YearMonth) -> DateRange:
    """Return the closed date interval of one Rome calendar month.

    The last day is taken from the calendar rather than from the start of the
    following month, so a month at the very end of the supported calendar is
    refused as an unrepresentable interval instead of overflowing.

    Args:
        period: Calendar month to convert.

    Returns:
        First through last day of that month.

    Raises:
        DomainError: If the month cannot close an interval.
    """

    last_day = monthrange(period.year, period.month)[1]
    return DateRange(
        start=date(period.year, period.month, 1),
        end=date(period.year, period.month, last_day),
    )


def resolve_month(period: YearMonth) -> ResolvedPeriod:
    """Resolve one Rome calendar month.

    Args:
        period: Calendar month the owner selected.

    Returns:
        A whole-month period, which is the only granularity against which a
        monthly budget has a defined position.

    Raises:
        DomainError: If the month cannot close an interval.
    """

    return ResolvedPeriod(
        granularity=PeriodGranularity.MONTH,
        interval=month_range(period),
    )


def resolve_year(year: int) -> ResolvedPeriod:
    """Resolve one Rome calendar year.

    Args:
        year: Four-digit calendar year the owner selected.

    Returns:
        A 1 January through 31 December period.

    Raises:
        DomainError: If the year is outside the supported calendar.
    """

    if not 1 <= year <= 9999:
        raise DomainError("reporting year must be between 1 and 9999")
    return ResolvedPeriod(
        granularity=PeriodGranularity.YEAR,
        interval=DateRange(start=date(year, 1, 1), end=date(year, 12, 31)),
    )


def resolve_custom(*, start: date, end: date) -> ResolvedPeriod:
    """Resolve an interval the owner named by both of its ends.

    This is the only granularity whose bounds are arbitrary, so this is where
    an untrusted selection becomes a refused domain value instead of an
    unbounded query. `DateRange` itself enforces ordering and the supported
    span, so the same rules apply however an interval is built.

    Args:
        start: First Rome-local date to include.
        end: Last Rome-local date to include.

    Returns:
        The validated closed interval.

    Raises:
        DomainError: If the interval is reversed or longer than
            `MAX_RANGE_DAYS`.
    """

    return ResolvedPeriod(
        granularity=PeriodGranularity.CUSTOM,
        interval=DateRange(start=start, end=end),
    )


__all__ = [
    "MAX_RANGE_DAYS",
    "DateRange",
    "PeriodGranularity",
    "ResolvedPeriod",
    "month_range",
    "resolve_custom",
    "resolve_day",
    "resolve_iso_week",
    "resolve_month",
    "resolve_year",
]
