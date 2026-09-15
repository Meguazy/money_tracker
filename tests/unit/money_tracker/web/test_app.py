"""Tests for observable behavior of the local web adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from money_tracker.application import (
    BudgetPlan,
    BudgetPlanEntry,
    CategoryManagement,
    CategoryReviewQueue,
    CategorySpendEntry,
    FundingSourceManagement,
    ManagedFundingSource,
    MerchantSpendEntry,
    MonthlyBudgetPositions,
    MonthlyOverview,
    PendingCategoryReview,
    PercentageChangeStatus,
    PeriodComparison,
    PeriodSplit,
    ProfileSettings,
    RangeOverview,
    TransactionRegister,
)
from money_tracker.application.ports import IdGenerator
from money_tracker.domain import (
    BalanceMode,
    BudgetStatus,
    Category,
    CategoryAllocation,
    CategoryProposal,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingClassSplit,
    FundingSource,
    FundingSourceStatus,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Profile,
    ReceiptMetadata,
    ReceiptStatus,
    ResolvedPeriod,
    WalletAvailability,
    WalletEntry,
    WalletEntryType,
    YearMonth,
    initial_taxonomy,
    previous_period,
)
from money_tracker.messaging import (
    BotEnvironment,
    DeadLetter,
    MessagingHealth,
    MessagingOperationsSnapshot,
    MessagingQueue,
    MessagingScope,
    OutboxDeadLetter,
    RequeuedItem,
    RequeueOutcome,
)
from money_tracker.persistence.messaging import MessagingConflictError
from money_tracker.web import WebSettings, create_app
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    BudgetWriter,
    CategoryManagementMutationAdapter,
    CategoryManager,
    CategoryReviewMutationAdapter,
    CategoryReviewWriter,
    ExpenseMutationAdapter,
    ExpenseWriter,
    FundingSourceMutationAdapter,
    FundingSourceWriter,
    MessagingOperationsAdapter,
    MessagingOperationsWriter,
    ReceiptReviewMutationAdapter,
    ReceiptReviewWriter,
)

PROFILE_ID = uuid4()
PERIOD = YearMonth(2026, 9)
ORIGIN = "http://localhost:8000"
ROME = ZoneInfo("Europe/Rome")
EXPENSE_ID = UUID("00000000-0000-0000-0000-000000000301")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000201")
RECEIPT_LINE_ID = UUID("00000000-0000-0000-0000-000000000401")
BUDGET_ID = UUID("00000000-0000-0000-0000-000000000501")
RECEIPT_ID = UUID("00000000-0000-0000-0000-000000000601")
RECEIPT_DIGEST = "9f" * 32


@dataclass
class RecordingProfileSettingsReader:
    """In-memory safe settings reader recording the trusted profile scope."""

    requested_profile: UUID | None = None

    def settings(self, *, profile_id: UUID) -> ProfileSettings:
        """Return fixed V1 preferences and one mutable default-source choice."""

        self.requested_profile = profile_id
        source = FundingSource(
            id=SOURCE_ID,
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
            version=3,
        )
        return ProfileSettings(
            profile=Profile(
                profile_id,
                default_funding_source_id=source.id,
            ),
            default_funding_source=source,
            available_default_sources=(source,),
        )


@dataclass
class OverviewReader:
    """Small in-memory implementation of the read-only web port."""

    requested_profile: UUID | None = None
    requested_period: YearMonth | None = None
    requested_range: ResolvedPeriod | None = None

    def range_overview(
        self,
        *,
        profile_id: UUID,
        period: ResolvedPeriod,
    ) -> RangeOverview:
        """Return fixed exact facts over whatever period was resolved."""

        self.requested_profile = profile_id
        self.requested_range = period
        taxonomy = initial_taxonomy()
        food = taxonomy.resolve_active("Spesa alimentare")
        month = period.calendar_month
        return RangeOverview(
            period=period,
            posted_total=Money(5000),
            split=FundingClassSplit(Money(2000), Money(3000), Money(0)),
            sources=(
                (
                    FundingSource(
                        id=SOURCE_ID,
                        profile_id=profile_id,
                        name="Carta",
                        funding_class=FundingClass.PERSONAL_FUNDS,
                        balance_mode=BalanceMode.UNTRACKED,
                        is_default=True,
                    ),
                    Money(2000),
                ),
            ),
            categories=(
                CategorySpendEntry(
                    category=food,
                    parent_name="Alimentazione",
                    amount=Money(5000),
                ),
            ),
            merchants=(
                MerchantSpendEntry(
                    merchant="Mercato Centrale",
                    amount=Money(5000),
                    movement_count=2,
                ),
            ),
            movement_count=2,
            comparison=PeriodComparison(
                current_period=period,
                previous_period=previous_period(period),
                current_total=Money(5000),
                previous_total=Money(4000),
                delta=Money(1000),
                percentage_change_basis_points=2500,
                percentage_change_status=PercentageChangeStatus.DEFINED,
            ),
            budgets=(
                None
                if month is None
                else MonthlyBudgetPositions(
                    period=month,
                    statuses=(
                        (
                            "Spesa alimentare",
                            BudgetStatus(month, Money(8000), Money(5000), Money(3000)),
                        ),
                    ),
                    unbudgeted_category_names=("Abbonamenti",),
                )
            ),
        )

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Return fixed exact facts and record trusted scoping inputs."""

        self.requested_profile = profile_id
        self.requested_period = period
        source = FundingSource(
            id=uuid4(),
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        )
        return MonthlyOverview(
            period=period,
            posted_total=Money(5000),
            split=PeriodSplit(
                period=period,
                split=FundingClassSplit(Money(2000), Money(3000), Money(0)),
                sources=((source, Money(2000)),),
            ),
            budgets=(
                (
                    "Spesa alimentare",
                    BudgetStatus(period, Money(8000), Money(5000), Money(3000)),
                ),
            ),
            unbudgeted_category_names=("Abbonamenti",),
        )

    def transaction_register(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> TransactionRegister:
        """Return one split movement over the real baseline taxonomy."""

        self.requested_profile = profile_id
        self.requested_period = period
        taxonomy = initial_taxonomy()
        food = taxonomy.resolve_active("Spesa alimentare")
        home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")
        source = FundingSource(
            id=SOURCE_ID,
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        )
        return TransactionRegister(
            period=period,
            expenses=(
                Expense(
                    id=EXPENSE_ID,
                    profile_id=profile_id,
                    amount=Money(5000),
                    occurred_at=datetime(2026, 9, 12, 10, 30, tzinfo=ROME),
                    description="Spesa mista",
                    merchant="Mercato",
                    status=ExpenseStatus.DRAFT,
                    category_allocations=(
                        CategoryAllocation(
                            category_id=food.id,
                            amount=Money(3000),
                            provenance="workflow-category-fit:v6",
                            evidence="pesche",
                            receipt_line_id=RECEIPT_LINE_ID,
                        ),
                        CategoryAllocation(
                            category_id=home.id,
                            amount=Money(2000),
                            provenance="owner:web:v1",
                            owner_override=True,
                        ),
                    ),
                    payment_allocations=(
                        PaymentAllocation(
                            funding_source_id=source.id,
                            amount=Money(5000),
                        ),
                    ),
                    source_reference="web:unit",
                    idempotency_key="web:unit",
                ),
            ),
            taxonomy=taxonomy,
            funding_sources=(source,),
        )

    def budget_plan(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> BudgetPlan:
        """Return one configured budget and the categories still available."""

        self.requested_profile = profile_id
        self.requested_period = period
        taxonomy = initial_taxonomy()
        food = taxonomy.resolve_active("Spesa alimentare")
        return BudgetPlan(
            period=period,
            entries=(
                BudgetPlanEntry(
                    budget=MonthlyBudget(
                        id=BUDGET_ID,
                        profile_id=profile_id,
                        category_id=food.id,
                        recurring_target=Money(40000),
                        overrides=((YearMonth(2026, 12), Money(60000)),),
                        version=3,
                    ),
                    category_name=food.name,
                    status=BudgetStatus(
                        period,
                        Money(40000),
                        Money(45000),
                        Money(-5000),
                    ),
                ),
            ),
            budgetable_categories=(taxonomy.resolve_active("Cura personale"),),
        )


@dataclass
class RecordingBudgetWriter:
    """In-memory budget writer that records the translated command."""

    created: MonthlyBudget | None = None
    replaced: tuple[UUID, int, Money, tuple[tuple[YearMonth, Money], ...]] | None = None
    command_keys: list[str] = field(default_factory=list)

    def create_budget(
        self,
        budget: MonthlyBudget,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Record the aggregate the adapter built from a browser request."""

        del actor, reason
        self.created = budget
        self.command_keys.append(idempotency_key)
        return budget

    def replace_budget(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        recurring_target: Money,
        overrides: tuple[tuple[YearMonth, Money], ...],
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Record the replacement plan and return the advanced aggregate."""

        del actor, reason
        self.replaced = (category_id, expected_version, recurring_target, overrides)
        self.command_keys.append(idempotency_key)
        return MonthlyBudget(
            id=BUDGET_ID,
            profile_id=profile_id,
            category_id=category_id,
            recurring_target=recurring_target,
            overrides=overrides,
            version=expected_version + 1,
        )


@dataclass
class RecordingFundingSourceWriter:
    """In-memory writer recording browser-translated source commands."""

    source: FundingSource = field(
        default_factory=lambda: FundingSource(
            id=SOURCE_ID,
            profile_id=PROFILE_ID,
            name="Buoni pasto",
            funding_class=FundingClass.BENEFIT,
            balance_mode=BalanceMode.LEDGER,
            version=2,
        )
    )
    entries: list[WalletEntry] = field(default_factory=list)
    actions: list[tuple[object, ...]] = field(default_factory=list)

    def management(self, *, profile_id: UUID) -> FundingSourceManagement:
        """Return one tracked wallet for the trusted owner."""

        assert profile_id == PROFILE_ID
        availability = WalletAvailability(
            booked_balance=Money(3000),
            available_balance=Money(1800),
            expiring=(),
        )
        return FundingSourceManagement(
            sources=(
                ManagedFundingSource(
                    source=self.source,
                    entries=tuple(self.entries),
                    availability=availability,
                ),
            ),
            observed_at=datetime(2026, 9, 14, 10, tzinfo=UTC),
        )

    def create(
        self,
        source: FundingSource,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Record a source creation translated by the browser adapter."""

        assert actor == "owner:web"
        self.actions.append(("create", source, idempotency_key, reason))
        self.source = source
        return source

    def rename(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        name: str,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Record a versioned rename."""

        assert profile_id == PROFILE_ID and actor == "owner:web"
        self.actions.append(
            ("rename", source_id, expected_version, name, idempotency_key, reason)
        )
        self.source = replace(self.source, name=name, version=expected_version + 1)
        return self.source

    def archive(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Record a versioned non-destructive archive."""

        assert profile_id == PROFILE_ID and actor == "owner:web"
        self.actions.append(
            ("archive", source_id, expected_version, idempotency_key, reason)
        )
        self.source = replace(
            self.source,
            status=FundingSourceStatus.ARCHIVED,
            version=expected_version + 1,
        )
        return self.source

    def set_default(
        self,
        *,
        profile_id: UUID,
        source_id: UUID,
        expected_version: int,
        expected_previous_default_id: UUID | None,
        expected_previous_default_version: int | None,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> FundingSource:
        """Record the complete optimistic default snapshot."""

        assert profile_id == PROFILE_ID and actor == "owner:web"
        self.actions.append(
            (
                "default",
                source_id,
                expected_version,
                expected_previous_default_id,
                expected_previous_default_version,
                idempotency_key,
                reason,
            )
        )
        self.source = replace(
            self.source,
            is_default=True,
            version=expected_version + 1,
        )
        return self.source

    def add_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> WalletEntry:
        """Record one immutable entry built by the browser adapter."""

        assert actor == "owner:web"
        self.actions.append(("wallet", entry, idempotency_key, reason))
        self.entries.append(entry)
        return entry


@dataclass
class RecordingCategoryManager:
    """In-memory category manager exposing browser-selected command facts."""

    category: Category = field(default_factory=lambda: initial_taxonomy().categories[1])
    renames: list[tuple[UUID, int, str, str]] = field(default_factory=list)
    archives: list[tuple[UUID, int, str]] = field(default_factory=list)

    def management(self, *, profile_id: UUID) -> CategoryManagement:
        """Return one stable category for the trusted owner."""

        assert profile_id == PROFILE_ID
        return CategoryManagement(categories=(self.category,), warnings=())

    def rename(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        name: str,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> Category:
        """Record trusted scope and return an advanced display version."""

        assert profile_id == PROFILE_ID
        assert actor == "owner:web"
        assert reason == "owner renamed category in browser"
        self.renames.append((category_id, expected_version, name, idempotency_key))
        self.category = replace(
            self.category,
            name=name,
            version=expected_version + 1,
        )
        return self.category

    def archive(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> Category:
        """Record trusted scope and return an archived stable node."""

        assert profile_id == PROFILE_ID
        assert actor == "owner:web"
        assert reason == "owner archived category in browser"
        self.archives.append((category_id, expected_version, idempotency_key))
        self.category = replace(
            self.category,
            status=CategoryStatus.ARCHIVED,
            version=expected_version + 1,
        )
        return self.category


@dataclass
class RecordingCategoryReviewWriter:
    """In-memory review writer recording only browser-selected decisions."""

    queue: CategoryReviewQueue
    actions: list[tuple[str, UUID, str | None]] = field(default_factory=list)

    def pending_reviews(self, *, profile_id: UUID) -> CategoryReviewQueue:
        """Return the fixed trusted-profile review queue."""

        assert profile_id == PROFILE_ID
        return self.queue

    def review_expense(self, *, profile_id: UUID, proposal_id: UUID) -> Expense:
        """Resolve the movement without accepting it from the request."""

        assert profile_id == PROFILE_ID
        review = next(
            item for item in self.queue.reviews if item.proposal.id == proposal_id
        )
        return review.expense

    def approve(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record approval and return the resulting posted movement."""

        del profile_id, actor, reason
        self.actions.append(("approve", proposal_id, None))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.POSTED, version=expense.version + 2
        )

    def use_existing(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        category_name: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record reuse and return the resulting posted movement."""

        del profile_id, actor, reason
        self.actions.append(("use_existing", proposal_id, category_name))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.POSTED, version=expense.version + 2
        )

    def cancel(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Record cancellation and return the discarded movement."""

        del profile_id, actor, reason
        self.actions.append(("cancel", proposal_id, None))
        expense = self.review_expense(profile_id=PROFILE_ID, proposal_id=proposal_id)
        assert expense.id == expense_id
        return replace(
            expense, status=ExpenseStatus.DISCARDED, version=expense.version + 1
        )


def _category_review_writer() -> RecordingCategoryReviewWriter:
    """Build one pending proposal over the real baseline taxonomy."""

    taxonomy = initial_taxonomy()
    expense = (
        OverviewReader()
        .transaction_register(profile_id=PROFILE_ID, period=PERIOD)
        .expenses[0]
    )
    proposal = CategoryProposal(
        id=uuid4(),
        profile_id=PROFILE_ID,
        proposed_name="Cura del giardino",
        description="Piante, terriccio e attrezzi per il giardino.",
        parent_id=taxonomy.resolve_active("Casa e utenze").id,
        rationale="Le categorie attive non distinguono questa finalità.",
        alternative_category_ids=(taxonomy.resolve_active("Acquisti personali").id,),
        excluded_names=("Giardinaggio",),
        source_reference=expense.source_reference,
    )
    return RecordingCategoryReviewWriter(
        CategoryReviewQueue(
            reviews=(PendingCategoryReview(proposal, expense),),
            taxonomy=taxonomy,
        )
    )


@dataclass
class RecordingReceiptReviewWriter:
    """In-memory receipt reader and review writer without a media store."""

    transitions: list[tuple[str, UUID, int]] = field(default_factory=list)
    status: ReceiptStatus = ReceiptStatus.STORED
    version: int = 1

    def _metadata(self) -> ReceiptMetadata:
        """Build the single retained receipt at its current review state."""

        return ReceiptMetadata(
            id=RECEIPT_ID,
            profile_id=PROFILE_ID,
            content_sha256=RECEIPT_DIGEST,
            media_type="image/jpeg",
            byte_size=204800,
            stored_at=datetime(2026, 9, 12, 18, 5, tzinfo=UTC),
            source_reference="local-admin:receipt:unit",
            status=self.status,
            original_filename="scontrino-mercato.jpg",
            version=self.version,
        )

    def stored_receipts(self, *, profile_id: UUID) -> tuple[ReceiptMetadata, ...]:
        """Return the fixed archive for the trusted profile only."""

        assert profile_id == PROFILE_ID
        return (self._metadata(),)

    def mark_needs_review(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Record the requested transition and advance the review state."""

        del profile_id, actor, reason
        self.transitions.append(("mark_needs_review", receipt_id, expected_version))
        self.status = ReceiptStatus.NEEDS_REVIEW
        self.version = expected_version + 1
        return self._metadata()

    def approve(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Record explicit approval and advance the review state."""

        del profile_id, actor, reason
        self.transitions.append(("approve", receipt_id, expected_version))
        self.status = ReceiptStatus.APPROVED
        self.version = expected_version + 1
        return self._metadata()


INBOX_ID = UUID("00000000-0000-0000-0000-0000000007d1")
OUTBOX_ID = UUID("00000000-0000-0000-0000-0000000007d2")
BOT_SCOPE = MessagingScope.for_environment(BotEnvironment.TEST)


@dataclass
class RecordingMessagingOperations:
    """In-memory durable messaging state without a Bot API transport."""

    requeues: list[tuple[str, UUID, int | None]] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    owner_paired: bool = True
    inbox_replay_count: int = 1
    outbox_replay_count: int = 0
    conflict: bool = False

    def operations_snapshot(
        self,
        bot_key: str,
        *,
        environment: BotEnvironment,
    ) -> MessagingOperationsSnapshot:
        """Return fixed content-free state and record the trusted scope."""

        self.scopes.append(bot_key)
        return MessagingOperationsSnapshot(
            environment=environment,
            owner_paired=self.owner_paired,
            health=MessagingHealth(
                bot_key=bot_key,
                next_offset=412,
                pending_inbox=2,
                retrying_inbox=1,
                inbox_dead_letters=1,
                pending_outbox=0,
                retrying_outbox=0,
                outbox_dead_letters=1,
                active_actions=3,
                last_observed_at=datetime(2026, 9, 12, 17, 55, tzinfo=UTC),
                last_poll_at=datetime(2026, 9, 12, 18, tzinfo=UTC),
                oldest_inbox_at=datetime(2026, 9, 12, 17, 40, tzinfo=UTC),
                oldest_outbox_at=None,
            ),
            downtime_exceeds_update_retention=False,
            inbox_dead_letters=(
                DeadLetter(
                    id=INBOX_ID,
                    update_id=907,
                    source_reference="telegram:telegram-test:907",
                    attempt_count=5,
                    replay_count=self.inbox_replay_count,
                    last_error_code="model_unavailable",
                    accepted_at=datetime(2026, 9, 12, 17, 40, tzinfo=UTC),
                ),
            ),
            outbox_dead_letters=(
                OutboxDeadLetter(
                    id=OUTBOX_ID,
                    response_kind="draft",
                    attempt_count=5,
                    replay_count=self.outbox_replay_count,
                    last_error_code="telegram_unavailable",
                    created_at=datetime(2026, 9, 12, 17, 45, tzinfo=UTC),
                ),
            ),
        )

    def requeue_inbox(
        self,
        bot_key: str,
        inbox_id: UUID,
        *,
        expected_replay_count: int | None,
    ) -> RequeuedItem:
        """Record one pinned inbox requeue inside the trusted scope."""

        return self._requeue(
            bot_key,
            inbox_id,
            expected_replay_count,
            queue=MessagingQueue.INBOX,
        )

    def requeue_outbox(
        self,
        bot_key: str,
        outbox_id: UUID,
        *,
        expected_replay_count: int | None,
    ) -> RequeuedItem:
        """Record one pinned outbox resend inside the trusted scope."""

        return self._requeue(
            bot_key,
            outbox_id,
            expected_replay_count,
            queue=MessagingQueue.OUTBOX,
        )

    def _requeue(
        self,
        bot_key: str,
        item_id: UUID,
        expected_replay_count: int | None,
        *,
        queue: MessagingQueue,
    ) -> RequeuedItem:
        """Apply the configured outcome and record the exact request."""

        self.requeues.append((bot_key, item_id, expected_replay_count))
        if self.conflict:
            raise MessagingConflictError("only a dead letter can be requeued")
        return RequeuedItem(
            id=item_id,
            queue=queue,
            outcome=RequeueOutcome.APPLIED,
        )


class FixedIds:
    """Stable identifier source for adapter composition in boundary tests."""

    def new(self) -> UUID:
        """Return a deterministic valid identifier."""

        return UUID("00000000-0000-0000-0000-000000000999")


def _app(
    reader: OverviewReader | None = None,
    *,
    static_dir: Path | None = None,
    ready: bool = True,
    budget_writer: BudgetWriter | None = None,
    category_manager: CategoryManager | None = None,
    funding_source_writer: FundingSourceWriter | None = None,
    category_review_writer: CategoryReviewWriter | None = None,
    receipt_writer: ReceiptReviewWriter | None = None,
    messaging_operations: MessagingOperationsWriter | None = None,
    settings_reader: RecordingProfileSettingsReader | None = None,
) -> FastAPI:
    """Compose an application with real middleware and an in-memory read port."""

    return create_app(
        settings=WebSettings(static_dir=static_dir),
        profile_id=PROFILE_ID,
        reporting=reader or OverviewReader(),
        profile_settings=settings_reader or RecordingProfileSettingsReader(),
        mutations=ExpenseMutationAdapter(
            cast(ExpenseWriter, object()),
            ids=cast(IdGenerator, FixedIds()),
        ),
        budgets=BudgetMutationAdapter(
            budget_writer or cast(BudgetWriter, object()),
            ids=cast(IdGenerator, FixedIds()),
        ),
        categories=CategoryManagementMutationAdapter(
            category_manager or cast(CategoryManager, object())
        ),
        funding_sources=FundingSourceMutationAdapter(
            funding_source_writer or cast(FundingSourceWriter, object()),
            ids=cast(IdGenerator, FixedIds()),
        ),
        category_reviews=CategoryReviewMutationAdapter(
            category_review_writer or cast(CategoryReviewWriter, object())
        ),
        receipts=ReceiptReviewMutationAdapter(
            receipt_writer or RecordingReceiptReviewWriter()
        ),
        messaging=MessagingOperationsAdapter(
            messaging_operations or RecordingMessagingOperations(),
            scope=BOT_SCOPE,
        ),
        readiness_check=lambda: ready,
    )


def _client(app: FastAPI) -> TestClient:
    """Use an allowed authority so only intentional policy failures vary."""

    return TestClient(app, base_url=ORIGIN)


def test_profile_settings_expose_only_safe_v1_preferences_in_trusted_scope() -> None:
    """Keep fixed preferences read-only and omit browser-controlled identity."""

    reader = RecordingProfileSettingsReader()
    client = _client(_app(settings_reader=reader))

    response = client.get(f"/api/v1/settings?profile_id={uuid4()}")

    assert response.status_code == 200
    assert response.json() == {
        "locale": "it-IT",
        "timezone": "Europe/Rome",
        "currency": "EUR",
        "default_funding_source": {
            "id": str(SOURCE_ID),
            "name": "Carta",
            "funding_class": "personal_funds",
            "version": 3,
        },
        "available_default_sources": [
            {
                "id": str(SOURCE_ID),
                "name": "Carta",
                "funding_class": "personal_funds",
                "version": 3,
            }
        ],
    }
    assert reader.requested_profile == PROFILE_ID
    assert "profile_id" not in response.text
    assert "token" not in response.text
    assert "database" not in response.text


def test_profile_settings_have_no_direct_mutation_route() -> None:
    """Prevent browser writes to fixed locale, timezone, or currency fields."""

    client = _client(_app())
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.put(
        "/api/v1/settings",
        json={"locale": "en-US", "timezone": "UTC", "currency": "USD"},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "settings-fixed-fields",
        },
    )

    assert response.status_code == 405


def test_monthly_overview_returns_exact_minor_units_in_the_trusted_profile() -> None:
    """Expose deterministic facts without accepting owner identity from input."""

    reader = OverviewReader()

    response = _client(_app(reader)).get("/api/v1/overview/monthly?period=2026-09")

    assert response.status_code == 200
    assert response.json() == {
        "period": {"year": 2026, "month": 9},
        "posted_total": {"minor_units": 5000, "currency": "EUR"},
        "funding_split": {
            "personal_funds": {"minor_units": 2000, "currency": "EUR"},
            "benefit": {"minor_units": 3000, "currency": "EUR"},
            "other": {"minor_units": 0, "currency": "EUR"},
        },
        "funding_sources": [
            {
                "name": "Carta",
                "funding_class": "personal_funds",
                "amount": {"minor_units": 2000, "currency": "EUR"},
            }
        ],
        "budgets": [
            {
                "category_name": "Spesa alimentare",
                "target": {"minor_units": 8000, "currency": "EUR"},
                "posted_spend": {"minor_units": 5000, "currency": "EUR"},
                "remaining": {"minor_units": 3000, "currency": "EUR"},
            }
        ],
        "unbudgeted_categories": ["Abbonamenti"],
    }
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_period == PERIOD
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["content-security-policy"].startswith("default-src")
    assert response.headers["cache-control"] == "no-store"


def test_transaction_register_uses_trusted_scope_and_safe_references() -> None:
    """Expose editor references without returning profile or channel metadata."""

    reader = OverviewReader()

    response = _client(_app(reader)).get(
        "/api/v1/transactions?period=2026-09",
    )

    assert response.status_code == 200
    assert response.json()["period"] == {"year": 2026, "month": 9}
    assert response.json()["categories"]
    assert [source["name"] for source in response.json()["funding_sources"]] == [
        "Carta"
    ]
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_period == PERIOD
    assert "profile_id" not in response.text
    assert "source_reference" not in response.text


def test_split_allocations_keep_exact_cents_and_their_receipt_line() -> None:
    """Give the editor every allocation with the metadata a correction must keep."""

    taxonomy = initial_taxonomy()
    home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")

    response = _client(_app()).get("/api/v1/transactions?period=2026-09")

    transaction = response.json()["transactions"][0]
    assert transaction["amount"] == {"minor_units": 5000, "currency": "EUR"}
    assert transaction["category_allocations"] == [
        {
            "category_id": str(taxonomy.resolve_active("Spesa alimentare").id),
            "category_name": "Spesa alimentare",
            "amount": {"minor_units": 3000, "currency": "EUR"},
            "provenance": "workflow-category-fit:v6",
            "evidence": "pesche",
            "owner_override": False,
            "receipt_line_id": str(RECEIPT_LINE_ID),
        },
        {
            "category_id": str(home.id),
            "category_name": "Prodotti casa, arredo e manutenzione",
            "amount": {"minor_units": 2000, "currency": "EUR"},
            "provenance": "owner:web:v1",
            "evidence": None,
            "owner_override": True,
            "receipt_line_id": None,
        },
    ]
    assert transaction["payment_allocations"] == [
        {
            "funding_source_id": str(SOURCE_ID),
            "funding_source_name": "Carta",
            "amount": {"minor_units": 5000, "currency": "EUR"},
        }
    ]


def test_category_management_reads_renames_and_archives_trusted_scope() -> None:
    """Expose versioned nodes and forward only strict owner commands."""

    manager = RecordingCategoryManager()
    client = _client(_app(category_manager=manager))
    listed = client.get("/api/v1/categories")

    assert listed.status_code == 200
    assert listed.json()["categories"][0]["id"] == str(manager.category.id)
    assert listed.json()["categories"][0]["version"] == 1

    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    headers = {
        "origin": ORIGIN,
        "x-csrf-token": issued.json()["token"],
        "Idempotency-Key": "category-unit-rename",
    }
    category_id = manager.category.id
    renamed = client.put(
        f"/api/v1/categories/{category_id}",
        json={"name": "Nome aggiornato", "expected_version": 1},
        headers=headers,
    )

    assert renamed.status_code == 200
    assert renamed.json()["version"] == 2
    assert manager.renames == [
        (category_id, 1, "Nome aggiornato", "category-unit-rename")
    ]

    headers["Idempotency-Key"] = "category-unit-archive"
    archived = client.post(
        f"/api/v1/categories/{category_id}/archive",
        json={"expected_version": 2},
        headers=headers,
    )

    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert manager.archives == [(category_id, 2, "category-unit-archive")]


def test_category_rename_rejects_unknown_json_fields() -> None:
    """Keep profile and unsupported taxonomy changes outside the body."""

    manager = RecordingCategoryManager()
    client = _client(_app(category_manager=manager))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    response = client.put(
        f"/api/v1/categories/{manager.category.id}",
        json={"name": "Altro", "expected_version": 1, "parent_id": str(uuid4())},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "category-unit-invalid",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert manager.renames == []


def test_category_proposals_expose_review_facts_without_workflow_identity() -> None:
    """Show the candidate, movement, and deterministic active choices safely."""

    writer = _category_review_writer()

    response = _client(_app(category_review_writer=writer)).get(
        "/api/v1/category-proposals"
    )

    assert response.status_code == 200
    proposal = response.json()["proposals"][0]
    assert proposal["id"] == str(writer.queue.reviews[0].proposal.id)
    assert proposal["proposed_name"] == "Cura del giardino"
    assert proposal["parent_name"] == "Casa e utenze"
    assert proposal["alternative_categories"][0]["name"] == "Acquisti personali"
    assert proposal["excluded_names"] == ["Giardinaggio"]
    assert proposal["can_repropose"] is True
    assert proposal["expense"]["id"] == str(writer.queue.reviews[0].expense.id)
    assert proposal["expense"]["amount"] == {
        "minor_units": 5000,
        "currency": "EUR",
    }
    assert "source_reference" not in response.text
    assert "profile_id" not in response.text


def test_category_approval_uses_only_the_proposal_wide_command_identity() -> None:
    """Resolve movement identity server-side and replay through the service key."""

    writer = _category_review_writer()
    proposal_id = writer.queue.reviews[0].proposal.id
    client = _client(_app(category_review_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/category-proposals/{proposal_id}/approve",
        json={},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"category-review:{proposal_id}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(writer.queue.reviews[0].expense.id),
        "status": "posted",
        "version": 3,
    }
    assert writer.actions == [("approve", proposal_id, None)]


def test_category_reuse_validates_strict_json_and_the_proposal_key() -> None:
    """Reject browser-controlled identity and pass only an active display name."""

    writer = _category_review_writer()
    proposal_id = writer.queue.reviews[0].proposal.id
    client = _client(_app(category_review_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    headers = {
        "origin": ORIGIN,
        "x-csrf-token": issued.json()["token"],
        "Idempotency-Key": f"category-review:{proposal_id}",
    }

    invalid = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={
            "category_name": "Acquisti personali",
            "expense_id": str(uuid4()),
        },
        headers=headers,
    )
    wrong_key = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={"category_name": "Acquisti personali"},
        headers={**headers, "Idempotency-Key": "different-key"},
    )
    accepted = client.post(
        f"/api/v1/category-proposals/{proposal_id}/use-existing",
        json={"category_name": "Acquisti personali"},
        headers=headers,
    )

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "request_invalid"
    assert wrong_key.status_code == 409
    assert wrong_key.json()["code"] == "category_review_conflict"
    assert accepted.status_code == 200
    assert writer.actions == [("use_existing", proposal_id, "Acquisti personali")]


def test_invalid_period_returns_a_bounded_problem() -> None:
    """Reject ambiguous dates without invoking the application reader."""

    reader = OverviewReader()
    response = _client(_app(reader)).get("/api/v1/overview/monthly?period=09-2026")

    assert response.status_code == 422
    assert response.json()["code"] == "period_invalid"
    assert reader.requested_period is None


def test_health_distinguishes_liveness_from_dependency_readiness() -> None:
    """Keep process health observable while PostgreSQL is unavailable."""

    client = _client(_app(ready=False))

    assert client.get("/api/v1/health/live").status_code == 200
    readiness = client.get("/api/v1/health/ready")
    assert readiness.status_code == 503
    assert readiness.json()["code"] == "database_unavailable"


def test_host_and_origin_allowlists_block_browser_boundary_bypass() -> None:
    """Reject DNS rebinding and cross-origin reads before routing."""

    client = _client(_app())

    bad_host = client.get("/api/v1/health/live", headers={"host": "evil.test"})
    bad_origin = client.get(
        "/api/v1/health/live",
        headers={"origin": "http://evil.test"},
    )

    assert bad_host.status_code == 403
    assert bad_host.json()["code"] == "host_not_allowed"
    assert bad_origin.status_code == 403
    assert bad_origin.json()["code"] == "origin_not_allowed"


def test_unsafe_methods_require_matching_origin_cookie_and_csrf_header() -> None:
    """Enforce the double-submit contract even on later mutation routes."""

    app = _app()

    @app.post("/api/v1/test-mutation")
    def mutation() -> dict[str, bool]:
        """Provide a test-only unsafe route behind production middleware."""

        return {"accepted": True}

    client = _client(app)
    rejected = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN},
    )
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    accepted = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN, "x-csrf-token": issued.json()["token"]},
    )

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_validation_failed"
    assert issued.headers["cache-control"] == "no-store"
    assert accepted.json() == {"accepted": True}


def test_expense_mutation_rejects_unknown_json_without_adapter_dispatch() -> None:
    """Keep strict parser details behind a bounded owner-facing problem."""

    client = _client(_app())
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    response = client.post(
        "/api/v1/expenses",
        json={"unexpected": "field"},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-invalid-body",
        },
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": "Richiesta non valida",
        "status": 422,
        "detail": "Controlla i campi JSON della richiesta.",
        "code": "request_invalid",
        "instance": "/api/v1/expenses",
    }


def test_compiled_dashboard_is_served_from_the_api_origin(tmp_path: Path) -> None:
    """Serve one build while keeping source maps and arbitrary files private."""

    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<main>Money Tracker</main>")

    client = _client(_app(static_dir=static_dir))
    response = client.get("/")
    links = {
        route: client.get(route)
        for route in (
            "/analisi",
            "/movimenti",
            "/categorie",
            "/budget",
            "/scontrini",
            "/messaggistica",
            "/impostazioni",
        )
    }

    assert response.status_code == 200
    assert "Money Tracker" in response.text
    assert all(link.status_code == 200 for link in links.values())
    assert all(link.text == response.text for link in links.values())


def test_budget_plan_exposes_targets_overrides_and_the_correction_version() -> None:
    """Give the planner what a replacement must quote, and nothing more."""

    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")

    response = _client(_app()).get("/api/v1/budgets?period=2026-09")

    assert response.status_code == 200
    assert response.json()["period"] == {"year": 2026, "month": 9}
    assert response.json()["entries"] == [
        {
            "category_id": str(food.id),
            "category_name": "Spesa alimentare",
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "overrides": [
                {
                    "period": {"year": 2026, "month": 12},
                    "target": {"minor_units": 60000, "currency": "EUR"},
                }
            ],
            "target": {"minor_units": 40000, "currency": "EUR"},
            "posted_spend": {"minor_units": 45000, "currency": "EUR"},
            "remaining": {"minor_units": -5000, "currency": "EUR"},
            "version": 3,
        }
    ]
    assert [item["name"] for item in response.json()["budgetable_categories"]] == [
        "Cura personale"
    ]
    assert "profile_id" not in response.text
    assert str(BUDGET_ID) not in response.text


def test_budget_creation_injects_trusted_scope_and_exact_targets() -> None:
    """Build the aggregate from trusted context, never from request identity."""

    writer = RecordingBudgetWriter()
    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(food.id),
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "overrides": [
                {
                    "period": {"year": 2026, "month": 12},
                    "target": {"minor_units": 60000, "currency": "EUR"},
                }
            ],
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-create",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"category_id": str(food.id), "version": 1}
    assert writer.created is not None
    assert writer.created.profile_id == PROFILE_ID
    assert writer.created.recurring_target == Money(40000)
    assert writer.created.overrides == ((YearMonth(2026, 12), Money(60000)),)
    assert writer.command_keys == ["unit-budget-create"]


def test_budget_replacement_quotes_the_observed_version() -> None:
    """Send the complete plan and the version the browser actually saw."""

    writer = RecordingBudgetWriter()
    taxonomy = initial_taxonomy()
    food = taxonomy.resolve_active("Spesa alimentare")
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.put(
        f"/api/v1/budgets/{food.id}",
        json={
            "recurring_target": {"minor_units": 50000, "currency": "EUR"},
            "overrides": [],
            "expected_version": 3,
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-replace",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"category_id": str(food.id), "version": 4}
    assert writer.replaced == (food.id, 3, Money(50000), ())


def test_budget_mutation_rejects_a_profile_field_in_the_request_body() -> None:
    """Refuse owner identity from the browser instead of silently ignoring it."""

    writer = RecordingBudgetWriter()
    client = _client(_app(budget_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(uuid4()),
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "profile_id": str(uuid4()),
        },
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": "unit-budget-unknown-field",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert writer.created is None


def test_receipt_register_exposes_safe_facts_without_the_archive_key() -> None:
    """Give the owner display metadata and the version a decision must quote."""

    response = _client(_app()).get("/api/v1/receipts")

    assert response.status_code == 200
    assert response.json() == {
        "receipts": [
            {
                "id": str(RECEIPT_ID),
                "media_type": "image/jpeg",
                "byte_size": 204800,
                "stored_at": "2026-09-12T18:05:00Z",
                "status": "stored",
                "original_filename": "scontrino-mercato.jpg",
                "version": 1,
            }
        ]
    }
    assert RECEIPT_DIGEST not in response.text
    assert "profile_id" not in response.text
    assert "source_reference" not in response.text


def test_receipt_review_requires_the_transition_wide_command_key() -> None:
    """Bind each HTTP retry to the receipt and the version the page displayed."""

    writer = RecordingReceiptReviewWriter()
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    refused = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/review",
        json={"expected_version": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}",
        },
    )
    accepted = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/review",
        json={"expected_version": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:1",
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "receipt_conflict"
    assert accepted.status_code == 200
    assert accepted.json() == {
        "id": str(RECEIPT_ID),
        "status": "needs_review",
        "version": 2,
    }
    assert writer.transitions == [("mark_needs_review", RECEIPT_ID, 1)]


def test_receipt_approval_quotes_the_version_the_browser_observed() -> None:
    """Advance mandatory review only from the state the owner actually saw."""

    writer = RecordingReceiptReviewWriter(
        status=ReceiptStatus.NEEDS_REVIEW,
        version=2,
    )
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/approve",
        json={"expected_version": 2},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(RECEIPT_ID),
        "status": "approved",
        "version": 3,
    }
    assert writer.transitions == [("approve", RECEIPT_ID, 2)]


def test_receipt_review_rejects_an_unknown_field_without_transitioning() -> None:
    """Refuse a body that names anything but the observed version."""

    writer = RecordingReceiptReviewWriter()
    client = _client(_app(receipt_writer=writer))
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})

    response = client.post(
        f"/api/v1/receipts/{RECEIPT_ID}/approve",
        json={"expected_version": 1, "content_sha256": RECEIPT_DIGEST},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": issued.json()["token"],
            "Idempotency-Key": f"receipt-review:{RECEIPT_ID}:1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert writer.transitions == []


def test_messaging_state_is_content_free_and_names_no_bot_key() -> None:
    """Give the operator counters and failures, never owner text or a scope."""

    operations = RecordingMessagingOperations()

    response = _client(_app(messaging_operations=operations)).get("/api/v1/messaging")

    assert response.status_code == 200
    assert response.json() == {
        "environment": "test",
        "owner_paired": True,
        "next_offset": 412,
        "last_poll_at": "2026-09-12T18:00:00Z",
        "last_observed_at": "2026-09-12T17:55:00Z",
        "downtime_exceeds_update_retention": False,
        "active_actions": 3,
        "inbox": {
            "pending": 2,
            "retrying": 1,
            "dead_letters": 1,
            "oldest_at": "2026-09-12T17:40:00Z",
        },
        "outbox": {
            "pending": 0,
            "retrying": 0,
            "dead_letters": 1,
            "oldest_at": None,
        },
        "inbox_dead_letters": [
            {
                "inbox_id": str(INBOX_ID),
                "update_id": 907,
                "attempt_count": 5,
                "replay_count": 1,
                "last_error_code": "model_unavailable",
                "accepted_at": "2026-09-12T17:40:00Z",
            }
        ],
        "outbox_dead_letters": [
            {
                "outbox_id": str(OUTBOX_ID),
                "response_kind": "draft",
                "attempt_count": 5,
                "replay_count": 0,
                "last_error_code": "telegram_unavailable",
                "created_at": "2026-09-12T17:45:00Z",
            }
        ],
    }
    assert "bot_key" not in response.text
    assert "source_reference" not in response.text
    assert "telegram-test" not in response.text
    assert operations.scopes == ["telegram-test"]


def test_requeue_requires_the_transition_wide_command_key() -> None:
    """Bind each requeue to the queue, the item, and the observed count."""

    operations = RecordingMessagingOperations()
    client = _client(_app(messaging_operations=operations))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    refused = client.post(
        f"/api/v1/messaging/inbox/{INBOX_ID}/replay",
        json={"expected_replay_count": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-replay:{INBOX_ID}",
        },
    )
    accepted = client.post(
        f"/api/v1/messaging/inbox/{INBOX_ID}/replay",
        json={"expected_replay_count": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-replay:{INBOX_ID}:1",
        },
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "messaging_conflict"
    assert accepted.status_code == 200
    assert accepted.json() == {
        "id": str(INBOX_ID),
        "queue": "inbox",
        "outcome": "applied",
    }
    assert operations.requeues == [("telegram-test", INBOX_ID, 1)]


def test_a_resend_uses_its_own_command_family_and_trusted_scope() -> None:
    """Keep an outbox key from ever naming an inbox item, or another bot."""

    operations = RecordingMessagingOperations()
    client = _client(_app(messaging_operations=operations))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    crossed = client.post(
        f"/api/v1/messaging/outbox/{OUTBOX_ID}/resend",
        json={"expected_replay_count": 0},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-replay:{OUTBOX_ID}:0",
        },
    )
    accepted = client.post(
        f"/api/v1/messaging/outbox/{OUTBOX_ID}/resend",
        json={"expected_replay_count": 0},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-resend:{OUTBOX_ID}:0",
        },
    )

    assert crossed.status_code == 409
    assert accepted.status_code == 200
    assert accepted.json()["queue"] == "outbox"
    assert operations.requeues == [("telegram-test", OUTBOX_ID, 0)]


def test_a_requeue_refused_by_messaging_is_one_bounded_conflict() -> None:
    """Never let a missing, foreign, or progressed item be told apart."""

    operations = RecordingMessagingOperations(conflict=True)
    client = _client(_app(messaging_operations=operations))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    response = client.post(
        f"/api/v1/messaging/inbox/{INBOX_ID}/replay",
        json={"expected_replay_count": 1},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-replay:{INBOX_ID}:1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "type": "about:blank",
        "title": "Coda già aggiornata",
        "status": 409,
        "detail": "Ricarica lo stato della messaggistica prima di riprovare.",
        "code": "messaging_conflict",
        "instance": f"/api/v1/messaging/inbox/{INBOX_ID}/replay",
    }


def test_a_requeue_body_cannot_name_a_bot_or_an_offset() -> None:
    """Refuse any field beyond the state the page displayed."""

    operations = RecordingMessagingOperations()
    client = _client(_app(messaging_operations=operations))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    response = client.post(
        f"/api/v1/messaging/inbox/{INBOX_ID}/replay",
        json={"expected_replay_count": 1, "bot_key": "telegram-production"},
        headers={
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": f"messaging-replay:{INBOX_ID}:1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"
    assert operations.requeues == []


def test_range_overview_returns_both_dimensions_over_a_resolved_week() -> None:
    """Answer a wider period without exposing profile identity or a budget."""

    reader = OverviewReader()

    response = _client(_app(reader)).get(
        "/api/v1/overview/range?granularity=week&day=2025-12-31",
    )

    assert response.status_code == 200
    assert response.json() == {
        "period": {
            "granularity": "week",
            "start": "2025-12-29",
            "end": "2026-01-04",
            "day_count": 7,
            "iso_year": 2026,
            "iso_week": 1,
        },
        "posted_total": {"minor_units": 5000, "currency": "EUR"},
        "funding_split": {
            "personal_funds": {"minor_units": 2000, "currency": "EUR"},
            "benefit": {"minor_units": 3000, "currency": "EUR"},
            "other": {"minor_units": 0, "currency": "EUR"},
        },
        "funding_sources": [
            {
                "name": "Carta",
                "funding_class": "personal_funds",
                "amount": {"minor_units": 2000, "currency": "EUR"},
            }
        ],
        "categories": [
            {
                "category_id": str(
                    initial_taxonomy().resolve_active("Spesa alimentare").id
                ),
                "category_name": "Spesa alimentare",
                "parent_name": "Alimentazione",
                "amount": {"minor_units": 5000, "currency": "EUR"},
            }
        ],
        "merchants": [
            {
                "merchant": "Mercato Centrale",
                "amount": {"minor_units": 5000, "currency": "EUR"},
                "movement_count": 2,
            }
        ],
        "movement_count": 2,
        "comparison": {
            "current_period": {
                "granularity": "week",
                "start": "2025-12-29",
                "end": "2026-01-04",
                "day_count": 7,
                "iso_year": 2026,
                "iso_week": 1,
            },
            "previous_period": {
                "granularity": "week",
                "start": "2025-12-22",
                "end": "2025-12-28",
                "day_count": 7,
                "iso_year": 2025,
                "iso_week": 52,
            },
            "current_total": {"minor_units": 5000, "currency": "EUR"},
            "previous_total": {"minor_units": 4000, "currency": "EUR"},
            "delta": {"minor_units": 1000, "currency": "EUR"},
            "percentage_change_basis_points": 2500,
            "percentage_change_status": "defined",
        },
        "budgets": None,
    }
    assert reader.requested_profile == PROFILE_ID
    assert "profile_id" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_range_overview_resolves_every_offered_granularity() -> None:
    """Let the domain calendar policy decide which Rome days each covers."""

    reader = OverviewReader()
    client = _client(_app(reader))
    resolved = {}
    for query in (
        "granularity=day&day=2026-09-12",
        "granularity=week&day=2026-09-12",
        "granularity=month&month=2026-09",
        "granularity=year&year=2026",
        "granularity=custom&start=2026-09-01&end=2026-09-15",
    ):
        response = client.get(f"/api/v1/overview/range?{query}")
        assert response.status_code == 200
        period = response.json()["period"]
        resolved[period["granularity"]] = (period["start"], period["end"])

    assert resolved == {
        "day": ("2026-09-12", "2026-09-12"),
        "week": ("2026-09-07", "2026-09-13"),
        "month": ("2026-09-01", "2026-09-30"),
        "year": ("2026-01-01", "2026-12-31"),
        "custom": ("2026-09-01", "2026-09-15"),
    }


def test_range_overview_reports_budgets_only_for_a_whole_calendar_month() -> None:
    """Omit a position the monthly budget calendar does not define."""

    client = _client(_app())

    month = client.get("/api/v1/overview/range?granularity=month&month=2026-09")
    spanning = client.get(
        "/api/v1/overview/range?granularity=custom&start=2026-09-01&end=2026-09-30",
    )
    year = client.get("/api/v1/overview/range?granularity=year&year=2026")

    assert month.json()["budgets"] == {
        "period": {"year": 2026, "month": 9},
        "positions": [
            {
                "category_name": "Spesa alimentare",
                "target": {"minor_units": 8000, "currency": "EUR"},
                "posted_spend": {"minor_units": 5000, "currency": "EUR"},
                "remaining": {"minor_units": 3000, "currency": "EUR"},
            }
        ],
        "unbudgeted_categories": ["Abbonamenti"],
    }
    assert spanning.json()["budgets"] == month.json()["budgets"]
    assert year.json()["budgets"] is None


def test_a_period_without_a_representable_comparison_is_refused() -> None:
    """Refuse a valid selection when its predecessor cannot be represented."""

    response = _client(_app()).get(
        "/api/v1/overview/range?granularity=day&day=0001-01-01",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "period_comparison_unavailable"
    assert response.json()["instance"] == "/api/v1/overview/range"


def test_an_unsupported_granularity_is_one_bounded_problem() -> None:
    """Refuse an unknown period vocabulary instead of guessing a default."""

    response = _client(_app()).get(
        "/api/v1/overview/range?granularity=quarter&day=2026-09-12",
    )

    assert response.status_code == 422
    assert response.json()["code"] == "period_granularity_invalid"
    assert response.json()["instance"] == "/api/v1/overview/range"


def test_parameters_must_match_the_requested_granularity() -> None:
    """Refuse an ambiguous selection rather than silently ignoring a value."""

    client = _client(_app())

    codes = {
        client.get(f"/api/v1/overview/range?{query}").json()["code"]
        for query in (
            "granularity=day",
            "granularity=day&day=2026-09-12&year=2026",
            "granularity=month&day=2026-09-12",
            "granularity=custom&start=2026-09-01",
            "granularity=year&year=2026&end=2026-09-30",
        )
    }

    assert codes == {"period_selection_invalid"}


def test_a_malformed_period_value_is_refused_without_coercion() -> None:
    """Accept exactly one representation of each calendar token."""

    client = _client(_app())

    codes = {
        client.get(f"/api/v1/overview/range?{query}").json()["code"]
        for query in (
            "granularity=day&day=2026-9-12",
            "granularity=day&day=20260912",
            "granularity=day&day=2026-02-30",
            "granularity=day&day=2026-W38-1",
            "granularity=month&month=2026-13",
            "granularity=year&year=26",
            "granularity=custom&start=2026-09-01&end=domani",
        )
    }

    assert codes == {"period_value_invalid"}


def test_an_untrusted_custom_interval_is_bounded_and_ordered() -> None:
    """Refuse a reversed or unbounded interval as a 422, never as a crash."""

    client = _client(_app())

    reversed_interval = client.get(
        "/api/v1/overview/range?granularity=custom&start=2026-09-30&end=2026-09-01",
    )
    too_wide = client.get(
        "/api/v1/overview/range?granularity=custom&start=2020-01-01&end=2026-12-31",
    )
    unrepresentable = client.get("/api/v1/overview/range?granularity=year&year=9999")

    assert reversed_interval.status_code == 422
    assert too_wide.status_code == 422
    assert unrepresentable.status_code == 422
    assert {
        reversed_interval.json()["code"],
        too_wide.json()["code"],
        unrepresentable.json()["code"],
    } == {"period_range_invalid"}


def test_the_range_read_never_accepts_a_profile_from_the_browser() -> None:
    """Keep owner scope a composition input on the wider read as well."""

    reader = OverviewReader()

    response = _client(_app(reader)).get(
        f"/api/v1/overview/range?granularity=day&day=2026-09-12&profile_id={uuid4()}",
    )

    assert response.status_code == 200
    assert reader.requested_profile == PROFILE_ID


def test_funding_sources_expose_wallet_state_without_scope_or_notes() -> None:
    """Return versioned source state and immutable movements in trusted scope."""

    writer = RecordingFundingSourceWriter()
    writer.entries.append(
        WalletEntry(
            id=uuid4(),
            profile_id=PROFILE_ID,
            funding_source_id=SOURCE_ID,
            entry_type=WalletEntryType.CREDIT,
            amount=Money(3000),
            occurred_at=datetime(2026, 9, 13, 10, tzinfo=UTC),
            note="owner-only explanation",
        )
    )

    response = _client(_app(funding_source_writer=writer)).get(
        "/api/v1/funding-sources"
    )

    assert response.status_code == 200
    source = response.json()["sources"][0]
    assert source["name"] == "Buoni pasto"
    assert source["balance_mode"] == "ledger"
    assert source["version"] == 2
    assert source["availability"]["booked_balance"]["minor_units"] == 3000
    assert source["availability"]["available_balance"]["minor_units"] == 1800
    assert source["entries"][0]["entry_type"] == "credit"
    assert "note" not in source["entries"][0]
    assert "profile_id" not in response.text


def test_funding_source_routes_forward_versions_and_complete_default_snapshot() -> None:
    """Translate strict owner commands while preserving optimistic state."""

    writer = RecordingFundingSourceWriter()
    client = _client(_app(funding_source_writer=writer))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    def headers(command_key: str) -> dict[str, str]:
        """Return valid browser mutation headers."""

        return {
            "origin": ORIGIN,
            "x-csrf-token": token,
            "Idempotency-Key": command_key,
        }

    renamed = client.put(
        f"/api/v1/funding-sources/{SOURCE_ID}",
        json={"name": "Benefit lavoro", "expected_version": 2},
        headers=headers("rename-source"),
    )
    previous_id = uuid4()
    made_default = client.post(
        f"/api/v1/funding-sources/{SOURCE_ID}/default",
        json={
            "expected_version": 3,
            "expected_previous_default_id": str(previous_id),
            "expected_previous_default_version": 7,
        },
        headers=headers("default-source"),
    )

    assert renamed.status_code == 200
    assert renamed.json()["version"] == 3
    assert made_default.status_code == 200
    assert writer.actions[0][:5] == (
        "rename",
        SOURCE_ID,
        2,
        "Benefit lavoro",
        "rename-source",
    )
    assert writer.actions[1][:7] == (
        "default",
        SOURCE_ID,
        3,
        previous_id,
        7,
        "default-source",
        "owner changed default funding source in browser",
    )


def test_wallet_route_builds_exact_entry_and_rejects_unpaired_default_state() -> None:
    """Keep wallet money exact and refuse incomplete optimistic snapshots."""

    writer = RecordingFundingSourceWriter()
    client = _client(_app(funding_source_writer=writer))
    token = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]
    headers = {
        "origin": ORIGIN,
        "x-csrf-token": token,
        "Idempotency-Key": "wallet-credit",
    }
    occurred_at = "2026-09-13T12:30:00+02:00"

    created = client.post(
        f"/api/v1/funding-sources/{SOURCE_ID}/wallet-entries",
        json={
            "entry_type": "credit",
            "amount": {"minor_units": 2550, "currency": "EUR"},
            "occurred_at": occurred_at,
            "expires_on": "2026-10-31",
        },
        headers=headers,
    )
    invalid_default = client.post(
        f"/api/v1/funding-sources/{SOURCE_ID}/default",
        json={
            "expected_version": 2,
            "expected_previous_default_id": str(uuid4()),
            "expected_previous_default_version": None,
        },
        headers={**headers, "Idempotency-Key": "default-incomplete"},
    )

    assert created.status_code == 201
    entry = writer.entries[0]
    assert entry.profile_id == PROFILE_ID
    assert entry.funding_source_id == SOURCE_ID
    assert entry.entry_type is WalletEntryType.CREDIT
    assert entry.amount == Money(2550)
    assert entry.expires_on == date(2026, 10, 31)
    assert invalid_default.status_code == 422
    assert invalid_default.json()["code"] == "request_invalid"
