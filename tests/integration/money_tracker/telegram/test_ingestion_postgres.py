"""Real PostgreSQL integration tests for Telegram authorization and ingestion."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg import connect

from money_tracker.messaging import BotEnvironment
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.telegram import TelegramInboundUpdate, TelegramUpdateIngestor

pytestmark = pytest.mark.postgres

_NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)


def _message(
    update_id: int,
    *,
    user_id: int = 1001,
    chat_id: int = 1001,
    chat_type: str = "private",
) -> TelegramInboundUpdate:
    """Build one synthetic parsed transport update."""

    return TelegramInboundUpdate(
        update_id=update_id,
        kind="message",
        telegram_user_id=user_id,
        telegram_chat_id=chat_id,
        chat_type=chat_type,
        received_at=_NOW,
        telegram_message_id=update_id + 1,
        text="Synthetic integration message",
    )


def test_authorization_precedes_real_durable_inbox_creation(
    postgres_url: str,
) -> None:
    """Store only paired private-owner work and acknowledge every observation."""

    upgrade_database(postgres_url)
    repository = PostgresMessagingRepository(postgres_url)
    bot_key = f"test-{uuid4().hex}"
    profile_id = uuid4()
    repository.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=_NOW)
    with connect(postgres_url) as connection:
        connection.execute(
            "INSERT INTO finance.profiles "
            "(id, locale, timezone, currency, active, created_at, updated_at) "
            "VALUES (%s, 'it-IT', 'Europe/Rome', 'EUR', true, %s, %s)",
            (profile_id, _NOW, _NOW),
        )
    code_hash = hashlib.sha256(b"synthetic-pairing").hexdigest()
    repository.create_pairing_code(
        code_id=uuid4(),
        bot_key=bot_key,
        profile_id=profile_id,
        code_hash=code_hash,
        expires_at=_NOW + timedelta(minutes=5),
        now=_NOW,
    )
    assert (
        repository.pair_owner(
            bot_key=bot_key,
            profile_id=profile_id,
            code_hash=code_hash,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            now=_NOW + timedelta(seconds=1),
        )
        is not None
    )
    updates = (
        _message(0),
        _message(1, user_id=2002, chat_id=2002),
        _message(2, chat_id=-1002, chat_type="group"),
        TelegramInboundUpdate(
            update_id=3,
            kind="unsupported",
            telegram_user_id=None,
            telegram_chat_id=None,
            chat_type=None,
            received_at=_NOW,
        ),
    )
    report = TelegramUpdateIngestor(repository).ingest(
        bot_key=bot_key,
        expected_offset=0,
        updates=updates,
        now=_NOW + timedelta(seconds=2),
    )

    assert report.next_offset == 4
    assert len(report.accepted) == 1
    assert report.rejected_count == 3
    assert report.unsupported_count == 0
    with connect(postgres_url) as connection:
        inbox = connection.execute(
            "SELECT update_id, telegram_user_id, telegram_chat_id "
            "FROM messaging.telegram_inbox WHERE bot_key = %s",
            (bot_key,),
        ).fetchall()
        counters = connection.execute(
            "SELECT rejected_update_count, unsupported_update_count "
            "FROM messaging.telegram_offsets WHERE bot_key = %s",
            (bot_key,),
        ).fetchone()
    assert inbox == [(0, 1001, 1001)]
    assert counters == (3, 0)
