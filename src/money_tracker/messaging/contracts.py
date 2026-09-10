"""Transport-neutral contracts for durable channel messaging.

The contracts contain only normalized values required for recovery. They never
carry a Bot API token, a token-bearing URL, a raw Telegram update, or a model or
repository object.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

_BOT_KEY = re.compile(r"^[a-z0-9][a-z0-9:-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_TELEGRAM_FILE_BYTES = 20 * 1024 * 1024


class BotEnvironment(StrEnum):
    """Credential and persistence environment for one Telegram bot."""

    PRODUCTION = "production"
    TEST = "test"


class InboxStatus(StrEnum):
    """Durable processing state of one accepted update."""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"


class OutboxStatus(StrEnum):
    """Durable delivery state of one logical outbound message."""

    PENDING = "pending"
    SENDING = "sending"
    RETRY = "retry"
    SENT = "sent"
    DEAD_LETTER = "dead_letter"


class PendingActionStatus(StrEnum):
    """Lifecycle of a correlation exposed to the owner."""

    ACTIVE = "active"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


class PendingActionTarget(StrEnum):
    """Trusted target type resolved by a pending interaction."""

    CATEGORY_REVIEW = "category_review"
    MOVEMENT = "movement"


def validate_bot_key(value: str) -> str:
    """Validate a non-secret stable bot persistence key.

    Args:
        value: Key selected by configuration. Tests may add an isolated suffix.

    Returns:
        The unchanged valid key.

    Raises:
        ValueError: If the key is blank, too long, or contains unsafe syntax.
    """

    if _BOT_KEY.fullmatch(value) is None:
        raise ValueError("bot key must be lowercase alphanumeric with ':' or '-'")
    return value


def require_aware(value: datetime, *, field: str) -> datetime:
    """Require a timezone-aware instant at a persistence boundary.

    Args:
        value: Instant to validate.
        field: Safe field name used in the diagnostic.

    Returns:
        The unchanged aware instant.

    Raises:
        ValueError: If the instant is naive.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class NormalizedTelegramUpdate:
    """Minimum authorized Telegram update retained for recovery.

    Attributes:
        id: Locally generated stable inbox identifier.
        bot_key: Non-secret bot/environment persistence key.
        update_id: Telegram ordering and deduplication identity.
        telegram_user_id: Authorized sender identity.
        telegram_chat_id: Authorized private chat identity.
        received_at: Telegram message/callback timestamp.
        accepted_at: Local durable-ingestion timestamp.
        telegram_message_id: Source message identity when present.
        text: Owner text or caption when present.
        callback_action: Bounded action name decoded by the gateway.
        callback_query_id: Identifier needed for callback acknowledgement.
        reply_to_message_id: Bot message addressed by an owner reply.
        has_image: Whether supported image metadata is attached.
        telegram_file_id: Opaque Bot API file lookup identifier.
        telegram_file_unique_id: Stable Telegram file deduplication identifier.
        file_media_type: Advertised supported image media type when available.
        file_size: Advertised encoded size when available.
    """

    id: UUID
    bot_key: str
    update_id: int
    telegram_user_id: int
    telegram_chat_id: int
    received_at: datetime
    accepted_at: datetime
    telegram_message_id: int | None = None
    text: str | None = None
    callback_action: str | None = None
    callback_query_id: str | None = None
    reply_to_message_id: int | None = None
    has_image: bool = False
    telegram_file_id: str | None = None
    telegram_file_unique_id: str | None = None
    file_media_type: str | None = None
    file_size: int | None = None

    def __post_init__(self) -> None:
        """Reject values that cannot form a safe recoverable work item."""

        validate_bot_key(self.bot_key)
        require_aware(self.received_at, field="received_at")
        require_aware(self.accepted_at, field="accepted_at")
        if self.update_id < 0:
            raise ValueError("update_id cannot be negative")
        if self.telegram_user_id <= 0 or self.telegram_chat_id <= 0:
            raise ValueError("Telegram user and chat identifiers must be positive")
        for name, value in (
            ("telegram_message_id", self.telegram_message_id),
            ("reply_to_message_id", self.reply_to_message_id),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.text is None and self.callback_action is None and not self.has_image:
            raise ValueError("normalized update carries no supported content")
        if self.has_image and (
            not self.telegram_file_id or not self.telegram_file_unique_id
        ):
            raise ValueError("image update requires safe Telegram file identifiers")
        if self.file_media_type not in {
            None,
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise ValueError("unsupported Telegram image media type")
        if self.file_size is not None and not (
            0 < self.file_size <= MAX_TELEGRAM_FILE_BYTES
        ):
            raise ValueError("Telegram file size is outside the accepted limit")

    @property
    def source_reference(self) -> str:
        """Return the stable finance/workflow idempotency source."""

        return f"telegram:{self.bot_key}:{self.update_id}"


@dataclass(frozen=True, slots=True)
class InboxClaim:
    """Leased oldest inbox work item returned to the sequential worker."""

    update: NormalizedTelegramUpdate
    status: InboxStatus
    attempt_count: int
    replay_count: int
    claim_token: UUID
    claimed_until: datetime

    def __post_init__(self) -> None:
        """Validate the lease and attempt metadata."""

        require_aware(self.claimed_until, field="claimed_until")
        if self.status is not InboxStatus.PROCESSING:
            raise ValueError("inbox claim must be processing")
        if self.attempt_count <= 0 or self.replay_count < 0:
            raise ValueError("invalid inbox claim counters")


@dataclass(frozen=True, slots=True)
class OutboundButton:
    """One bounded owner-facing button stored with an outbox message."""

    label: str
    callback_data: str

    def __post_init__(self) -> None:
        """Enforce Telegram's compact callback contract."""

        if not self.label.strip():
            raise ValueError("button label cannot be blank")
        if not self.callback_data or len(self.callback_data.encode("utf-8")) > 64:
            raise ValueError("callback data must contain at most 64 UTF-8 bytes")


@dataclass(frozen=True, slots=True)
class OutboxMessageDraft:
    """One logical outbound response to create idempotently."""

    id: UUID
    bot_key: str
    telegram_chat_id: int
    logical_key: str
    response_kind: str
    text: str
    content_hash: str
    buttons: tuple[OutboundButton, ...] = ()
    inbox_id: UUID | None = None
    reply_to_message_id: int | None = None

    def __post_init__(self) -> None:
        """Validate stable identity and non-empty owner-visible content."""

        validate_bot_key(self.bot_key)
        if self.telegram_chat_id <= 0:
            raise ValueError("outbox chat identifier must be positive")
        if not self.logical_key.strip() or not self.response_kind.strip():
            raise ValueError("outbox requires logical identity")
        if not self.text.strip():
            raise ValueError("outbox text cannot be blank")
        if _SHA256.fullmatch(self.content_hash) is None:
            raise ValueError("outbox content hash must be lowercase SHA-256")
        if self.reply_to_message_id is not None and self.reply_to_message_id <= 0:
            raise ValueError("reply target must be positive")


@dataclass(frozen=True, slots=True)
class ControlRevocationDraft:
    """One idempotent request to strip the controls off a delivered summary.

    This is its own contract rather than an emptier `OutboxMessageDraft`,
    because the two have opposite invariants: a response must carry owner
    text and learns its Telegram identifier only after delivery, while a
    revocation carries no text and names its target up front. Widening the
    response contract to admit blank text would remove the check that keeps an
    empty message from ever reaching the owner.
    """

    id: UUID
    bot_key: str
    telegram_chat_id: int
    logical_key: str
    target_message_id: int
    content_hash: str
    inbox_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate stable identity and a concrete revocation target."""

        validate_bot_key(self.bot_key)
        if self.telegram_chat_id <= 0:
            raise ValueError("outbox chat identifier must be positive")
        if not self.logical_key.strip():
            raise ValueError("outbox requires logical identity")
        if self.target_message_id <= 0:
            raise ValueError("revocation target must be positive")
        if _SHA256.fullmatch(self.content_hash) is None:
            raise ValueError("outbox content hash must be lowercase SHA-256")


OutboundWork = OutboxMessageDraft | ControlRevocationDraft
"""Either durable outbound operation the gateway delivery loop performs."""


@dataclass(frozen=True, slots=True)
class PendingActionDraft:
    """Durable trusted mapping from a visible interaction to its target."""

    id: UUID
    bot_key: str
    profile_id: UUID
    telegram_user_id: int
    telegram_chat_id: int
    outbox_id: UUID
    target_type: PendingActionTarget
    target_id: UUID
    allowed_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject a cross-scope or actionless mapping."""

        validate_bot_key(self.bot_key)
        if self.telegram_user_id <= 0 or self.telegram_chat_id <= 0:
            raise ValueError("pending action requires positive Telegram identities")
        if not self.allowed_actions or any(
            not action.strip() for action in self.allowed_actions
        ):
            raise ValueError("pending action requires bounded action names")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("pending action names must be unique")


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    """Leased outbound work ready for the Bot API sender."""

    message: OutboundWork
    status: OutboxStatus
    attempt_count: int
    claim_token: UUID
    claimed_until: datetime

    def __post_init__(self) -> None:
        """Validate the delivery lease."""

        require_aware(self.claimed_until, field="claimed_until")
        if self.status is not OutboxStatus.SENDING:
            raise ValueError("outbox claim must be sending")
        if self.attempt_count <= 0:
            raise ValueError("outbox attempt count must be positive")


@dataclass(frozen=True, slots=True)
class OwnerBinding:
    """Trusted sole-owner Telegram identity for one bot."""

    bot_key: str
    profile_id: UUID
    telegram_user_id: int
    telegram_chat_id: int
    paired_at: datetime

    def __post_init__(self) -> None:
        """Validate the persisted private-chat binding."""

        validate_bot_key(self.bot_key)
        require_aware(self.paired_at, field="paired_at")
        if self.telegram_user_id <= 0 or self.telegram_chat_id <= 0:
            raise ValueError("owner binding requires positive Telegram identities")


@dataclass(frozen=True, slots=True)
class PendingAction:
    """Persisted active or terminal owner-interaction correlation."""

    id: UUID
    bot_key: str
    profile_id: UUID
    telegram_user_id: int
    telegram_chat_id: int
    outbox_id: UUID
    target_type: PendingActionTarget
    target_id: UUID
    allowed_actions: tuple[str, ...]
    status: PendingActionStatus
    telegram_message_id: int | None
    created_at: datetime
    consumed_at: datetime | None

    def __post_init__(self) -> None:
        """Validate persisted action time and status consistency."""

        validate_bot_key(self.bot_key)
        require_aware(self.created_at, field="created_at")
        if self.consumed_at is not None:
            require_aware(self.consumed_at, field="consumed_at")
        if (self.status is PendingActionStatus.CONSUMED) != (
            self.consumed_at is not None
        ):
            raise ValueError("pending action consumption state is inconsistent")


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """One failed work item an operator may inspect and replay.

    The fields are exactly what deciding on a replay needs and nothing more:
    no owner text, no Telegram identity, and no failure message. An operator
    troubleshooting a queue does not need to read the owner's finances, and a
    record that carried them would end up in a terminal or a ticket.

    Attributes:
        id: Stable inbox identifier a replay names.
        update_id: Telegram ordering identity, useful for correlating a report.
        source_reference: Stable idempotency source a replay preserves.
        attempt_count: Automatic attempts already spent.
        replay_count: Times an operator already returned this item to pending.
        last_error_code: Safe bounded reason the final attempt recorded.
        accepted_at: When the gateway durably accepted the update.
    """

    id: UUID
    update_id: int
    source_reference: str
    attempt_count: int
    replay_count: int
    last_error_code: str | None
    accepted_at: datetime

    def __post_init__(self) -> None:
        """Validate content-free operator metadata."""

        require_aware(self.accepted_at, field="accepted_at")
        if self.update_id < 0 or self.attempt_count < 0 or self.replay_count < 0:
            raise ValueError("dead letter counters cannot be negative")
        if not self.source_reference.strip():
            raise ValueError("a dead letter must keep its source reference")


@dataclass(frozen=True, slots=True)
class OutboxDeadLetter:
    """Content-free projection of one failed logical outbound message.

    Attributes:
        id: Stable outbox identifier used for an explicit manual resend.
        response_kind: Bounded presentation kind useful for diagnosis.
        attempt_count: Delivery attempts in the exhausted automatic cycle.
        replay_count: Manual resend cycles already requested by an operator.
        last_error_code: Safe bounded reason recorded by the final attempt.
        created_at: When the logical response was first committed.
    """

    id: UUID
    response_kind: str
    attempt_count: int
    replay_count: int
    last_error_code: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate content-free delivery failure metadata."""

        require_aware(self.created_at, field="created_at")
        if not self.response_kind.strip():
            raise ValueError("outbox dead letter requires a response kind")
        if self.attempt_count < 0 or self.replay_count < 0:
            raise ValueError("outbox dead letter counters cannot be negative")


@dataclass(frozen=True, slots=True)
class MessagingHealth:
    """Content-free messaging state summary for operations."""

    bot_key: str
    next_offset: int
    pending_inbox: int
    retrying_inbox: int
    inbox_dead_letters: int
    pending_outbox: int
    retrying_outbox: int
    outbox_dead_letters: int
    active_actions: int
    last_observed_at: datetime | None
    last_poll_at: datetime | None
    oldest_inbox_at: datetime | None
    oldest_outbox_at: datetime | None

    def __post_init__(self) -> None:
        """Validate non-negative operational counters."""

        validate_bot_key(self.bot_key)
        if self.next_offset < 0 or any(
            value < 0
            for value in (
                self.pending_inbox,
                self.retrying_inbox,
                self.inbox_dead_letters,
                self.pending_outbox,
                self.retrying_outbox,
                self.outbox_dead_letters,
                self.active_actions,
            )
        ):
            raise ValueError("messaging health counters cannot be negative")
        for name, value in (
            ("last_observed_at", self.last_observed_at),
            ("last_poll_at", self.last_poll_at),
            ("oldest_inbox_at", self.oldest_inbox_at),
            ("oldest_outbox_at", self.oldest_outbox_at),
        ):
            if value is not None:
                require_aware(value, field=name)


WorkflowResult = Mapping[str, object]
"""JSON-compatible typed workflow result persisted before inbox completion."""
