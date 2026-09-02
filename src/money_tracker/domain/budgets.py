"""Monthly budget calendar and exact status contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from money_tracker.domain.errors import DomainError
from money_tracker.domain.money import Money
from money_tracker.domain.profile import V1_TIMEZONE


@dataclass(frozen=True, slots=True, order=True)
class YearMonth:
    """One Rome calendar month used for budgets and overrides.

    Attributes:
        year: Four-digit calendar year.
        month: Calendar month from 1 through 12.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        """Validate representable calendar fields."""

        if not 1 <= self.year <= 9999:
            raise DomainError("budget year must be between 1 and 9999")
        if not 1 <= self.month <= 12:
            raise DomainError("budget month must be between 1 and 12")

    @classmethod
    def from_datetime(cls, value: datetime) -> YearMonth:
        """Resolve an instant to its Europe/Rome reporting month.

        Args:
            value: Timezone-aware occurrence instant.

        Returns:
            Rome-local year and month.

        Raises:
            DomainError: If the input has no timezone.
        """

        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainError("budget calendar conversion requires a timezone")
        local = value.astimezone(ZoneInfo(V1_TIMEZONE))
        return cls(local.year, local.month)

    def bounds(self) -> tuple[datetime, datetime]:
        """Return the half-open Rome-local interval for this month.

        Returns:
            Inclusive month start and exclusive next-month start, both aware.
        """

        timezone = ZoneInfo(V1_TIMEZONE)
        start = datetime(self.year, self.month, 1, tzinfo=timezone)
        if self.month == 12:
            end = datetime(self.year + 1, 1, 1, tzinfo=timezone)
        else:
            end = datetime(self.year, self.month + 1, 1, tzinfo=timezone)
        return start, end


@dataclass(frozen=True, slots=True)
class MonthlyBudget:
    """Recurring category target with optional per-month overrides.

    Attributes:
        id: Stable budget identifier.
        profile_id: Owner scope.
        category_id: Category whose posted allocations consume the target.
        recurring_target: Default exact monthly target.
        overrides: Explicit replacement targets keyed by calendar month.
        version: Optimistic concurrency version.
    """

    id: UUID
    profile_id: UUID
    category_id: UUID
    recurring_target: Money
    overrides: tuple[tuple[YearMonth, Money], ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        """Require positive targets and unique override months."""

        if self.recurring_target.minor_units <= 0:
            raise DomainError("monthly budget target must be positive")
        months = tuple(month for month, _ in self.overrides)
        if len(months) != len(set(months)):
            raise DomainError("monthly budget overrides must have unique months")
        if any(target.minor_units <= 0 for _, target in self.overrides):
            raise DomainError("monthly budget override must be positive")
        if self.version < 1:
            raise DomainError("monthly budget version must be positive")

    def target_for(self, period: YearMonth) -> Money:
        """Return the override for a month or the recurring target.

        Args:
            period: Rome calendar month.

        Returns:
            Effective target without rollover from any other month.
        """

        return dict(self.overrides).get(period, self.recurring_target)


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """Deterministic comparison of one target and posted category spend.

    Attributes:
        period: Calendar month being reported.
        target: Effective monthly target.
        posted_spend: Posted category allocation total.
        remaining: Target minus spend; negative means overspent.
    """

    period: YearMonth
    target: Money
    posted_spend: Money
    remaining: Money


def budget_status(
    budget: MonthlyBudget,
    *,
    period: YearMonth,
    posted_spend: Money,
) -> BudgetStatus:
    """Compute exact no-rollover status for one monthly budget.

    Args:
        budget: Recurring budget and its explicit overrides.
        period: Month being evaluated.
        posted_spend: Posted category allocation total for that month.

    Returns:
        Target, spend, and signed remaining amount.
    """

    target = budget.target_for(period)
    return BudgetStatus(
        period=period,
        target=target,
        posted_spend=posted_spend,
        remaining=target - posted_spend,
    )
