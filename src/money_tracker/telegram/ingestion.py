"""Authorization and normalization before durable Telegram ingestion."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

from money_tracker.messaging import (
    MAX_TELEGRAM_FILE_BYTES,
    MessagingRepository,
    NormalizedTelegramUpdate,
)
from money_tracker.telegram.contracts import IngestReport, TelegramInboundUpdate

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
