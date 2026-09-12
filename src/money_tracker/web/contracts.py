"""Strict JSON contracts for the local control-plane API."""

from __future__ import annotations

from typing import Literal, Self, cast
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
    ReceiptMetadata,
    ReceiptStatus,
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
    "ReceiptContract",
    "ReceiptMutationResponse",
    "ReceiptRegisterResponse",
    "ReceiptReviewRequest",
    "TransactionCategoryAllocationContract",
    "TransactionContract",
    "TransactionPaymentAllocationContract",
    "TransactionRegisterResponse",
]
