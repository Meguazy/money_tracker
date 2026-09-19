"""Real-process integration tests for safe Telegram runtime failures."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import connect

from money_tracker.messaging import (
    BotEnvironment,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.messaging import PostgresMessagingRepository

pytestmark = pytest.mark.postgres

_NOW = datetime(2026, 9, 10, 8, tzinfo=UTC)


def _environment(postgres_url: str) -> dict[str, str]:
    """Build non-secret process configuration against the isolated database.

    These tests compare the operator-facing standard error exactly. Structured
    log records share that stream, so the level is pinned rather than inherited
    from whichever `.env` or shell runs the suite.
    """

    environment = os.environ.copy()
    environment.update(
        {
            "LOG_LEVEL": "CRITICAL",
            "MONEY_TRACKER_DATABASE_URL": postgres_url,
            "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "test",
            "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE": (
                "/tmp/money-tracker-unused-production-token"
            ),
            "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE": (
                "/tmp/money-tracker-unused-test-token"
            ),
        }
    )
    return environment


@pytest.mark.parametrize(
    ("entrypoint", "command", "prefix"),
    [
        (
            ".venv/bin/money-tracker-telegram",
            "health",
            "Telegram gateway failed",
        ),
        (
            ".venv/bin/money-tracker-worker",
            "process-once",
            "Workflow worker failed",
        ),
    ],
)
def test_unmigrated_schema_stops_safely_without_a_traceback(
    postgres_url: str, entrypoint: str, command: str, prefix: str
) -> None:
    """Classify an empty real database as an operator-fixable schema error."""

    completed = subprocess.run(
        [str(Path(entrypoint).resolve()), command],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == f"{prefix}: database_schema_invalid\n"
    assert "Traceback" not in completed.stderr


def test_health_rejects_a_database_missing_the_latest_messaging_migration(
    postgres_url: str,
) -> None:
    """Keep readiness false until outbox recovery state exists."""

    upgrade_database(postgres_url, "20260909_0005")
    PostgresMessagingRepository(postgres_url).ensure_bot(
        "telegram-test",
        environment=BotEnvironment.TEST,
        now=_NOW,
    )

    completed = subprocess.run(
        [str(Path(".venv/bin/money-tracker-telegram").resolve()), "health"],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Telegram gateway failed: database_schema_invalid\n"
    assert "Traceback" not in completed.stderr


def test_production_cli_lists_and_resends_one_redacted_outbox_dead_letter(
    postgres_url: str,
) -> None:
    """Exercise content-free delivery recovery through the installed entry point."""

    upgrade_database(postgres_url)
    repository = PostgresMessagingRepository(postgres_url)
    bot_key = "telegram-test"
    repository.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=_NOW)
    private_marker = "private-integration-response"
    response = OutboxMessageDraft(
        id=uuid4(),
        bot_key=bot_key,
        telegram_chat_id=1001,
        logical_key=f"integration:delivery:{uuid4()}",
        response_kind="draft",
        text=private_marker,
        content_hash=hashlib.sha256(private_marker.encode()).hexdigest(),
    )
    repository.enqueue_notice(response, now=_NOW)
    now = _NOW
    for _ in range(5):
        claim = repository.claim_next_outbox(
            bot_key,
            now=now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        assert claim is not None
        repository.fail_outbox(
            claim,
            error_code="telegram_unavailable",
            transient=True,
            retry_at=now + timedelta(seconds=2),
            now=now,
        )
        now += timedelta(seconds=2)

    entrypoint = str(Path(".venv/bin/money-tracker-telegram").resolve())
    listed = subprocess.run(
        [entrypoint, "dead-letters"],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )
    assert listed.returncode == 0
    assert listed.stderr == ""
    assert private_marker not in listed.stdout
    projection = json.loads(listed.stdout)["outbox_dead_letters"]
    assert projection == [
        {
            "attempt_count": 5,
            "created_at": _NOW.isoformat(),
            "last_error_code": "telegram_unavailable",
            "outbox_id": str(response.id),
            "replay_count": 0,
            "response_kind": "draft",
        }
    ]

    resent = subprocess.run(
        [entrypoint, "resend", "--outbox-id", str(response.id)],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )
    assert resent.returncode == 0
    assert resent.stderr == ""
    assert json.loads(resent.stdout) == {
        "outbox_id": str(response.id),
        "status": "queued",
    }
    with connect(postgres_url) as connection:
        state = connection.execute(
            "SELECT status, attempt_count, replay_count "
            "FROM messaging.telegram_outbox WHERE id = %s",
            (response.id,),
        ).fetchone()
    assert state == ("pending", 0, 1)


def test_production_cli_reports_warm_text_delivery_p95(
    postgres_url: str,
) -> None:
    """Measure accepted input to durable Bot API send through the real CLI."""

    upgrade_database(postgres_url)
    repository = PostgresMessagingRepository(postgres_url)
    bot_key = "telegram-test"
    repository.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=_NOW)
    durations = (4_000, 2_500)
    for position, duration_ms in enumerate(durations):
        accepted_at = _NOW + timedelta(minutes=position)
        update = NormalizedTelegramUpdate(
            id=uuid4(),
            bot_key=bot_key,
            update_id=100 + position,
            telegram_user_id=1001,
            telegram_chat_id=1001,
            telegram_message_id=110 + position,
            received_at=accepted_at,
            accepted_at=accepted_at,
            text=f"Synthetic latency sample {position}",
        )
        repository.commit_ingest_batch(
            bot_key=bot_key,
            expected_offset=repository.polling_offset(bot_key),
            observed_update_ids=(update.update_id,),
            accepted_updates=(update,),
            rejected_count=0,
            unsupported_count=0,
            now=accepted_at,
        )
        inbox = repository.claim_next_inbox(
            bot_key,
            now=accepted_at + timedelta(seconds=1),
            lease=timedelta(minutes=1),
            claim_token=uuid4(),
        )
        assert inbox is not None
        text = "Synthetic owner response"
        response = OutboxMessageDraft(
            id=uuid4(),
            bot_key=bot_key,
            telegram_chat_id=1001,
            logical_key=f"integration:latency:{position}",
            response_kind="draft",
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            inbox_id=update.id,
        )
        repository.complete_inbox(
            inbox,
            workflow_status="drafted",
            workflow_result={"kind": "drafted"},
            response=response,
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
            telegram_message_id=120 + position,
            now=accepted_at + timedelta(milliseconds=duration_ms),
        )

    completed = subprocess.run(
        [
            str(Path(".venv/bin/money-tracker-telegram").resolve()),
            "latency",
            "--since",
            _NOW.isoformat(),
        ],
        check=False,
        capture_output=True,
        env=_environment(postgres_url),
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "environment": "test",
        "maximum_ms": 2_500,
        "measurement": "accepted_at_to_bot_api_send_commit",
        "sample_count": 2,
        "warm_p95_ms": 2_500,
        "warm_sample_count": 1,
        "warmup_excluded": 1,
        "breakdown": {
            "total_ms": {"samples": 1, "p50": 2_500, "p95": 2_500, "maximum": 2_500},
            "accepted_to_completed_ms": {
                "samples": 1,
                "p50": 1_000,
                "p95": 1_000,
                "maximum": 1_000,
            },
            "completed_to_sent_ms": {
                "samples": 1,
                "p50": 1_500,
                "p95": 1_500,
                "maximum": 1_500,
            },
            "retried_samples": 0,
            "by_response_kind": {
                "draft": {"samples": 1, "p50": 2_500, "p95": 2_500, "maximum": 2_500}
            },
        },
    }
