"""Behavior tests for authorization and durable gateway ordering."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from money_tracker.messaging import BotEnvironment, OwnerBinding
from money_tracker.telegram import (
    BotIdentity,
    TelegramGateway,
    TelegramInboundUpdate,
    TelegramSettings,
    TelegramUpdateIngestor,
)

_NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


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
        return self.offset


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
