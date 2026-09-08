"""Read-only execution of the scoped tools behind the query and advice routes.

Every operation here reads. There is no write path, no service dependency that
has one, and no argument by which a caller could reach one, which is what
AD-006 means by giving those workflows read-only domain tools.

The results are the answer. A model chooses which tool to run and which
category the owner meant; this service produces the figures, and the figures
come from the domain read models over stored records. No number an owner ever
sees is written by a model, so "numeric statements traceable to tool output"
holds by construction rather than by measurement.

A result also carries what it could not find. An absent budget or an unknown
category is reported as such instead of being smoothed into a zero, because
J7 requires a missing category or budget to be stated plainly and J8 requires
insufficient data to be named rather than papered over with confidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from money_tracker.application.ports import ReportingRepository
from money_tracker.domain import (
    BudgetStatus,
    Category,
    FundingClassSplit,
    FundingSource,
    Money,
    TaxonomyError,
    YearMonth,
    budget_status,
    posted_category_spend,
    posted_funding_class_split,
)
from money_tracker.domain.finance import posted_funding_source_spend


@dataclass(frozen=True, slots=True)
class CategorySpending:
    """Posted spend rolled up under one category for one month.

    Attributes:
        period: Calendar month reported.
        category_name: Active category the owner's words resolved to.
        posted_spend: Sum of posted allocations under that category and its
            descendants.
        split: The same total separated into personal funds and benefits.
    """

    period: YearMonth
    category_name: str
    posted_spend: Money
    split: FundingClassSplit


@dataclass(frozen=True, slots=True)
class CategoryBudget:
    """One category's budget position for one month.

    Attributes:
        period: Calendar month reported.
        category_name: Active category the owner's words resolved to.
        status: Target, posted spend, and signed remaining amount, or None when
            the owner configured no budget for this category.
    """

    period: YearMonth
    category_name: str
    status: BudgetStatus | None


@dataclass(frozen=True, slots=True)
class PeriodSplit:
    """Posted totals for one month separated by economic source class.

    Attributes:
        period: Calendar month reported.
        split: Personal-fund, benefit, and other posted totals.
        sources: Configured sources and their net posted payment totals,
            including zero consumption. These amounts are not wallet balances.
    """

    period: YearMonth
    split: FundingClassSplit
    sources: tuple[tuple[FundingSource, Money], ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetsAtRisk:
    """Every configured budget in one month, worst position first.

    Attributes:
        period: Calendar month reported.
        statuses: Budget positions paired with their category names, ordered by
            signed remaining amount so overspent budgets come first.
        unbudgeted_category_names: Active categories with posted spend and no
            budget, which is the data J8 must name rather than assume away.
    """

    period: YearMonth
    statuses: tuple[tuple[str, BudgetStatus], ...]
    unbudgeted_category_names: tuple[str, ...]


class ReportingService:
    """Execute the read-only tools the bounded agent routes may call."""

    def __init__(self, repository: ReportingRepository) -> None:
        """Bind reporting to a replaceable read-only persistence port.

        Args:
            repository: Read-only reporting boundary. It exposes no mutation,
                so this service cannot acquire one.
        """

        self._repository = repository

    def category_spending(
        self,
        *,
        profile_id: UUID,
        category_name: str,
        period: YearMonth,
    ) -> CategorySpending:
        """Report posted spend under one named category for one month.

        Args:
            profile_id: Trusted owner scope.
            category_name: Category as the owner named it.
            period: Rome calendar month to report.

        Returns:
            The roll-up and its personal-versus-benefit split.

        Raises:
            TaxonomyError: If no active category matches the name.
        """

        category = self._resolve(profile_id, category_name)
        expenses = self._repository.get_posted_expenses(profile_id, period=period)
        taxonomy = self._repository.get_taxonomy(profile_id)
        matching = tuple(
            expense
            for expense in expenses
            if any(
                category.id in taxonomy.ancestor_ids(allocation.category_id)
                for allocation in expense.category_allocations
            )
        )
        return CategorySpending(
            period=period,
            category_name=category.name,
            posted_spend=posted_category_spend(
                expenses, category_id=category.id, taxonomy=taxonomy
            ),
            split=posted_funding_class_split(
                matching,
                funding_sources=self._repository.get_funding_sources(profile_id),
            ),
        )

    def category_budget(
        self,
        *,
        profile_id: UUID,
        category_name: str,
        period: YearMonth,
    ) -> CategoryBudget:
        """Report one category's budget position for one month.

        Args:
            profile_id: Trusted owner scope.
            category_name: Category as the owner named it.
            period: Rome calendar month to report.

        Returns:
            The budget position, or the same report with no status when the
            owner configured no budget for that category.

        Raises:
            TaxonomyError: If no active category matches the name.
        """

        category = self._resolve(profile_id, category_name)
        budget = self._repository.get_budget(profile_id, category.id)
        if budget is None:
            return CategoryBudget(
                period=period, category_name=category.name, status=None
            )
        return CategoryBudget(
            period=period,
            category_name=category.name,
            status=budget_status(
                budget,
                period=period,
                posted_spend=self._spend(profile_id, category.id, period),
            ),
        )

    def period_split(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> PeriodSplit:
        """Report one month's posted totals by economic source class.

        Args:
            profile_id: Trusted owner scope.
            period: Rome calendar month to report.

        Returns:
            The personal-fund, benefit, and other posted totals.
        """

        expenses = self._repository.get_posted_expenses(profile_id, period=period)
        sources = self._repository.get_funding_sources(profile_id)
        return PeriodSplit(
            period=period,
            split=posted_funding_class_split(expenses, funding_sources=sources),
            sources=tuple(
                (
                    source,
                    posted_funding_source_spend(expenses, funding_source_id=source.id),
                )
                for source in sorted(
                    sources, key=lambda item: (item.name, str(item.id))
                )
            ),
        )

    def budgets_at_risk(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> BudgetsAtRisk:
        """Survey every configured budget for one month, worst position first.

        Ordering is by signed remaining amount, so an overspent budget always
        precedes a comfortable one. J8 reports exceeded and at-risk budgets
        first, and doing that ordering here keeps it deterministic rather than
        leaving it to a model's sense of severity.

        Args:
            profile_id: Trusted owner scope.
            period: Rome calendar month to report.

        Returns:
            Ordered budget positions and the categories that have posted spend
            but no budget to compare it against.
        """

        expenses = self._repository.get_posted_expenses(profile_id, period=period)
        taxonomy = self._repository.get_taxonomy(profile_id)
        budgets = self._repository.get_budgets(profile_id)
        budgeted = {budget.category_id for budget in budgets}
        statuses = tuple(
            (
                taxonomy.category(budget.category_id).name,
                budget_status(
                    budget,
                    period=period,
                    posted_spend=posted_category_spend(
                        expenses, category_id=budget.category_id, taxonomy=taxonomy
                    ),
                ),
            )
            for budget in budgets
        )
        allocated = {
            allocation.category_id
            for expense in expenses
            for allocation in expense.category_allocations
        }
        return BudgetsAtRisk(
            period=period,
            statuses=tuple(
                sorted(statuses, key=lambda item: item[1].remaining.minor_units)
            ),
            unbudgeted_category_names=tuple(
                sorted(
                    taxonomy.category(category_id).name
                    for category_id in allocated
                    if not budgeted & set(taxonomy.ancestor_ids(category_id))
                )
            ),
        )

    def _resolve(self, profile_id: UUID, category_name: str) -> Category:
        """Resolve owner words against the profile's own active taxonomy.

        Raises:
            TaxonomyError: If no active category matches the name.
        """

        try:
            return self._repository.get_taxonomy(profile_id).resolve_active(
                category_name
            )
        except TaxonomyError as error:
            raise TaxonomyError("active category does not exist") from error

    def _spend(self, profile_id: UUID, category_id: UUID, period: YearMonth) -> Money:
        """Compute posted spend under one category for one month."""

        return posted_category_spend(
            self._repository.get_posted_expenses(profile_id, period=period),
            category_id=category_id,
            taxonomy=self._repository.get_taxonomy(profile_id),
        )


__all__ = [
    "BudgetsAtRisk",
    "CategoryBudget",
    "CategorySpending",
    "PeriodSplit",
    "ReportingService",
]
