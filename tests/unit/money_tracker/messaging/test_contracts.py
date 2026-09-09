"""Unit tests for normalized durable messaging contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from money_tracker.messaging import (
    MAX_TELEGRAM_FILE_BYTES,
    InboxClaim,
    InboxStatus,
    NormalizedTelegramUpdate,
    OutboundButton,
    OutboxMessageDraft,
)


def _update(**overrides: object) -> NormalizedTelegramUpdate:
    """Build a valid normalized private-chat update."""

    values: dict[str, object] = {
        "id": uuid4(),
        "bot_key": "telegram-test",
        "update_id": 42,
        "telegram_user_id": 1001,
        "telegram_chat_id": 1001,
        "received_at": datetime(2026, 9, 9, 8, tzinfo=UTC),
        "accepted_at": datetime(2026, 9, 9, 8, 0, 1, tzinfo=UTC),
        "telegram_message_id": 8,
        "text": "Messaggio sintetico",
    }
    values.update(overrides)
    return NormalizedTelegramUpdate(**values)  # type: ignore[arg-type]


def test_normalized_update_builds_stable_transport_neutral_source() -> None:
    """Derive the finance idempotency source without carrying Bot API data."""

    update = _update()

    assert update.source_reference == "telegram:telegram-test:42"
    assert "payload" not in update.__dataclass_fields__
    assert "token" not in update.__dataclass_fields__


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bot_key", "TEST BOT", "bot key"),
        ("update_id", -1, "update_id"),
        ("telegram_user_id", 0, "identifiers"),
        ("telegram_chat_id", -100, "identifiers"),
        ("accepted_at", datetime(2026, 9, 9), "timezone-aware"),
        ("file_size", MAX_TELEGRAM_FILE_BYTES + 1, "accepted limit"),
    ],
)
def test_normalized_update_rejects_unsafe_identity_and_metadata(
    field: str,
    value: object,
    message: str,
) -> None:
    """Refuse values that violate private-chat recovery boundaries."""

    with pytest.raises(ValueError, match=message):
        _update(**{field: value})


def test_image_requires_only_bounded_opaque_metadata() -> None:
    """Accept supported file identity without a download URL or raw payload."""

    update = _update(
        text=None,
        has_image=True,
        telegram_file_id="opaque-file",
        telegram_file_unique_id="stable-file",
        file_media_type="image/jpeg",
        file_size=1024,
    )

    assert update.has_image
    assert not hasattr(update, "download_url")
    assert not hasattr(update, "raw_update")


def test_claim_and_outbox_validate_processing_and_callback_bounds() -> None:
    """Reject invalid leases and callback data over Telegram's limit."""

    update = _update()
    with pytest.raises(ValueError, match="processing"):
        InboxClaim(
            update=update,
            status=InboxStatus.PENDING,
            attempt_count=1,
            replay_count=0,
            claim_token=uuid4(),
            claimed_until=datetime(2026, 9, 9, 8, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="64"):
        OutboundButton(label="Modify", callback_data="x" * 65)
    with pytest.raises(ValueError, match="SHA-256"):
        OutboxMessageDraft(
            id=uuid4(),
            bot_key="telegram-test",
            telegram_chat_id=1001,
            logical_key="response:42",
            response_kind="draft",
            text="Risposta sintetica",
            content_hash="not-a-hash",
        )
