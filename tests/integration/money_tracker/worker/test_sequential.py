"""Real PostgreSQL tests for the durable claim-dispatch-commit protocol.

The model boundary is the one thing these tests script. Everything the worker
is responsible for -- ordering, leases, the single terminal transaction, and
recovery after a crash between the finance write and the messaging write --
runs against the real database over the real protocol, because that is where
the AD-018 guarantees actually live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from langchain_core.exceptions import ModelConnectionError
from psycopg import connect

from money_tracker.application import CategoryReviewService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.messaging import (
    BotEnvironment,
    InboxClaim,
    NormalizedTelegramUpdate,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.presentation import TelegramResponseRenderer
from money_tracker.worker import SequentialWorker
from money_tracker.workflows import (
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    DispatchStatus,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
    WorkflowIntent,
)

pytestmark = pytest.mark.postgres

_NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


class _Clock:
    """Advancing aware clock so bounded retry windows can actually elapse."""

    def __init__(self) -> None:
        self.instant = _NOW

    def now(self) -> datetime:
        """Return the current test instant."""

        return self.instant

    def advance(self, seconds: int) -> None:
        """Move the test clock past a scheduled retry."""

        self.instant += timedelta(seconds=seconds)


class _Dispatcher:
    """Scripted stand-in for the model-backed workflow entry point."""

    def __init__(self, *outcomes: DispatchOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[ChannelMessage] = []

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Return the next scripted result for one translated work unit."""

        self.calls.append(message)
        return self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]


class _CrashingMessagingRepository(PostgresMessagingRepository):
    """Real repository that dies once before its terminal transaction.

    This is not a database stand-in: the crash happens before any statement
    runs, so PostgreSQL sees exactly what a killed process would have left
    behind, and every other call in the test uses the production adapter.
    """

    def __init__(self, database_url: str) -> None:
        super().__init__(database_url)
        self.crashes = 1

    def complete_inbox(self, claim: InboxClaim, **values: object) -> UUID:
        """Fail the first terminal transaction the way a crash would."""

        if self.crashes:
            self.crashes -= 1
            raise ConnectionResetError("the worker process died before committing")
        return super().complete_inbox(claim, **values)  # type: ignore[arg-type]


def _bootstrap(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, PostgresMessagingRepository, Profile, str]:
    """Migrate, seed one paired owner, and initialize one bot scope."""

    upgrade_database(postgres_url)
    finance = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    finance.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=_NOW)
    finance.create_funding_source(
        FundingSource(
            id=uuid4(),
            profile_id=profile.id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        ),
        occurred_at=_NOW,
    )
    messaging = PostgresMessagingRepository(postgres_url)
    bot_key = f"test-{uuid4().hex}"
    messaging.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=_NOW)
    _pair(messaging, bot_key, profile.id)
    return finance, messaging, profile, bot_key


def _pair(
    messaging: PostgresMessagingRepository,
    bot_key: str,
    profile_id: UUID,
) -> None:
    """Create the sole owner binding through the supported hashed code path."""

    code_hash = sha256(f"code-{bot_key}".encode()).hexdigest()
    messaging.create_pairing_code(
        code_id=uuid4(),
        bot_key=bot_key,
        profile_id=profile_id,
        code_hash=code_hash,
        expires_at=_NOW + timedelta(minutes=10),
        now=_NOW,
    )
    binding = messaging.pair_owner(
        bot_key=bot_key,
        profile_id=profile_id,
        code_hash=code_hash,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        now=_NOW,
    )
    assert binding is not None


def _movement(
    finance: PostgresFinanceRepository,
    profile: Profile,
    reference: str,
) -> Expense:
    """Store one posted movement the worker can summarize."""

    sources = finance.get_funding_sources(profile.id)
    category = finance.get_taxonomy(profile.id).categories[0]
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=_NOW,
        description="croccantini per i gatti",
        merchant=None,
        status=ExpenseStatus.POSTED,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id,
                amount=Money(1200),
                provenance="test-fixture",
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=sources[0].id, amount=Money(1200)),
        ),
        source_reference=reference,
        idempotency_key=f"key:{reference}",
    )
    return finance.create_expense(
        expense,
        audit_event=AuditEvent(
            id=uuid4(),
            profile_id=profile.id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action="create",
            actor="test",
            source_reference=reference,
            occurred_at=_NOW,
            prior_version=None,
            current_version=1,
            reason="integration fixture",
        ),
        request_hash=sha256(reference.encode()).hexdigest(),
    )


def _ingest(
    messaging: PostgresMessagingRepository,
    bot_key: str,
    *update_ids: int,
) -> tuple[NormalizedTelegramUpdate, ...]:
    """Commit one authorized batch the way the gateway does."""

    updates = tuple(
        NormalizedTelegramUpdate(
            id=uuid4(),
            bot_key=bot_key,
            update_id=update_id,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            telegram_message_id=update_id + 1,
            received_at=_NOW,
            accepted_at=_NOW,
            text=f"ho speso 12 euro ({update_id})",
        )
        for update_id in update_ids
    )
    messaging.commit_ingest_batch(
        bot_key=bot_key,
        expected_offset=messaging.polling_offset(bot_key),
        observed_update_ids=update_ids,
        accepted_updates=updates,
        rejected_count=0,
        unsupported_count=0,
        now=_NOW,
    )
    return updates


def _outcome(status: DispatchStatus, **overrides: object) -> DispatchOutcome:
    """Build one typed dispatch result."""

    fields: dict[str, object] = {
        "routing": RoutingDecision(
            intent=WorkflowIntent.EXPENSE,
            source=RoutingSource.MODEL,
            reason=RoutingReason.MODEL_CLASSIFICATION,
        ),
        "status": status,
    }
    fields.update(overrides)
    return DispatchOutcome(**fields)  # type: ignore[arg-type]


def _worker(
    finance: PostgresFinanceRepository,
    messaging: PostgresMessagingRepository,
    bot_key: str,
    dispatcher: _Dispatcher,
    clock: _Clock,
) -> SequentialWorker:
    """Assemble the production worker over the real durable boundaries."""

    return SequentialWorker(
        bot_key=bot_key,
        repository=messaging,
        configuration=finance,
        review=CategoryReviewService(
            finance, finance, clock=SystemClock(), ids=UUID4Generator()
        ),
        dispatcher=dispatcher,
        renderer=TelegramResponseRenderer(),
        clock=clock,
    )


def _rows(postgres_url: str, query: str, parameters: tuple[object, ...]) -> list[tuple]:
    """Read rows from the isolated integration database."""

    with connect(postgres_url) as connection:
        return list(connection.execute(query, parameters).fetchall())


def test_one_work_unit_commits_result_response_and_correlation(
    postgres_url: str,
) -> None:
    """Land every AD-018 effect of a processed update in one transaction."""

    finance, messaging, profile, bot_key = _bootstrap(postgres_url)
    movement = _movement(finance, profile, "integration:worker:1")
    (update,) = _ingest(messaging, bot_key, 10)
    clock = _Clock()
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=movement.id))

    assert _worker(finance, messaging, bot_key, dispatcher, clock).process_once()

    inbox = _rows(
        postgres_url,
        "SELECT status, workflow_status FROM messaging.telegram_inbox WHERE id = %s",
        (update.id,),
    )
    outbox = _rows(
        postgres_url,
        "SELECT logical_key, text_content FROM messaging.telegram_outbox "
        "WHERE inbox_id = %s",
        (update.id,),
    )
    actions = _rows(
        postgres_url,
        "SELECT target_type, target_id, status "
        "FROM messaging.telegram_pending_actions WHERE bot_key = %s",
        (bot_key,),
    )
    assert inbox == [("completed", "drafted")]
    assert outbox[0][0] == f"inbox:{update.id}:response:movement"
    assert "12,00 €" in outbox[0][1]
    assert actions == [("movement", movement.id, "active")]


def test_a_committed_response_is_immediately_deliverable(
    postgres_url: str,
) -> None:
    """Hand the independent outbox loop work as soon as the item completes."""

    finance, messaging, profile, bot_key = _bootstrap(postgres_url)
    movement = _movement(finance, profile, "integration:worker:2")
    _ingest(messaging, bot_key, 11)
    clock = _Clock()
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=movement.id))
    _worker(finance, messaging, bot_key, dispatcher, clock).process_once()

    claim = messaging.claim_next_outbox(
        bot_key,
        now=clock.now(),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )

    assert claim is not None
    assert [button.label for button in claim.message.buttons] == [
        "Modifica",
        "Annulla",
    ]


def test_a_crash_before_the_messaging_commit_creates_one_response(
    postgres_url: str,
) -> None:
    """Recover the same logical response instead of a second one."""

    finance, messaging, profile, bot_key = _bootstrap(postgres_url)
    crashing = _CrashingMessagingRepository(postgres_url)
    movement = _movement(finance, profile, "integration:worker:3")
    (update,) = _ingest(messaging, bot_key, 12)
    clock = _Clock()
    dispatcher = _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=movement.id))
    worker = _worker(finance, crashing, bot_key, dispatcher, clock)

    with pytest.raises(ConnectionResetError):
        worker.process_once()
    clock.advance(600)
    _release(postgres_url, update.id)
    assert worker.process_once() is True

    outbox = _rows(
        postgres_url,
        "SELECT id FROM messaging.telegram_outbox WHERE inbox_id = %s",
        (update.id,),
    )
    assert len(dispatcher.calls) == 2
    assert len(outbox) == 1


def test_an_ineligible_head_row_holds_back_later_work(
    postgres_url: str,
) -> None:
    """Keep strict Telegram order while the first item waits for its retry."""

    finance, messaging, profile, bot_key = _bootstrap(postgres_url)
    movement = _movement(finance, profile, "integration:worker:4")
    first, second = _ingest(messaging, bot_key, 13, 14)
    clock = _Clock()
    worker = _worker(finance, messaging, bot_key, _Failing(), clock)

    assert worker.process_once() is True
    assert (
        messaging.claim_next_inbox(
            bot_key,
            now=clock.now(),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        is None
    )

    clock.advance(600)
    succeeding = _worker(
        finance,
        messaging,
        bot_key,
        _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=movement.id)),
        clock,
    )
    assert succeeding.process_once() is True
    assert succeeding.process_once() is True
    completed = _rows(
        postgres_url,
        "SELECT id FROM messaging.telegram_inbox "
        "WHERE bot_key = %s AND status = 'completed' ORDER BY update_id",
        (bot_key,),
    )
    assert [row[0] for row in completed] == [first.id, second.id]


def test_a_callback_consumes_the_correlation_it_answered(
    postgres_url: str,
) -> None:
    """Stop a second press from reaching the same target twice."""

    finance, messaging, profile, bot_key = _bootstrap(postgres_url)
    movement = _movement(finance, profile, "integration:worker:5")
    _ingest(messaging, bot_key, 15)
    clock = _Clock()
    _worker(
        finance,
        messaging,
        bot_key,
        _Dispatcher(_outcome(DispatchStatus.DRAFTED, expense_id=movement.id)),
        clock,
    ).process_once()
    action_id = _rows(
        postgres_url,
        "SELECT id FROM messaging.telegram_pending_actions WHERE bot_key = %s",
        (bot_key,),
    )[0][0]
    _ingest_callback(messaging, bot_key, 16, f"a:{action_id}:undo")

    _worker(
        finance,
        messaging,
        bot_key,
        _Dispatcher(_outcome(DispatchStatus.VOIDED, expense_id=movement.id)),
        clock,
    ).process_once()

    assert (
        messaging.resolve_callback_action(
            bot_key=bot_key,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            action_id=action_id,
            action="undo",
        )
        is None
    )
    assert _rows(
        postgres_url,
        "SELECT status FROM messaging.telegram_pending_actions WHERE id = %s",
        (action_id,),
    ) == [("consumed",)]


class _Failing:
    """Dispatcher whose model boundary is unavailable."""

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Fail the way an unreachable local model does."""

        raise ModelConnectionError("llama.cpp is unavailable")


def _ingest_callback(
    messaging: PostgresMessagingRepository,
    bot_key: str,
    update_id: int,
    callback_data: str,
) -> NormalizedTelegramUpdate:
    """Commit one authorized callback update."""

    update = NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=bot_key,
        update_id=update_id,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        telegram_message_id=update_id + 1,
        received_at=_NOW,
        accepted_at=_NOW,
        callback_action=callback_data,
        callback_query_id=f"q{update_id}",
    )
    messaging.commit_ingest_batch(
        bot_key=bot_key,
        expected_offset=messaging.polling_offset(bot_key),
        observed_update_ids=(update_id,),
        accepted_updates=(update,),
        rejected_count=0,
        unsupported_count=0,
        now=_NOW,
    )
    return update


def _release(postgres_url: str, inbox_id: UUID) -> None:
    """Return a row whose claiming process died to the eligible queue.

    A production deployment recovers this through the expired lease. The test
    releases it explicitly so the recovery assertion is about the response
    identity rather than about waiting out a five-minute lease.
    """

    with connect(postgres_url) as connection:
        connection.execute(
            "UPDATE messaging.telegram_inbox SET status = 'pending', "
            "claim_token = NULL, claimed_until = NULL WHERE id = %s",
            (inbox_id,),
        )
