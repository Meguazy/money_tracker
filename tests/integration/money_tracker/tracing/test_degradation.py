"""Real degradation tests: the product behaves the same when the store fails.

Each test runs production code in a process of its own, because the Langfuse SDK
keeps one client per project key per process, against a store that is stopped
(a closed loopback port), slow (every request held past the export bound), or
refusing (every request answered 503) through the fault-injection proxy in
place of the real Langfuse. The Compose stack is never stopped. The finance,
Telegram, worker, and HTTP results are compared with an untraced run, the added
latency with the 25 ms budget, the flush and stop times with the export bound,
and the journal is required to name the degradation without an endpoint, a
traceback, a credential, or owner content.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from money_tracker.domain import (
    BalanceMode,
    FundingClass,
    FundingSource,
    Profile,
    initial_taxonomy,
)
from money_tracker.messaging import (
    BotEnvironment,
    MessagingScope,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
)
from money_tracker.persistence.database import bootstrap_checkpointer, upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from tests.integration.money_tracker.tracing.channel_harness import QUESTION
from tests.integration.money_tracker.tracing.faults import MODES, faulty_store
from tests.integration.money_tracker.tracing.store import ENVIRONMENT, required
from tests.integration.money_tracker.tracing.test_advice_tracing import (
    BUDGET_QUESTION,
    _api,
    _seed,
)

pytestmark = [
    pytest.mark.langfuse,
    pytest.mark.inference,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("RUN_LANGFUSE_INTEGRATION_TESTS") != "1",
        reason=(
            "set RUN_LANGFUSE_INTEGRATION_TESTS=1 with the observability profile "
            "and the local model running"
        ),
    ),
]

ROOT = Path(__file__).resolve().parents[4]
EXPORT_TIMEOUT_SECONDS = 1
BUDGET_MS = 25.0
DEGRADED_EVENTS = {"tracing.export.degraded", "tracing.flush.incomplete"}


def _traced_environment(base_url: str) -> dict[str, str]:
    """Process environment tracing into a failing store with a 1 s bound."""

    environment = os.environ.copy()
    environment.update(
        {
            "MONEY_TRACKER_TRACING_ENABLED": "true",
            "LANGFUSE_BASE_URL": base_url,
            "MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS": str(EXPORT_TIMEOUT_SECONDS),
            "LANGFUSE_TRACING_ENVIRONMENT": ENVIRONMENT,
            "LOG_LEVEL": "INFO",
            "LOG_FORMAT": "json",
            "PYTHONPATH": str(ROOT),
        }
    )
    return environment


def _events(journal: Path) -> list[dict[str, object]]:
    """Parse a process journal, ignoring lines that are not JSON records."""

    records = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _assert_content_free_degradation(
    journal: Path, *, base_url: str, owner_texts: list[str]
) -> None:
    """Require a named degradation and no endpoint, traceback, secret, or content."""

    text = journal.read_text(encoding="utf-8")
    names = {str(record.get("event")) for record in _events(journal)}
    assert names & DEGRADED_EVENTS, sorted(names)
    forbidden = [
        base_url,
        base_url.removeprefix("http://"),
        "/api/public",
        "Traceback",
        required("LANGFUSE_SECRET_KEY"),
        *owner_texts,
    ]
    if database_url := os.getenv("MONEY_TRACKER_TEST_DATABASE_URL", "").strip():
        forbidden.append(database_url)
    leaked = [index for index, value in enumerate(forbidden) if value in text]
    assert leaked == [], f"journal leaked values at positions {leaked}"


@pytest.mark.parametrize("mode", MODES)
def test_the_channel_is_unchanged_and_within_budget_when_the_store_fails(
    mode: str, postgres_url: str, tmp_path: Path
) -> None:
    """Deliver the same answers, drain the queue, and flush within the bound."""

    journal = tmp_path / "channel.jsonl"
    with faulty_store(mode) as (base_url, proxy):
        with journal.open("wb") as sink:
            completed = subprocess.run(
                [
                    str(ROOT / ".venv/bin/python"),
                    "-m",
                    "tests.integration.money_tracker.tracing.channel_harness",
                ],
                cwd=ROOT,
                env=_traced_environment(base_url),
                stdout=subprocess.PIPE,
                stderr=sink,
                timeout=900,
                check=False,
            )
    assert completed.returncode == 0, "the channel harness failed"
    results = json.loads(completed.stdout.decode("utf-8").splitlines()[-1])

    assert results["tracing_enabled"] is True
    assert results["identical_answers"] is True
    assert results["pending_inbox"] == 0
    assert results["retrying_inbox"] == 0
    assert results["inbox_dead_letters"] == 0
    assert results["pending_outbox"] == 0
    median = results["median_ms"]
    assert median["traced"] - median["untraced"] <= BUDGET_MS, median
    assert results["flush_ms"] <= EXPORT_TIMEOUT_SECONDS * 1000 + 500
    if proxy is not None:
        assert proxy.requests > 0
    _assert_content_free_degradation(journal, base_url=base_url, owner_texts=[QUESTION])


@pytest.mark.parametrize("mode", MODES)
def test_the_api_answers_the_same_and_stops_within_the_bound_when_the_store_fails(
    mode: str, postgres_url: str, tmp_path: Path
) -> None:
    """Serve identical advice and exit cleanly on SIGINT with a failing store."""

    _seed(postgres_url)
    with _api(postgres_url, tmp_path, tracing=False) as plain:
        expected = plain.ask(BUDGET_QUESTION)
    with faulty_store(mode) as (base_url, _proxy):
        with _api(
            postgres_url,
            tmp_path,
            tracing=True,
            overrides={
                "LANGFUSE_BASE_URL": base_url,
                "MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS": str(
                    EXPORT_TIMEOUT_SECONDS
                ),
            },
        ) as traced:
            actual = traced.ask(BUDGET_QUESTION)
            ready = traced.origin

    assert expected.status_code == actual.status_code == 200
    assert actual.json() == expected.json()
    assert traced.events("tracing.enabled")
    assert traced.exit_code == 0
    assert traced.stop_seconds is not None
    assert traced.stop_seconds <= plain.stop_seconds + EXPORT_TIMEOUT_SECONDS + 1.0  # type: ignore[operator]
    assert ready
    _assert_content_free_degradation(
        traced.journal, base_url=base_url, owner_texts=[BUDGET_QUESTION]
    )


def _paired_bot_with_pending_help(postgres_url: str) -> PostgresMessagingRepository:
    """Seed the test bot scope with an owner and one queued `/help` update."""

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    now = datetime.now(UTC)
    finance = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    finance.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    finance.create_funding_source(
        FundingSource(
            id=uuid4(),
            profile_id=profile.id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        ),
        occurred_at=now,
    )
    messaging = PostgresMessagingRepository(postgres_url)
    bot_key = MessagingScope.for_environment(BotEnvironment.TEST).bot_key
    messaging.ensure_bot(bot_key, environment=BotEnvironment.TEST, now=now)
    code_hash = sha256(b"degradation-pairing").hexdigest()
    messaging.create_pairing_code(
        code_id=uuid4(),
        bot_key=bot_key,
        profile_id=profile.id,
        code_hash=code_hash,
        expires_at=now + timedelta(minutes=10),
        now=now,
    )
    text = "Fatto: sei il proprietario del bot."
    assert messaging.pair_owner(
        bot_key=bot_key,
        code_hash=code_hash,
        telegram_user_id=4242,
        telegram_chat_id=4242,
        confirmation=OutboxMessageDraft(
            id=uuid4(),
            bot_key=bot_key,
            telegram_chat_id=4242,
            logical_key="pairing:owner",
            response_kind="paired",
            text=text,
            content_hash=sha256(text.encode()).hexdigest(),
        ),
        now=now,
    )
    update = NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=bot_key,
        update_id=1,
        telegram_user_id=4242,
        telegram_chat_id=4242,
        received_at=now,
        accepted_at=now,
        telegram_message_id=9,
        text="/help",
    )
    messaging.commit_ingest_batch(
        bot_key=bot_key,
        expected_offset=messaging.polling_offset(bot_key),
        observed_update_ids=(1,),
        accepted_updates=(update,),
        rejected_count=0,
        unsupported_count=0,
        now=now,
    )
    return messaging


@pytest.mark.parametrize("mode", MODES)
def test_the_worker_service_drains_and_stops_within_the_bound_when_the_store_fails(
    mode: str, postgres_url: str, tmp_path: Path
) -> None:
    """Commit the queued work unit and exit 0 promptly after SIGINT."""

    messaging = _paired_bot_with_pending_help(postgres_url)
    bot_key = MessagingScope.for_environment(BotEnvironment.TEST).bot_key
    journal = tmp_path / "worker.jsonl"
    with faulty_store(mode) as (base_url, _proxy):
        environment = _traced_environment(base_url)
        environment.update(
            {
                "MONEY_TRACKER_DATABASE_URL": postgres_url,
                "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "test",
            }
        )
        with journal.open("wb") as sink:
            process = subprocess.Popen(
                [str(ROOT / ".venv/bin/money-tracker-worker"), "run"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=sink,
            )
        try:
            deadline = time.monotonic() + 120
            while messaging.health(bot_key).pending_inbox:
                assert process.poll() is None, "the worker exited early"
                assert time.monotonic() < deadline, "the worker never drained"
                time.sleep(0.25)
        finally:
            started = time.monotonic()
            process.send_signal(signal.SIGINT)
            try:
                exit_code = process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait()
            stop_seconds = time.monotonic() - started

    health = messaging.health(bot_key)
    assert health.inbox_dead_letters == 0
    assert health.retrying_inbox == 0
    assert health.pending_outbox == 2
    assert exit_code == 0
    assert stop_seconds <= 1.0 + EXPORT_TIMEOUT_SECONDS + 2.0
    names = {str(record.get("event")) for record in _events(journal)}
    assert {"tracing.enabled", "work_unit.committed", "worker.loop.stopped"} <= names
    _assert_content_free_degradation(journal, base_url=base_url, owner_texts=[])
