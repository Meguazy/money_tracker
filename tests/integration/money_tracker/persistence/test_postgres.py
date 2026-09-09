"""Integration tests against the real dedicated application PostgreSQL server."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from psycopg import Connection, connect, errors

from money_tracker.application import (
    AggregateNotFoundError,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ReceiptMetadataService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    CategoryProposal,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Profile,
    ReceiptMetadata,
    ReceiptStatus,
    WalletEntry,
    WalletEntryType,
    WalletError,
    YearMonth,
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
) -> tuple[PostgresFinanceRepository, Profile, FundingSource, UUID]:
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
    category_id: UUID,
    *,
    expense_id: UUID | None = None,
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


def _generic_audit(
    *,
    profile_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    action: str,
    prior_version: int | None,
    current_version: int,
) -> AuditEvent:
    """Build audit metadata for non-expense integration aggregates."""

    return AuditEvent(
        id=uuid4(),
        profile_id=profile_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        action=action,
        actor="integration",
        source_reference=f"integration:{aggregate_type}",
        occurred_at=SystemClock().now(),
        prior_version=prior_version,
        current_version=current_version,
        reason="repository integration",
    )


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


@pytest.mark.postgres
def test_wallet_debit_is_balance_safe_and_audited(postgres_url: str) -> None:
    """Lock the wallet, reject overdraft, and leave rejected mutations absent."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Buoni pasto integrazione",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.LEDGER,
    )
    repository.create_funding_source(source, occurred_at=SystemClock().now())
    opening = WalletEntry(
        id=uuid4(),
        profile_id=profile.id,
        funding_source_id=source.id,
        entry_type=WalletEntryType.OPENING,
        amount=Money(5000),
        occurred_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
    )
    debit = WalletEntry(
        id=uuid4(),
        profile_id=profile.id,
        funding_source_id=source.id,
        entry_type=WalletEntryType.DEBIT,
        amount=Money(-3000),
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
    )
    repository.append_wallet_entry(
        opening,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="wallet_entry",
            aggregate_id=opening.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    repository.append_wallet_entry(
        debit,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="wallet_entry",
            aggregate_id=debit.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    overdraft = replace(debit, id=uuid4(), amount=Money(-2001))

    with pytest.raises(WalletError, match="insufficient"):
        repository.append_wallet_entry(
            overdraft,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="wallet_entry",
                aggregate_id=overdraft.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    entries = repository.get_wallet_entries(profile.id, source.id)
    assert [entry.amount for entry in entries] == [Money(5000), Money(-3000)]
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_type = 'wallet_entry'",
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_concurrent_wallet_debits_cannot_overdraw(postgres_url: str) -> None:
    """Serialize two debit attempts so only one can consume a shared balance."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Welfare integrazione",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.LEDGER,
    )
    repository.create_funding_source(source, occurred_at=SystemClock().now())
    opening = WalletEntry(
        id=uuid4(),
        profile_id=profile.id,
        funding_source_id=source.id,
        entry_type=WalletEntryType.OPENING,
        amount=Money(1000),
        occurred_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
    )
    repository.append_wallet_entry(
        opening,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="wallet_entry",
            aggregate_id=opening.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    debits = tuple(
        WalletEntry(
            id=uuid4(),
            profile_id=profile.id,
            funding_source_id=source.id,
            entry_type=WalletEntryType.DEBIT,
            amount=Money(-800),
            occurred_at=datetime(2026, 9, 2, 10 + index, tzinfo=UTC),
        )
        for index in range(2)
    )
    barrier = Barrier(2)

    def attempt(entry: WalletEntry) -> str:
        """Race one debit after both workers are ready."""

        barrier.wait()
        try:
            repository.append_wallet_entry(
                entry,
                audit_event=_generic_audit(
                    profile_id=profile.id,
                    aggregate_type="wallet_entry",
                    aggregate_id=entry.id,
                    action="create",
                    prior_version=None,
                    current_version=1,
                ),
            )
        except WalletError:
            return "insufficient"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, debits))

    assert sorted(results) == ["committed", "insufficient"]
    assert (
        sum(
            entry.amount.minor_units
            for entry in repository.get_wallet_entries(profile.id, source.id)
        )
        == 200
    )


@pytest.mark.postgres
def test_budget_round_trip_and_stale_version_conflict(postgres_url: str) -> None:
    """Replace all overrides atomically and reject a stale budget writer."""

    upgrade_database(postgres_url)
    repository, profile, _, category_id = _repository_fixture(postgres_url)
    budget = MonthlyBudget(
        id=uuid4(),
        profile_id=profile.id,
        category_id=category_id,
        recurring_target=Money(20_000),
        overrides=((YearMonth(2026, 10), Money(25_000)),),
    )
    repository.save_budget(
        budget,
        expected_version=None,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="monthly_budget",
            aggregate_id=budget.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    updated = replace(
        budget,
        recurring_target=Money(22_000),
        overrides=((YearMonth(2026, 11), Money(30_000)),),
        version=2,
    )
    repository.save_budget(
        updated,
        expected_version=1,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="monthly_budget",
            aggregate_id=budget.id,
            action="replace",
            prior_version=1,
            current_version=2,
        ),
    )

    assert repository.get_budget(profile.id, category_id) == updated
    stale = replace(updated, recurring_target=Money(23_000), version=3)
    with pytest.raises(OptimisticConcurrencyError, match="changed"):
        repository.save_budget(
            stale,
            expected_version=1,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="monthly_budget",
                aggregate_id=budget.id,
                action="replace",
                prior_version=2,
                current_version=3,
            ),
        )
    assert repository.get_budget(profile.id, category_id) == updated


@pytest.mark.postgres
def test_wallet_rejects_untracked_source_without_partial_audit(
    postgres_url: str,
) -> None:
    """Reject ledger entries for label-only sources before any database append."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta non tracciata",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
    )
    repository.create_funding_source(source, occurred_at=SystemClock().now())
    entry = WalletEntry(
        id=uuid4(),
        profile_id=profile.id,
        funding_source_id=source.id,
        entry_type=WalletEntryType.CREDIT,
        amount=Money(1000),
        occurred_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="ledger source"):
        repository.append_wallet_entry(
            entry,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="wallet_entry",
                aggregate_id=entry.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    assert repository.get_wallet_entries(profile.id, source.id) == ()
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (entry.id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_wallet_entry_rolls_back_when_matching_audit_cannot_persist(
    postgres_url: str,
) -> None:
    """Roll back the wallet append if the same transaction rejects its audit."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Wallet rollback",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.LEDGER,
    )
    repository.create_funding_source(source, occurred_at=SystemClock().now())
    entry = WalletEntry(
        id=uuid4(),
        profile_id=profile.id,
        funding_source_id=source.id,
        entry_type=WalletEntryType.OPENING,
        amount=Money(4000),
        occurred_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    with pytest.raises(errors.ForeignKeyViolation):
        repository.append_wallet_entry(
            entry,
            audit_event=_generic_audit(
                profile_id=uuid4(),
                aggregate_type="wallet_entry",
                aggregate_id=entry.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    assert repository.get_wallet_entries(profile.id, source.id) == ()


@pytest.mark.postgres
def test_duplicate_budget_create_preserves_original_and_single_audit(
    postgres_url: str,
) -> None:
    """Reject a second category budget without replacing the first snapshot."""

    upgrade_database(postgres_url)
    repository, profile, _, category_id = _repository_fixture(postgres_url)
    original = MonthlyBudget(
        id=uuid4(),
        profile_id=profile.id,
        category_id=category_id,
        recurring_target=Money(15_000),
    )
    repository.save_budget(
        original,
        expected_version=None,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="monthly_budget",
            aggregate_id=original.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    duplicate = replace(original, id=uuid4(), recurring_target=Money(99_000))

    with pytest.raises(OptimisticConcurrencyError, match="already exists"):
        repository.save_budget(
            duplicate,
            expected_version=None,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="monthly_budget",
                aggregate_id=duplicate.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    assert repository.get_budget(profile.id, category_id) == original
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_type = 'monthly_budget'",
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_concurrent_budget_replacements_allow_exactly_one_winner(
    postgres_url: str,
) -> None:
    """Use the version predicate to resolve a real two-writer budget race."""

    upgrade_database(postgres_url)
    repository, profile, _, category_id = _repository_fixture(postgres_url)
    original = MonthlyBudget(
        id=uuid4(),
        profile_id=profile.id,
        category_id=category_id,
        recurring_target=Money(10_000),
    )
    repository.save_budget(
        original,
        expected_version=None,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="monthly_budget",
            aggregate_id=original.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    candidates = (
        replace(original, recurring_target=Money(11_000), version=2),
        replace(original, recurring_target=Money(12_000), version=2),
    )
    barrier = Barrier(2)

    def attempt(candidate: MonthlyBudget) -> str:
        """Race one replacement from the same observed version."""

        barrier.wait()
        try:
            repository.save_budget(
                candidate,
                expected_version=1,
                audit_event=_generic_audit(
                    profile_id=profile.id,
                    aggregate_type="monthly_budget",
                    aggregate_id=original.id,
                    action="replace",
                    prior_version=1,
                    current_version=2,
                ),
            )
        except OptimisticConcurrencyError:
            return "stale"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, candidates))

    assert sorted(results) == ["committed", "stale"]
    persisted = repository.get_budget(profile.id, category_id)
    assert persisted is not None
    assert persisted.version == 2
    assert persisted.recurring_target in {Money(11_000), Money(12_000)}
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_type = 'monthly_budget'",
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_latest_correctable_movement_follows_recording_order(
    postgres_url: str,
) -> None:
    """Offer the movement the owner last saw, never a voided one."""

    upgrade_database(postgres_url)
    repository, profile, source, category_id = _repository_fixture(postgres_url)
    assert repository.get_latest_correctable_movement(profile.id) is None

    older = _domain_draft(profile, source, category_id, idempotency_key="latest:1")
    repository.create_expense(
        older,
        audit_event=_audit(older, "create", None),
        request_hash="a" * 64,
    )
    newer = _domain_draft(profile, source, category_id, idempotency_key="latest:2")
    repository.create_expense(
        newer,
        audit_event=_audit(newer, "create", None),
        request_hash="b" * 64,
    )

    assert repository.get_latest_correctable_movement(profile.id) == newer.id

    with connect(postgres_url) as connection:
        connection.execute(
            "UPDATE finance.expenses SET status = 'voided' WHERE id = %s",
            (newer.id,),
        )

    assert repository.get_latest_correctable_movement(profile.id) == older.id


def _category_proposal(
    profile: Profile,
    *,
    parent_id: UUID | None,
    alternative_category_ids: tuple[UUID, ...],
    proposed_name: str = "Mobilità dolce",
) -> CategoryProposal:
    """Build a pending proposal against persisted taxonomy identifiers."""

    return CategoryProposal(
        id=uuid4(),
        profile_id=profile.id,
        proposed_name=proposed_name,
        description="Biciclette, monopattini e relativa manutenzione.",
        parent_id=parent_id,
        rationale="La distinzione cambia report e decisioni di spesa.",
        alternative_category_ids=alternative_category_ids,
        excluded_names=("Micromobilità una tantum",),
        source_reference="integration:proposal",
    )


@pytest.mark.postgres
def test_category_proposal_round_trip_is_taxonomy_scoped_and_audited(
    postgres_url: str,
) -> None:
    """Persist arrays and relationships through the real PostgreSQL protocol."""

    upgrade_database(postgres_url)
    repository, profile, _, root_id = _repository_fixture(postgres_url)
    alternative_id = initial_taxonomy().categories[-1].id
    proposal = _category_proposal(
        profile,
        parent_id=root_id,
        alternative_category_ids=(alternative_id,),
    )

    repository.create_category_proposal(
        proposal,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="category_proposal",
            aggregate_id=proposal.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )

    assert repository.get_category_proposal(profile.id, proposal.id) == proposal
    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        repository.get_category_proposal(uuid4(), proposal.id)
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT normalized_name FROM finance.category_proposals WHERE id = %s",
            (proposal.id,),
        ).fetchone() == ("mobilita dolce",)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (proposal.id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_category_proposal_rejects_existing_name_without_partial_rows(
    postgres_url: str,
) -> None:
    """Reject active category and alias duplicates before proposal insertion."""

    upgrade_database(postgres_url)
    repository, profile, _, root_id = _repository_fixture(postgres_url)
    existing = initial_taxonomy().categories[0]
    proposal = _category_proposal(
        profile,
        parent_id=root_id,
        alternative_category_ids=(),
        proposed_name=existing.name,
    )

    with pytest.raises(ValueError, match="duplicates"):
        repository.create_category_proposal(
            proposal,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="category_proposal",
                aggregate_id=proposal.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.category_proposals"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (proposal.id,),
        ).fetchone() == (0,)


@pytest.mark.postgres
@pytest.mark.parametrize("invalid_relationship", ["parent", "alternative"])
def test_category_proposal_rejects_out_of_scope_relationships(
    postgres_url: str,
    invalid_relationship: str,
) -> None:
    """Require every relationship to resolve inside the active profile taxonomy."""

    upgrade_database(postgres_url)
    repository, profile, _, root_id = _repository_fixture(postgres_url)
    proposal = _category_proposal(
        profile,
        parent_id=uuid4() if invalid_relationship == "parent" else root_id,
        alternative_category_ids=(uuid4(),)
        if invalid_relationship == "alternative"
        else (),
    )

    with pytest.raises(ValueError, match=invalid_relationship):
        repository.create_category_proposal(
            proposal,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="category_proposal",
                aggregate_id=proposal.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )


@pytest.mark.postgres
def test_category_proposal_rolls_back_when_audit_fails(postgres_url: str) -> None:
    """Keep proposal and audit atomic when the audit violates profile scope."""

    upgrade_database(postgres_url)
    repository, profile, _, root_id = _repository_fixture(postgres_url)
    proposal = _category_proposal(
        profile,
        parent_id=root_id,
        alternative_category_ids=(),
    )

    with pytest.raises(errors.ForeignKeyViolation):
        repository.create_category_proposal(
            proposal,
            audit_event=_generic_audit(
                profile_id=uuid4(),
                aggregate_type="category_proposal",
                aggregate_id=proposal.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        repository.get_category_proposal(profile.id, proposal.id)


def _receipt_metadata(
    profile: Profile,
    *,
    digest: str = "b" * 64,
    media_type: str = "image/png",
    byte_size: int = 4096,
) -> ReceiptMetadata:
    """Build validated metadata for content stored outside PostgreSQL."""

    return ReceiptMetadata(
        id=uuid4(),
        profile_id=profile.id,
        content_sha256=digest,
        media_type=media_type,
        byte_size=byte_size,
        stored_at=datetime(2026, 9, 2, 21, tzinfo=UTC),
        source_reference="integration:receipt",
        original_filename="ricevuta.png",
    )


@pytest.mark.postgres
def test_receipt_metadata_round_trip_is_profile_scoped_and_audited(
    postgres_url: str,
) -> None:
    """Persist safe metadata without database paths or media payloads."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    metadata = _receipt_metadata(profile)

    result = repository.create_receipt_metadata(
        metadata,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=metadata.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )

    assert result == metadata
    assert repository.get_receipt_metadata(profile.id, metadata.id) == metadata
    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        repository.get_receipt_metadata(uuid4(), metadata.id)
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (metadata.id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_receipt_digest_retry_returns_first_record_and_audits_once(
    postgres_url: str,
) -> None:
    """Use profile plus SHA-256 as natural idempotency for repeated uploads."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    first = _receipt_metadata(profile)
    repository.create_receipt_metadata(
        first,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=first.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    retry = replace(
        first,
        id=uuid4(),
        source_reference="integration:receipt-retry",
        original_filename="copia.png",
    )

    result = repository.create_receipt_metadata(
        retry,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=retry.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )

    assert result == first
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.receipts"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_type = 'receipt'",
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_concurrent_receipt_digest_retries_converge_on_one_record(
    postgres_url: str,
) -> None:
    """Deduplicate two real concurrent inserts and retain one creation audit."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    candidates = (
        _receipt_metadata(profile),
        replace(
            _receipt_metadata(profile),
            source_reference="integration:receipt-concurrent",
            original_filename="seconda-copia.png",
        ),
    )
    barrier = Barrier(2)

    def attempt(candidate: ReceiptMetadata) -> ReceiptMetadata:
        """Race one registration after both database writers are ready."""

        barrier.wait()
        return repository.create_receipt_metadata(
            candidate,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="receipt",
                aggregate_id=candidate.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(attempt, candidates))

    assert results[0] == results[1]
    assert results[0] in candidates
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.receipts"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_type = 'receipt'",
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_receipt_digest_metadata_conflict_preserves_first_record(
    postgres_url: str,
) -> None:
    """Reject inconsistent size/type claims for an already registered digest."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    first = _receipt_metadata(profile)
    repository.create_receipt_metadata(
        first,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=first.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    conflict = replace(first, id=uuid4(), byte_size=first.byte_size + 1)

    with pytest.raises(ValueError, match="conflicts"):
        repository.create_receipt_metadata(
            conflict,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="receipt",
                aggregate_id=conflict.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    assert repository.get_receipt_metadata(profile.id, first.id) == first


@pytest.mark.postgres
def test_receipt_metadata_rolls_back_when_audit_fails(postgres_url: str) -> None:
    """Roll back newly inserted metadata if its audit cannot commit."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    metadata = _receipt_metadata(profile)

    with pytest.raises(errors.ForeignKeyViolation):
        repository.create_receipt_metadata(
            metadata,
            audit_event=_generic_audit(
                profile_id=uuid4(),
                aggregate_type="receipt",
                aggregate_id=metadata.id,
                action="create",
                prior_version=None,
                current_version=1,
            ),
        )

    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        repository.get_receipt_metadata(profile.id, metadata.id)


@pytest.mark.postgres
def test_receipt_version_migration_preserves_existing_metadata(
    postgres_url: str,
) -> None:
    """Upgrade a pre-version receipt row in place with initial version one."""

    upgrade_database(postgres_url, "20260902_0002")
    profile_id = uuid4()
    receipt_id = uuid4()
    stored_at = datetime(2026, 9, 2, 21, tzinfo=UTC)
    with connect(postgres_url) as connection:
        _insert_profile(connection, profile_id)
        connection.execute(
            "INSERT INTO finance.receipts "
            "(id, profile_id, content_sha256, media_type, byte_size, stored_at, "
            "source_reference, status, original_filename) "
            "VALUES (%s, %s, %s, 'image/png', 4096, %s, %s, 'stored', %s)",
            (
                receipt_id,
                profile_id,
                "c" * 64,
                stored_at,
                "integration:pre-version-receipt",
                "ricevuta.png",
            ),
        )

    upgrade_database(postgres_url)

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT status, version FROM finance.receipts WHERE id = %s",
            (receipt_id,),
        ).fetchone()
    assert row == ("stored", 1)


@pytest.mark.postgres
def test_receipt_review_lifecycle_persists_and_audits_every_transition(
    postgres_url: str,
) -> None:
    """Exercise registration, mandatory review, and owner approval end to end."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    service = ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    )
    stored = service.register(
        _receipt_metadata(profile, digest="d" * 64),
        actor="receipt-store",
        reason="validated content stored",
    )
    review = service.mark_needs_review(
        profile_id=profile.id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="draft ready for review",
    )
    approved = service.approve(
        profile_id=profile.id,
        receipt_id=stored.id,
        expected_version=2,
        actor="owner",
        reason="owner approved preview",
    )

    assert review.status is ReceiptStatus.NEEDS_REVIEW
    assert approved.status is ReceiptStatus.APPROVED
    assert repository.get_receipt_metadata(profile.id, stored.id) == approved
    with connect(postgres_url) as connection:
        audits = connection.execute(
            "SELECT action, prior_version, current_version "
            "FROM finance.audit_events WHERE aggregate_id = %s "
            "ORDER BY current_version",
            (stored.id,),
        ).fetchall()
    assert audits == [
        ("create", None, 1),
        ("mark_needs_review", 1, 2),
        ("approve", 2, 3),
    ]


@pytest.mark.postgres
def test_concurrent_receipt_review_writers_have_exactly_one_winner(
    postgres_url: str,
) -> None:
    """Use the real PostgreSQL compare-and-swap boundary under contention."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    stored = _receipt_metadata(profile, digest="e" * 64)
    repository.create_receipt_metadata(
        stored,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=stored.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    candidate = stored.mark_needs_review()
    barrier = Barrier(2)

    def attempt(_: int) -> bool:
        """Race a review transition and report whether this writer committed."""

        barrier.wait()
        try:
            repository.replace_receipt_metadata(
                candidate,
                expected_version=1,
                audit_event=_generic_audit(
                    profile_id=profile.id,
                    aggregate_type="receipt",
                    aggregate_id=stored.id,
                    action="mark_needs_review",
                    prior_version=1,
                    current_version=2,
                ),
            )
        except OptimisticConcurrencyError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(attempt, range(2)))

    assert sorted(outcomes) == [False, True]
    assert repository.get_receipt_metadata(profile.id, stored.id) == candidate
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (stored.id,),
        ).fetchone() == (2,)


@pytest.mark.postgres
def test_receipt_review_rolls_back_when_audit_fails(postgres_url: str) -> None:
    """Keep stored state unchanged when its transition audit cannot commit."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    stored = _receipt_metadata(profile, digest="f" * 64)
    repository.create_receipt_metadata(
        stored,
        audit_event=_generic_audit(
            profile_id=profile.id,
            aggregate_type="receipt",
            aggregate_id=stored.id,
            action="create",
            prior_version=None,
            current_version=1,
        ),
    )
    review = stored.mark_needs_review()

    with pytest.raises(errors.ForeignKeyViolation):
        repository.replace_receipt_metadata(
            review,
            expected_version=1,
            audit_event=_generic_audit(
                profile_id=uuid4(),
                aggregate_type="receipt",
                aggregate_id=stored.id,
                action="mark_needs_review",
                prior_version=1,
                current_version=2,
            ),
        )

    assert repository.get_receipt_metadata(profile.id, stored.id) == stored
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_id = %s",
            (stored.id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_receipt_repository_rejects_noninitial_version(
    postgres_url: str,
) -> None:
    """Reject a caller that tries to create stored metadata after version one."""

    upgrade_database(postgres_url)
    repository, profile, _, _ = _repository_fixture(postgres_url)
    metadata = replace(_receipt_metadata(profile), version=2)

    with pytest.raises(ValueError, match="version one"):
        repository.create_receipt_metadata(
            metadata,
            audit_event=_generic_audit(
                profile_id=profile.id,
                aggregate_type="receipt",
                aggregate_id=metadata.id,
                action="create",
                prior_version=None,
                current_version=2,
            ),
        )

    with pytest.raises(AggregateNotFoundError, match="does not exist"):
        repository.get_receipt_metadata(profile.id, metadata.id)
