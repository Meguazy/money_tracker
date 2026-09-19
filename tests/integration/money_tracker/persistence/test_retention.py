"""Real PostgreSQL tests for runtime retention of messaging and checkpoints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from psycopg import connect

from money_tracker.domain import AuditEvent, CategoryProposal, Profile, initial_taxonomy
from money_tracker.messaging import (
    BotEnvironment,
    NormalizedTelegramUpdate,
    OutboundButton,
    OutboxMessageDraft,
    PendingActionDraft,
    PendingActionTarget,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.persistence.retention import (
    PostgresRuntimeRetention,
    RuntimeRetentionError,
)
from money_tracker.retention_cli import main as retention_main
from money_tracker.workflows import ThreadNamespace, workflow_thread_id

pytestmark = pytest.mark.postgres

_OWNER_TEXT = "Contenuto proprietario da trattenere"
_RETENTION = timedelta(days=30)


class _State(TypedDict):
    """Minimal checkpointed graph state."""

    value: int


def _increment(state: _State) -> _State:
    """Advance the state so the graph writes a real checkpoint."""

    return {"value": state["value"] + 1}


@dataclass(frozen=True, slots=True)
class _Scenario:
    """Identifiers of the runtime state one test built.

    Attributes:
        bot_key: Isolated bot persistence scope.
        profile_id: Owner profile the controls belong to.
        consumed: Inbox items whose whole conversation is concluded.
        active: Inbox item whose response still offers an active control.
        active_action_id: The control that must survive.
        active_message_id: Telegram message carrying that control.
        active_target_id: Movement that control acts on.
        dead_letter: Inbox item that failed permanently.
        undelivered: Inbox item whose response became an outbox dead letter.
        recent: Inbox item that concluded inside the window.
        pending_proposal_id: Category review still waiting for the owner.
    """

    bot_key: str
    profile_id: UUID
    consumed: tuple[NormalizedTelegramUpdate, ...]
    active: NormalizedTelegramUpdate
    active_action_id: UUID
    active_message_id: int
    active_target_id: UUID
    dead_letter: NormalizedTelegramUpdate
    undelivered: NormalizedTelegramUpdate
    recent: NormalizedTelegramUpdate
    pending_proposal_id: UUID


class _Conversation:
    """Drive the production messaging repository through complete work units."""

    def __init__(self, database_url: str, bot_key: str, profile_id: UUID) -> None:
        """Bind one bot scope and owner profile."""

        self.repository = PostgresMessagingRepository(database_url)
        self.bot_key = bot_key
        self.profile_id = profile_id
        self._next_update = 1
        self._next_message = 100

    def receive(self, at: datetime) -> NormalizedTelegramUpdate:
        """Ingest one owner message and return its normalized update."""

        update_id = self._next_update
        self._next_update += 1
        update = NormalizedTelegramUpdate(
            id=uuid4(),
            bot_key=self.bot_key,
            update_id=update_id,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            telegram_message_id=update_id,
            received_at=at,
            accepted_at=at,
            text=f"{_OWNER_TEXT} {update_id}",
        )
        self.repository.commit_ingest_batch(
            bot_key=self.bot_key,
            expected_offset=self.repository.polling_offset(self.bot_key),
            observed_update_ids=(update_id,),
            accepted_updates=(update,),
            rejected_count=0,
            unsupported_count=0,
            now=at,
        )
        return update

    def response(self, update: NormalizedTelegramUpdate) -> OutboxMessageDraft:
        """Build the owner reply a workflow commits for one update."""

        text = f"Risposta a {update.update_id}: {_OWNER_TEXT}"
        return OutboxMessageDraft(
            id=uuid4(),
            bot_key=self.bot_key,
            telegram_chat_id=1001,
            logical_key=f"inbox:{update.id}:response:draft",
            response_kind="draft",
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            buttons=(OutboundButton(label="Modifica", callback_data="modify:x"),),
            inbox_id=update.id,
        )

    def complete(
        self,
        update: NormalizedTelegramUpdate,
        at: datetime,
        *,
        offer_control_on: UUID | None = None,
        consume: UUID | None = None,
    ) -> tuple[OutboxMessageDraft, UUID | None]:
        """Process one claimed update into a response and optional control."""

        claim = self.repository.claim_next_inbox(
            self.bot_key, now=at, lease=timedelta(minutes=1), claim_token=uuid4()
        )
        assert claim is not None and claim.update.id == update.id
        response = self.response(update)
        action = (
            None
            if offer_control_on is None
            else PendingActionDraft(
                id=uuid4(),
                bot_key=self.bot_key,
                profile_id=self.profile_id,
                telegram_user_id=1001,
                telegram_chat_id=1001,
                outbox_id=response.id,
                target_type=PendingActionTarget.MOVEMENT,
                target_id=offer_control_on,
                allowed_actions=("modify", "undo"),
            )
        )
        self.repository.complete_inbox(
            claim,
            workflow_status="drafted",
            workflow_result={"kind": "expense", "status": "drafted"},
            response=response,
            pending_action=action,
            consumed_action_id=consume,
            now=at,
        )
        return response, None if action is None else action.id

    def fail(self, update: NormalizedTelegramUpdate, at: datetime) -> None:
        """Dead-letter one claimed update with its stable owner notice."""

        claim = self.repository.claim_next_inbox(
            self.bot_key, now=at, lease=timedelta(minutes=1), claim_token=uuid4()
        )
        assert claim is not None and claim.update.id == update.id
        text = "Notifica di errore senza contenuto privato"
        self.repository.fail_inbox(
            claim,
            error_code="workflow_failed",
            transient=False,
            retry_at=None,
            response=OutboxMessageDraft(
                id=uuid4(),
                bot_key=self.bot_key,
                telegram_chat_id=1001,
                logical_key=f"inbox:{update.id}:response:terminal_failure",
                response_kind="terminal_failure",
                text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                inbox_id=update.id,
            ),
            now=at,
        )

    def deliver(self, at: datetime, *, fail: bool = False) -> int:
        """Send the oldest queued response, or dead-letter it when asked."""

        work = self.repository.claim_next_outbox(
            self.bot_key, now=at, lease=timedelta(minutes=1), claim_token=uuid4()
        )
        assert work is not None
        if fail:
            self.repository.fail_outbox(
                work,
                error_code="telegram_rejected",
                transient=False,
                retry_at=None,
                now=at,
            )
            return 0
        message_id = self._next_message
        self._next_message += 1
        self.repository.complete_outbox(work, telegram_message_id=message_id, now=at)
        return message_id


def _pending_proposal(database_url: str, profile: Profile, at: datetime) -> UUID:
    """Persist one category review that is still waiting for the owner."""

    proposal = CategoryProposal(
        id=uuid4(),
        profile_id=profile.id,
        proposed_name="Mobilità dolce",
        description="Biciclette, monopattini e relativa manutenzione.",
        parent_id=None,
        rationale="La distinzione cambia report e decisioni di spesa.",
        alternative_category_ids=(),
        excluded_names=(),
        source_reference="integration:retention",
    )
    PostgresFinanceRepository(database_url).create_category_proposal(
        proposal,
        audit_event=AuditEvent(
            id=uuid4(),
            profile_id=profile.id,
            aggregate_type="category_proposal",
            aggregate_id=proposal.id,
            action="create",
            actor="integration",
            source_reference="integration:retention",
            occurred_at=at,
            prior_version=None,
            current_version=1,
            reason="runtime retention integration",
        ),
    )
    return proposal.id


def _build(database_url: str, old: datetime, recent: datetime) -> _Scenario:
    """Create concluded, active, failed, and recent runtime state."""

    upgrade_database(database_url)
    profile = Profile(uuid4())
    PostgresFinanceRepository(database_url).bootstrap_profile(
        profile, taxonomy=initial_taxonomy(), occurred_at=old
    )
    bot_key = f"test-{uuid4().hex}"
    chat = _Conversation(database_url, bot_key, profile.id)
    chat.repository.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=old)
    step = timedelta(seconds=10)

    # A control offered and then used: the whole exchange is concluded.
    offered = chat.receive(old)
    _, consumed_action = chat.complete(offered, old + step, offer_control_on=uuid4())
    chat.deliver(old + 2 * step)
    answering = chat.receive(old + 3 * step)
    chat.complete(answering, old + 4 * step, consume=consumed_action)
    chat.deliver(old + 5 * step)

    # A control still offered: its message and correlation must survive.
    target = uuid4()
    active = chat.receive(old + 6 * step)
    _, active_action = chat.complete(active, old + 7 * step, offer_control_on=target)
    active_message = chat.deliver(old + 8 * step)

    # A permanent workflow failure the operator has not resolved.
    dead_letter = chat.receive(old + 9 * step)
    chat.fail(dead_letter, old + 10 * step)
    chat.deliver(old + 11 * step)

    # A response Telegram refused for good.
    undelivered = chat.receive(old + 12 * step)
    chat.complete(undelivered, old + 13 * step)
    chat.deliver(old + 14 * step, fail=True)

    # A conversation that concluded inside the window.
    recent_update = chat.receive(recent)
    chat.complete(recent_update, recent + step)
    chat.deliver(recent + 2 * step)

    assert active_action is not None
    return _Scenario(
        bot_key=bot_key,
        profile_id=profile.id,
        consumed=(offered, answering),
        active=active,
        active_action_id=active_action,
        active_message_id=active_message,
        active_target_id=target,
        dead_letter=dead_letter,
        undelivered=undelivered,
        recent=recent_update,
        pending_proposal_id=_pending_proposal(database_url, profile, old),
    )


def _checkpoint(database_url: str, *thread_ids: str) -> None:
    """Write real LangGraph checkpoints under the given thread identifiers."""

    builder = StateGraph(_State)
    builder.add_node("increment", _increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    with open_postgres_checkpointer(database_url) as saver:
        graph = builder.compile(checkpointer=saver)
        for thread_id in thread_ids:
            graph.invoke({"value": 1}, {"configurable": {"thread_id": thread_id}})


def _rows(database_url: str) -> dict[str, int]:
    """Count every table runtime retention may change."""

    tables = (
        "messaging.telegram_inbox",
        "messaging.telegram_outbox",
        "messaging.telegram_pending_actions",
        "workflow.checkpoints",
        "workflow.checkpoint_blobs",
        "workflow.checkpoint_writes",
    )
    with connect(database_url) as connection:
        counts = {}
        for table in tables:
            row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert row is not None
            counts[table] = int(row[0])
    return counts


def _threads(database_url: str) -> set[str]:
    """Return every workflow thread that still has a checkpoint."""

    with connect(database_url) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT thread_id FROM workflow.checkpoints"
            )
        }


def _inbox_ids(database_url: str) -> set[UUID]:
    """Return every inbox item that still exists."""

    with connect(database_url) as connection:
        return {
            row[0]
            for row in connection.execute("SELECT id FROM messaging.telegram_inbox")
        }


def test_retention_deletes_only_concluded_content_and_keeps_live_controls(
    postgres_url: str,
) -> None:
    """Delete concluded work, keep what can still be acted on, and roll back plans."""

    old = datetime.now(UTC)
    clock = old + timedelta(days=40)
    scenario = _build(postgres_url, old, old + timedelta(days=20))
    bootstrap_checkpointer(postgres_url)
    local_reference = f"local-admin:route:{uuid4()}"
    expired_threads = {
        workflow_thread_id(ThreadNamespace.ROUTER, update.source_reference)
        for update in scenario.consumed
    } | {
        workflow_thread_id(ThreadNamespace.EXPENSE, scenario.active.source_reference),
        workflow_thread_id(
            ThreadNamespace.MOVEMENT_CHANGE, scenario.undelivered.source_reference
        ),
        workflow_thread_id(ThreadNamespace.READ, local_reference),
        workflow_thread_id(ThreadNamespace.CATEGORY_REVIEW, str(uuid4())),
    }
    kept_threads = {
        workflow_thread_id(
            ThreadNamespace.ROUTER, scenario.dead_letter.source_reference
        ),
        workflow_thread_id(
            ThreadNamespace.CATEGORY_REVIEW, str(scenario.pending_proposal_id)
        ),
        "unrecognised-family:anything",
        "thread-without-namespace",
    }
    _checkpoint(postgres_url, *sorted(expired_threads | kept_threads))
    retention = PostgresRuntimeRetention(postgres_url, clock=lambda: clock)
    before = _rows(postgres_url)

    plan = retention.run(retention=_RETENTION, confirm=False)

    assert _rows(postgres_url) == before
    assert plan.dry_run
    assert plan.cutoff == clock - _RETENTION
    assert not plan.vacuumed

    deleted = retention.run(retention=_RETENTION, confirm=True)

    assert deleted.deleted == plan.deleted
    assert deleted.retained == plan.retained
    assert not deleted.dry_run
    assert deleted.vacuumed
    assert deleted.deleted.pending_actions == 1
    # Both replies of the concluded exchange and the dead letter's notice.
    assert deleted.deleted.outbox_messages == 3
    assert deleted.deleted.inbox_items == 3
    assert deleted.deleted.detached_outbox_messages == 1
    assert deleted.deleted.checkpoint_threads == len(expired_threads)
    assert _threads(postgres_url) == kept_threads
    assert _inbox_ids(postgres_url) == {
        scenario.dead_letter.id,
        scenario.undelivered.id,
        scenario.recent.id,
    }
    # Retained past the cutoff: the dead letter and the undelivered item, the
    # active control's message and the refused response, the active control,
    # and the paused review plus both unrecognised threads and the dead letter's.
    assert deleted.retained.inbox_items == 2
    assert deleted.retained.outbox_messages == 2
    assert deleted.retained.pending_actions == 1
    assert deleted.retained.checkpoint_threads == len(kept_threads)
    with connect(postgres_url) as connection:
        detached = connection.execute(
            "SELECT inbox_id FROM messaging.telegram_outbox "
            "WHERE telegram_message_id = %s",
            (scenario.active_message_id,),
        ).fetchone()
    assert detached == (None,)

    # The surviving control still works through the production correlations.
    repository = PostgresMessagingRepository(postgres_url)
    resolved = repository.resolve_reply_action(
        bot_key=scenario.bot_key,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        telegram_message_id=scenario.active_message_id,
    )
    assert resolved is not None and resolved.id == scenario.active_action_id
    assert repository.delivered_control_messages(
        bot_key=scenario.bot_key,
        target_type=PendingActionTarget.MOVEMENT,
        target_id=scenario.active_target_id,
        excluding_outbox_id=uuid4(),
    ) == (scenario.active_message_id,)

    again = retention.run(retention=_RETENTION, confirm=True)

    assert again.deleted.total == 0
    assert again.deleted.detached_outbox_messages == 0
    assert not again.vacuumed


def test_retention_cli_reports_counts_without_owner_content(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plan and confirm through the production command against real time."""

    now = datetime.now(UTC)
    scenario = _build(postgres_url, now - timedelta(days=45), now - timedelta(hours=1))
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    monkeypatch.setenv("LOG_FORMAT", "json")
    capsys.readouterr()

    retention_main(["plan"])
    planned = capsys.readouterr()
    retention_main(["delete"])
    unconfirmed = capsys.readouterr()
    before = _rows_without_checkpoints(postgres_url)
    retention_main(["delete", "--confirm"])
    confirmed = capsys.readouterr()

    plan = json.loads(planned.out)
    dry_run = json.loads(unconfirmed.out)
    result = json.loads(confirmed.out)
    assert plan["command"] == "plan" and plan["dry_run"] is True
    assert dry_run["command"] == "delete" and dry_run["dry_run"] is True
    assert result["dry_run"] is False
    assert plan["deleted"] == dry_run["deleted"] == result["deleted"]
    assert result["retention_days"] == 30
    assert result["deleted"]["inbox_items"] == 3
    assert result["checkpoint_store_present"] is False
    assert result["vacuumed"] is True
    assert (
        before["messaging.telegram_inbox"] - 3
        == _rows_without_checkpoints(postgres_url)["messaging.telegram_inbox"]
    )
    assert scenario.recent.id in _inbox_ids(postgres_url)
    for captured in (planned, unconfirmed, confirmed):
        assert _OWNER_TEXT not in captured.out + captured.err
        assert postgres_url not in captured.out + captured.err
        assert scenario.bot_key not in captured.out


def _rows_without_checkpoints(database_url: str) -> dict[str, int]:
    """Count the messaging tables of a database without checkpoint tables."""

    with connect(database_url) as connection:
        row = connection.execute(
            "SELECT (SELECT count(*) FROM messaging.telegram_inbox), "
            "(SELECT count(*) FROM messaging.telegram_outbox)"
        ).fetchone()
    assert row is not None
    return {
        "messaging.telegram_inbox": int(row[0]),
        "messaging.telegram_outbox": int(row[1]),
    }


def test_retention_refuses_a_schema_it_was_not_written_for(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail closed, deleting nothing, when the migration revision differs."""

    old = datetime.now(UTC) - timedelta(days=45)
    _build(postgres_url, old, old)
    before = _rows_without_checkpoints(postgres_url)
    with connect(postgres_url) as connection:
        connection.execute("UPDATE public.alembic_version SET version_num = 'other'")

    with pytest.raises(RuntimeRetentionError) as refused:
        PostgresRuntimeRetention(postgres_url).run(retention=_RETENTION, confirm=True)
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    with pytest.raises(SystemExit) as exited:
        retention_main(["delete", "--confirm"])

    assert refused.value.reason_code == "database_schema_incompatible"
    assert not refused.value.transient
    assert exited.value.code == 1
    assert "database_schema_incompatible" in capsys.readouterr().err
    assert _rows_without_checkpoints(postgres_url) == before
