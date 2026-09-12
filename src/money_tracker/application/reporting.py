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
    CategoryStatus,
    Expense,
    FundingClassSplit,
    FundingSource,
    Money,
    MonthlyBudget,
    ResolvedPeriod,
    Taxonomy,
    TaxonomyError,
    YearMonth,
    budget_status,
    posted_category_spend,
    posted_funding_class_split,
    sum_money,
)
from money_tracker.domain.finance import ExpenseStatus, posted_funding_source_spend


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


@dataclass(frozen=True, slots=True)
class BudgetPlanEntry:
    """One configured budget, its identity, and its position in one month.

    Unlike the analytics views, this entry carries the aggregate itself. The
    control plane has to offer the owner a correction of the stored plan, which
    needs the stable identifier, every explicit override, and the optimistic
    version that a replacement must quote.

    Attributes:
        budget: Complete stored aggregate, including its version.
        category_name: Active display name of the budgeted category.
        status: Effective target, posted spend, and signed remaining amount for
            the reported month.
    """

    budget: MonthlyBudget
    category_name: str
    status: BudgetStatus


@dataclass(frozen=True, slots=True)
class BudgetPlan:
    """Every configured budget for one month plus what may still be budgeted.

    Attributes:
        period: Rome calendar month reported.
        entries: Configured budgets ordered by signed remaining amount, so an
            overspent budget precedes a comfortable one exactly as the overview
            orders them.
        budgetable_categories: Active categories that have no budget yet, which
            is what the owner may still configure.
    """

    period: YearMonth
    entries: tuple[BudgetPlanEntry, ...]
    budgetable_categories: tuple[Category, ...]


@dataclass(frozen=True, slots=True)
class MonthlyOverview:
    """Deterministic control-plane summary for one Rome calendar month.

    The total is derived from payment allocations, while budget positions are
    derived from category allocations. Each dimension reconciles independently
    to the same posted movements, so receipt parents or lines are never added a
    second time.

    Attributes:
        period: Calendar month reported.
        posted_total: Net posted consumption, including signed refunds.
        split: The total separated by funding class and individual source.
        budgets: Configured budgets ordered from most overspent to most free.
        unbudgeted_category_names: Active purposes with spend and no applicable
            parent or direct budget.
    """

    period: YearMonth
    posted_total: Money
    split: PeriodSplit
    budgets: tuple[tuple[str, BudgetStatus], ...]
    unbudgeted_category_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CategorySpendEntry:
    """Posted spend allocated directly to one category over a period.

    Entries partition the purpose dimension: every posted category allocation
    belongs to exactly one entry, so the entries sum to the period total
    without a parent and its children being counted twice. A parent is named
    for display only; its own figure never includes a child's.

    Attributes:
        category: Category the allocations name, active or archived.
        parent_name: Display name of the root category, or None at the root.
        amount: Exact signed sum of the allocations assigned to it.
    """

    category: Category
    parent_name: str | None
    amount: Money


@dataclass(frozen=True, slots=True)
class MonthlyBudgetPositions:
    """Budget positions for a period that is exactly one calendar month.

    A monthly budget has no rollover, so a target compares against one month
    and against nothing else. There is no honest position for a day, a week,
    a year, or a partial or multi-month interval, and this read reports the
    absence instead of prorating a target into a number the owner never set.

    Attributes:
        period: Calendar month the positions belong to.
        statuses: Configured positions paired with their category names,
            worst position first, exactly as the monthly overview orders them.
        unbudgeted_category_names: Active purposes with spend and no
            applicable direct or parent budget.
    """

    period: YearMonth
    statuses: tuple[tuple[str, BudgetStatus], ...]
    unbudgeted_category_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RangeOverview:
    """Deterministic posted facts for any resolved Rome calendar period.

    The monthly overview answers the same question for one month and keeps
    its own shape, because the model-visible tools read through it. This read
    accepts every granularity the control plane offers and adds the purpose
    dimension the monthly overview never exposed as amounts.

    The two AD-007 dimensions are reported side by side and never added to
    each other. `posted_total` comes from the payment dimension, `categories`
    from the purpose dimension, and a posted movement reconciles both to its
    own total, so the two views describe the same money from two angles
    rather than two sums to combine.

    Attributes:
        period: Resolved granularity and closed Rome interval reported.
        posted_total: Net posted consumption over the interval, refunds
            signed, derived from the payment dimension.
        split: The same total separated into personal funds and benefits.
        sources: Configured sources and their net posted payment totals,
            including zero consumption. These are not wallet balances.
        categories: Purpose dimension, largest absolute spend first.
        movement_count: How many posted movements the figures come from.
        budgets: Monthly budget positions when the interval is exactly one
            calendar month, otherwise None.
    """

    period: ResolvedPeriod
    posted_total: Money
    split: FundingClassSplit
    sources: tuple[tuple[FundingSource, Money], ...]
    categories: tuple[CategorySpendEntry, ...]
    movement_count: int
    budgets: MonthlyBudgetPositions | None


@dataclass(frozen=True, slots=True)
class TransactionRegister:
    """Control-plane movements and their display references for one month.

    Unlike analytics, the register retains draft, needs-review, voided, and
    discarded records so the owner can understand and operate the complete
    lifecycle. It owns no data and grants no mutation authority.

    Attributes:
        period: Rome calendar month selected by the owner.
        expenses: Complete aggregates ordered newest first by persistence.
        taxonomy: Category names needed to render stable allocation IDs.
        funding_sources: Funding-source names needed to render payment IDs.
    """

    period: YearMonth
    expenses: tuple[Expense, ...]
    taxonomy: Taxonomy
    funding_sources: tuple[FundingSource, ...]


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
        return self._period_split_from(period, expenses=expenses, sources=sources)

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
        return self._budgets_at_risk_from(
            period,
            expenses=expenses,
            taxonomy=taxonomy,
            budgets=budgets,
        )

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Build the first dashboard overview from existing read-only tools.

        Args:
            profile_id: Trusted owner scope supplied by runtime composition.
            period: Rome calendar month to summarize.

        Returns:
            Exact posted total, funding split, and budget risk summary.
        """

        expenses = self._repository.get_posted_expenses(profile_id, period=period)
        split = self._period_split_from(
            period,
            expenses=expenses,
            sources=self._repository.get_funding_sources(profile_id),
        )
        risks = self._budgets_at_risk_from(
            period,
            expenses=expenses,
            taxonomy=self._repository.get_taxonomy(profile_id),
            budgets=self._repository.get_budgets(profile_id),
        )
        posted_total = split.split.personal_funds + split.split.benefit
        posted_total += split.split.other
        return MonthlyOverview(
            period=period,
            posted_total=posted_total,
            split=split,
            budgets=risks.statuses,
            unbudgeted_category_names=risks.unbudgeted_category_names,
        )

    def range_overview(
        self,
        *,
        profile_id: UUID,
        period: ResolvedPeriod,
    ) -> RangeOverview:
        """Summarize posted movements over any resolved calendar period.

        The period arrives already resolved, because deciding which Rome days
        a day, an ISO week, a month, a year, or a validated custom interval
        covers is calendar policy and belongs in the domain rather than in
        this service or in the adapter that called it.

        Args:
            profile_id: Trusted owner scope supplied by runtime composition.
            period: Resolved granularity and closed Rome interval to report.

        Returns:
            Exact posted total, both reconciling dimensions, and the monthly
            budget positions when, and only when, the interval is one whole
            calendar month.
        """

        expenses = self._repository.get_posted_expenses_in_range(
            profile_id,
            interval=period.interval,
        )
        taxonomy = self._repository.get_taxonomy(profile_id)
        sources = self._repository.get_funding_sources(profile_id)
        split = posted_funding_class_split(expenses, funding_sources=sources)
        month = period.calendar_month
        budgets = None
        if month is not None:
            risks = self._budgets_at_risk_from(
                month,
                expenses=expenses,
                taxonomy=taxonomy,
                budgets=self._repository.get_budgets(profile_id),
            )
            budgets = MonthlyBudgetPositions(
                period=month,
                statuses=risks.statuses,
                unbudgeted_category_names=risks.unbudgeted_category_names,
            )
        return RangeOverview(
            period=period,
            posted_total=split.personal_funds + split.benefit + split.other,
            split=split,
            sources=tuple(
                (
                    source,
                    posted_funding_source_spend(
                        expenses,
                        funding_source_id=source.id,
                    ),
                )
                for source in sorted(
                    sources,
                    key=lambda item: (item.name, str(item.id)),
                )
            ),
            categories=self._category_entries(expenses, taxonomy=taxonomy),
            movement_count=sum(
                1 for expense in expenses if expense.status is ExpenseStatus.POSTED
            ),
            budgets=budgets,
        )

    def transaction_register(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> TransactionRegister:
        """Return every movement state and its display references for a month.

        Args:
            profile_id: Trusted owner scope supplied by runtime composition.
            period: Rome calendar month to inspect.

        Returns:
            Complete movement lifecycle records plus category and funding names.
        """

        return TransactionRegister(
            period=period,
            expenses=self._repository.get_expenses(profile_id, period=period),
            taxonomy=self._repository.get_taxonomy(profile_id),
            funding_sources=self._repository.get_funding_sources(profile_id),
        )

    def budget_plan(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> BudgetPlan:
        """Return the stored budget plan and its position in one month.

        The overview answers how the configured budgets are doing. This read
        additionally exposes each aggregate's identity, overrides, and version,
        because the control plane must be able to correct the plan and not only
        display it.

        Args:
            profile_id: Trusted owner scope supplied by runtime composition.
            period: Rome calendar month to report.

        Returns:
            Configured budgets worst position first, plus the active categories
            that have no budget yet.
        """

        expenses = self._repository.get_posted_expenses(profile_id, period=period)
        taxonomy = self._repository.get_taxonomy(profile_id)
        budgets = self._repository.get_budgets(profile_id)
        entries = tuple(
            BudgetPlanEntry(
                budget=budget,
                category_name=taxonomy.category(budget.category_id).name,
                status=budget_status(
                    budget,
                    period=period,
                    posted_spend=posted_category_spend(
                        expenses,
                        category_id=budget.category_id,
                        taxonomy=taxonomy,
                    ),
                ),
            )
            for budget in budgets
        )
        budgeted = {budget.category_id for budget in budgets}
        return BudgetPlan(
            period=period,
            entries=tuple(
                sorted(entries, key=lambda item: item.status.remaining.minor_units)
            ),
            budgetable_categories=tuple(
                sorted(
                    (
                        category
                        for category in taxonomy.categories
                        if category.status is CategoryStatus.ACTIVE
                        and category.id not in budgeted
                    ),
                    key=lambda item: (item.name, str(item.id)),
                )
            ),
        )

    @staticmethod
    def _category_entries(
        expenses: tuple[Expense, ...],
        *,
        taxonomy: Taxonomy,
    ) -> tuple[CategorySpendEntry, ...]:
        """Partition posted spend across the categories actually allocated.

        Allocations are grouped by the category they name, never rolled into
        an ancestor, so a receipt parent and its lines cannot both appear.
        The result is ordered by largest absolute amount so an overview leads
        with what the money went to, with the display name breaking ties.
        """

        amounts: dict[UUID, list[Money]] = {}
        for expense in expenses:
            if expense.status is not ExpenseStatus.POSTED:
                continue
            for allocation in expense.category_allocations:
                amounts.setdefault(allocation.category_id, []).append(allocation.amount)
        entries = []
        for category_id, allocated in amounts.items():
            category = taxonomy.category(category_id)
            parent = category.parent_id
            entries.append(
                CategorySpendEntry(
                    category=category,
                    parent_name=(
                        None if parent is None else taxonomy.category(parent).name
                    ),
                    amount=sum_money(tuple(allocated)),
                )
            )
        return tuple(
            sorted(
                entries,
                key=lambda item: (
                    -abs(item.amount.minor_units),
                    item.category.name,
                    str(item.category.id),
                ),
            )
        )

    @staticmethod
    def _period_split_from(
        period: YearMonth,
        *,
        expenses: tuple[Expense, ...],
        sources: tuple[FundingSource, ...],
    ) -> PeriodSplit:
        """Compute funding views from one immutable movement collection."""

        return PeriodSplit(
            period=period,
            split=posted_funding_class_split(expenses, funding_sources=sources),
            sources=tuple(
                (
                    source,
                    posted_funding_source_spend(
                        expenses,
                        funding_source_id=source.id,
                    ),
                )
                for source in sorted(
                    sources,
                    key=lambda item: (item.name, str(item.id)),
                )
            ),
        )

    @staticmethod
    def _budgets_at_risk_from(
        period: YearMonth,
        *,
        expenses: tuple[Expense, ...],
        taxonomy: Taxonomy,
        budgets: tuple[MonthlyBudget, ...],
    ) -> BudgetsAtRisk:
        """Compute category budget views from one movement collection."""

        budgeted = {budget.category_id for budget in budgets}
        statuses = tuple(
            (
                taxonomy.category(budget.category_id).name,
                budget_status(
                    budget,
                    period=period,
                    posted_spend=posted_category_spend(
                        expenses,
                        category_id=budget.category_id,
                        taxonomy=taxonomy,
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
    "BudgetPlan",
    "BudgetPlanEntry",
    "BudgetsAtRisk",
    "CategoryBudget",
    "CategorySpendEntry",
    "CategorySpending",
    "MonthlyBudgetPositions",
    "MonthlyOverview",
    "PeriodSplit",
    "RangeOverview",
    "ReportingService",
    "TransactionRegister",
]
