"""Strict JSON contracts for the local control-plane API."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from money_tracker.api.contracts import MoneyContract
from money_tracker.application import MonthlyOverview
from money_tracker.domain import BudgetStatus, FundingClass, Money, YearMonth


class _WebContract(BaseModel):
    """Reject unknown or coercion-dependent fields at the web boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PeriodContract(_WebContract):
    """Calendar month represented without an ambiguous timestamp."""

    year: int
    month: int

    @classmethod
    def from_domain(cls, period: YearMonth) -> Self:
        """Build a wire period from a validated domain value."""

        return cls(year=period.year, month=period.month)


class FundingClassSplitContract(_WebContract):
    """Exact posted totals partitioned by economic source class."""

    personal_funds: MoneyContract
    benefit: MoneyContract
    other: MoneyContract

    @classmethod
    def from_amounts(cls, *, personal: Money, benefit: Money, other: Money) -> Self:
        """Build a split without converting exact cents to floating point."""

        return cls(
            personal_funds=MoneyContract(minor_units=personal.minor_units),
            benefit=MoneyContract(minor_units=benefit.minor_units),
            other=MoneyContract(minor_units=other.minor_units),
        )


class FundingSourceSpendContract(_WebContract):
    """Net posted consumption for one configured funding source."""

    name: str
    funding_class: FundingClass
    amount: MoneyContract


class BudgetPositionContract(_WebContract):
    """One configured category budget and its exact monthly position."""

    category_name: str
    target: MoneyContract
    posted_spend: MoneyContract
    remaining: MoneyContract

    @classmethod
    def from_domain(cls, category_name: str, status: BudgetStatus) -> Self:
        """Build a wire budget position from deterministic domain arithmetic."""

        return cls(
            category_name=category_name,
            target=MoneyContract(minor_units=status.target.minor_units),
            posted_spend=MoneyContract(minor_units=status.posted_spend.minor_units),
            remaining=MoneyContract(minor_units=status.remaining.minor_units),
        )


class MonthlyOverviewResponse(_WebContract):
    """First dashboard summary over one Rome calendar month."""

    period: PeriodContract
    posted_total: MoneyContract
    funding_split: FundingClassSplitContract
    funding_sources: tuple[FundingSourceSpendContract, ...]
    budgets: tuple[BudgetPositionContract, ...]
    unbudgeted_categories: tuple[str, ...]

    @classmethod
    def from_application(cls, overview: MonthlyOverview) -> Self:
        """Map an application read model without exposing profile identity."""

        split = overview.split.split
        return cls(
            period=PeriodContract.from_domain(overview.period),
            posted_total=MoneyContract(
                minor_units=overview.posted_total.minor_units,
            ),
            funding_split=FundingClassSplitContract.from_amounts(
                personal=split.personal_funds,
                benefit=split.benefit,
                other=split.other,
            ),
            funding_sources=tuple(
                FundingSourceSpendContract(
                    name=source.name,
                    funding_class=source.funding_class,
                    amount=MoneyContract(minor_units=amount.minor_units),
                )
                for source, amount in overview.split.sources
            ),
            budgets=tuple(
                BudgetPositionContract.from_domain(name, status)
                for name, status in overview.budgets
            ),
            unbudgeted_categories=overview.unbudgeted_category_names,
        )


class HealthResponse(_WebContract):
    """Content-free process or dependency health state."""

    status: Literal["healthy", "unavailable"]


class CsrfTokenResponse(_WebContract):
    """Per-browser double-submit value for later mutation requests."""

    token: str


__all__ = [
    "BudgetPositionContract",
    "CsrfTokenResponse",
    "FundingClassSplitContract",
    "FundingSourceSpendContract",
    "HealthResponse",
    "MonthlyOverviewResponse",
    "PeriodContract",
]
