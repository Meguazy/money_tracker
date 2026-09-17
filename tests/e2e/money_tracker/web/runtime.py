"""Prepare and serve an isolated real-service browser acceptance runtime."""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg import connect, sql

from money_tracker.application import (
    CategoryProposalService,
    FinanceService,
    ReceiptMetadataService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    CategoryProposal,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    ReceiptMetadata,
    initial_taxonomy,
)
from money_tracker.messaging import (
    BotEnvironment,
    MessagingScope,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository

ROME = ZoneInfo("Europe/Rome")
E2E_PORT = "8765"


def _database_url() -> str:
    """Return the URL only when it identifies the isolated test database.

    Returns:
        Validated PostgreSQL URL for ``money_tracker_test``.

    Raises:
        RuntimeError: If cleanup could reach any non-test database.
    """

    value = os.getenv("MONEY_TRACKER_TEST_DATABASE_URL", "").strip()
    parsed = urlparse(value)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5433
        or parsed.path != "/money_tracker_test"
    ):
        raise RuntimeError(
            "browser E2E cleanup is restricted to local money_tracker_test on port 5433"
        )
    return value


def cleanup() -> None:
    """Drop only application-owned schemas in the isolated test database.

    Raises:
        RuntimeError: If the URL or the connected database is not the isolated
            ``money_tracker_test`` database.
    """

    database_url = _database_url()
    with connect(database_url, autocommit=True) as connection:
        if connection.execute("SELECT current_database()").fetchone() != (
            "money_tracker_test",
        ):
            raise RuntimeError(
                "refusing browser E2E cleanup outside money_tracker_test"
            )
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def _pending_proposal(
    *,
    repository: PostgresFinanceRepository,
    profile: Profile,
    source: FundingSource,
    name: str,
) -> None:
    """Store a proposal and correlated draft through production services.

    Args:
        repository: Real PostgreSQL finance adapter.
        profile: Isolated E2E owner profile.
        source: Funding source assigned to the draft.
        name: Unique proposed category displayed by the browser.
    """

    reference = f"e2e:web:category-review:{uuid4()}"
    amount = Money(1250)
    ids = UUID4Generator()
    FinanceService(repository, clock=SystemClock(), ids=ids).create_expense(
        Expense(
            id=uuid4(),
            profile_id=profile.id,
            amount=amount,
            occurred_at=datetime(2026, 9, 12, 10, 30, tzinfo=ROME),
            description=f"Bozza E2E per {name}",
            merchant=None,
            status=ExpenseStatus.DRAFT,
            category_allocations=(),
            payment_allocations=(
                PaymentAllocation(funding_source_id=source.id, amount=amount),
            ),
            source_reference=reference,
            idempotency_key=f"expense:{reference}",
        ),
        request_hash=hashlib.sha256(reference.encode()).hexdigest(),
        actor="e2e-fixture",
        reason="browser acceptance category review fixture",
    )
    taxonomy = repository.get_taxonomy(profile.id)
    CategoryProposalService(repository, clock=SystemClock(), ids=ids).submit(
        CategoryProposal(
            id=uuid4(),
            profile_id=profile.id,
            proposed_name=name,
            description=f"Ambito riutilizzabile {name}.",
            parent_id=None,
            rationale="Nessuna categoria attiva descrive la finalità.",
            alternative_category_ids=(taxonomy.resolve_active("Formazione").id,),
            excluded_names=(),
            source_reference=reference,
        ),
        actor="e2e-fixture",
        reason="candidate awaiting browser acceptance",
    )


def _messaging_failures(database_url: str, *, now: datetime) -> None:
    """Create one real inbox and outbox dead letter for browser recovery.

    Args:
        database_url: Validated isolated PostgreSQL URL.
        now: Shared aware acceptance instant.

    Raises:
        RuntimeError: If the production repository does not offer the fixture
            items for claiming, so no dead letter could be produced.
    """

    scope = MessagingScope.for_environment(BotEnvironment.TEST)
    repository = PostgresMessagingRepository(database_url)
    repository.ensure_bot(scope.bot_key, environment=scope.environment, now=now)
    update = NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=scope.bot_key,
        update_id=26091601,
        telegram_user_id=260916,
        telegram_chat_id=260916,
        telegram_message_id=1,
        received_at=now,
        accepted_at=now,
        text="contenuto sintetico e2e",
    )
    repository.commit_ingest_batch(
        bot_key=scope.bot_key,
        expected_offset=repository.polling_offset(scope.bot_key),
        observed_update_ids=(update.update_id,),
        accepted_updates=(update,),
        rejected_count=0,
        unsupported_count=0,
        now=now,
    )
    claim = repository.claim_next_inbox(
        scope.bot_key,
        now=now + timedelta(seconds=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    if claim is None:
        raise RuntimeError("browser E2E inbox fixture was not claimable")
    notice = "Notifica sintetica E2E"
    repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=False,
        retry_at=None,
        response=OutboxMessageDraft(
            id=uuid4(),
            bot_key=scope.bot_key,
            telegram_chat_id=update.telegram_chat_id,
            logical_key=f"inbox:{update.id}:response:terminal_failure",
            response_kind="terminal_failure",
            text=notice,
            content_hash=hashlib.sha256(notice.encode()).hexdigest(),
            inbox_id=update.id,
        ),
        now=now + timedelta(seconds=2),
    )
    delivery_now = now + timedelta(minutes=1)
    for _ in range(5):
        outbox = repository.claim_next_outbox(
            scope.bot_key,
            now=delivery_now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        if outbox is None:
            raise RuntimeError("browser E2E outbox fixture was not claimable")
        repository.fail_outbox(
            outbox,
            error_code="telegram_unavailable",
            transient=True,
            retry_at=delivery_now + timedelta(seconds=2),
            now=delivery_now,
        )
        delivery_now += timedelta(seconds=2)


def prepare() -> None:
    """Create one isolated profile and real persistent browser fixtures."""

    database_url = _database_url()
    cleanup()
    upgrade_database(database_url)
    repository = PostgresFinanceRepository(database_url)
    profile = Profile(uuid4())
    now = datetime(2026, 9, 16, 12, tzinfo=ROME)
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta E2E",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    for name in ("Giardinaggio E2E", "Didattica E2E", "Collezionismo E2E"):
        _pending_proposal(
            repository=repository,
            profile=profile,
            source=card,
            name=name,
        )
    ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).register(
        ReceiptMetadata(
            id=uuid4(),
            profile_id=profile.id,
            content_sha256="a1" * 32,
            media_type="image/jpeg",
            byte_size=131072,
            stored_at=now,
            source_reference=f"e2e:web:receipt:{uuid4()}",
            original_filename="scontrino-e2e.jpg",
        ),
        actor="e2e-fixture",
        reason="browser acceptance receipt fixture",
    )
    _messaging_failures(database_url, now=now)


def serve() -> int:
    """Run the production API against isolated PostgreSQL until terminated.

    Returns:
        Production process exit code.
    """

    prepare()
    repository_root = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    environment.update(
        {
            "MONEY_TRACKER_DATABASE_URL": _database_url(),
            "MONEY_TRACKER_WEB_HOST": "127.0.0.1",
            "MONEY_TRACKER_WEB_PORT": E2E_PORT,
            "MONEY_TRACKER_WEB_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "MONEY_TRACKER_WEB_ALLOWED_ORIGINS": (
                f"http://127.0.0.1:{E2E_PORT},http://localhost:{E2E_PORT}"
            ),
            "MONEY_TRACKER_WEB_STATIC_DIR": str(repository_root / "frontend/dist"),
            "MONEY_TRACKER_TELEGRAM_ENVIRONMENT": "test",
            # `.env` may switch tracing on for the host services; synthetic
            # browser fixtures must never reach the owner's trace store.
            "MONEY_TRACKER_TRACING_ENABLED": "false",
            "LOG_LEVEL": "CRITICAL",
        }
    )
    process = subprocess.Popen(
        [str(repository_root / ".venv/bin/money-tracker-api"), "run"],
        env=environment,
    )

    def stop(_signum: int, _frame: object) -> None:
        """Forward a supervisor stop to the production child process."""

        process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return process.wait()
    finally:
        cleanup()


def main() -> None:
    """Run the requested isolated browser-runtime operation."""

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "cleanup", "serve"))
    command = parser.parse_args().command
    if command == "prepare":
        prepare()
        return
    if command == "cleanup":
        cleanup()
        return
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
