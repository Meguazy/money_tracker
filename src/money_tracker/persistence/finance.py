"""PostgreSQL adapter for authoritative finance aggregates and audit history."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from psycopg import Connection, connect

from money_tracker.application.errors import (
    AggregateNotFoundError,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
)
from money_tracker.domain import (
    TAXONOMY_VERSION,
    AuditEvent,
    BalanceMode,
    Category,
    CategoryAllocation,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Profile,
    Taxonomy,
    WalletEntry,
    WalletEntryType,
    YearMonth,
    normalize_category_name,
    require_available_balance,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PostgresFinanceRepository:
    """Store complete expense aggregates under explicit PostgreSQL transactions."""

    def __init__(self, database_url: str) -> None:
        """Bind the adapter to the dedicated application database.

        Args:
            database_url: PostgreSQL connection URL retained outside diagnostics.
        """

        if not database_url.startswith("postgresql://"):
            raise ValueError("finance repository requires a PostgreSQL URL")
        self._database_url = database_url

    def bootstrap_profile(
        self,
        profile: Profile,
        *,
        taxonomy: Taxonomy,
        occurred_at: datetime,
    ) -> None:
        """Create the sole profile and accepted taxonomy idempotently.

        Args:
            profile: Stable V1 profile contract.
            taxonomy: Accepted seed categories.
            occurred_at: Aware bootstrap timestamp.

        Raises:
            ValueError: If time is naive or an existing seed differs.
        """

        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("bootstrap timestamp must include a timezone")
        with connect(self._database_url) as connection:
            inserted = connection.execute(
                "INSERT INTO finance.profiles "
                "(id, locale, timezone, currency, default_funding_source_id, "
                "active, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, true, %s, %s) "
                "ON CONFLICT (id) DO NOTHING RETURNING id",
                (
                    profile.id,
                    profile.locale,
                    profile.timezone,
                    profile.currency,
                    profile.default_funding_source_id,
                    occurred_at,
                    occurred_at,
                ),
            ).fetchone()
            if inserted is None:
                existing = connection.execute(
                    "SELECT locale, timezone, currency, default_funding_source_id "
                    "FROM finance.profiles WHERE id = %s",
                    (profile.id,),
                ).fetchone()
                expected = (
                    profile.locale,
                    profile.timezone,
                    profile.currency,
                    profile.default_funding_source_id,
                )
                if existing != expected:
                    raise ValueError("existing profile differs from bootstrap contract")
            for category in taxonomy.categories:
                self._insert_seed_category(
                    connection, profile.id, category, occurred_at
                )

    def create_funding_source(
        self,
        source: FundingSource,
        *,
        occurred_at: datetime,
    ) -> None:
        """Persist one funding source for administrative bootstrap.

        Args:
            source: Validated source contract.
            occurred_at: Aware creation timestamp.
        """

        with connect(self._database_url) as connection:
            connection.execute(
                "INSERT INTO finance.funding_sources "
                "(id, profile_id, name, normalized_name, funding_class, "
                "balance_mode, status, is_default, version, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)",
                (
                    source.id,
                    source.profile_id,
                    source.name,
                    normalize_category_name(source.name),
                    source.funding_class.value,
                    source.balance_mode.value,
                    source.status.value,
                    source.is_default,
                    occurred_at,
                    occurred_at,
                ),
            )

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Load categories and aliases for one profile.

        Args:
            profile_id: Trusted owner scope.

        Returns:
            Validated immutable taxonomy.
        """

        with connect(self._database_url) as connection:
            rows = connection.execute(
                "SELECT category.id, category.name, category.description, "
                "category.parent_id, category.status, category.seeded, "
                "COALESCE(array_agg(alias.alias ORDER BY alias.alias) "
                "FILTER (WHERE alias.alias IS NOT NULL), '{}') "
                "FROM finance.categories category "
                "LEFT JOIN finance.category_aliases alias "
                "ON alias.category_id = category.id "
                "WHERE category.profile_id = %s "
                "GROUP BY category.id "
                "ORDER BY category.created_at, category.id",
                (profile_id,),
            ).fetchall()
        return Taxonomy(
            tuple(
                Category(
                    id=row[0],
                    name=row[1],
                    description=row[2],
                    parent_id=row[3],
                    status=CategoryStatus(row[4]),
                    seeded=row[5],
                    aliases=tuple(row[6]),
                )
                for row in rows
            )
        )

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Load all funding sources for one profile.

        Args:
            profile_id: Trusted owner scope.

        Returns:
            Sources ordered by stable identifier.
        """

        with connect(self._database_url) as connection:
            rows = connection.execute(
                "SELECT id, profile_id, name, funding_class, balance_mode, "
                "status, is_default FROM finance.funding_sources "
                "WHERE profile_id = %s ORDER BY id",
                (profile_id,),
            ).fetchall()
        return tuple(
            FundingSource(
                id=row[0],
                profile_id=row[1],
                name=row[2],
                funding_class=FundingClass(row[3]),
                balance_mode=BalanceMode(row[4]),
                status=FundingSourceStatus(row[5]),
                is_default=row[6],
            )
            for row in rows
        )

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Load one complete aggregate within a trusted profile.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.

        Returns:
            Reconstructed validated expense.

        Raises:
            AggregateNotFoundError: If no matching aggregate exists.
        """

        with connect(self._database_url) as connection:
            return self._load_expense(connection, profile_id, expense_id)

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Atomically claim an idempotency key and create one aggregate.

        Args:
            expense: Validated aggregate.
            request_hash: Canonical lowercase SHA-256 of request semantics.
            audit_event: Matching immutable creation event.

        Returns:
            Created expense or the existing result for an identical request.

        Raises:
            IdempotencyConflictError: If the key was used for other content.
        """

        if _SHA256.fullmatch(request_hash) is None:
            raise ValueError("request_hash must be lowercase SHA-256")
        with connect(self._database_url) as connection:
            claimed = connection.execute(
                "INSERT INTO finance.idempotency_records "
                "(profile_id, idempotency_key, command_type, request_hash, "
                "aggregate_id, created_at) VALUES (%s, %s, 'create_expense', "
                "%s, %s, %s) ON CONFLICT DO NOTHING RETURNING aggregate_id",
                (
                    expense.profile_id,
                    expense.idempotency_key,
                    request_hash,
                    expense.id,
                    audit_event.occurred_at,
                ),
            ).fetchone()
            if claimed is None:
                prior = connection.execute(
                    "SELECT request_hash, aggregate_id "
                    "FROM finance.idempotency_records "
                    "WHERE profile_id = %s AND idempotency_key = %s",
                    (expense.profile_id, expense.idempotency_key),
                ).fetchone()
                if prior is None or prior[0] != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was used for different request content"
                    )
                return self._load_expense(connection, expense.profile_id, prior[1])
            self._insert_expense(connection, expense, audit_event.occurred_at)
            self._insert_audit(connection, audit_event)
        return expense

    def replace_expense(
        self,
        expense: Expense,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Atomically replace an aggregate if its version is still current.

        Args:
            expense: Full replacement state.
            expected_version: Version observed by the caller.
            audit_event: Immutable matching mutation record.

        Raises:
            OptimisticConcurrencyError: If another mutation already won.
        """

        with connect(self._database_url) as connection:
            cursor = connection.execute(
                "UPDATE finance.expenses SET amount_minor = %s, currency = %s, "
                "occurred_at = %s, description = %s, merchant = %s, status = %s, "
                "original_expense_id = %s, receipt_id = %s, version = %s, "
                "updated_at = %s WHERE id = %s AND profile_id = %s AND version = %s",
                (
                    expense.amount.minor_units,
                    expense.amount.currency,
                    expense.occurred_at,
                    expense.description,
                    expense.merchant,
                    expense.status.value,
                    expense.original_expense_id,
                    expense.receipt_id,
                    expense.version,
                    audit_event.occurred_at,
                    expense.id,
                    expense.profile_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(
                    "expense changed since the requested version"
                )
            connection.execute(
                "DELETE FROM finance.category_allocations WHERE expense_id = %s",
                (expense.id,),
            )
            connection.execute(
                "DELETE FROM finance.payment_allocations WHERE expense_id = %s",
                (expense.id,),
            )
            self._insert_allocations(connection, expense)
            self._insert_audit(connection, audit_event)

    def get_wallet_entries(
        self,
        profile_id: UUID,
        funding_source_id: UUID,
    ) -> tuple[WalletEntry, ...]:
        """Load immutable entries for one tracked funding source.

        Args:
            profile_id: Trusted owner scope.
            funding_source_id: Owning ledger source.

        Returns:
            Entries in deterministic occurrence and identifier order.
        """

        with connect(self._database_url) as connection:
            return self._load_wallet_entries(connection, profile_id, funding_source_id)

    def append_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Lock a tracked source and append one balance-safe entry atomically.

        Args:
            entry: Validated immutable ledger movement.
            audit_event: Matching immutable audit event.

        Raises:
            AggregateNotFoundError: If the source is outside the profile.
            ValueError: If the source is inactive or untracked.
            WalletError: If a debit exceeds the booked balance.
        """

        with connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT id, profile_id, name, funding_class, balance_mode, "
                "status, is_default FROM finance.funding_sources "
                "WHERE id = %s AND profile_id = %s FOR UPDATE",
                (entry.funding_source_id, entry.profile_id),
            ).fetchone()
            if row is None:
                raise AggregateNotFoundError("funding source does not exist in profile")
            source = FundingSource(
                id=row[0],
                profile_id=row[1],
                name=row[2],
                funding_class=FundingClass(row[3]),
                balance_mode=BalanceMode(row[4]),
                status=FundingSourceStatus(row[5]),
                is_default=row[6],
            )
            if source.status is not FundingSourceStatus.ACTIVE:
                raise ValueError("wallet source must be active")
            if source.balance_mode is not BalanceMode.LEDGER:
                raise ValueError("wallet entry requires a ledger source")
            existing = self._load_wallet_entries(
                connection, entry.profile_id, entry.funding_source_id
            )
            if entry.entry_type is WalletEntryType.DEBIT:
                require_available_balance(source, existing, -entry.amount)
            connection.execute(
                "INSERT INTO finance.wallet_entries "
                "(id, profile_id, funding_source_id, entry_type, amount_minor, "
                "currency, occurred_at, expires_on, expense_id, note, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.id,
                    entry.profile_id,
                    entry.funding_source_id,
                    entry.entry_type.value,
                    entry.amount.minor_units,
                    entry.amount.currency,
                    entry.occurred_at,
                    entry.expires_on,
                    entry.expense_id,
                    entry.note,
                    audit_event.occurred_at,
                ),
            )
            self._insert_audit(connection, audit_event)

    def get_budget(self, profile_id: UUID, category_id: UUID) -> MonthlyBudget | None:
        """Load one category budget and all explicit monthly overrides.

        Args:
            profile_id: Trusted owner scope.
            category_id: Stable budgeted category.

        Returns:
            Configured budget or ``None``.
        """

        with connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT id, profile_id, category_id, target_minor, currency, version "
                "FROM finance.monthly_budgets "
                "WHERE profile_id = %s AND category_id = %s",
                (profile_id, category_id),
            ).fetchone()
            if row is None:
                return None
            override_rows = connection.execute(
                "SELECT year, month, target_minor, currency "
                "FROM finance.monthly_budget_overrides WHERE budget_id = %s "
                "ORDER BY year, month",
                (row[0],),
            ).fetchall()
        return MonthlyBudget(
            id=row[0],
            profile_id=row[1],
            category_id=row[2],
            recurring_target=Money(row[3], row[4]),
            overrides=tuple(
                (YearMonth(item[0], item[1]), Money(item[2], item[3]))
                for item in override_rows
            ),
            version=row[5],
        )

    def save_budget(
        self,
        budget: MonthlyBudget,
        *,
        expected_version: int | None,
        audit_event: AuditEvent,
    ) -> None:
        """Create or replace a complete budget and audit atomically.

        Args:
            budget: Complete target and override state.
            expected_version: ``None`` for create, otherwise observed version.
            audit_event: Matching immutable audit event.

        Raises:
            AggregateNotFoundError: If the category is inactive or outside scope.
            OptimisticConcurrencyError: If create conflicts or version is stale.
        """

        with connect(self._database_url) as connection:
            category = connection.execute(
                "SELECT 1 FROM finance.categories "
                "WHERE id = %s AND profile_id = %s AND status = 'active'",
                (budget.category_id, budget.profile_id),
            ).fetchone()
            if category is None:
                raise AggregateNotFoundError("active budget category does not exist")
            if expected_version is None:
                inserted = connection.execute(
                    "INSERT INTO finance.monthly_budgets "
                    "(id, profile_id, category_id, target_minor, currency, version, "
                    "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (profile_id, category_id) DO NOTHING RETURNING id",
                    (
                        budget.id,
                        budget.profile_id,
                        budget.category_id,
                        budget.recurring_target.minor_units,
                        budget.recurring_target.currency,
                        budget.version,
                        audit_event.occurred_at,
                        audit_event.occurred_at,
                    ),
                ).fetchone()
                if inserted is None:
                    raise OptimisticConcurrencyError("budget already exists")
            else:
                cursor = connection.execute(
                    "UPDATE finance.monthly_budgets SET target_minor = %s, "
                    "currency = %s, version = %s, updated_at = %s "
                    "WHERE id = %s AND profile_id = %s AND category_id = %s "
                    "AND version = %s",
                    (
                        budget.recurring_target.minor_units,
                        budget.recurring_target.currency,
                        budget.version,
                        audit_event.occurred_at,
                        budget.id,
                        budget.profile_id,
                        budget.category_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise OptimisticConcurrencyError(
                        "budget changed since the requested version"
                    )
                connection.execute(
                    "DELETE FROM finance.monthly_budget_overrides WHERE budget_id = %s",
                    (budget.id,),
                )
            for period, target in budget.overrides:
                connection.execute(
                    "INSERT INTO finance.monthly_budget_overrides "
                    "(budget_id, year, month, target_minor, currency) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        budget.id,
                        period.year,
                        period.month,
                        target.minor_units,
                        target.currency,
                    ),
                )
            self._insert_audit(connection, audit_event)

    def _insert_seed_category(
        self,
        connection: Connection[Any],
        profile_id: UUID,
        category: Category,
        occurred_at: datetime,
    ) -> None:
        """Insert or verify one immutable code-owned seed node."""

        inserted = connection.execute(
            "INSERT INTO finance.categories "
            "(id, profile_id, name, normalized_name, description, parent_id, "
            "status, seeded, taxonomy_version, version, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, 1, %s, %s) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (
                category.id,
                profile_id,
                category.name,
                category.normalized_name,
                category.description,
                category.parent_id,
                category.status.value,
                TAXONOMY_VERSION,
                occurred_at,
                occurred_at,
            ),
        ).fetchone()
        if inserted is None:
            existing = connection.execute(
                "SELECT profile_id, name, normalized_name, description, parent_id, "
                "status, seeded, taxonomy_version FROM finance.categories "
                "WHERE id = %s",
                (category.id,),
            ).fetchone()
            expected = (
                profile_id,
                category.name,
                category.normalized_name,
                category.description,
                category.parent_id,
                category.status.value,
                True,
                TAXONOMY_VERSION,
            )
            if existing != expected:
                raise ValueError("existing category differs from taxonomy seed")

    def _insert_expense(
        self,
        connection: Connection[Any],
        expense: Expense,
        occurred_at: datetime,
    ) -> None:
        """Insert a parent and both allocation dimensions in one transaction."""

        connection.execute(
            "INSERT INTO finance.expenses "
            "(id, profile_id, amount_minor, currency, occurred_at, description, "
            "merchant, status, source_reference, idempotency_key, "
            "original_expense_id, receipt_id, version, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                expense.id,
                expense.profile_id,
                expense.amount.minor_units,
                expense.amount.currency,
                expense.occurred_at,
                expense.description,
                expense.merchant,
                expense.status.value,
                expense.source_reference,
                expense.idempotency_key,
                expense.original_expense_id,
                expense.receipt_id,
                expense.version,
                occurred_at,
                occurred_at,
            ),
        )
        self._insert_allocations(connection, expense)

    def _insert_allocations(
        self,
        connection: Connection[Any],
        expense: Expense,
    ) -> None:
        """Insert both complete allocation dimensions for one aggregate."""

        for category_allocation in expense.category_allocations:
            connection.execute(
                "INSERT INTO finance.category_allocations "
                "(expense_id, category_id, amount_minor, currency, provenance, "
                "evidence, owner_override, receipt_line_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    expense.id,
                    category_allocation.category_id,
                    category_allocation.amount.minor_units,
                    category_allocation.amount.currency,
                    category_allocation.provenance,
                    category_allocation.evidence,
                    category_allocation.owner_override,
                    category_allocation.receipt_line_id,
                ),
            )
        for payment_allocation in expense.payment_allocations:
            connection.execute(
                "INSERT INTO finance.payment_allocations "
                "(expense_id, funding_source_id, amount_minor, currency) "
                "VALUES (%s, %s, %s, %s)",
                (
                    expense.id,
                    payment_allocation.funding_source_id,
                    payment_allocation.amount.minor_units,
                    payment_allocation.amount.currency,
                ),
            )

    def _insert_audit(
        self,
        connection: Connection[Any],
        event: AuditEvent,
    ) -> None:
        """Append one immutable audit event inside the active transaction."""

        connection.execute(
            "INSERT INTO finance.audit_events "
            "(id, profile_id, aggregate_type, aggregate_id, action, actor, "
            "source_reference, occurred_at, prior_version, current_version, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event.id,
                event.profile_id,
                event.aggregate_type,
                event.aggregate_id,
                event.action,
                event.actor,
                event.source_reference,
                event.occurred_at,
                event.prior_version,
                event.current_version,
                event.reason,
            ),
        )

    def _load_expense(
        self,
        connection: Connection[Any],
        profile_id: UUID,
        expense_id: UUID,
    ) -> Expense:
        """Reconstruct one validated aggregate using its owned rows."""

        row = connection.execute(
            "SELECT id, profile_id, amount_minor, currency, occurred_at, "
            "description, merchant, status, source_reference, idempotency_key, "
            "original_expense_id, receipt_id, version FROM finance.expenses "
            "WHERE profile_id = %s AND id = %s",
            (profile_id, expense_id),
        ).fetchone()
        if row is None:
            raise AggregateNotFoundError("expense does not exist in profile")
        category_rows = connection.execute(
            "SELECT category_id, amount_minor, currency, provenance, evidence, "
            "owner_override, receipt_line_id FROM finance.category_allocations "
            "WHERE expense_id = %s ORDER BY category_id",
            (expense_id,),
        ).fetchall()
        payment_rows = connection.execute(
            "SELECT funding_source_id, amount_minor, currency "
            "FROM finance.payment_allocations WHERE expense_id = %s "
            "ORDER BY funding_source_id",
            (expense_id,),
        ).fetchall()
        return Expense(
            id=cast(UUID, row[0]),
            profile_id=cast(UUID, row[1]),
            amount=Money(row[2], row[3]),
            occurred_at=row[4],
            description=row[5],
            merchant=row[6],
            status=ExpenseStatus(row[7]),
            source_reference=row[8],
            idempotency_key=row[9],
            original_expense_id=row[10],
            receipt_id=row[11],
            version=row[12],
            category_allocations=tuple(
                CategoryAllocation(
                    category_id=item[0],
                    amount=Money(item[1], item[2]),
                    provenance=item[3],
                    evidence=item[4],
                    owner_override=item[5],
                    receipt_line_id=item[6],
                )
                for item in category_rows
            ),
            payment_allocations=tuple(
                PaymentAllocation(
                    funding_source_id=item[0],
                    amount=Money(item[1], item[2]),
                )
                for item in payment_rows
            ),
        )

    def _load_wallet_entries(
        self,
        connection: Connection[Any],
        profile_id: UUID,
        funding_source_id: UUID,
    ) -> tuple[WalletEntry, ...]:
        """Reconstruct immutable wallet entries inside an active transaction."""

        rows = connection.execute(
            "SELECT id, profile_id, funding_source_id, entry_type, amount_minor, "
            "currency, occurred_at, expires_on, expense_id, note "
            "FROM finance.wallet_entries WHERE profile_id = %s "
            "AND funding_source_id = %s ORDER BY occurred_at, id",
            (profile_id, funding_source_id),
        ).fetchall()
        return tuple(
            WalletEntry(
                id=row[0],
                profile_id=row[1],
                funding_source_id=row[2],
                entry_type=WalletEntryType(row[3]),
                amount=Money(row[4], row[5]),
                occurred_at=row[6],
                expires_on=row[7],
                expense_id=row[8],
                note=row[9],
            )
            for row in rows
        )
