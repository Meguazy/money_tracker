"""Integration tests against the real dedicated application PostgreSQL server."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from typing import TypedDict
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from psycopg import Connection, connect, errors, sql

from money_tracker.application import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    downgrade_database,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository


class _CounterState(TypedDict):
    """Small serializable graph state used to verify checkpoint persistence."""

    value: int


def _increment(state: _CounterState) -> _CounterState:
    """Increment deterministic test state without any external boundary."""

    return {"value": state["value"] + 1}


def _isolated_test_url() -> str:
    """Return a URL only when it names the exact local isolated test database."""

    if os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_INTEGRATION_TESTS=1 for real PostgreSQL tests")
    database_url = os.getenv("MONEY_TRACKER_TEST_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("MONEY_TRACKER_TEST_DATABASE_URL is required")
    parsed = urlparse(database_url)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5433
        or parsed.path != "/money_tracker_test"
    ):
        raise RuntimeError(
            "integration cleanup is restricted to local money_tracker_test on port 5433"
        )
    return database_url


def _reset_test_database(database_url: str) -> None:
    """Remove only schemas owned by the isolated integration-test database."""

    with connect(database_url, autocommit=True) as connection:
        current_database = connection.execute("SELECT current_database()").fetchone()
        if current_database != ("money_tracker_test",):
            raise RuntimeError("refusing cleanup outside money_tracker_test")
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def _insert_profile(
    connection: Connection[tuple[object, ...]], profile_id: object
) -> None:
    """Insert one active V1 profile into an isolated integration fixture."""

    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO finance.profiles "
        "(id, locale, timezone, currency, active, created_at, updated_at) "
        "VALUES (%s, 'it-IT', 'Europe/Rome', 'EUR', true, %s, %s)",
        (profile_id, now, now),
    )


def _insert_category(
    connection: Connection[tuple[object, ...]],
    *,
    category_id: object,
    profile_id: object,
    name: str,
    parent_id: object | None = None,
) -> None:
    """Insert one active category for an isolated database fixture."""

    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO finance.categories "
        "(id, profile_id, name, normalized_name, description, parent_id, "
        "status, seeded, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 'Integration fixture', %s, 'active', false, "
        "1, %s, %s)",
        (category_id, profile_id, name, name.casefold(), parent_id, now, now),
    )


def _insert_source(
    connection: Connection[tuple[object, ...]],
    *,
    source_id: object,
    profile_id: object,
    balance_mode: str = "untracked",
) -> None:
    """Insert one active funding source for an isolated database fixture."""

    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO finance.funding_sources "
        "(id, profile_id, name, normalized_name, funding_class, balance_mode, "
        "status, is_default, version, created_at, updated_at) "
        "VALUES (%s, %s, 'Carta test', %s, 'personal_funds', %s, 'active', "
        "false, 1, %s, %s)",
        (source_id, profile_id, str(source_id), balance_mode, now, now),
    )


def _insert_posted_expense(
    connection: Connection[tuple[object, ...]],
    *,
    expense_id: object,
    profile_id: object,
    category_id: object,
    source_id: object,
    total: int,
    category_amount: int | None = None,
    payment_amount: int | None = None,
) -> None:
    """Insert a posted aggregate whose deferred checks run at commit."""

    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO finance.expenses "
        "(id, profile_id, amount_minor, currency, occurred_at, description, "
        "status, source_reference, idempotency_key, version, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'EUR', %s, 'Integration expense', 'posted', "
        "'integration:test', %s, 1, %s, %s)",
        (expense_id, profile_id, total, now, f"integration:{expense_id}", now, now),
    )
    connection.execute(
        "INSERT INTO finance.category_allocations "
        "(expense_id, category_id, amount_minor, currency, provenance) "
        "VALUES (%s, %s, %s, 'EUR', 'integration')",
        (
            expense_id,
            category_id,
            total if category_amount is None else category_amount,
        ),
    )
    connection.execute(
        "INSERT INTO finance.payment_allocations "
        "(expense_id, funding_source_id, amount_minor, currency) "
        "VALUES (%s, %s, %s, 'EUR')",
        (
            expense_id,
            source_id,
            total if payment_amount is None else payment_amount,
        ),
    )


def _repository_fixture(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, Profile, FundingSource, object]:
    """Bootstrap a real repository with accepted taxonomy and one source."""

    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta integrazione",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
    )
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    repository.create_funding_source(source, occurred_at=now)
    return repository, profile, source, taxonomy.categories[0].id


def _domain_draft(
    profile: Profile,
    source: FundingSource,
    category_id: object,
    *,
    expense_id: object | None = None,
    idempotency_key: str = "integration:command",
) -> Expense:
    """Build a reconciled draft using real persisted references."""

    return Expense(
        id=expense_id or uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        description="Repository integration",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(category_id, Money(1200), "integration"),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(1200)),),
        source_reference="integration:test",
        idempotency_key=idempotency_key,
    )


def _audit(expense: Expense, action: str, prior_version: int | None) -> AuditEvent:
    """Build one valid integration audit event."""

    return AuditEvent(
        id=UUID4Generator().new(),
        profile_id=expense.profile_id,
        aggregate_type="expense",
        aggregate_id=expense.id,
        action=action,
        actor="integration",
        source_reference=expense.source_reference,
        occurred_at=SystemClock().now(),
        prior_version=prior_version,
        current_version=expense.version,
        reason="repository integration",
    )


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Yield a clean real test database and remove only its owned data afterward."""

    database_url = _isolated_test_url()
    _reset_test_database(database_url)
    try:
        yield database_url
    finally:
        _reset_test_database(database_url)


@pytest.mark.postgres
def test_forward_migration_safe_rollback_and_checkpoint_bootstrap(
    postgres_url: str,
) -> None:
    """Exercise forward, empty-foundation rollback, and fresh checkpoint setup."""

    upgrade_database(postgres_url, "20260901_0001")
    with connect(postgres_url) as connection:
        schemas = {
            row[0]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('finance', 'messaging', 'workflow')"
            )
        }
        privileges = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert schemas == {"finance", "messaging", "workflow"}
    assert privileges == (False, False, False, False)

    downgrade_database(postgres_url, "base")
    with connect(postgres_url) as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name IN ('finance', 'messaging', 'workflow')"
        ).fetchone()
    assert remaining == (0,)

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    with connect(postgres_url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'workflow'"
            )
        }
    assert {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }.issubset(tables)


@pytest.mark.postgres
def test_checkpoint_survives_connection_recreation(postgres_url: str) -> None:
    """Recover graph state through a new real PostgreSQL connection."""

    upgrade_database(postgres_url)
    builder = StateGraph(_CounterState)
    builder.add_node("increment", _increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    thread_id = f"integration-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    with open_postgres_checkpointer(postgres_url, setup=True) as saver:
        graph = builder.compile(checkpointer=saver)
        result = graph.invoke({"value": 1}, config)
    assert result == {"value": 2}

    with open_postgres_checkpointer(postgres_url) as saver:
        restarted_graph = builder.compile(checkpointer=saver)
        snapshot = restarted_graph.get_state(config)
    assert snapshot.values == {"value": 2}


@pytest.mark.postgres
def test_finance_migration_creates_authoritative_tables(postgres_url: str) -> None:
    """Create every Step 2 table while retaining explicit schema ownership."""

    upgrade_database(postgres_url)
    with connect(postgres_url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'finance'"
            )
        }

    assert {
        "profiles",
        "categories",
        "category_aliases",
        "category_proposals",
        "funding_sources",
        "wallet_entries",
        "monthly_budgets",
        "monthly_budget_overrides",
        "receipts",
        "expenses",
        "category_allocations",
        "payment_allocations",
        "audit_events",
        "idempotency_records",
    } == tables
    with pytest.raises(RuntimeError, match="destructive"):
        downgrade_database(postgres_url, "20260901_0001")


@pytest.mark.postgres
def test_reconciled_posted_expense_survives_reconnection(postgres_url: str) -> None:
    """Commit a valid aggregate and reload both dimensions on a new connection."""

    upgrade_database(postgres_url)
    profile_id, category_id, source_id, expense_id = (uuid4() for _ in range(4))
    with connect(postgres_url) as connection:
        _insert_profile(connection, profile_id)
        _insert_category(
            connection,
            category_id=category_id,
            profile_id=profile_id,
            name="Integration category",
        )
        _insert_source(connection, source_id=source_id, profile_id=profile_id)
        _insert_posted_expense(
            connection,
            expense_id=expense_id,
            profile_id=profile_id,
            category_id=category_id,
            source_id=source_id,
            total=599,
        )

    with connect(postgres_url) as restarted:
        persisted = restarted.execute(
            "SELECT expense.amount_minor, expense.currency, "
            "category.amount_minor, payment.amount_minor "
            "FROM finance.expenses expense "
            "JOIN finance.category_allocations category "
            "ON category.expense_id = expense.id "
            "JOIN finance.payment_allocations payment "
            "ON payment.expense_id = expense.id "
            "WHERE expense.id = %s",
            (expense_id,),
        ).fetchone()
    assert persisted == (599, "EUR", 599, 599)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "category_amount,payment_amount,error_text",
    [(598, 599, "category allocations"), (599, 598, "payment allocations")],
)
def test_deferred_reconciliation_rolls_back_invalid_post(
    postgres_url: str,
    category_amount: int,
    payment_amount: int,
    error_text: str,
) -> None:
    """Reject either mismatched dimension at commit and leave no partial rows."""

    upgrade_database(postgres_url)
    profile_id, category_id, source_id, expense_id = (uuid4() for _ in range(4))
    with connect(postgres_url) as setup:
        _insert_profile(setup, profile_id)
        _insert_category(
            setup,
            category_id=category_id,
            profile_id=profile_id,
            name="Integration category",
        )
        _insert_source(setup, source_id=source_id, profile_id=profile_id)

    with pytest.raises(errors.RaiseException, match=error_text):
        with connect(postgres_url) as connection:
            _insert_posted_expense(
                connection,
                expense_id=expense_id,
                profile_id=profile_id,
                category_id=category_id,
                source_id=source_id,
                total=599,
                category_amount=category_amount,
                payment_amount=payment_amount,
            )

    with connect(postgres_url) as verification:
        assert verification.execute(
            "SELECT count(*) FROM finance.expenses WHERE id = %s", (expense_id,)
        ).fetchone() == (0,)
        assert verification.execute(
            "SELECT count(*) FROM finance.category_allocations WHERE expense_id = %s",
            (expense_id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_database_rejects_third_level_category(postgres_url: str) -> None:
    """Enforce taxonomy depth in PostgreSQL as well as the pure domain."""

    upgrade_database(postgres_url)
    profile_id, root_id, child_id = (uuid4() for _ in range(3))
    with connect(postgres_url) as connection:
        _insert_profile(connection, profile_id)
        _insert_category(
            connection,
            category_id=root_id,
            profile_id=profile_id,
            name="Root",
        )
        _insert_category(
            connection,
            category_id=child_id,
            profile_id=profile_id,
            name="Child",
            parent_id=root_id,
        )
        with pytest.raises(errors.RaiseException, match="two levels"):
            _insert_category(
                connection,
                category_id=uuid4(),
                profile_id=profile_id,
                name="Grandchild",
                parent_id=child_id,
            )


@pytest.mark.postgres
def test_audit_and_wallet_rows_are_immutable(postgres_url: str) -> None:
    """Prevent update or deletion of authoritative append-only history."""

    upgrade_database(postgres_url)
    profile_id, source_id, audit_id, entry_id = (uuid4() for _ in range(4))
    now = datetime.now(UTC)
    with connect(postgres_url) as setup:
        _insert_profile(setup, profile_id)
        _insert_source(
            setup,
            source_id=source_id,
            profile_id=profile_id,
            balance_mode="ledger",
        )
        setup.execute(
            "INSERT INTO finance.audit_events "
            "(id, profile_id, aggregate_type, aggregate_id, action, actor, "
            "source_reference, occurred_at, current_version, reason) "
            "VALUES (%s, %s, 'profile', %s, 'create', 'integration', "
            "'integration:test', %s, 1, 'fixture')",
            (audit_id, profile_id, profile_id, now),
        )
        setup.execute(
            "INSERT INTO finance.wallet_entries "
            "(id, profile_id, funding_source_id, entry_type, amount_minor, "
            "currency, occurred_at, created_at) "
            "VALUES (%s, %s, %s, 'credit', 1000, 'EUR', %s, %s)",
            (entry_id, profile_id, source_id, now, now),
        )

    with pytest.raises(errors.RaiseException, match="audit_events.*immutable"):
        with connect(postgres_url) as connection:
            connection.execute(
                "UPDATE finance.audit_events SET reason = 'rewritten' WHERE id = %s",
                (audit_id,),
            )
    with pytest.raises(errors.RaiseException, match="wallet_entries.*immutable"):
        with connect(postgres_url) as connection:
            connection.execute(
                "DELETE FROM finance.wallet_entries WHERE id = %s", (entry_id,)
            )


@pytest.mark.postgres
def test_only_one_active_profile_and_one_default_source_are_allowed(
    postgres_url: str,
) -> None:
    """Enforce the single-owner boundary and an unambiguous default source."""

    upgrade_database(postgres_url)
    profile_id = uuid4()
    with connect(postgres_url) as setup:
        _insert_profile(setup, profile_id)
    with pytest.raises(errors.UniqueViolation):
        with connect(postgres_url) as connection:
            _insert_profile(connection, uuid4())

    now = datetime.now(UTC)
    with pytest.raises(errors.UniqueViolation):
        with connect(postgres_url) as connection:
            for index in range(2):
                connection.execute(
                    "INSERT INTO finance.funding_sources "
                    "(id, profile_id, name, normalized_name, funding_class, "
                    "balance_mode, status, is_default, version, created_at, "
                    "updated_at) "
                    "VALUES (%s, %s, %s, %s, 'personal_funds', 'untracked', "
                    "'active', true, 1, %s, %s)",
                    (
                        uuid4(),
                        profile_id,
                        f"Source {index}",
                        f"source-{index}",
                        now,
                        now,
                    ),
                )


@pytest.mark.postgres
def test_repository_bootstrap_is_repeatable_and_seeds_exact_taxonomy(
    postgres_url: str,
) -> None:
    """Repeat profile bootstrap without duplicating or silently changing seed data."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    now = SystemClock().now()

    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)

    loaded = repository.get_taxonomy(profile.id)
    assert len(loaded.categories) == 24
    assert {item.id for item in loaded.categories} == {
        item.id for item in taxonomy.categories
    }
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.categories WHERE profile_id = %s",
            (profile.id,),
        ).fetchone() == (24,)


@pytest.mark.postgres
def test_repository_idempotency_returns_prior_result_and_rejects_key_reuse(
    postgres_url: str,
) -> None:
    """Produce one expense/audit for retries and reject different request content."""

    upgrade_database(postgres_url)
    repository, profile, source, category_id = _repository_fixture(postgres_url)
    first = _domain_draft(profile, source, category_id)
    request_hash = "a" * 64

    created = repository.create_expense(
        first,
        request_hash=request_hash,
        audit_event=_audit(first, "create", None),
    )
    retried_input = _domain_draft(
        profile,
        source,
        category_id,
        idempotency_key=first.idempotency_key,
    )
    retried = repository.create_expense(
        retried_input,
        request_hash=request_hash,
        audit_event=_audit(retried_input, "create", None),
    )

    assert retried == created
    with pytest.raises(IdempotencyConflictError, match="different request"):
        repository.create_expense(
            retried_input,
            request_hash="b" * 64,
            audit_event=_audit(retried_input, "create", None),
        )
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.expenses WHERE profile_id = %s",
            (profile.id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE profile_id = %s",
            (profile.id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_concurrent_replacements_allow_exactly_one_winner(postgres_url: str) -> None:
    """Prevent two writers from replacing the same observed aggregate version."""

    upgrade_database(postgres_url)
    repository, profile, source, category_id = _repository_fixture(postgres_url)
    draft = _domain_draft(profile, source, category_id)
    repository.create_expense(
        draft,
        request_hash="c" * 64,
        audit_event=_audit(draft, "create", None),
    )
    first = draft.corrected(
        amount=Money(1300),
        occurred_at=draft.occurred_at,
        description="First concurrent correction",
        merchant=None,
        category_allocations=(
            CategoryAllocation(category_id, Money(1300), "integration"),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(1300)),),
    )
    second = replace(first, description="Second concurrent correction")
    barrier = Barrier(2)

    def attempt(replacement: Expense) -> str:
        """Race one replacement after both workers hold the same version."""

        barrier.wait()
        try:
            repository.replace_expense(
                replacement,
                expected_version=draft.version,
                audit_event=_audit(replacement, "correct", draft.version),
            )
        except OptimisticConcurrencyError:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, (first, second)))

    assert sorted(results) == ["committed", "conflict"]
    stored = repository.get_expense(profile.id, draft.id)
    assert stored.version == 2
    assert stored.description in {
        "First concurrent correction",
        "Second concurrent correction",
    }
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_id = %s AND action = 'correct'",
            (draft.id,),
        ).fetchone() == (1,)
