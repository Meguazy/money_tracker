"""Authorization, pairing, and normalization before durable ingestion.

Pairing lives here rather than in the worker for one reason: the message that
carries the code must never be stored. It is recognized, consumed, and dropped
before anything reaches the inbox, so a secret the owner typed cannot survive
in a durable row, a backup, or an operator dump.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from uuid import UUID, uuid4

from money_tracker.messaging import (
    MAX_TELEGRAM_FILE_BYTES,
    MessagingRepository,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
    OwnerBinding,
)
from money_tracker.presentation import pairing_confirmation
from money_tracker.telegram.contracts import IngestReport, TelegramInboundUpdate
from money_tracker.workflows import SupportedCommand, parse_command

_CALLBACK = re.compile(
    r"^a:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):([a-z][a-z0-9_]{0,15})$"
)
_MAX_TEXT_CHARACTERS = 4096


class TelegramUpdateIngestor:
    """Authorize private-owner updates and commit one complete observed batch.

    Attributes:
        The private repository port owns all durable state; this service retains
        no update or owner identity between calls.
    """

    def __init__(
        self,
        repository: MessagingRepository,
    ) -> None:
        """Bind the durable messaging port.

        Args:
            repository: Owner of offsets, bindings, and normalized inbox rows.
        """

        self._repository = repository

    def ingest(
        self,
        *,
        bot_key: str,
        expected_offset: int,
        updates: tuple[TelegramInboundUpdate, ...],
        now: datetime,
    ) -> IngestReport:
        """Normalize authorized work before atomically advancing the offset.

        Unauthorized users and non-private chats contribute only a counter. No
        message content, identity, or file metadata from them reaches storage.

        A private pairing attempt is consumed here and never retained. A
        successful one counts as an authorized observation that produced no
        work; a failed one counts as rejected, exactly like any other message
        from someone this bot does not recognize.

        Args:
            bot_key: Stable non-secret bot persistence scope.
            expected_offset: Offset used for the Bot API request.
            updates: Complete ordered response batch.
            now: Aware local acceptance timestamp.

        Returns:
            Content-free disposition counts and accepted normalized rows.
        """

        if not updates:
            self._repository.record_empty_poll(bot_key, now=now)
            return IngestReport(
                next_offset=expected_offset,
                accepted=(),
                rejected_count=0,
                unsupported_count=0,
                callback_query_ids=(),
            )
        identifiers = tuple(update.update_id for update in updates)
        if identifiers != tuple(sorted(set(identifiers))):
            raise ValueError("Telegram update identifiers must be unique and ordered")
        binding = self._repository.owner_binding(bot_key)
        accepted: list[NormalizedTelegramUpdate] = []
        callbacks: list[str] = []
        rejected = 0
        unsupported = 0
        for update in updates:
            code = _pairing_code(update)
            if code is not None:
                paired = self._pair(bot_key, update, code, now=now)
                if paired is None:
                    rejected += 1
                else:
                    binding = paired
                    unsupported += 1
                continue
            if (
                binding is None
                or update.chat_type != "private"
                or update.telegram_user_id != binding.telegram_user_id
                or update.telegram_chat_id != binding.telegram_chat_id
            ):
                rejected += 1
                continue
            normalized = _normalize(bot_key, update, now=now)
            if normalized is None:
                unsupported += 1
                continue
            accepted.append(normalized)
            if normalized.callback_query_id is not None:
                callbacks.append(normalized.callback_query_id)
        next_offset = self._repository.commit_ingest_batch(
            bot_key=bot_key,
            expected_offset=expected_offset,
            observed_update_ids=identifiers,
            accepted_updates=tuple(accepted),
            rejected_count=rejected,
            unsupported_count=unsupported,
            now=now,
        )
        return IngestReport(
            next_offset=next_offset,
            accepted=tuple(accepted),
            rejected_count=rejected,
            unsupported_count=unsupported,
            callback_query_ids=tuple(callbacks),
        )

    def _pair(
        self,
        bot_key: str,
        update: TelegramInboundUpdate,
        code: str,
        *,
        now: datetime,
    ) -> OwnerBinding | None:
        """Consume one pairing attempt without retaining what it carried.

        Args:
            bot_key: Bot and environment persistence scope.
            update: Private message naming the pairing command.
            code: Untrusted code the owner typed, possibly empty.
            now: Aware local instant.

        Returns:
            The resulting binding, or None when the attempt establishes none.
        """

        if (
            not code
            or update.telegram_user_id is None
            or update.telegram_chat_id is None
        ):
            return None
        chat_id = update.telegram_chat_id
        return self._repository.pair_owner(
            bot_key=bot_key,
            code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            telegram_user_id=update.telegram_user_id,
            telegram_chat_id=chat_id,
            confirmation=_confirmation(bot_key, chat_id),
            now=now,
        )


def _confirmation(bot_key: str, telegram_chat_id: int) -> OutboxMessageDraft:
    """Build the response queued with a newly created owner binding."""

    response = pairing_confirmation()
    return OutboxMessageDraft(
        id=uuid4(),
        bot_key=bot_key,
        telegram_chat_id=telegram_chat_id,
        logical_key="pairing:owner",
        response_kind=response.kind.value,
        text=response.text,
        content_hash=response.content_hash,
    )


def _pairing_code(update: TelegramInboundUpdate) -> str | None:
    """Return the code a private pairing attempt carries, if it is one.

    Recognition deliberately precedes authorization and succeeds even when the
    code is missing or wrong. Every private `/pair` is then dropped rather than
    stored, so a mistyped code cannot end up retained as ordinary text.

    Args:
        update: Parsed transport update, still untrusted.

    Returns:
        The remainder of the command, possibly empty, or None when the update
        is not a private pairing attempt.
    """

    if update.kind != "message" or update.chat_type != "private":
        return None
    text = (update.text or "").strip()
    if parse_command(text) is not SupportedCommand.PAIR:
        return None
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def _normalize(
    bot_key: str,
    update: TelegramInboundUpdate,
    *,
    now: datetime,
) -> NormalizedTelegramUpdate | None:
    """Translate one already-authorized update to minimum durable fields."""

    if update.kind == "unsupported":
        return None
    text = update.text
    if text is not None:
        if not text.strip() or len(text) > _MAX_TEXT_CHARACTERS:
            text = None
    image = update.image
    if image is not None and (
        image.file_size is not None and image.file_size > MAX_TELEGRAM_FILE_BYTES
    ):
        image = None
    callback = update.callback_data
    if update.kind == "callback_query":
        if (
            callback is None
            or _CALLBACK.fullmatch(callback) is None
            or update.callback_query_id is None
        ):
            return None
        match = _CALLBACK.fullmatch(callback)
        if match is None:
            return None
        action_id = UUID(match.group(1))
        if str(action_id) not in callback:
            return None
    if text is None and callback is None and image is None:
        return None
    if update.telegram_user_id is None or update.telegram_chat_id is None:
        return None
    return NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=bot_key,
        update_id=update.update_id,
        telegram_user_id=update.telegram_user_id,
        telegram_chat_id=update.telegram_chat_id,
        received_at=update.received_at,
        accepted_at=now,
        telegram_message_id=update.telegram_message_id,
        text=text,
        callback_action=callback,
        callback_query_id=update.callback_query_id,
        reply_to_message_id=update.reply_to_message_id,
        has_image=image is not None,
        telegram_file_id=None if image is None else image.file_id,
        telegram_file_unique_id=None if image is None else image.file_unique_id,
        file_media_type=None if image is None else image.media_type,
        file_size=None if image is None else image.file_size,
    )
