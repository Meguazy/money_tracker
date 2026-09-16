"""Behavior tests for authorization and durable gateway ordering."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from money_tracker.messaging import (
    BotEnvironment,
    MessagingHealth,
    OutboxMessageDraft,
    OwnerBinding,
)
from money_tracker.telegram import (
    BotIdentity,
    TelegramGateway,
    TelegramInboundUpdate,
    TelegramSentMessage,
    TelegramSettings,
    TelegramUpdateIngestor,
)
from money_tracker.telegram.transport import TelegramTransportError
from tests.unit.money_tracker.tracing.memory_tracer import InMemoryTracer

_NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)
_CODE = "one-use-synthetic-code"
_CODE_HASH = hashlib.sha256(_CODE.encode()).hexdigest()


class _Clock:
    """Fixed aware gateway clock."""

    def now(self) -> datetime:
        """Return the deterministic test instant."""

        return _NOW


class _Repository:
    """Small real-behavior fake for the external PostgreSQL boundary."""

    def __init__(self, *, paired: bool = True, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.offset = 0
        self.rows: tuple[object, ...] = ()
        self.binding = (
            OwnerBinding("telegram-test", uuid4(), 1001, 1001, _NOW) if paired else None
        )
        self.confirmations: list[Any] = []
        self.counters: tuple[int, int] = (0, 0)
        self.notices: list[OutboxMessageDraft] = []
        self.last_poll_at: datetime | None = _NOW

    def ensure_bot(self, *_: object, **__: object) -> None:
        """Record gateway initialization."""

        self.events.append("ensure")

    def polling_offset(self, _: str) -> int:
        """Return the current fake durable offset."""

        return self.offset

    def owner_binding(self, _: str) -> OwnerBinding | None:
        """Return the trusted persisted owner scope."""

        return self.binding

    def record_empty_poll(self, _: str, **__: object) -> None:
        """Record an empty committed poll."""

        self.events.append("empty")

    def commit_ingest_batch(self, **values: Any) -> int:
        """Store normalized rows before returning the advanced offset."""

        self.events.append("commit")
        self.rows = tuple(values["accepted_updates"])
        self.offset = max(values["observed_update_ids"]) + 1
        self.counters = (values["rejected_count"], values["unsupported_count"])
        return self.offset

    def health(self, _: str) -> MessagingHealth:
        """Return a content-free snapshot carrying the configured poll time."""

        return MessagingHealth(
            bot_key="telegram-test",
            next_offset=self.offset,
            pending_inbox=0,
            retrying_inbox=0,
            inbox_dead_letters=0,
            pending_outbox=0,
            retrying_outbox=0,
            outbox_dead_letters=0,
            active_actions=0,
            last_observed_at=self.last_poll_at,
            last_poll_at=self.last_poll_at,
            oldest_inbox_at=None,
            oldest_outbox_at=None,
        )

    def enqueue_notice(self, notice: OutboxMessageDraft, **_: Any) -> UUID:
        """Record one owner notice that answers no inbox item."""

        self.notices.append(notice)
        return notice.id

    def pair_owner(self, **values: Any) -> OwnerBinding | None:
        """Bind the owner only for the one live synthetic code."""

        self.events.append("pair")
        self.confirmations.append(values["confirmation"])
        if self.binding is not None:
            matches = (
                self.binding.telegram_user_id,
                self.binding.telegram_chat_id,
            ) == (values["telegram_user_id"], values["telegram_chat_id"])
            return self.binding if matches else None
        if values["code_hash"] != _CODE_HASH:
            return None
        self.binding = OwnerBinding(
            "telegram-test",
            uuid4(),
            values["telegram_user_id"],
            values["telegram_chat_id"],
            _NOW,
        )
        return self.binding


class _Transport:
    """Deterministic fake for the genuinely external Bot API boundary."""

    def __init__(
        self,
        updates: tuple[TelegramInboundUpdate, ...],
        events: list[str],
    ) -> None:
        self.updates = updates
        self.events = events

    def validate_bot(self) -> BotIdentity:
        """Record credential validation."""

        self.events.append("validate")
        return BotIdentity(9)

    def disable_webhook(self) -> None:
        """Record backlog-preserving webhook removal."""

        self.events.append("webhook")

    def get_updates(self, *, offset: int) -> tuple[TelegramInboundUpdate, ...]:
        """Return the configured batch."""

        self.events.append(f"poll:{offset}")
        return self.updates

    def answer_callback_query(self, _: str) -> None:
        """Record callback acknowledgement."""

        self.events.append("ack")

    def close(self) -> None:
        """Record resource release."""

        self.events.append("close")

    def get_file(self, **_: object) -> object:
        """This gateway test never resolves file paths."""

        raise AssertionError("file lookup must not occur during ingestion")

    def send_message(self, _: object) -> object:
        """This ingress test never sends an outbox message."""

        raise AssertionError("send must not occur during ingestion")


def _settings() -> TelegramSettings:
    """Return one explicit test-bot configuration."""

    return TelegramSettings(
        environment=BotEnvironment.TEST,
        bot_key="telegram-test",
        token_file=Path("/not/read"),
    )


def _message(
    *,
    update_id: int,
    user_id: int = 1001,
    chat_id: int = 1001,
    chat_type: str = "private",
) -> TelegramInboundUpdate:
    """Build one synthetic parsed message."""

    return TelegramInboundUpdate(
        update_id=update_id,
        kind="message",
        telegram_user_id=user_id,
        telegram_chat_id=chat_id,
        chat_type=chat_type,
        received_at=_NOW,
        telegram_message_id=update_id + 1,
        text="Synthetic message",
    )


def test_gateway_commits_before_callback_ack_and_next_poll() -> None:
    """Advance acknowledgement only after normalized work is durable."""

    events: list[str] = []
    callback = TelegramInboundUpdate(
        update_id=0,
        kind="callback_query",
        telegram_user_id=1001,
        telegram_chat_id=1001,
        chat_type="private",
        received_at=_NOW,
        telegram_message_id=8,
        callback_data="a:00000000-0000-0000-0000-000000000001:undo",
        callback_query_id="callback",
    )
    repository = _Repository(events=events)
    transport = _Transport((callback,), events)
    gateway = TelegramGateway(
        settings=_settings(),
        transport=transport,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        ingestor=TelegramUpdateIngestor(repository),  # type: ignore[arg-type]
        clock=_Clock(),
    )

    gateway.prepare()
    report = gateway.run_once()

    assert report.next_offset == 1
    assert events == ["ensure", "validate", "webhook", "poll:0", "commit", "ack"]
    assert len(repository.rows) == 1


def test_unauthorized_users_and_groups_create_no_work() -> None:
    """Discard foreign and non-private content before durable normalization."""

    repository = _Repository()
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]
    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(
            _message(update_id=0, user_id=2002, chat_id=2002),
            _message(update_id=1, chat_id=-1002, chat_type="group"),
        ),
        now=_NOW,
    )

    assert report.rejected_count == 2
    assert not report.accepted
    assert not repository.rows
    assert repository.offset == 2


def test_unpaired_and_unsupported_updates_advance_without_work() -> None:
    """Avoid backlog loops while retaining no unauthorized private content."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]
    unsupported = TelegramInboundUpdate(
        update_id=0,
        kind="unsupported",
        telegram_user_id=None,
        telegram_chat_id=None,
        chat_type=None,
        received_at=_NOW,
    )
    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(unsupported,),
        now=_NOW,
    )

    assert report.rejected_count == 1
    assert not repository.rows
    assert repository.offset == 1


def test_outbox_delivery_commits_only_after_bot_api_success() -> None:
    """Translate one durable lease to a send result and correlation commit."""

    from datetime import timedelta
    from uuid import uuid4

    from money_tracker.messaging import (
        OutboxClaim,
        OutboxMessageDraft,
        OutboxStatus,
    )
    from money_tracker.telegram import TelegramSentMessage

    events: list[str] = []
    repository = _Repository(events=events)
    message = OutboxMessageDraft(
        id=uuid4(),
        bot_key="telegram-test",
        telegram_chat_id=1001,
        logical_key="integration:response",
        response_kind="help",
        text="Risposta sintetica",
        content_hash="a" * 64,
    )
    claim = OutboxClaim(
        message=message,
        status=OutboxStatus.SENDING,
        attempt_count=1,
        claim_token=uuid4(),
        claimed_until=_NOW + timedelta(minutes=1),
    )

    def claim_next_outbox(*_: object, **__: object) -> OutboxClaim:
        events.append("claim-outbox")
        return claim

    def complete_outbox(*_: object, **__: object) -> None:
        events.append("complete-outbox")

    repository.claim_next_outbox = claim_next_outbox  # type: ignore[attr-defined]
    repository.complete_outbox = complete_outbox  # type: ignore[attr-defined]

    class SendingTransport(_Transport):
        """Bot API fake that returns one successful send identity."""

        def send_message(self, _: object) -> TelegramSentMessage:
            """Record external success before the durable completion."""

            self.events.append("send")
            return TelegramSentMessage(77)

    transport = SendingTransport((), events)
    gateway = TelegramGateway(
        settings=_settings(),
        transport=transport,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        ingestor=TelegramUpdateIngestor(repository),  # type: ignore[arg-type]
        clock=_Clock(),
    )

    assert gateway.deliver_outbox_once()
    assert events == ["claim-outbox", "send", "complete-outbox"]


def _pair_message(
    *,
    update_id: int = 0,
    code: str | None = _CODE,
    user_id: int = 1001,
    chat_id: int = 1001,
    chat_type: str = "private",
) -> TelegramInboundUpdate:
    """Build one synthetic pairing attempt."""

    return TelegramInboundUpdate(
        update_id=update_id,
        kind="message",
        telegram_user_id=user_id,
        telegram_chat_id=chat_id,
        chat_type=chat_type,
        received_at=_NOW,
        telegram_message_id=update_id + 1,
        text="/pair" if code is None else f"/pair {code}",
    )


def test_pairing_binds_the_owner_without_storing_the_code() -> None:
    """Create the binding and leave no trace of the secret that made it."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(),),
        now=_NOW,
    )

    assert repository.binding is not None
    assert repository.binding.telegram_user_id == 1001
    assert report.accepted == ()
    assert repository.rows == ()
    assert report.unsupported_count == 1
    assert report.rejected_count == 0


def test_the_pairing_confirmation_is_queued_for_the_owner_chat() -> None:
    """Tell the newly bound owner that pairing worked, in their own chat."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(),),
        now=_NOW,
    )

    confirmation = repository.confirmations[0]
    assert confirmation.telegram_chat_id == 1001
    assert confirmation.bot_key == "telegram-test"
    assert confirmation.inbox_id is None
    assert confirmation.buttons == ()
    assert _CODE not in confirmation.text


def test_a_wrong_pairing_code_binds_nothing_and_stores_nothing() -> None:
    """Refuse the attempt without retaining the code that failed."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(code="wrong-code"),),
        now=_NOW,
    )

    assert repository.binding is None
    assert repository.rows == ()
    assert report.rejected_count == 1


def test_a_pairing_command_without_a_code_is_never_retained() -> None:
    """Drop a bare /pair rather than storing it as ordinary owner text."""

    repository = _Repository()
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(code=None),),
        now=_NOW,
    )

    assert repository.rows == ()
    assert report.accepted == ()


def test_a_paired_owner_never_stores_a_later_pairing_attempt() -> None:
    """Keep a resent code out of storage even once the binding exists."""

    repository = _Repository()
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(),),
        now=_NOW,
    )

    assert repository.rows == ()
    assert all(_CODE not in str(row) for row in repository.rows)


def test_a_stranger_cannot_pair_a_bot_that_already_has_an_owner() -> None:
    """Refuse a second owner instead of rebinding the bot."""

    repository = _Repository()
    original = repository.binding
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(user_id=2002, chat_id=2002),),
        now=_NOW,
    )

    assert repository.binding is original
    assert report.rejected_count == 1


def test_a_group_pairing_attempt_is_not_treated_as_pairing() -> None:
    """Never create the sole private owner binding from a group message."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(chat_id=-1002, chat_type="group"),),
        now=_NOW,
    )

    assert repository.binding is None
    assert "pair" not in repository.events
    assert report.rejected_count == 1


def test_work_after_a_successful_pairing_is_accepted_in_the_same_batch() -> None:
    """Let the owner's first real message land without waiting for a poll."""

    repository = _Repository(paired=False)
    ingestor = TelegramUpdateIngestor(repository)  # type: ignore[arg-type]

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=(_pair_message(update_id=0), _message(update_id=1)),
        now=_NOW,
    )

    assert len(report.accepted) == 1
    assert report.accepted[0].text == "Synthetic message"


def _downtime_gateway(repository: _Repository, clock_now: datetime) -> TelegramGateway:
    """Build one gateway whose clock sits at the configured instant."""

    class _At:
        def now(self) -> datetime:
            """Return the configured restart instant."""

            return clock_now

    return TelegramGateway(
        settings=_settings(),
        transport=_Transport((), []),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        ingestor=TelegramUpdateIngestor(repository),  # type: ignore[arg-type]
        clock=_At(),
    )


def test_a_short_outage_warns_the_owner_about_nothing() -> None:
    """Stay quiet when Telegram still holds everything it received."""

    repository = _Repository()

    _downtime_gateway(repository, _NOW + timedelta(hours=23)).prepare()

    assert repository.notices == []


def test_a_long_outage_warns_that_updates_may_be_lost() -> None:
    """Say plainly that Telegram may have discarded what it could not deliver."""

    repository = _Repository()

    _downtime_gateway(repository, _NOW + timedelta(hours=30)).prepare()

    assert len(repository.notices) == 1
    notice = repository.notices[0]
    assert notice.response_kind == "downtime"
    assert notice.telegram_chat_id == 1001
    assert notice.inbox_id is None
    assert notice.logical_key == f"downtime:{_NOW.isoformat()}"


def test_restarting_inside_one_outage_produces_one_warning() -> None:
    """Key the warning by the gap it describes, not by the restart count."""

    repository = _Repository()

    _downtime_gateway(repository, _NOW + timedelta(hours=30)).prepare()
    _downtime_gateway(repository, _NOW + timedelta(hours=31)).prepare()

    assert len({notice.logical_key for notice in repository.notices}) == 1
    assert len({notice.id for notice in repository.notices}) == 1


def test_an_unpaired_bot_warns_nobody() -> None:
    """Never queue a message for an owner chat that does not exist yet."""

    repository = _Repository(paired=False)

    _downtime_gateway(repository, _NOW + timedelta(hours=30)).prepare()

    assert repository.notices == []


def test_a_bot_that_never_polled_warns_nobody() -> None:
    """Treat a first start as a first start rather than an infinite outage."""

    repository = _Repository()
    repository.last_poll_at = None

    _downtime_gateway(repository, _NOW + timedelta(days=400)).prepare()

    assert repository.notices == []


def _traced_gateway(
    repository: _Repository,
    transport: _Transport,
    tracer: InMemoryTracer,
) -> TelegramGateway:
    """Assemble the production gateway with an in-memory tracer."""

    return TelegramGateway(
        settings=_settings(),
        transport=transport,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        ingestor=TelegramUpdateIngestor(repository),  # type: ignore[arg-type]
        clock=_Clock(),
        tracer=tracer,
    )


def test_each_accepted_update_starts_its_own_trace_after_commit() -> None:
    """Open one ingest stage per stored update, seeded by its source reference."""

    events: list[str] = []
    tracer = InMemoryTracer()
    repository = _Repository(events=events)
    transport = _Transport(
        (
            _message(update_id=4),
            _message(update_id=5, user_id=2002, chat_id=2002),
            _message(update_id=6),
        ),
        events,
    )

    _traced_gateway(repository, transport, tracer).run_once()

    spans = tracer.named("telegram-ingest")
    assert [span.trace_seed for span in spans] == [
        "telegram:telegram-test:4",
        "telegram:telegram-test:6",
    ]
    assert {span.trace_name for span in spans} == {"telegram-update"}
    first = spans[0]
    assert isinstance(first.input, dict)
    assert first.input["text"] == "Synthetic message"
    assert first.input["update_id"] == 4
    assert first.metadata["batch_accepted"] == 2
    assert first.metadata["batch_rejected"] == 1
    assert first.metadata["inbox_id"] == str(repository.rows[0].id)  # type: ignore[attr-defined]
    recorded = repr([(span.input, span.metadata, span.output) for span in spans])
    assert "1001" not in recorded
    assert "2002" not in recorded


def test_a_callback_ingest_records_its_acknowledgement() -> None:
    """Say whether Telegram accepted the callback acknowledgement."""

    events: list[str] = []
    tracer = InMemoryTracer()
    callback = TelegramInboundUpdate(
        update_id=0,
        kind="callback_query",
        telegram_user_id=1001,
        telegram_chat_id=1001,
        chat_type="private",
        received_at=_NOW,
        telegram_message_id=8,
        callback_data="a:00000000-0000-0000-0000-000000000001:undo",
        callback_query_id="callback-query-identity",
    )
    repository = _Repository(events=events)

    _traced_gateway(repository, _Transport((callback,), events), tracer).run_once()

    (span,) = tracer.named("telegram-ingest")
    assert span.metadata["callback_acknowledged"] is True
    assert isinstance(span.input, dict)
    assert span.input["callback_action"] == callback.callback_data
    assert "callback-query-identity" not in repr(span.input)


def test_an_untraced_gateway_opens_no_ingest_span() -> None:
    """Leave the default gateway exactly as it was without a tracer."""

    events: list[str] = []
    repository = _Repository(events=events)
    gateway = TelegramGateway(
        settings=_settings(),
        transport=_Transport((_message(update_id=1),), events),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        ingestor=TelegramUpdateIngestor(repository),  # type: ignore[arg-type]
        clock=_Clock(),
    )

    report = gateway.run_once()

    assert len(report.accepted) == 1


def _delivery(
    *,
    source_reference: str | None,
    transport: _Transport,
    tracer: InMemoryTracer,
    events: list[str],
) -> TelegramGateway:
    """Assemble a gateway whose repository leases one response."""

    from money_tracker.messaging import OutboxClaim, OutboxStatus

    repository = _Repository(events=events)
    message = OutboxMessageDraft(
        id=UUID("00000000-0000-0000-0000-00000000d001"),
        bot_key="telegram-test",
        telegram_chat_id=1001,
        logical_key="inbox:x:response:movement",
        response_kind="movement",
        text="Risposta sintetica",
        content_hash="a" * 64,
        inbox_id=UUID("00000000-0000-0000-0000-00000000d002"),
    )
    claim = OutboxClaim(
        message=message,
        status=OutboxStatus.SENDING,
        attempt_count=2,
        claim_token=uuid4(),
        claimed_until=_NOW + timedelta(minutes=1),
        source_reference=source_reference,
    )

    def claim_next_outbox(*_: object, **__: object) -> OutboxClaim:
        return claim

    def complete_outbox(*_: object, **__: object) -> None:
        events.append("complete-outbox")

    def fail_outbox(*_: object, **values: object) -> None:
        events.append(f"fail-outbox:{values['error_code']}")

    repository.claim_next_outbox = claim_next_outbox  # type: ignore[attr-defined]
    repository.complete_outbox = complete_outbox  # type: ignore[attr-defined]
    repository.fail_outbox = fail_outbox  # type: ignore[attr-defined]
    return _traced_gateway(repository, transport, tracer)


class _SendingTransport(_Transport):
    """Bot API fake that accepts every send."""

    def send_message(self, _: object) -> TelegramSentMessage:
        """Return one successful send identity."""

        self.events.append("send")
        return TelegramSentMessage(77)


class _RefusingTransport(_Transport):
    """Bot API fake whose sends are refused transiently."""

    def send_message(self, _: object) -> TelegramSentMessage:
        """Refuse like a rate-limited Bot API."""

        raise TelegramTransportError("rate_limited", transient=True)


def test_delivering_an_answer_joins_the_trace_of_its_update() -> None:
    """Record the delivered text and message identity in the update's trace."""

    events: list[str] = []
    tracer = InMemoryTracer()
    gateway = _delivery(
        source_reference="telegram:telegram-test:41",
        transport=_SendingTransport((), events),
        tracer=tracer,
        events=events,
    )

    assert gateway.deliver_outbox_once()

    (span,) = tracer.named("telegram-delivery")
    assert span.trace_seed == "telegram:telegram-test:41"
    assert span.trace_name == "telegram-update"
    assert isinstance(span.input, dict)
    assert span.input["text"] == "Risposta sintetica"
    assert span.input["operation"] == "send_message"
    assert span.metadata["attempt_count"] == 2
    assert span.output == {"delivery_outcome": "sent", "telegram_message_id": 77}
    assert "1001" not in repr((span.input, span.metadata))


def test_a_refused_delivery_records_its_reason_and_retry() -> None:
    """Mark the delivery stage failed with the transport reason code."""

    events: list[str] = []
    tracer = InMemoryTracer()
    gateway = _delivery(
        source_reference="telegram:telegram-test:41",
        transport=_RefusingTransport((), events),
        tracer=tracer,
        events=events,
    )

    assert not gateway.deliver_outbox_once()

    (span,) = tracer.named("telegram-delivery")
    assert span.failure == ("rate_limited", "TelegramTransportError")
    assert span.metadata["delivery_outcome"] == "retry_scheduled"
    assert span.metadata["failure_transient"] is True
    assert events == ["fail-outbox:rate_limited"]


def test_work_no_update_caused_is_delivered_untraced() -> None:
    """Keep pairing confirmations and downtime notices out of every trace."""

    events: list[str] = []
    tracer = InMemoryTracer()
    gateway = _delivery(
        source_reference=None,
        transport=_SendingTransport((), events),
        tracer=tracer,
        events=events,
    )

    assert gateway.deliver_outbox_once()
    assert tracer.observations == []
