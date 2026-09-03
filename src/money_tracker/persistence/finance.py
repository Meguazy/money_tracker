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
    CategoryProposal,
    CategoryProposalStatus,
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
    ReceiptMetadata,
    ReceiptStatus,
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

    def profile_exists(self, profile_id: UUID) -> bool:
        """Return whether an active profile exists for administrative preflight.

        Args:
            profile_id: Trusted profile identifier.

        Returns:
            ``True`` only for an active persisted profile.
        """

        with connect(self._database_url) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM finance.profiles WHERE id = %s AND active",
                    (profile_id,),
                ).fetchone()
                is not None
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

    def list_profiles(self) -> tuple[UUID, ...]:
        """List active profile identifiers in creation order.

        V1 has a single owner, so this normally returns at most one identifier.
        It exists so a local command can report the identifier its other
        operations require instead of an operator querying the database.

        Returns:
            Active profile identifiers, oldest first.
        """

        with connect(self._database_url) as connection:
            rows = connection.execute(
                "SELECT id FROM finance.profiles WHERE active ORDER BY created_at"
            ).fetchall()
        return tuple(row[0] for row in rows)

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

    def get_category_proposal(
        self,
        profile_id: UUID,
        proposal_id: UUID,
    ) -> CategoryProposal:
        """Load one proposal inside its trusted profile scope.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Stable proposal identifier.

        Returns:
            Reconstructed proposal review state.

        Raises:
            AggregateNotFoundError: If the proposal is outside the profile.
        """

        with connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT id, profile_id, proposed_name, description, parent_id, "
                "rationale, alternative_category_ids, excluded_names, "
                "source_reference, status FROM finance.category_proposals "
                "WHERE id = %s AND profile_id = %s",
                (proposal_id, profile_id),
            ).fetchone()
        if row is None:
            raise AggregateNotFoundError("category proposal does not exist in profile")
        return self._category_proposal_from_row(row)

    def get_pending_category_proposals(
        self,
        profile_id: UUID,
    ) -> tuple[CategoryProposal, ...]:
        """Load every open review in one profile scope, oldest first.

        Routing correlates an owner reply against these, and refuses to guess
        when several are open, so the order is stable rather than arbitrary.

        Args:
            profile_id: Trusted owner scope.

        Returns:
            Pending proposals in creation order.
        """

        with connect(self._database_url) as connection:
            rows = connection.execute(
                "SELECT id, profile_id, proposed_name, description, parent_id, "
                "rationale, alternative_category_ids, excluded_names, "
                "source_reference, status FROM finance.category_proposals "
                "WHERE profile_id = %s AND status = 'pending' "
                "ORDER BY created_at, id",
                (profile_id,),
            ).fetchall()
        return tuple(self._category_proposal_from_row(row) for row in rows)

    def settle_category_review(
        self,
        proposal: CategoryProposal,
        *,
        expense: Expense,
        expected_version: int,
        category: Category | None,
        proposal_audit: AuditEvent,
        expense_audit: AuditEvent,
    ) -> None:
        """Settle one owner decision, its optional new node, and the movement.

        This is the single transaction J3 requires: approving a candidate
        creates the node and completes the movement together, so no state
        exists in which a category was created for a movement that never
        received it, or a movement claims a category that was never created.

        The proposal row is updated only while it is still pending, which makes
        a replayed owner decision a no-op detectable by the caller rather than
        a second settlement.

        Args:
            proposal: Decided proposal carrying its terminal status.
            expense: Full replacement state for the reviewed movement.
            expected_version: Movement version observed by the caller.
            category: Node to create when the owner approved the candidate,
                otherwise None.
            proposal_audit: Immutable record of the owner decision.
            expense_audit: Immutable record of the movement mutation.

        Raises:
            ValueError: If the decision is not terminal, the node does not
                belong to the profile, or the review was already settled.
            OptimisticConcurrencyError: If another mutation already won.
        """

        if proposal.status is CategoryProposalStatus.PENDING:
            raise ValueError("settling a review requires a decided proposal")
        if category is not None and proposal.status is not (
            CategoryProposalStatus.APPROVED
        ):
            raise ValueError("only an approved candidate may create a category")
        with connect(self._database_url) as connection:
            if category is not None:
                self._insert_reviewed_category(
                    connection,
                    proposal.profile_id,
                    category,
                    proposal_audit.occurred_at,
                )
            self._settle_proposal_row(connection, proposal, proposal_audit)
            self._replace_expense_within(
                connection,
                expense,
                expected_version=expected_version,
                audit_event=expense_audit,
            )
            self._insert_audit(connection, proposal_audit)

    def repropose_category(
        self,
        rejected: CategoryProposal,
        successor: CategoryProposal,
        *,
        rejected_audit: AuditEvent,
        successor_audit: AuditEvent,
    ) -> None:
        """Refuse one candidate and open its replacement atomically.

        The movement is untouched: the owner asked for a different name, not
        for a different movement, and its draft keeps waiting.

        Args:
            rejected: Refused proposal carrying its rejected status.
            successor: New pending proposal excluding every refused candidate.
            rejected_audit: Immutable record of the refusal.
            successor_audit: Immutable record of the replacement.

        Raises:
            ValueError: If the statuses are not rejected and pending, the
                successor reuses a refused candidate, or the review was already
                settled.
        """

        if rejected.status is not CategoryProposalStatus.REJECTED:
            raise ValueError("re-proposal requires a rejected predecessor")
        if successor.status is not CategoryProposalStatus.PENDING:
            raise ValueError("a replacement candidate must be pending")
        refused = {
            normalize_category_name(name) for name in rejected.exclusions_for_next()
        }
        if not refused.issubset(
            {normalize_category_name(name) for name in successor.excluded_names}
        ):
            raise ValueError("a replacement must exclude every refused candidate")
        with connect(self._database_url) as connection:
            self._settle_proposal_row(connection, rejected, rejected_audit)
            self._insert_audit(connection, rejected_audit)
            self._insert_category_proposal_row(connection, successor, successor_audit)
            self._insert_audit(connection, successor_audit)

    def create_category_proposal(
        self,
        proposal: CategoryProposal,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Persist a pending category candidate and audit atomically.

        The adapter validates only deterministic taxonomy relationships. It does
        not approve the candidate or create a category; those owner decisions
        belong to a later bounded workflow.

        Args:
            proposal: Validated pending proposal.
            audit_event: Matching immutable creation audit.

        Raises:
            ValueError: If status, name, parent, or alternatives violate the
                current active taxonomy.
        """

        if proposal.status is not CategoryProposalStatus.PENDING:
            raise ValueError("new category proposal must be pending")
        normalized_name = normalize_category_name(proposal.proposed_name)
        with connect(self._database_url) as connection:
            if proposal.parent_id is not None:
                parent = connection.execute(
                    "SELECT parent_id, status FROM finance.categories "
                    "WHERE id = %s AND profile_id = %s",
                    (proposal.parent_id, proposal.profile_id),
                ).fetchone()
                if parent is None or parent[0] is not None or parent[1] != "active":
                    raise ValueError("proposal parent must be an active profile root")
            if proposal.alternative_category_ids:
                rows = connection.execute(
                    "SELECT id FROM finance.categories WHERE profile_id = %s "
                    "AND status = 'active' AND id = ANY(%s)",
                    (
                        proposal.profile_id,
                        list(proposal.alternative_category_ids),
                    ),
                ).fetchall()
                if {row[0] for row in rows} != set(proposal.alternative_category_ids):
                    raise ValueError(
                        "proposal alternatives must be active profile categories"
                    )
            duplicate = connection.execute(
                "SELECT 1 FROM finance.categories WHERE profile_id = %s "
                "AND status = 'active' AND normalized_name = %s "
                "UNION ALL SELECT 1 FROM finance.category_aliases alias "
                "JOIN finance.categories category ON category.id = alias.category_id "
                "WHERE category.profile_id = %s AND category.status = 'active' "
                "AND alias.normalized_alias = %s LIMIT 1",
                (
                    proposal.profile_id,
                    normalized_name,
                    proposal.profile_id,
                    normalized_name,
                ),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("proposal name duplicates an active category or alias")
            self._insert_category_proposal_row(connection, proposal, audit_event)
            self._insert_audit(connection, audit_event)

    def get_receipt_metadata(
        self,
        profile_id: UUID,
        receipt_id: UUID,
    ) -> ReceiptMetadata:
        """Load one receipt metadata record inside its owner scope.

        Args:
            profile_id: Trusted owner scope.
            receipt_id: Stable receipt identifier.

        Returns:
            Content-addressed receipt metadata without media bytes or paths.

        Raises:
            AggregateNotFoundError: If the receipt is outside the profile.
        """

        with connect(self._database_url) as connection:
            row = connection.execute(
                "SELECT id, profile_id, content_sha256, media_type, byte_size, "
                "stored_at, source_reference, status, original_filename, version "
                "FROM finance.receipts WHERE id = %s AND profile_id = %s",
                (receipt_id, profile_id),
            ).fetchone()
        if row is None:
            raise AggregateNotFoundError("receipt metadata does not exist in profile")
        return self._receipt_metadata_from_row(row)

    def create_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        audit_event: AuditEvent,
    ) -> ReceiptMetadata:
        """Create metadata once per profile and content digest.

        An identical content retry returns the first record without appending a
        second audit event. A size or media-type disagreement for an existing
        digest is treated as corrupted caller state rather than a retry.

        Args:
            metadata: Validated initial metadata from a separate media store.
            audit_event: Matching immutable creation audit.

        Returns:
            Newly persisted metadata or the pre-existing content record.

        Raises:
            ValueError: If status is not initial or digest metadata conflicts.
        """

        if metadata.status is not ReceiptStatus.STORED or metadata.version != 1:
            raise ValueError("new receipt metadata must be stored at version one")
        with connect(self._database_url) as connection:
            inserted = connection.execute(
                "INSERT INTO finance.receipts "
                "(id, profile_id, content_sha256, media_type, byte_size, stored_at, "
                "source_reference, status, original_filename, version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (profile_id, content_sha256) DO NOTHING RETURNING id",
                (
                    metadata.id,
                    metadata.profile_id,
                    metadata.content_sha256,
                    metadata.media_type,
                    metadata.byte_size,
                    metadata.stored_at,
                    metadata.source_reference,
                    metadata.status.value,
                    metadata.original_filename,
                    metadata.version,
                ),
            ).fetchone()
            if inserted is not None:
                self._insert_audit(connection, audit_event)
                return metadata
            row = connection.execute(
                "SELECT id, profile_id, content_sha256, media_type, byte_size, "
                "stored_at, source_reference, status, original_filename, version "
                "FROM finance.receipts WHERE profile_id = %s AND content_sha256 = %s",
                (metadata.profile_id, metadata.content_sha256),
            ).fetchone()
            if row is None:
                raise RuntimeError("receipt content conflict disappeared")
            existing = self._receipt_metadata_from_row(row)
            if (
                existing.media_type != metadata.media_type
                or existing.byte_size != metadata.byte_size
            ):
                raise ValueError("receipt digest conflicts with stored media metadata")
            return existing

    def replace_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Atomically replace receipt review state at the observed version.

        Only lifecycle state and version are mutable. Content identity, storage
        facts, source provenance, and display filename remain immutable.

        Args:
            metadata: Complete validated metadata after a domain transition.
            expected_version: Version observed by the caller.
            audit_event: Matching immutable transition audit.

        Raises:
            OptimisticConcurrencyError: If another transition already won.
        """

        with connect(self._database_url) as connection:
            cursor = connection.execute(
                "UPDATE finance.receipts SET status = %s, version = %s "
                "WHERE id = %s AND profile_id = %s AND version = %s",
                (
                    metadata.status.value,
                    metadata.version,
                    metadata.id,
                    metadata.profile_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError(
                    "receipt changed since the requested version"
                )
            self._insert_audit(connection, audit_event)

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
            self._replace_expense_within(
                connection,
                expense,
                expected_version=expected_version,
                audit_event=audit_event,
            )

    def _replace_expense_within(
        self,
        connection: Connection[Any],
        expense: Expense,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Replace one aggregate inside a caller-owned transaction.

        Extracted so a review settlement can replace the movement in the same
        transaction that creates its category, which is what makes the owner's
        approval atomic rather than two writes that can half-apply.

        Args:
            connection: Active transaction.
            expense: Full replacement state.
            expected_version: Version observed by the caller.
            audit_event: Immutable matching mutation record.

        Raises:
            OptimisticConcurrencyError: If another mutation already won.
        """

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

    @staticmethod
    def _category_proposal_from_row(row: tuple[Any, ...]) -> CategoryProposal:
        """Reconstruct one proposal from its selected column order."""

        return CategoryProposal(
            id=row[0],
            profile_id=row[1],
            proposed_name=row[2],
            description=row[3],
            parent_id=row[4],
            rationale=row[5],
            alternative_category_ids=tuple(row[6]),
            excluded_names=tuple(row[7]),
            source_reference=row[8],
            status=CategoryProposalStatus(row[9]),
        )

    def _settle_proposal_row(
        self,
        connection: Connection[Any],
        proposal: CategoryProposal,
        audit_event: AuditEvent,
    ) -> None:
        """Move one still-pending proposal to its decided status.

        The `status = 'pending'` predicate is the idempotency guard: a replayed
        decision matches no row and is refused here rather than settling the
        review a second time.

        Raises:
            ValueError: If the review was already settled.
        """

        cursor = connection.execute(
            "UPDATE finance.category_proposals SET status = %s, updated_at = %s "
            "WHERE id = %s AND profile_id = %s AND status = 'pending'",
            (
                proposal.status.value,
                audit_event.occurred_at,
                proposal.id,
                proposal.profile_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("category review is no longer pending")

    def _insert_reviewed_category(
        self,
        connection: Connection[Any],
        profile_id: UUID,
        category: Category,
        occurred_at: datetime,
    ) -> None:
        """Create one owner-approved node inside the active transaction.

        Unlike a seed node this is never idempotent by identifier: a second
        insertion of the same approved candidate is a defect, not a replay, so
        the unique active-name constraint is allowed to reject it.

        Raises:
            ValueError: If the parent is not an active root of this profile.
        """

        if category.parent_id is not None:
            parent = connection.execute(
                "SELECT parent_id, status FROM finance.categories "
                "WHERE id = %s AND profile_id = %s",
                (category.parent_id, profile_id),
            ).fetchone()
            if parent is None or parent[0] is not None or parent[1] != "active":
                raise ValueError("approved category parent must be an active root")
        connection.execute(
            "INSERT INTO finance.categories "
            "(id, profile_id, name, normalized_name, description, parent_id, "
            "status, seeded, taxonomy_version, version, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s, 1, %s, %s)",
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
        )

    def _insert_category_proposal_row(
        self,
        connection: Connection[Any],
        proposal: CategoryProposal,
        audit_event: AuditEvent,
    ) -> None:
        """Insert one proposal row inside a caller-owned transaction."""

        connection.execute(
            "INSERT INTO finance.category_proposals "
            "(id, profile_id, proposed_name, normalized_name, description, "
            "parent_id, rationale, alternative_category_ids, excluded_names, "
            "source_reference, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                proposal.id,
                proposal.profile_id,
                proposal.proposed_name,
                normalize_category_name(proposal.proposed_name),
                proposal.description,
                proposal.parent_id,
                proposal.rationale,
                list(proposal.alternative_category_ids),
                list(proposal.excluded_names),
                proposal.source_reference,
                proposal.status.value,
                audit_event.occurred_at,
                audit_event.occurred_at,
            ),
        )

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

    @staticmethod
    def _receipt_metadata_from_row(row: tuple[Any, ...]) -> ReceiptMetadata:
        """Reconstruct safe receipt metadata from a database result row."""

        return ReceiptMetadata(
            id=row[0],
            profile_id=row[1],
            content_sha256=row[2],
            media_type=row[3],
            byte_size=row[4],
            stored_at=row[5],
            source_reference=row[6],
            status=ReceiptStatus(row[7]),
            original_filename=row[8],
            version=row[9],
        )
