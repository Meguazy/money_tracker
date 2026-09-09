"""Typed, redacted values returned by the Telegram transport boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from money_tracker.messaging import NormalizedTelegramUpdate, require_aware


@dataclass(frozen=True, slots=True)
class BotIdentity:
    """Minimal proof that a Bot API credential names a bot.

    Attributes:
        bot_id: Positive Telegram bot identity, never emitted by diagnostics.
    """

    bot_id: int

    def __post_init__(self) -> None:
        """Reject malformed validation responses."""

        if self.bot_id <= 0:
            raise ValueError("Bot API returned an invalid bot identity")


@dataclass(frozen=True, slots=True)
class TelegramFile:
    """Opaque file identity and safe metadata without a download URL.

    Attributes:
        file_id: Bot-specific lookup identifier.
        file_unique_id: Stable Telegram file identity.
        media_type: Advertised supported image media type.
        file_size: Advertised byte count when present.
        file_path: Safe relative API path when explicitly resolved.
    """

    file_id: str
    file_unique_id: str
    media_type: str
    file_size: int | None = None
    file_path: str | None = None

    def __post_init__(self) -> None:
        """Reject missing opaque identities and unsafe remote paths."""

        if not self.file_id or not self.file_unique_id:
            raise ValueError("Telegram file identity is incomplete")
        if self.file_size is not None and self.file_size <= 0:
            raise ValueError("Telegram file size must be positive")
        if self.file_path is not None and (
            self.file_path.startswith("/")
            or ".." in self.file_path.split("/")
            or "://" in self.file_path
        ):
            raise ValueError("Telegram file path is unsafe")


@dataclass(frozen=True, slots=True)
class TelegramInboundUpdate:
    """Minimum parsed Bot API update retained only until durable ingestion.

    Attributes:
        update_id: Bot API ordering and acknowledgement identity.
        kind: Supported transport shape or unsupported sentinel.
        telegram_user_id: Sender identity needed only for authorization.
        telegram_chat_id: Chat identity needed only for authorization.
        chat_type: Telegram chat classification.
        received_at: Aware source timestamp.
        telegram_message_id: Source message identity when applicable.
        text: Message text or caption when applicable.
        callback_data: Untrusted bounded callback data.
        callback_query_id: Opaque acknowledgement identity.
        reply_to_message_id: Addressed bot message when applicable.
        image: Supported image metadata when present.
    """

    update_id: int
    kind: Literal["message", "callback_query", "unsupported"]
    telegram_user_id: int | None
    telegram_chat_id: int | None
    chat_type: str | None
    received_at: datetime
    telegram_message_id: int | None = None
    text: str | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None
    reply_to_message_id: int | None = None
    image: TelegramFile | None = None

    def __post_init__(self) -> None:
        """Validate ordering identity and transport timestamp."""

        require_aware(self.received_at, field="received_at")
        if self.update_id < 0:
            raise ValueError("Telegram update identifier cannot be negative")


@dataclass(frozen=True, slots=True)
class TelegramSentMessage:
    """Minimal successful send result needed for durable correlation.

    Attributes:
        telegram_message_id: Positive delivered message identity.
    """

    telegram_message_id: int

    def __post_init__(self) -> None:
        """Reject an invalid Bot API send result."""

        if self.telegram_message_id <= 0:
            raise ValueError("Bot API returned an invalid message identity")


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Content-free outcome of one durable Bot API batch.

    Attributes:
        next_offset: Persisted offset after the complete observed batch.
        accepted: Authorized normalized records committed locally.
        rejected_count: Unauthorized or non-private observation count.
        unsupported_count: Authorized but unsupported observation count.
        callback_query_ids: Accepted callbacks eligible for acknowledgement.
    """

    next_offset: int
    accepted: tuple[NormalizedTelegramUpdate, ...]
    rejected_count: int
    unsupported_count: int
    callback_query_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate safe aggregate ingestion counters."""

        if (
            self.next_offset < 0
            or self.rejected_count < 0
            or self.unsupported_count < 0
        ):
            raise ValueError("ingestion counters cannot be negative")
