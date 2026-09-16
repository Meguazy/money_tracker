"""Real PostgreSQL tests for durable messaging state and concurrency."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from psycopg import connect

from money_tracker.messaging import (
    BotEnvironment,
    ControlRevocationDraft,
    NormalizedTelegramUpdate,
    OutboundButton,
    OutboxMessageDraft,
    PendingActionDraft,
    PendingActionTarget,
    RequeueOutcome,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.messaging import (
    MessagingConflictError,
    PostgresMessagingRepository,
)

pytestmark = pytest.mark.postgres

_NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def _insert_profile(database_url: str, profile_id: UUID) -> None:
    """Insert an isolated owner profile required by messaging foreign keys."""

    with connect(database_url) as connection:
        connection.execute(
            "INSERT INTO finance.profiles "
            "(id, locale, timezone, currency, active, created_at, updated_at) "
            "VALUES (%s, 'it-IT', 'Europe/Rome', 'EUR', true, %s, %s)",
            (profile_id, _NOW, _NOW),
        )


def _repository(
    database_url: str,
    *,
    bot_key: str | None = None,
    environment: BotEnvironment = BotEnvironment.TEST,
) -> tuple[PostgresMessagingRepository, str]:
    """Migrate and initialize one uniquely named bot persistence scope."""

    upgrade_database(database_url)
    repository = PostgresMessagingRepository(database_url)
    key = bot_key or f"test-{uuid4().hex}"
    repository.ensure_bot(key, environment=environment, now=_NOW)
    return repository, key


def _update(
    bot_key: str,
    update_id: int,
    *,
    accepted_at: datetime | None = None,
) -> NormalizedTelegramUpdate:
    """Build a synthetic private-chat update without production content."""

    now = accepted_at or (_NOW + timedelta(seconds=update_id))
    return NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=bot_key,
        update_id=update_id,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        telegram_message_id=update_id + 1,
        received_at=now,
        accepted_at=now,
        text=f"Integration message {update_id}",
    )


def _ingest(
    repository: PostgresMessagingRepository,
    bot_key: str,
    *updates: NormalizedTelegramUpdate,
) -> int:
    """Commit a contiguous synthetic batch at the current durable offset."""

    return repository.commit_ingest_batch(
        bot_key=bot_key,
        expected_offset=repository.polling_offset(bot_key),
        observed_update_ids=tuple(update.update_id for update in updates),
        accepted_updates=tuple(updates),
        rejected_count=0,
        unsupported_count=0,
        now=max(update.accepted_at for update in updates),
    )


def _response(
    *,
    bot_key: str,
    inbox_id: UUID | None,
    response_id: UUID | None = None,
    logical_key: str = "response:integration",
) -> OutboxMessageDraft:
    """Build a deterministic logical outbox response."""

    text = "Risposta di integrazione"
    return OutboxMessageDraft(
        id=response_id or uuid4(),
        bot_key=bot_key,
        telegram_chat_id=1001,
        logical_key=logical_key,
        response_kind="draft",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        buttons=(OutboundButton(label="Modifica", callback_data="modify:integration"),),
        inbox_id=inbox_id,
    )


def _failure_response(
    update: NormalizedTelegramUpdate, *, terminal: bool
) -> OutboxMessageDraft:
    """Build the stable owner notice committed with a failed inbox claim."""

    kind = "terminal_failure" if terminal else "recoverable_failure"
    text = "Notifica di errore senza contenuto privato"
    return OutboxMessageDraft(
        id=uuid5(NAMESPACE_URL, f"money-tracker:outbox:{update.id}:{kind}"),
        bot_key=update.bot_key,
        telegram_chat_id=update.telegram_chat_id,
        logical_key=f"inbox:{update.id}:response:{kind}",
        response_kind=kind,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        inbox_id=update.id,
        reply_to_message_id=update.telegram_message_id,
    )


def _count(
    database_url: str,
    query: str,
    parameters: tuple[object, ...] = (),
) -> int:
    """Return one integer aggregate from the isolated test database."""

    with connect(database_url) as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return int(row[0])


def test_migration_creates_minimal_normalized_messaging_tables(
    postgres_url: str,
) -> None:
    """Create every queue table without a raw update or token-bearing column."""

    repository, bot_key = _repository(postgres_url)
    assert repository.polling_offset(bot_key) == 0

    with connect(postgres_url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'messaging'"
            )
        }
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'messaging'"
            )
        }

    assert tables == {
        "telegram_inbox",
        "telegram_offsets",
        "telegram_outbox",
        "telegram_owner_bindings",
        "telegram_pairing_codes",
        "telegram_pending_actions",
    }
    assert not {"raw_update", "payload", "token", "download_url"} & columns


def test_outbox_recovery_migration_preserves_existing_messages(
    postgres_url: str,
) -> None:
    """Add replay accounting without replacing a durable logical response."""

    upgrade_database(postgres_url, "20260909_0005")
    repository = PostgresMessagingRepository(postgres_url)
    bot_key = f"test-{uuid4().hex}"
    repository.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=_NOW)
    response = _response(
        bot_key=bot_key,
        inbox_id=None,
        logical_key="migration:outbox-recovery",
    )
    repository.enqueue_notice(response, now=_NOW)

    upgrade_database(postgres_url)

    with connect(postgres_url) as connection:
        state = connection.execute(
            "SELECT id, status, replay_count, text_content "
            "FROM messaging.telegram_outbox WHERE id = %s",
            (response.id,),
        ).fetchone()
    assert state == (response.id, "pending", 0, response.text)


def test_health_reports_a_migrated_host_before_its_bot_scope_exists(
    postgres_url: str,
) -> None:
    """Answer the first readiness check on a host that has never paired."""

    upgrade_database(postgres_url)
    repository = PostgresMessagingRepository(postgres_url)
    unpaired = f"test-{uuid4().hex}"

    health = repository.health(unpaired)

    assert health.bot_key == unpaired
    assert health.next_offset == 0
    assert health.last_poll_at is None
    assert health.last_observed_at is None
    assert (health.pending_inbox, health.retrying_inbox, health.inbox_dead_letters) == (
        0,
        0,
        0,
    )
    assert (
        health.pending_outbox,
        health.retrying_outbox,
        health.outbox_dead_letters,
    ) == (0, 0, 0)
    assert health.active_actions == 0
    assert health.oldest_inbox_at is None
    assert health.oldest_outbox_at is None

    repository.ensure_bot(unpaired, environment=BotEnvironment.TEST, now=_NOW)

    assert repository.health(unpaired) == health


def test_ingest_deduplicates_redelivery_and_advances_offset_atomically(
    postgres_url: str,
) -> None:
    """Retain one inbox row and make an exact stale Bot API batch harmless."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 10)

    assert _ingest(repository, bot_key, update) == 11
    assert (
        repository.commit_ingest_batch(
            bot_key=bot_key,
            expected_offset=0,
            observed_update_ids=(10,),
            accepted_updates=(update,),
            rejected_count=0,
            unsupported_count=0,
            now=_NOW + timedelta(minutes=1),
        )
        == 11
    )
    assert repository.polling_offset(bot_key) == 11
    assert (
        _count(
            postgres_url,
            "SELECT count(*) FROM messaging.telegram_inbox WHERE bot_key = %s",
            (bot_key,),
        )
        == 1
    )


def test_concurrent_claims_return_only_one_oldest_item(postgres_url: str) -> None:
    """Serialize competing real connections around the per-bot offset row."""

    repository, bot_key = _repository(postgres_url)
    first = _update(bot_key, 20)
    second = _update(bot_key, 21)
    _ingest(repository, bot_key, first, second)
    barrier = Barrier(2)

    def claim() -> UUID | None:
        barrier.wait()
        result = repository.claim_next_inbox(
            bot_key,
            now=_NOW + timedelta(minutes=1),
            lease=timedelta(minutes=2),
            claim_token=uuid4(),
        )
        return None if result is None else result.update.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = tuple(executor.map(lambda _: claim(), range(2)))

    assert claimed.count(first.id) == 1
    assert claimed.count(None) == 1


def test_retry_blocks_later_update_then_dead_letter_unblocks_it(
    postgres_url: str,
) -> None:
    """Preserve strict ordering until the head item reaches a terminal state."""

    repository, bot_key = _repository(postgres_url)
    first = _update(bot_key, 30)
    second = _update(bot_key, 31)
    _ingest(repository, bot_key, first, second)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None and claim.update.id == first.id
    retry_at = _NOW + timedelta(minutes=2)
    repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=True,
        retry_at=retry_at,
        response=_failure_response(claim.update, terminal=False),
        now=_NOW + timedelta(minutes=1, seconds=1),
    )

    assert (
        repository.claim_next_inbox(
            bot_key,
            now=_NOW + timedelta(minutes=1, seconds=30),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        is None
    )
    retried = repository.claim_next_inbox(
        bot_key,
        now=retry_at,
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert retried is not None and retried.update.id == first.id
    repository.fail_inbox(
        retried,
        error_code="invalid_work",
        transient=False,
        retry_at=None,
        response=_failure_response(retried.update, terminal=True),
        now=retry_at + timedelta(seconds=1),
    )
    next_claim = repository.claim_next_inbox(
        bot_key,
        now=retry_at + timedelta(seconds=2),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert next_claim is not None and next_claim.update.id == second.id
    assert repository.health(bot_key).inbox_dead_letters == 1


def test_exhausted_failure_replays_with_same_source_identity(
    postgres_url: str,
) -> None:
    """Dead-letter the fifth failure and safely reset only attempt metadata."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 40)
    _ingest(repository, bot_key, update)
    now = _NOW + timedelta(minutes=1)
    claim = None
    for attempt in range(1, 6):
        claim = repository.claim_next_inbox(
            bot_key,
            now=now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        assert claim is not None and claim.attempt_count == attempt
        repository.fail_inbox(
            claim,
            error_code="transient_dependency",
            transient=True,
            retry_at=now + timedelta(seconds=2),
            response=_failure_response(claim.update, terminal=attempt == 5),
            now=now,
        )
        now += timedelta(seconds=2)
    assert repository.health(bot_key).inbox_dead_letters == 1

    repository.replay_inbox(bot_key, update.id, now=now)
    replay = repository.claim_next_inbox(
        bot_key,
        now=now,
        lease=timedelta(seconds=30),
        claim_token=uuid4(),
    )
    assert replay is not None
    assert replay.attempt_count == 1
    assert replay.replay_count == 1
    assert replay.update.source_reference == update.source_reference


def test_completion_creates_one_outbox_and_restart_safe_action(
    postgres_url: str,
) -> None:
    """Commit completion atomically and resolve its delivered owner correlation."""

    repository, bot_key = _repository(postgres_url)
    profile_id = uuid4()
    _insert_profile(postgres_url, profile_id)
    update = _update(bot_key, 50)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    response = _response(bot_key=bot_key, inbox_id=update.id)
    action = PendingActionDraft(
        id=uuid4(),
        bot_key=bot_key,
        profile_id=profile_id,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        outbox_id=response.id,
        target_type=PendingActionTarget.MOVEMENT,
        target_id=uuid4(),
        allowed_actions=("modify", "undo"),
    )
    outbox_id = repository.complete_inbox(
        claim,
        workflow_status="drafted",
        workflow_result={"kind": "expense", "status": "drafted"},
        response=response,
        pending_action=action,
        consumed_action_id=None,
        now=_NOW + timedelta(minutes=1, seconds=1),
    )

    assert outbox_id == response.id
    assert repository.health(bot_key).pending_outbox == 1
    restarted = PostgresMessagingRepository(postgres_url)
    outbox = restarted.claim_next_outbox(
        bot_key,
        now=_NOW + timedelta(minutes=2),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert outbox is not None and outbox.message.buttons == response.buttons
    assert outbox.source_reference == update.source_reference
    assert (
        restarted.resolve_callback_action(
            bot_key=bot_key,
            telegram_user_id=9999,
            telegram_chat_id=1001,
            action_id=action.id,
            action="modify",
        )
        is None
    )
    restarted.complete_outbox(
        outbox,
        telegram_message_id=777,
        now=_NOW + timedelta(minutes=2, seconds=1),
    )
    resolved = restarted.resolve_reply_action(
        bot_key=bot_key,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        telegram_message_id=777,
    )
    assert resolved is not None
    assert resolved.target_id == action.target_id
    assert (
        _count(
            postgres_url,
            "SELECT count(*) FROM messaging.telegram_outbox WHERE bot_key = %s",
            (bot_key,),
        )
        == 1
    )


def _confirmation(bot_key: str, chat_id: int = 1001) -> OutboxMessageDraft:
    """Build the confirmation the pairing transaction queues for the owner."""

    text = "Fatto: questo account è ora l'unico proprietario del bot."
    return OutboxMessageDraft(
        id=uuid4(),
        bot_key=bot_key,
        telegram_chat_id=chat_id,
        logical_key="pairing:owner",
        response_kind="paired",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def test_pairing_code_is_hashed_one_use_and_scoped_per_bot(
    postgres_url: str,
) -> None:
    """Allow test and production bot bindings without retaining a plain code."""

    profile_id = uuid4()
    test_repository, test_bot = _repository(
        postgres_url,
        bot_key=f"test-{uuid4().hex}",
    )
    production_bot = f"prod-{uuid4().hex}"
    test_repository.ensure_bot(
        production_bot,
        environment=BotEnvironment.PRODUCTION,
        now=_NOW,
    )
    _insert_profile(postgres_url, profile_id)
    code_hash = hashlib.sha256(b"synthetic-one-use-code").hexdigest()
    for bot_key in (test_bot, production_bot):
        test_repository.create_pairing_code(
            code_id=uuid4(),
            bot_key=bot_key,
            profile_id=profile_id,
            code_hash=code_hash,
            expires_at=_NOW + timedelta(minutes=10),
            now=_NOW,
        )
        binding = test_repository.pair_owner(
            bot_key=bot_key,
            code_hash=code_hash,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            confirmation=_confirmation(bot_key),
            now=_NOW + timedelta(seconds=1),
        )
        assert binding is not None and binding.profile_id == profile_id

    assert (
        test_repository.pair_owner(
            bot_key=test_bot,
            code_hash=code_hash,
            telegram_user_id=2002,
            telegram_chat_id=2002,
            confirmation=_confirmation(test_bot, 2002),
            now=_NOW + timedelta(seconds=2),
        )
        is None
    )
    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT code_hash FROM messaging.telegram_pairing_codes WHERE bot_key = %s",
            (test_bot,),
        ).fetchone()
    assert row == (code_hash,)
    assert "synthetic-one-use-code" not in row[0]


def test_environment_and_log_safe_failure_metadata_reject_conflicts(
    postgres_url: str,
) -> None:
    """Reject bot environment drift and sensitive free-form error details."""

    repository, bot_key = _repository(postgres_url)
    with pytest.raises(MessagingConflictError, match="different environment"):
        repository.ensure_bot(
            bot_key,
            environment=BotEnvironment.PRODUCTION,
            now=_NOW,
        )
    update = _update(bot_key, 60)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    with pytest.raises(ValueError, match="safe bounded"):
        repository.fail_inbox(
            claim,
            error_code="HTTP body contained private message",
            transient=False,
            retry_at=None,
            response=_failure_response(claim.update, terminal=True),
            now=_NOW + timedelta(minutes=1, seconds=1),
        )


def test_outbox_retry_is_bounded_and_becomes_visible_dead_letter(
    postgres_url: str,
) -> None:
    """Persist delivery retry metadata and stop after the fifth send attempt."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 70)
    _ingest(repository, bot_key, update)
    inbox = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert inbox is not None
    repository.complete_inbox(
        inbox,
        workflow_status="clarification",
        workflow_result={"kind": "clarification"},
        response=_response(
            bot_key=bot_key,
            inbox_id=update.id,
            logical_key="response:delivery-retry",
        ),
        pending_action=None,
        consumed_action_id=None,
        now=_NOW + timedelta(minutes=1, seconds=1),
    )

    now = _NOW + timedelta(minutes=2)
    for attempt in range(1, 6):
        outbox = repository.claim_next_outbox(
            bot_key,
            now=now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        assert outbox is not None and outbox.attempt_count == attempt
        repository.fail_outbox(
            outbox,
            error_code="telegram_unavailable",
            transient=True,
            retry_at=now + timedelta(seconds=2),
            now=now,
        )
        now += timedelta(seconds=2)

    health = repository.health(bot_key)
    assert health.outbox_dead_letters == 1
    assert health.retrying_outbox == 0
    assert (
        repository.claim_next_outbox(
            bot_key,
            now=now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        is None
    )
    listed = repository.outbox_dead_letters(bot_key, limit=10)
    assert len(listed) == 1
    item = listed[0]
    assert item.id == outbox.message.id
    assert item.attempt_count == 5
    assert item.replay_count == 0
    assert item.last_error_code == "telegram_unavailable"
    assert outbox.message.text not in str(item)

    foreign_bot_key = f"production-{uuid4().hex}"
    repository.ensure_bot(
        foreign_bot_key, environment=BotEnvironment.PRODUCTION, now=_NOW
    )
    with pytest.raises(MessagingConflictError):
        repository.replay_outbox(foreign_bot_key, item.id, now=now)
    assert repository.outbox_dead_letters(bot_key, limit=10) == (item,)

    repository.replay_outbox(bot_key, item.id, now=now)

    assert repository.outbox_dead_letters(bot_key, limit=10) == ()
    replayed = repository.claim_next_outbox(
        bot_key,
        now=now + timedelta(seconds=1),
        lease=timedelta(seconds=30),
        claim_token=uuid4(),
    )
    assert replayed is not None
    assert replayed.message.id == item.id
    assert replayed.attempt_count == 1
    with connect(postgres_url) as connection:
        state = connection.execute(
            "SELECT replay_count FROM messaging.telegram_outbox WHERE id = %s",
            (item.id,),
        ).fetchone()
    assert state == (1,)


@pytest.mark.postgres
def test_dead_letters_are_listable_and_replayable_without_content(
    postgres_url: str,
) -> None:
    """Give an operator enough to act on, and nothing the owner wrote."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 0)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(seconds=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=False,
        retry_at=None,
        response=_failure_response(claim.update, terminal=True),
        now=_NOW + timedelta(seconds=2),
    )

    listed = repository.dead_letters(bot_key, limit=10)

    assert len(listed) == 1
    item = listed[0]
    assert item.id == update.id
    assert item.last_error_code == "model_unavailable"
    assert item.source_reference == update.source_reference
    assert update.text is not None
    assert update.text not in str(item)
    notice = repository.claim_next_outbox(
        bot_key,
        now=_NOW + timedelta(seconds=3),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert notice is not None
    assert notice.message.response_kind == "terminal_failure"
    assert update.text not in notice.message.text

    foreign_bot_key = f"production-{uuid4().hex}"
    repository.ensure_bot(
        foreign_bot_key, environment=BotEnvironment.PRODUCTION, now=_NOW
    )
    with pytest.raises(MessagingConflictError):
        repository.replay_inbox(
            foreign_bot_key, item.id, now=_NOW + timedelta(seconds=3)
        )
    assert repository.dead_letters(bot_key, limit=10) == (item,)

    repository.replay_inbox(bot_key, item.id, now=_NOW + timedelta(seconds=3))

    assert repository.dead_letters(bot_key, limit=10) == ()
    replayed = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(seconds=4),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert replayed is not None
    assert replayed.update.source_reference == update.source_reference
    assert replayed.replay_count == 1


@pytest.mark.postgres
def test_an_operator_notice_is_queued_once_per_logical_key(
    postgres_url: str,
) -> None:
    """Let a restart inside one outage add no second warning."""

    repository, bot_key = _repository(postgres_url)
    notice = _response(
        bot_key=bot_key,
        inbox_id=None,
        logical_key="downtime:2026-09-08T05:30:00+00:00",
    )

    first = repository.enqueue_notice(notice, now=_NOW)
    second = repository.enqueue_notice(notice, now=_NOW + timedelta(minutes=5))

    assert first == second
    assert (
        _count(
            postgres_url,
            "SELECT count(*) FROM messaging.telegram_outbox WHERE bot_key = %s",
            (bot_key,),
        )
        == 1
    )
    claim = repository.claim_next_outbox(
        bot_key,
        now=_NOW + timedelta(minutes=6),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None and claim.message.inbox_id is None
    assert claim.source_reference is None


@pytest.mark.postgres
def test_a_notice_that_claims_an_inbox_item_is_refused(
    postgres_url: str,
) -> None:
    """Keep the answer-to-a-work-unit path distinct from an operator notice."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 0)
    _ingest(repository, bot_key, update)

    with pytest.raises(ValueError, match="answers no inbox item"):
        repository.enqueue_notice(
            _response(bot_key=bot_key, inbox_id=update.id),
            now=_NOW,
        )


def test_text_delivery_latencies_use_committed_inbox_and_outbox_times(
    postgres_url: str,
) -> None:
    """Measure successful text work without reading its content."""

    repository, bot_key = _repository(postgres_url)
    durations = (2_000, 3_500)
    for position, duration_ms in enumerate(durations):
        accepted_at = _NOW + timedelta(minutes=position)
        update = _update(bot_key, 80 + position, accepted_at=accepted_at)
        _ingest(repository, bot_key, update)
        inbox = repository.claim_next_inbox(
            bot_key,
            now=accepted_at + timedelta(seconds=1),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        assert inbox is not None
        repository.complete_inbox(
            inbox,
            workflow_status="drafted",
            workflow_result={"kind": "drafted"},
            response=_response(
                bot_key=bot_key,
                inbox_id=update.id,
                logical_key=f"latency:{position}",
            ),
            pending_action=None,
            consumed_action_id=None,
            now=accepted_at + timedelta(seconds=1),
        )
        outbox = repository.claim_next_outbox(
            bot_key,
            now=accepted_at + timedelta(seconds=1),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        assert outbox is not None
        repository.complete_outbox(
            outbox,
            telegram_message_id=90 + position,
            now=accepted_at + timedelta(milliseconds=duration_ms),
        )

    assert (
        repository.text_delivery_latencies(
            bot_key,
            since=_NOW,
            limit=10,
        )
        == durations
    )


@pytest.mark.postgres
def test_a_new_summary_revokes_the_controls_on_the_one_it_replaces(
    postgres_url: str,
) -> None:
    """Stop offering the same movement two sets of live buttons.

    The owner keeps every summary in their chat. Without this the older one
    still offers `Registra`, `Modifica` and `Annulla` for a movement that has
    since changed, and pressing it acts on a description that is no longer
    true.
    """

    repository, bot_key = _repository(postgres_url)
    profile_id = uuid4()
    movement_id = uuid4()
    _insert_profile(postgres_url, profile_id)

    first = _update(bot_key, 90)
    _ingest(repository, bot_key, first)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    summary = _response(bot_key=bot_key, inbox_id=first.id, logical_key="summary:one")
    repository.complete_inbox(
        claim,
        workflow_status="drafted",
        workflow_result={"kind": "expense", "status": "drafted"},
        response=summary,
        pending_action=PendingActionDraft(
            id=uuid4(),
            bot_key=bot_key,
            profile_id=profile_id,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            outbox_id=summary.id,
            target_type=PendingActionTarget.MOVEMENT,
            target_id=movement_id,
            allowed_actions=("post", "modify", "undo"),
        ),
        consumed_action_id=None,
        now=_NOW + timedelta(minutes=1, seconds=1),
    )
    delivery = repository.claim_next_outbox(
        bot_key,
        now=_NOW + timedelta(minutes=2),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert delivery is not None
    repository.complete_outbox(
        delivery,
        telegram_message_id=555,
        now=_NOW + timedelta(minutes=2, seconds=1),
    )

    superseded = repository.delivered_control_messages(
        bot_key=bot_key,
        target_type=PendingActionTarget.MOVEMENT,
        target_id=movement_id,
        excluding_outbox_id=uuid4(),
    )
    assert superseded == (555,)

    second = _update(bot_key, 91)
    _ingest(repository, bot_key, second)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=3),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    replacement = _response(
        bot_key=bot_key, inbox_id=second.id, logical_key="summary:two"
    )
    revocation = ControlRevocationDraft(
        id=uuid4(),
        bot_key=bot_key,
        telegram_chat_id=1001,
        logical_key=f"inbox:{second.id}:revoke:555",
        target_message_id=555,
        content_hash=hashlib.sha256(b"revoke:1001:555").hexdigest(),
        inbox_id=second.id,
    )
    repository.complete_inbox(
        claim,
        workflow_status="posted",
        workflow_result={"kind": "expense", "status": "posted"},
        response=replacement,
        pending_action=None,
        consumed_action_id=None,
        now=_NOW + timedelta(minutes=3, seconds=1),
        revocations=(revocation,),
    )

    restarted = PostgresMessagingRepository(postgres_url)
    queued = []
    for minute in (4, 5):
        work = restarted.claim_next_outbox(
            bot_key,
            now=_NOW + timedelta(minutes=minute),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        assert work is not None
        queued.append(work.message)
        restarted.complete_outbox(
            work,
            telegram_message_id=getattr(work.message, "target_message_id", 556),
            now=_NOW + timedelta(minutes=minute, seconds=1),
        )

    revoked = [item for item in queued if isinstance(item, ControlRevocationDraft)]
    assert len(revoked) == 1
    assert revoked[0].target_message_id == 555
    assert revoked[0].logical_key == revocation.logical_key
    assert restarted.health(bot_key).outbox_dead_letters == 0


@pytest.mark.postgres
def test_a_revocation_is_queued_once_and_never_measured_as_latency(
    postgres_url: str,
) -> None:
    """Keep replay idempotent and the NFR-PER-001 sample about owner answers.

    A revocation is delivered after the answer the owner waited for, and one
    work unit can queue several, so counting them would quietly add slower
    samples nobody experienced.
    """

    repository, bot_key = _repository(postgres_url)
    profile_id = uuid4()
    _insert_profile(postgres_url, profile_id)
    update = _update(bot_key, 92)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    response = _response(bot_key=bot_key, inbox_id=update.id, logical_key="answer")
    revocation = ControlRevocationDraft(
        id=uuid4(),
        bot_key=bot_key,
        telegram_chat_id=1001,
        logical_key=f"inbox:{update.id}:revoke:777",
        target_message_id=777,
        content_hash=hashlib.sha256(b"revoke:1001:777").hexdigest(),
        inbox_id=update.id,
    )
    arguments = {
        "workflow_status": "posted",
        "workflow_result": {"kind": "expense", "status": "posted"},
        "response": response,
        "pending_action": None,
        "consumed_action_id": None,
        "now": _NOW + timedelta(minutes=1, seconds=1),
        "revocations": (revocation, revocation),
    }

    repository.complete_inbox(claim, **arguments)

    assert (
        _count(
            postgres_url,
            "SELECT count(*) FROM messaging.telegram_outbox "
            "WHERE operation = 'revoke_controls'",
        )
        == 1
    )

    for minute in (2, 3):
        work = repository.claim_next_outbox(
            bot_key,
            now=_NOW + timedelta(minutes=minute),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        assert work is not None
        repository.complete_outbox(
            work,
            telegram_message_id=getattr(work.message, "target_message_id", 778),
            now=_NOW + timedelta(minutes=minute, seconds=30),
        )

    latencies = repository.text_delivery_latencies(
        bot_key,
        since=_NOW,
        limit=10,
    )
    assert len(latencies) == 1


@pytest.mark.postgres
def test_a_pinned_requeue_applies_once_and_then_reports_itself_done(
    postgres_url: str,
) -> None:
    """Let a lost response be retried without requeuing a second time."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 0)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(seconds=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=False,
        retry_at=None,
        response=_failure_response(claim.update, terminal=True),
        now=_NOW + timedelta(seconds=2),
    )
    item = repository.dead_letters(bot_key, limit=10)[0]
    assert item.replay_count == 0

    applied = repository.requeue_inbox(
        bot_key,
        item.id,
        expected_replay_count=0,
        now=_NOW + timedelta(seconds=3),
    )
    repeated = repository.requeue_inbox(
        bot_key,
        item.id,
        expected_replay_count=0,
        now=_NOW + timedelta(seconds=4),
    )

    assert applied is RequeueOutcome.APPLIED
    assert repeated is RequeueOutcome.REPLAYED
    assert (
        _count(
            postgres_url,
            "SELECT replay_count FROM messaging.telegram_inbox WHERE id = %s",
            (item.id,),
        )
        == 1
    )
    claimed = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(seconds=5),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claimed is not None
    assert claimed.update.source_reference == update.source_reference


@pytest.mark.postgres
def test_a_requeue_pinned_to_a_state_that_moved_on_changes_nothing(
    postgres_url: str,
) -> None:
    """Refuse a stale view exactly as a foreign or unknown item is refused."""

    repository, bot_key = _repository(postgres_url)
    update = _update(bot_key, 0)
    _ingest(repository, bot_key, update)
    claim = repository.claim_next_inbox(
        bot_key,
        now=_NOW + timedelta(seconds=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=False,
        retry_at=None,
        response=_failure_response(claim.update, terminal=True),
        now=_NOW + timedelta(seconds=2),
    )
    item = repository.dead_letters(bot_key, limit=10)[0]

    with pytest.raises(MessagingConflictError):
        repository.requeue_inbox(
            bot_key,
            item.id,
            expected_replay_count=4,
            now=_NOW + timedelta(seconds=3),
        )
    with pytest.raises(MessagingConflictError):
        repository.requeue_inbox(
            bot_key,
            uuid4(),
            expected_replay_count=0,
            now=_NOW + timedelta(seconds=3),
        )

    assert repository.dead_letters(bot_key, limit=10) == (item,)


@pytest.mark.postgres
def test_a_pinned_resend_starts_one_delivery_cycle_for_one_response(
    postgres_url: str,
) -> None:
    """Keep a repeated resend request from queuing a second physical message."""

    repository, bot_key = _repository(postgres_url)
    response_id = uuid4()
    repository.enqueue_notice(
        _response(bot_key=bot_key, inbox_id=None, response_id=response_id),
        now=_NOW,
    )
    now = _NOW + timedelta(minutes=1)
    for _ in range(5):
        claimed = repository.claim_next_outbox(
            bot_key,
            now=now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        assert claimed is not None
        repository.fail_outbox(
            claimed,
            error_code="telegram_unavailable",
            transient=True,
            retry_at=now + timedelta(seconds=2),
            now=now,
        )
        now += timedelta(seconds=2)
    item = repository.outbox_dead_letters(bot_key, limit=10)[0]
    assert item.replay_count == 0

    applied = repository.requeue_outbox(
        bot_key,
        item.id,
        expected_replay_count=0,
        now=now + timedelta(minutes=1),
    )
    repeated = repository.requeue_outbox(
        bot_key,
        item.id,
        expected_replay_count=0,
        now=now + timedelta(minutes=2),
    )

    assert applied is RequeueOutcome.APPLIED
    assert repeated is RequeueOutcome.REPLAYED
    assert (
        _count(
            postgres_url,
            "SELECT replay_count FROM messaging.telegram_outbox WHERE id = %s",
            (item.id,),
        )
        == 1
    )
    assert repository.outbox_dead_letters(bot_key, limit=10) == ()
