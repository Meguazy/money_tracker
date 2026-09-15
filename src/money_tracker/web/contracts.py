"""Strict JSON contracts for the local control-plane API."""

from __future__ import annotations

from datetime import date
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from money_tracker.api.contracts import ExpenseCreateRequest, MoneyContract
from money_tracker.application import (
    BudgetPlan,
    BudgetPlanEntry,
    CategoryManagement,
    CategoryReviewQueue,
    FundingSourceManagement,
    MonthlyBudgetPositions,
    MonthlyOverview,
    ProfileSettings,
    RangeOverview,
    TransactionRegister,
)
from money_tracker.domain import (
    MAX_REVIEW_PROPOSALS,
    BalanceMode,
    BudgetStatus,
    Category,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    Money,
    MonthlyBudget,
    PeriodGranularity,
    ReceiptMetadata,
    ReceiptStatus,
    ResolvedPeriod,
    WalletEntry,
    WalletEntryType,
    YearMonth,
)
from money_tracker.messaging import (
    DeadLetter,
    MessagingHealth,
    MessagingOperationsSnapshot,
    MessagingQueue,
    OutboxDeadLetter,
    RequeuedItem,
    RequeueOutcome,
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


class ResolvedPeriodContract(_WebContract):
    """One reporting period as the Rome calendar policy resolved it.

    The browser sends a selection and receives the resolved calendar facts
    back. Nothing here is recomputed client-side: an ISO week's numbering is
    not derivable from its dates without repeating the calendar rule, and the
    week containing 31 December 2025 belongs to ISO year 2026.
    """

    granularity: PeriodGranularity
    start: date
    end: date
    day_count: int
    iso_year: int | None
    iso_week: int | None

    @classmethod
    def from_domain(cls, period: ResolvedPeriod) -> Self:
        """Build the wire period from a resolved domain value."""

        return cls(
            granularity=period.granularity,
            start=period.interval.start,
            end=period.interval.end,
            day_count=period.interval.day_count,
            iso_year=period.iso_year,
            iso_week=period.iso_week,
        )


class CategorySpendContract(_WebContract):
    """Exact posted spend allocated directly to one category.

    The parent is named for display only. Each entry carries the allocations
    that name its own category and never a descendant's, so the entries are a
    partition of the purpose dimension rather than overlapping roll-ups.
    """

    category_id: UUID
    category_name: str
    parent_name: str | None
    amount: MoneyContract


class MonthlyBudgetPositionsContract(_WebContract):
    """Budget positions, present only for a whole calendar month."""

    period: PeriodContract
    positions: tuple[BudgetPositionContract, ...]
    unbudgeted_categories: tuple[str, ...]

    @classmethod
    def from_application(cls, budgets: MonthlyBudgetPositions) -> Self:
        """Build the monthly budget block from deterministic positions."""

        return cls(
            period=PeriodContract.from_domain(budgets.period),
            positions=tuple(
                BudgetPositionContract.from_domain(name, status)
                for name, status in budgets.statuses
            ),
            unbudgeted_categories=budgets.unbudgeted_category_names,
        )


class RangeOverviewResponse(_WebContract):
    """Deterministic posted facts over any resolved Rome calendar period.

    `budgets` is null whenever the period is not exactly one calendar month.
    A monthly budget has no rollover, so there is no defined position for a
    day, a week, a year, or a partial or multi-month interval, and the field is
    omitted rather than filled with a prorated figure the owner never set.
    """

    period: ResolvedPeriodContract
    posted_total: MoneyContract
    funding_split: FundingClassSplitContract
    funding_sources: tuple[FundingSourceSpendContract, ...]
    categories: tuple[CategorySpendContract, ...]
    movement_count: int
    budgets: MonthlyBudgetPositionsContract | None

    @classmethod
    def from_application(cls, overview: RangeOverview) -> Self:
        """Map the range read without exposing profile identity."""

        return cls(
            period=ResolvedPeriodContract.from_domain(overview.period),
            posted_total=MoneyContract(
                minor_units=overview.posted_total.minor_units,
            ),
            funding_split=FundingClassSplitContract.from_amounts(
                personal=overview.split.personal_funds,
                benefit=overview.split.benefit,
                other=overview.split.other,
            ),
            funding_sources=tuple(
                FundingSourceSpendContract(
                    name=source.name,
                    funding_class=source.funding_class,
                    amount=MoneyContract(minor_units=amount.minor_units),
                )
                for source, amount in overview.sources
            ),
            categories=tuple(
                CategorySpendContract(
                    category_id=entry.category.id,
                    category_name=entry.category.name,
                    parent_name=entry.parent_name,
                    amount=MoneyContract(minor_units=entry.amount.minor_units),
                )
                for entry in overview.categories
            ),
            movement_count=overview.movement_count,
            budgets=(
                None
                if overview.budgets is None
                else MonthlyBudgetPositionsContract.from_application(overview.budgets)
            ),
        )


class CategoryReferenceContract(_WebContract):
    """Render one taxonomy node without exposing profile state."""

    id: UUID
    name: str
    parent_id: UUID | None
    status: CategoryStatus


class ManagedCategoryContract(_WebContract):
    """Render owner-managed state while preserving stable taxonomy identity."""

    id: UUID
    name: str
    description: str
    parent_id: UUID | None
    parent_name: str | None
    aliases: tuple[str, ...]
    status: CategoryStatus
    seeded: bool
    version: int = Field(ge=1)


class TaxonomyWarningContract(_WebContract):
    """Expose one non-blocking, owner-safe taxonomy complexity warning."""

    code: str
    message: str


class CategoryManagementResponse(_WebContract):
    """Complete category administration state for the trusted owner."""

    categories: tuple[ManagedCategoryContract, ...]
    warnings: tuple[TaxonomyWarningContract, ...]

    @classmethod
    def from_application(cls, management: CategoryManagement) -> Self:
        """Map stable category nodes and warnings without profile identity."""

        names = {category.id: category.name for category in management.categories}
        return cls(
            categories=tuple(
                ManagedCategoryContract(
                    id=category.id,
                    name=category.name,
                    description=category.description,
                    parent_id=category.parent_id,
                    parent_name=(
                        None
                        if category.parent_id is None
                        else names.get(category.parent_id)
                    ),
                    aliases=category.aliases,
                    status=category.status,
                    seeded=category.seeded,
                    version=category.version,
                )
                for category in management.categories
            ),
            warnings=tuple(
                TaxonomyWarningContract(code=warning.code, message=warning.message)
                for warning in management.warnings
            ),
        )


class CategoryRenameRequest(_WebContract):
    """Rename one category from the exact version displayed to the owner."""

    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class CategoryArchiveRequest(_WebContract):
    """Archive one category from the exact version displayed to the owner."""

    expected_version: int = Field(ge=1)


class CategoryMutationResponse(_WebContract):
    """Return the category identity, state, and version after a mutation."""

    id: UUID
    name: str
    status: CategoryStatus
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, category: Category) -> Self:
        """Map the updated node without exposing profile-owned content beyond name."""

        return cls(
            id=category.id,
            name=category.name,
            status=category.status,
            version=category.version,
        )


class DefaultFundingSourceContract(_WebContract):
    """Active source snapshot used for the default-payment preference."""

    id: UUID
    name: str
    funding_class: FundingClass
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, source: FundingSource) -> Self:
        """Map one eligible source without profile or wallet details."""

        return cls(
            id=source.id,
            name=source.name,
            funding_class=source.funding_class,
            version=source.version,
        )


class ProfileSettingsResponse(_WebContract):
    """Owner-facing V1 preferences without identity or infrastructure data."""

    locale: str
    timezone: str
    currency: str
    default_funding_source: DefaultFundingSourceContract | None
    available_default_sources: tuple[DefaultFundingSourceContract, ...]

    @classmethod
    def from_application(cls, settings: ProfileSettings) -> Self:
        """Map persisted preferences while deliberately omitting profile scope."""

        return cls(
            locale=settings.profile.locale,
            timezone=settings.profile.timezone,
            currency=settings.profile.currency,
            default_funding_source=(
                None
                if settings.default_funding_source is None
                else DefaultFundingSourceContract.from_domain(
                    settings.default_funding_source
                )
            ),
            available_default_sources=tuple(
                DefaultFundingSourceContract.from_domain(source)
                for source in settings.available_default_sources
            ),
        )


class FundingSourceReferenceContract(_WebContract):
    """Render one funding source available to the transaction editor."""

    id: UUID
    name: str
    funding_class: FundingClass
    balance_mode: BalanceMode
    status: FundingSourceStatus
    is_default: bool


class ExpiringWalletBalanceContract(_WebContract):
    """One remaining dated wallet lot after deterministic consumption."""

    expires_on: date
    amount: MoneyContract


class WalletAvailabilityContract(_WebContract):
    """Authoritative tracked-wallet balances at one observation instant."""

    booked_balance: MoneyContract
    available_balance: MoneyContract
    expiring: tuple[ExpiringWalletBalanceContract, ...]


class WalletEntryContract(_WebContract):
    """Immutable wallet movement without operator notes or trusted scope."""

    id: UUID
    entry_type: WalletEntryType
    amount: MoneyContract
    occurred_at: AwareDatetime
    expires_on: date | None
    expense_id: UUID | None

    @classmethod
    def from_domain(cls, entry: WalletEntry) -> Self:
        """Map one ledger row while deliberately omitting its optional note."""

        return cls(
            id=entry.id,
            entry_type=entry.entry_type,
            amount=MoneyContract(minor_units=entry.amount.minor_units),
            occurred_at=entry.occurred_at,
            expires_on=entry.expires_on,
            expense_id=entry.expense_id,
        )


class ManagedFundingSourceContract(_WebContract):
    """One source and wallet details safe for the local control plane."""

    id: UUID
    name: str
    funding_class: FundingClass
    balance_mode: BalanceMode
    status: FundingSourceStatus
    is_default: bool
    version: int = Field(ge=1)
    availability: WalletAvailabilityContract | None
    entries: tuple[WalletEntryContract, ...]


class FundingSourceManagementResponse(_WebContract):
    """Complete source administration snapshot for the trusted profile."""

    observed_at: AwareDatetime
    sources: tuple[ManagedFundingSourceContract, ...]

    @classmethod
    def from_application(cls, management: FundingSourceManagement) -> Self:
        """Map sources without exposing profile identity, notes, or command data."""

        return cls(
            observed_at=management.observed_at,
            sources=tuple(
                ManagedFundingSourceContract(
                    id=item.source.id,
                    name=item.source.name,
                    funding_class=item.source.funding_class,
                    balance_mode=item.source.balance_mode,
                    status=item.source.status,
                    is_default=item.source.is_default,
                    version=item.source.version,
                    availability=(
                        None
                        if item.availability is None
                        else WalletAvailabilityContract(
                            booked_balance=MoneyContract(
                                minor_units=item.availability.booked_balance.minor_units
                            ),
                            available_balance=MoneyContract(
                                minor_units=item.availability.available_balance.minor_units
                            ),
                            expiring=tuple(
                                ExpiringWalletBalanceContract(
                                    expires_on=lot.expires_on,
                                    amount=MoneyContract(
                                        minor_units=lot.amount.minor_units
                                    ),
                                )
                                for lot in item.availability.expiring
                            ),
                        )
                    ),
                    entries=tuple(
                        WalletEntryContract.from_domain(entry)
                        for entry in reversed(item.entries)
                    ),
                )
                for item in management.sources
            ),
        )


class FundingSourceCreateRequest(_WebContract):
    """Create one active funding label or tracked wallet."""

    name: str = Field(min_length=1, max_length=200)
    funding_class: FundingClass
    balance_mode: BalanceMode
    is_default: bool = False


class FundingSourceRenameRequest(_WebContract):
    """Rename one source from the exact displayed version."""

    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class FundingSourceArchiveRequest(_WebContract):
    """Archive one non-default zero-balance source without deleting history."""

    expected_version: int = Field(ge=1)


class FundingSourceDefaultRequest(_WebContract):
    """Move the default from the complete state the browser observed."""

    expected_version: int = Field(ge=1)
    expected_previous_default_id: UUID | None
    expected_previous_default_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_previous_default_snapshot(self) -> Self:
        """Require previous default identity and version as one snapshot."""

        if (self.expected_previous_default_id is None) != (
            self.expected_previous_default_version is None
        ):
            raise ValueError("previous default identity and version must be paired")
        return self


class WalletEntryCreateRequest(_WebContract):
    """Append a manual opening, credit, or adjustment to one tracked wallet."""

    entry_type: Literal["opening", "credit", "adjustment"]
    amount: MoneyContract
    occurred_at: AwareDatetime
    expires_on: date | None = None


class FundingSourceMutationResponse(_WebContract):
    """Return source identity and state needed to refresh after a mutation."""

    id: UUID
    name: str
    status: FundingSourceStatus
    is_default: bool
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, source: FundingSource) -> Self:
        """Map one source without trusted profile or financial history."""

        return cls(
            id=source.id,
            name=source.name,
            status=source.status,
            is_default=source.is_default,
            version=source.version,
        )


class WalletEntryMutationResponse(_WebContract):
    """Return immutable entry identity after an append or replay."""

    id: UUID
    entry_type: WalletEntryType

    @classmethod
    def from_domain(cls, entry: WalletEntry) -> Self:
        """Map an entry without returning owner notes, scope, or amount."""

        return cls(id=entry.id, entry_type=entry.entry_type)


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


class ReceiptContract(_WebContract):
    """Safe derived facts about one retained original receipt.

    The content digest is deliberately absent. It is the archive storage key
    under AD-014, the browser has no route that consumes it, and the review
    decisions this page offers are addressed by the stable receipt identifier.
    Source reference and profile identity are omitted for the same reason the
    movement register omits them.

    Attributes:
        id: Stable receipt identifier and the only value a decision quotes.
        media_type: Decoded media type accepted by the archive.
        byte_size: Exact retained byte count.
        stored_at: Instant the private archive accepted the content.
        status: Retention and review state.
        original_filename: Sanitized display name, never a storage path.
        version: Optimistic version a review decision must quote.
    """

    id: UUID
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    byte_size: int = Field(ge=1)
    stored_at: AwareDatetime
    status: ReceiptStatus
    original_filename: str | None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, metadata: ReceiptMetadata) -> Self:
        """Map retained metadata without exposing its archive key or scope."""

        return cls(
            id=metadata.id,
            media_type=cast(
                Literal["image/jpeg", "image/png", "image/webp"],
                metadata.media_type,
            ),
            byte_size=metadata.byte_size,
            stored_at=metadata.stored_at,
            status=metadata.status,
            original_filename=metadata.original_filename,
            version=metadata.version,
        )


class ReceiptRegisterResponse(_WebContract):
    """Every retained receipt and its current review state."""

    receipts: tuple[ReceiptContract, ...]

    @classmethod
    def from_application(cls, receipts: tuple[ReceiptMetadata, ...]) -> Self:
        """Map the retained archive metadata into the control-plane shape."""

        return cls(
            receipts=tuple(ReceiptContract.from_domain(item) for item in receipts)
        )


class ReceiptReviewRequest(_WebContract):
    """Explicit review transition request under the displayed version."""

    expected_version: int = Field(ge=1)


class ReceiptMutationResponse(_WebContract):
    """Return only the identity and state needed after a review transition."""

    id: UUID
    status: ReceiptStatus
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, metadata: ReceiptMetadata) -> Self:
        """Map a transition result without returning profile or archive data."""

        return cls(id=metadata.id, status=metadata.status, version=metadata.version)


class MessagingQueueContract(_WebContract):
    """Depth, failure count, and age of one durable messaging queue.

    Attributes:
        pending: Items waiting for their first or next attempt.
        retrying: Items inside a bounded automatic retry cycle.
        dead_letters: Items that exhausted their attempts and need a decision.
        oldest_at: Instant the oldest still unfinished item entered the queue.
    """

    pending: int = Field(ge=0)
    retrying: int = Field(ge=0)
    dead_letters: int = Field(ge=0)
    oldest_at: AwareDatetime | None


class InboxDeadLetterContract(_WebContract):
    """One failed work item, with no trace of what the owner wrote.

    The source reference is deliberately absent. It is the finance command
    identity a replay preserves, the register and receipt responses omit it for
    the same reason, and nothing the browser does needs it: a requeue is
    addressed by the stable inbox identifier.

    Attributes:
        inbox_id: Stable identifier a requeue names.
        update_id: Telegram ordering identity, useful for correlating reports.
        attempt_count: Automatic attempts already spent.
        replay_count: Requeues an operator already applied, and the value a
            new decision pins itself to.
        last_error_code: Bounded machine-readable reason of the last attempt.
        accepted_at: Instant the gateway durably accepted the update.
    """

    inbox_id: UUID
    update_id: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    replay_count: int = Field(ge=0)
    last_error_code: str | None
    accepted_at: AwareDatetime

    @classmethod
    def from_domain(cls, item: DeadLetter) -> Self:
        """Map one failed work item without its finance command identity."""

        return cls(
            inbox_id=item.id,
            update_id=item.update_id,
            attempt_count=item.attempt_count,
            replay_count=item.replay_count,
            last_error_code=item.last_error_code,
            accepted_at=item.accepted_at,
        )


class OutboxDeadLetterContract(_WebContract):
    """One undelivered logical response, without its text.

    Attributes:
        outbox_id: Stable identifier a resend names.
        response_kind: Bounded presentation kind, useful for diagnosis.
        attempt_count: Delivery attempts in the exhausted automatic cycle.
        replay_count: Manual resend cycles already requested.
        last_error_code: Bounded machine-readable reason of the last attempt.
        created_at: Instant the logical response was first committed.
    """

    outbox_id: UUID
    response_kind: str = Field(min_length=1, max_length=100)
    attempt_count: int = Field(ge=0)
    replay_count: int = Field(ge=0)
    last_error_code: str | None
    created_at: AwareDatetime

    @classmethod
    def from_domain(cls, item: OutboxDeadLetter) -> Self:
        """Map one delivery failure without the message it would have sent."""

        return cls(
            outbox_id=item.id,
            response_kind=item.response_kind,
            attempt_count=item.attempt_count,
            replay_count=item.replay_count,
            last_error_code=item.last_error_code,
            created_at=item.created_at,
        )


class MessagingOperationsResponse(_WebContract):
    """Content-free state of the configured bot and both failure queues.

    Nothing here can carry owner content: every value is a count, an age, a
    bounded reason code, or a stable local identifier. The bot key itself is
    also absent, because the browser never selects a scope; only the
    environment it is looking at is worth naming.

    Attributes:
        environment: Bot environment selected by process configuration.
        owner_paired: Whether the sole owner binding exists yet.
        next_offset: Next Bot API update offset owned by the gateway.
        last_poll_at: Instant of the last successful poll, when one happened.
        last_observed_at: Instant of the last observed update batch.
        downtime_exceeds_update_retention: Whether the gap since the last poll
            is longer than the Bot API keeps undelivered updates.
        active_actions: Correlations still offered to the owner.
        inbox: Inbound work queue state.
        outbox: Outbound logical response queue state.
        inbox_dead_letters: Failed work items awaiting an operator decision.
        outbox_dead_letters: Undelivered responses awaiting a decision.
    """

    environment: Literal["production", "test"]
    owner_paired: bool
    next_offset: int = Field(ge=0)
    last_poll_at: AwareDatetime | None
    last_observed_at: AwareDatetime | None
    downtime_exceeds_update_retention: bool
    active_actions: int = Field(ge=0)
    inbox: MessagingQueueContract
    outbox: MessagingQueueContract
    inbox_dead_letters: tuple[InboxDeadLetterContract, ...]
    outbox_dead_letters: tuple[OutboxDeadLetterContract, ...]

    @classmethod
    def from_application(cls, snapshot: MessagingOperationsSnapshot) -> Self:
        """Map the operator snapshot into the control-plane shape."""

        health: MessagingHealth = snapshot.health
        return cls(
            environment=snapshot.environment.value,
            owner_paired=snapshot.owner_paired,
            next_offset=health.next_offset,
            last_poll_at=health.last_poll_at,
            last_observed_at=health.last_observed_at,
            downtime_exceeds_update_retention=(
                snapshot.downtime_exceeds_update_retention
            ),
            active_actions=health.active_actions,
            inbox=MessagingQueueContract(
                pending=health.pending_inbox,
                retrying=health.retrying_inbox,
                dead_letters=health.inbox_dead_letters,
                oldest_at=health.oldest_inbox_at,
            ),
            outbox=MessagingQueueContract(
                pending=health.pending_outbox,
                retrying=health.retrying_outbox,
                dead_letters=health.outbox_dead_letters,
                oldest_at=health.oldest_outbox_at,
            ),
            inbox_dead_letters=tuple(
                InboxDeadLetterContract.from_domain(item)
                for item in snapshot.inbox_dead_letters
            ),
            outbox_dead_letters=tuple(
                OutboxDeadLetterContract.from_domain(item)
                for item in snapshot.outbox_dead_letters
            ),
        )


class MessagingRequeueRequest(_WebContract):
    """Requeue one dead letter from the exact state the page displayed."""

    expected_replay_count: int = Field(ge=0)


class MessagingRequeueResponse(_WebContract):
    """Identity and effect of one accepted requeue.

    Attributes:
        id: Dead letter the request named.
        queue: Durable queue it belongs to.
        outcome: Whether this call requeued it or replayed a done transition.
    """

    id: UUID
    queue: MessagingQueue
    outcome: RequeueOutcome

    @classmethod
    def from_domain(cls, item: RequeuedItem) -> Self:
        """Map the requeue result without exposing queue internals."""

        return cls(id=item.id, queue=item.queue, outcome=item.outcome)


class HealthResponse(_WebContract):
    """Content-free process or dependency health state."""

    status: Literal["healthy", "unavailable"]


class CsrfTokenResponse(_WebContract):
    """Per-browser double-submit value for later mutation requests."""

    token: str


__all__ = [
    "BudgetPositionContract",
    "CategoryProposalContract",
    "CategoryArchiveRequest",
    "CategoryManagementResponse",
    "CategoryMutationResponse",
    "CategoryRenameRequest",
    "CategoryReferenceContract",
    "CategoryReviewDecisionRequest",
    "CategoryReviewQueueResponse",
    "CategoryReviewReuseRequest",
    "CategorySpendContract",
    "CsrfTokenResponse",
    "ExpenseCancelRequest",
    "ExpenseMutationResponse",
    "ExpensePostRequest",
    "ExpenseReplaceRequest",
    "ManagedCategoryContract",
    "FundingClassSplitContract",
    "FundingSourceReferenceContract",
    "FundingSourceSpendContract",
    "HealthResponse",
    "MonthlyBudgetPositionsContract",
    "MonthlyOverviewResponse",
    "PeriodContract",
    "RangeOverviewResponse",
    "ReceiptContract",
    "ReceiptMutationResponse",
    "TaxonomyWarningContract",
    "ReceiptRegisterResponse",
    "ReceiptReviewRequest",
    "ResolvedPeriodContract",
    "TransactionCategoryAllocationContract",
    "TransactionContract",
    "TransactionPaymentAllocationContract",
    "TransactionRegisterResponse",
]
