"""Strict JSON contracts for the local control-plane API."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from money_tracker.api.contracts import ExpenseCreateRequest, MoneyContract
from money_tracker.application import (
    BudgetPlan,
    BudgetPlanEntry,
    CategoryReviewQueue,
    MonthlyOverview,
    TransactionRegister,
)
from money_tracker.domain import (
    MAX_REVIEW_PROPOSALS,
    BalanceMode,
    BudgetStatus,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSourceStatus,
    Money,
    MonthlyBudget,
    YearMonth,
)


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


class CategoryReferenceContract(_WebContract):
    """Render one taxonomy node without exposing profile state."""

    id: UUID
    name: str
    parent_id: UUID | None
    status: CategoryStatus


class FundingSourceReferenceContract(_WebContract):
    """Render one funding source available to the transaction editor."""

    id: UUID
    name: str
    funding_class: FundingClass
    balance_mode: BalanceMode
    status: FundingSourceStatus
    is_default: bool


class TransactionCategoryAllocationContract(_WebContract):
    """Render one exact purpose allocation with its current display name."""

    category_id: UUID
    category_name: str | None
    amount: MoneyContract
    provenance: str
    evidence: str | None
    owner_override: bool
    receipt_line_id: UUID | None


class TransactionPaymentAllocationContract(_WebContract):
    """Render one exact payment allocation with its current source name."""

    funding_source_id: UUID
    funding_source_name: str | None
    amount: MoneyContract


class TransactionContract(_WebContract):
    """One movement lifecycle record safe for the local control plane."""

    id: UUID
    amount: MoneyContract
    occurred_at: AwareDatetime
    description: str
    merchant: str | None
    status: ExpenseStatus
    category_allocations: tuple[TransactionCategoryAllocationContract, ...]
    payment_allocations: tuple[TransactionPaymentAllocationContract, ...]
    receipt_id: UUID | None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(
        cls,
        expense: Expense,
        *,
        category_names: dict[UUID, str],
        funding_source_names: dict[UUID, str],
    ) -> Self:
        """Map one aggregate without leaking profile or inbound channel IDs."""

        return cls(
            id=expense.id,
            amount=MoneyContract(minor_units=expense.amount.minor_units),
            occurred_at=expense.occurred_at,
            description=expense.description,
            merchant=expense.merchant,
            status=expense.status,
            category_allocations=tuple(
                TransactionCategoryAllocationContract(
                    category_id=item.category_id,
                    category_name=category_names.get(item.category_id),
                    amount=MoneyContract(minor_units=item.amount.minor_units),
                    provenance=item.provenance,
                    evidence=item.evidence,
                    owner_override=item.owner_override,
                    receipt_line_id=item.receipt_line_id,
                )
                for item in expense.category_allocations
            ),
            payment_allocations=tuple(
                TransactionPaymentAllocationContract(
                    funding_source_id=item.funding_source_id,
                    funding_source_name=funding_source_names.get(
                        item.funding_source_id
                    ),
                    amount=MoneyContract(minor_units=item.amount.minor_units),
                )
                for item in expense.payment_allocations
            ),
            receipt_id=expense.receipt_id,
            version=expense.version,
        )


class TransactionRegisterResponse(_WebContract):
    """Complete monthly register plus stable editor reference data."""

    period: PeriodContract
    transactions: tuple[TransactionContract, ...]
    categories: tuple[CategoryReferenceContract, ...]
    funding_sources: tuple[FundingSourceReferenceContract, ...]

    @classmethod
    def from_application(cls, register: TransactionRegister) -> Self:
        """Map a control-plane read model without granting mutation authority."""

        category_names = {
            category.id: category.name for category in register.taxonomy.categories
        }
        funding_source_names = {
            source.id: source.name for source in register.funding_sources
        }
        return cls(
            period=PeriodContract.from_domain(register.period),
            transactions=tuple(
                TransactionContract.from_domain(
                    expense,
                    category_names=category_names,
                    funding_source_names=funding_source_names,
                )
                for expense in register.expenses
            ),
            categories=tuple(
                CategoryReferenceContract(
                    id=category.id,
                    name=category.name,
                    parent_id=category.parent_id,
                    status=category.status,
                )
                for category in sorted(
                    register.taxonomy.categories,
                    key=lambda item: (item.name, str(item.id)),
                )
            ),
            funding_sources=tuple(
                FundingSourceReferenceContract(
                    id=source.id,
                    name=source.name,
                    funding_class=source.funding_class,
                    balance_mode=source.balance_mode,
                    status=source.status,
                    is_default=source.is_default,
                )
                for source in sorted(
                    register.funding_sources,
                    key=lambda item: (item.name, str(item.id)),
                )
            ),
        )


class BudgetOverrideContract(_WebContract):
    """One explicit monthly target replacing the recurring one."""

    period: PeriodContract
    target: MoneyContract


class BudgetPlanEntryContract(_WebContract):
    """One configured budget with the identity a correction must quote."""

    category_id: UUID
    category_name: str
    recurring_target: MoneyContract
    overrides: tuple[BudgetOverrideContract, ...]
    target: MoneyContract
    posted_spend: MoneyContract
    remaining: MoneyContract
    version: int = Field(ge=1)

    @classmethod
    def from_application(cls, entry: BudgetPlanEntry) -> Self:
        """Map one budget position without exposing profile or budget identity.

        The stable category, not the budget row, addresses the resource, so the
        browser never learns an identifier it has no route for.
        """

        return cls(
            category_id=entry.budget.category_id,
            category_name=entry.category_name,
            recurring_target=MoneyContract(
                minor_units=entry.budget.recurring_target.minor_units,
            ),
            overrides=tuple(
                BudgetOverrideContract(
                    period=PeriodContract.from_domain(period),
                    target=MoneyContract(minor_units=target.minor_units),
                )
                for period, target in entry.budget.overrides
            ),
            target=MoneyContract(minor_units=entry.status.target.minor_units),
            posted_spend=MoneyContract(
                minor_units=entry.status.posted_spend.minor_units,
            ),
            remaining=MoneyContract(minor_units=entry.status.remaining.minor_units),
            version=entry.budget.version,
        )


class BudgetPlanResponse(_WebContract):
    """Stored monthly budget plan and the categories still available."""

    period: PeriodContract
    entries: tuple[BudgetPlanEntryContract, ...]
    budgetable_categories: tuple[CategoryReferenceContract, ...]

    @classmethod
    def from_application(cls, plan: BudgetPlan) -> Self:
        """Map the plan read model into the control-plane wire shape."""

        return cls(
            period=PeriodContract.from_domain(plan.period),
            entries=tuple(
                BudgetPlanEntryContract.from_application(entry)
                for entry in plan.entries
            ),
            budgetable_categories=tuple(
                CategoryReferenceContract(
                    id=category.id,
                    name=category.name,
                    parent_id=category.parent_id,
                    status=category.status,
                )
                for category in plan.budgetable_categories
            ),
        )


class BudgetMutationResponse(_WebContract):
    """Return only the identity and version needed after a budget mutation."""

    category_id: UUID
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, budget: MonthlyBudget) -> Self:
        """Map an aggregate without returning trusted profile or budget rows."""

        return cls(category_id=budget.category_id, version=budget.version)


class BudgetWriteRequest(_WebContract):
    """Complete recurring target and override plan for one category."""

    recurring_target: MoneyContract
    overrides: tuple[BudgetOverrideContract, ...] = ()


class BudgetCreateRequest(BudgetWriteRequest):
    """First budget attached to one active category."""

    category_id: UUID


class BudgetReplaceRequest(BudgetWriteRequest):
    """Full budget replacement under optimistic concurrency."""

    expected_version: int = Field(ge=1)


class ExpenseReplaceRequest(ExpenseCreateRequest):
    """Full movement replacement under optimistic concurrency."""

    expected_version: int = Field(ge=1)


class ExpensePostRequest(_WebContract):
    """Explicit draft registration request."""

    expected_version: int = Field(ge=1)
    receipt_approved: bool = False


class ExpenseCancelRequest(_WebContract):
    """Explicit draft discard or posted reversal request."""

    expected_version: int = Field(ge=1)


class ExpenseMutationResponse(_WebContract):
    """Return only identity and lifecycle facts needed after a mutation."""

    id: UUID
    status: ExpenseStatus
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, expense: Expense) -> Self:
        """Map an aggregate without returning trusted profile or channel data."""

        return cls(id=expense.id, status=expense.status, version=expense.version)


class CategoryReviewExpenseContract(_WebContract):
    """Movement facts needed to understand one pending category decision."""

    id: UUID
    amount: MoneyContract
    occurred_at: AwareDatetime
    description: str
    merchant: str | None
    status: ExpenseStatus
    version: int = Field(ge=1)


class CategoryProposalContract(_WebContract):
    """One pending candidate and its deterministic resolution choices."""

    id: UUID
    proposed_name: str
    description: str
    parent_name: str | None
    rationale: str
    alternative_categories: tuple[CategoryReferenceContract, ...]
    excluded_names: tuple[str, ...]
    can_repropose: bool
    expense: CategoryReviewExpenseContract


class CategoryReviewQueueResponse(_WebContract):
    """Open category proposals plus active choices for manual reuse."""

    proposals: tuple[CategoryProposalContract, ...]
    active_categories: tuple[CategoryReferenceContract, ...]

    @classmethod
    def from_application(cls, queue: CategoryReviewQueue) -> Self:
        """Map owner review state without exposing profile or source identity."""

        categories = {category.id: category for category in queue.taxonomy.categories}

        def reference(category_id: UUID) -> CategoryReferenceContract:
            category = categories[category_id]
            return CategoryReferenceContract(
                id=category.id,
                name=category.name,
                parent_id=category.parent_id,
                status=category.status,
            )

        return cls(
            proposals=tuple(
                CategoryProposalContract(
                    id=item.proposal.id,
                    proposed_name=item.proposal.proposed_name,
                    description=item.proposal.description,
                    parent_name=(
                        None
                        if item.proposal.parent_id is None
                        else categories[item.proposal.parent_id].name
                    ),
                    rationale=item.proposal.rationale,
                    alternative_categories=tuple(
                        reference(category_id)
                        for category_id in item.proposal.alternative_category_ids
                        if categories[category_id].status is CategoryStatus.ACTIVE
                    ),
                    excluded_names=item.proposal.excluded_names,
                    can_repropose=(
                        len(item.proposal.exclusions_for_next()) < MAX_REVIEW_PROPOSALS
                    ),
                    expense=CategoryReviewExpenseContract(
                        id=item.expense.id,
                        amount=MoneyContract(
                            minor_units=item.expense.amount.minor_units
                        ),
                        occurred_at=item.expense.occurred_at,
                        description=item.expense.description,
                        merchant=item.expense.merchant,
                        status=item.expense.status,
                        version=item.expense.version,
                    ),
                )
                for item in queue.reviews
            ),
            active_categories=tuple(
                reference(category.id)
                for category in sorted(
                    queue.taxonomy.categories,
                    key=lambda item: (item.name, str(item.id)),
                )
                if category.status is CategoryStatus.ACTIVE
            ),
        )


class CategoryReviewDecisionRequest(_WebContract):
    """Strict empty body for an approve or cancel decision."""


class CategoryReviewReuseRequest(_WebContract):
    """Existing category selected by its current active display name."""

    category_name: str = Field(min_length=1, max_length=200)


class HealthResponse(_WebContract):
    """Content-free process or dependency health state."""

    status: Literal["healthy", "unavailable"]


class CsrfTokenResponse(_WebContract):
    """Per-browser double-submit value for later mutation requests."""

    token: str


__all__ = [
    "BudgetPositionContract",
    "CategoryProposalContract",
    "CategoryReferenceContract",
    "CategoryReviewDecisionRequest",
    "CategoryReviewQueueResponse",
    "CategoryReviewReuseRequest",
    "CsrfTokenResponse",
    "ExpenseCancelRequest",
    "ExpenseMutationResponse",
    "ExpensePostRequest",
    "ExpenseReplaceRequest",
    "FundingClassSplitContract",
    "FundingSourceReferenceContract",
    "FundingSourceSpendContract",
    "HealthResponse",
    "MonthlyOverviewResponse",
    "PeriodContract",
    "TransactionCategoryAllocationContract",
    "TransactionContract",
    "TransactionPaymentAllocationContract",
    "TransactionRegisterResponse",
]
