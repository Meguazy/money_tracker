"""Read-only semantic verification for an isolated restored data set.

The Restic restore path proves snapshot, dump, configuration, and media-byte
integrity. This component verifies the recovered application's cross-record
business invariants before any runtime process is allowed to use it.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from psycopg import Connection, Error, connect

from money_tracker.domain.receipts import SHA256_PATTERN
from money_tracker.local_integrity import LocalIntegrityError, libpq_dsn, sha256_file
from money_tracker.persistence.schema import APPLICATION_SCHEMA_REVISION
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger(__name__)


class RestoreIntegrityError(RuntimeError):
    """A restored target failed a safe semantic or filesystem check.

    Attributes:
        reason_code: Stable content-free explanation suitable for diagnostics.
    """

    def __init__(self, reason_code: str) -> None:
        """Create a failure without retaining a database URL or private path.

        Args:
            reason_code: Stable machine-readable failure code.
        """

        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RestoreIntegritySettings:
    """Explicit isolated targets accepted by the restore verifier.

    Attributes:
        database_url: Credential-bearing URL for the restored database.
        expected_database: Exact isolated database name the verifier may read.
        receipt_root: Absolute private directory containing restored originals.
    """

    database_url: str
    expected_database: str
    receipt_root: Path

    @classmethod
    def from_env(cls) -> RestoreIntegritySettings:
        """Load the same explicit targets used by the restore drill.

        Returns:
            Settings that identify one restored database and receipt archive.

        Raises:
            RestoreIntegrityError: If a setting is absent or ambiguous.
        """

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RestoreIntegrityError("restore_setting_missing")
            return value

        receipt_root = Path(required("MONEY_TRACKER_RESTORE_RECEIPT_ROOT"))
        if not receipt_root.is_absolute():
            raise RestoreIntegrityError("receipt_root_not_absolute")
        return cls(
            database_url=required("MONEY_TRACKER_RESTORE_DATABASE_URL"),
            expected_database=required("MONEY_TRACKER_RESTORE_EXPECTED_DATABASE"),
            receipt_root=receipt_root,
        )


@dataclass(frozen=True, slots=True)
class RestoreIntegrityResult:
    """Content-free counts from a completed semantic verification.

    Attributes:
        database_name: Verified isolated database identity.
        alembic_revision: Exact application schema revision found.
        active_profile_count: Number of active owner profiles.
        default_source_count: Number of configured default funding sources.
        expense_count: Number of restored expense aggregates.
        receipt_count: Number of database-referenced receipt originals checked.
        idempotency_record_count: Number of replay-protection records checked.
        inbox_count: Number of durable inbound work items.
        outbox_count: Number of durable outbound responses.
        pending_action_count: Number of durable action correlations.
    """

    database_name: str
    alembic_revision: str
    active_profile_count: int
    default_source_count: int
    expense_count: int
    receipt_count: int
    idempotency_record_count: int
    inbox_count: int
    outbox_count: int
    pending_action_count: int


class RestoredDataVerifier:
    """Verify restored finance, messaging, idempotency, and media semantics."""

    def __init__(self, settings: RestoreIntegritySettings) -> None:
        """Bind verification to one explicitly isolated restored target.

        Args:
            settings: Exact database identity and private receipt directory.
        """

        self._settings = settings

    def verify(self) -> RestoreIntegrityResult:
        """Verify one repeatable-read database snapshot and its receipt files.

        Returns:
            Safe counts and schema identity for the verified target.

        Raises:
            RestoreIntegrityError: If identity, schema, business invariants, or
                receipt storage is invalid, or PostgreSQL cannot be read.
        """

        parsed = urlparse(self._settings.database_url)
        database_name = unquote(parsed.path.removeprefix("/"))
        if (
            parsed.scheme not in {"postgresql", "postgresql+psycopg"}
            or not database_name
            or database_name != self._settings.expected_database
        ):
            self._reject("database_scope_invalid")
        self._require_private_receipt_root()
        try:
            with connect(libpq_dsn(self._settings.database_url)) as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                identity = self._scalar(connection, "SELECT current_database()")
                revision = self._scalar(
                    connection,
                    "SELECT version_num FROM public.alembic_version",
                )
                if identity != database_name:
                    self._reject("database_identity_invalid")
                if revision != APPLICATION_SCHEMA_REVISION:
                    self._reject("database_schema_incompatible")
                self._verify_finance(connection)
                self._verify_messaging(connection)
                receipt_hashes = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT content_sha256 FROM finance.receipts "
                        "WHERE status <> 'deleted' ORDER BY content_sha256"
                    ).fetchall()
                )
                result = RestoreIntegrityResult(
                    database_name=database_name,
                    alembic_revision=str(revision),
                    active_profile_count=self._count(
                        connection, "SELECT count(*) FROM finance.profiles WHERE active"
                    ),
                    default_source_count=self._count(
                        connection,
                        "SELECT count(*) FROM finance.funding_sources WHERE is_default",
                    ),
                    expense_count=self._count(
                        connection, "SELECT count(*) FROM finance.expenses"
                    ),
                    receipt_count=len(receipt_hashes),
                    idempotency_record_count=self._count(
                        connection,
                        "SELECT count(*) FROM finance.idempotency_records",
                    ),
                    inbox_count=self._count(
                        connection, "SELECT count(*) FROM messaging.telegram_inbox"
                    ),
                    outbox_count=self._count(
                        connection, "SELECT count(*) FROM messaging.telegram_outbox"
                    ),
                    pending_action_count=self._count(
                        connection,
                        "SELECT count(*) FROM messaging.telegram_pending_actions",
                    ),
                )
        except RestoreIntegrityError:
            raise
        except Error as error:
            _LOGGER.exception(
                "restore.integrity.database_failed",
                reason_code="database_unavailable",
            )
            raise RestoreIntegrityError("database_unavailable") from error
        self._verify_receipts(receipt_hashes)
        _LOGGER.info(
            "restore.integrity.verified",
            database_name=result.database_name,
            alembic_revision=result.alembic_revision,
            active_profile_count=result.active_profile_count,
            default_source_count=result.default_source_count,
            expense_count=result.expense_count,
            receipt_count=result.receipt_count,
            idempotency_record_count=result.idempotency_record_count,
            inbox_count=result.inbox_count,
            outbox_count=result.outbox_count,
            pending_action_count=result.pending_action_count,
        )
        return result

    def _require_private_receipt_root(self) -> None:
        """Require a real owner-only receipt directory without changing it."""

        try:
            metadata = self._settings.receipt_root.stat(follow_symlinks=False)
        except OSError as error:
            raise RestoreIntegrityError("receipt_root_unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode):
            self._reject("receipt_root_invalid")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            self._reject("receipt_root_not_private")

    def _verify_receipts(self, hashes: tuple[str, ...]) -> None:
        """Verify each non-deleted database receipt against a private file."""

        for digest in hashes:
            if SHA256_PATTERN.fullmatch(digest) is None:
                self._reject("receipt_hash_invalid")
            path = self._settings.receipt_root / digest[:2] / digest
            try:
                metadata = path.stat(follow_symlinks=False)
                actual = sha256_file(path)
            except (OSError, LocalIntegrityError) as error:
                raise RestoreIntegrityError("receipt_file_unavailable") from error
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                self._reject("receipt_file_not_private")
            if actual != digest:
                self._reject("receipt_checksum_mismatch")

    def _verify_finance(self, connection: Connection[tuple[object, ...]]) -> None:
        """Check cross-row finance and replay-protection invariants."""

        checks = (
            (
                "profile_default_source_invalid",
                """
                SELECT count(*) FROM finance.profiles AS profile
                WHERE
                    (profile.default_funding_source_id IS NULL AND EXISTS (
                        SELECT 1 FROM finance.funding_sources AS source
                        WHERE source.profile_id = profile.id AND source.is_default
                    ))
                    OR (profile.default_funding_source_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM finance.funding_sources AS source
                        WHERE source.id = profile.default_funding_source_id
                          AND source.profile_id = profile.id
                          AND source.status = 'active'
                          AND source.is_default
                    ))
                """,
            ),
            (
                "expense_reconciliation_invalid",
                """
                SELECT count(*) FROM finance.expenses AS expense
                WHERE expense.status IN ('posted', 'voided') AND (
                    COALESCE((SELECT sum(amount_minor)
                              FROM finance.category_allocations
                              WHERE expense_id = expense.id), 0) <> expense.amount_minor
                    OR COALESCE((
                                 SELECT sum(amount_minor)
                                 FROM finance.payment_allocations
                                 WHERE expense_id = expense.id
                    ), 0) <> expense.amount_minor
                    OR EXISTS (
                        SELECT 1 FROM finance.category_allocations AS allocation
                        JOIN finance.categories AS category
                          ON category.id = allocation.category_id
                        WHERE allocation.expense_id = expense.id
                          AND (category.profile_id <> expense.profile_id
                               OR ((allocation.amount_minor > 0)
                                   <> (expense.amount_minor > 0)))
                    )
                    OR EXISTS (
                        SELECT 1 FROM finance.payment_allocations AS allocation
                        JOIN finance.funding_sources AS source
                          ON source.id = allocation.funding_source_id
                        WHERE allocation.expense_id = expense.id
                          AND (source.profile_id <> expense.profile_id
                               OR ((allocation.amount_minor > 0)
                                   <> (expense.amount_minor > 0)))
                    )
                )
                """,
            ),
            (
                "finance_scope_invalid",
                """
                SELECT
                    (SELECT count(*) FROM finance.wallet_entries AS entry
                     JOIN finance.funding_sources AS source
                       ON source.id = entry.funding_source_id
                     WHERE source.profile_id <> entry.profile_id
                        OR source.balance_mode <> 'ledger')
                  + (SELECT count(*) FROM finance.monthly_budgets AS budget
                     JOIN finance.categories AS category
                       ON category.id = budget.category_id
                     WHERE category.profile_id <> budget.profile_id)
                """,
            ),
            (
                "idempotency_target_missing",
                """
                SELECT count(*) FROM finance.idempotency_records AS record
                WHERE NOT EXISTS (
                    SELECT 1 FROM finance.expenses AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.receipts AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.categories AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.funding_sources AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.wallet_entries AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.monthly_budgets AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                    UNION ALL
                    SELECT 1 FROM finance.category_proposals AS item
                    WHERE item.id = record.aggregate_id
                      AND item.profile_id = record.profile_id
                )
                """,
            ),
        )
        for reason_code, query in checks:
            if self._count(connection, query):
                self._reject(reason_code)

    def _verify_messaging(self, connection: Connection[tuple[object, ...]]) -> None:
        """Check offset, queue scope, owner, and pending-action correlations."""

        checks = (
            (
                "messaging_offset_invalid",
                """
                SELECT count(*) FROM messaging.telegram_inbox AS inbox
                JOIN messaging.telegram_offsets AS offset_row
                  ON offset_row.bot_key = inbox.bot_key
                WHERE inbox.update_id >= offset_row.next_offset
                """,
            ),
            (
                "messaging_scope_invalid",
                """
                SELECT
                    (SELECT count(*) FROM messaging.telegram_outbox AS outbox
                     JOIN messaging.telegram_inbox AS inbox
                       ON inbox.id = outbox.inbox_id
                     WHERE outbox.bot_key <> inbox.bot_key)
                  + (SELECT count(*) FROM messaging.telegram_owner_bindings AS binding
                     JOIN finance.profiles AS profile ON profile.id = binding.profile_id
                     WHERE NOT profile.active)
                  + (SELECT count(*) FROM messaging.telegram_pending_actions AS action
                     JOIN messaging.telegram_outbox AS outbox
                       ON outbox.id = action.outbox_id
                     WHERE action.bot_key <> outbox.bot_key)
                """,
            ),
            (
                "pending_action_target_missing",
                """
                SELECT count(*) FROM messaging.telegram_pending_actions AS action
                WHERE (action.target_type = 'movement' AND NOT EXISTS (
                           SELECT 1 FROM finance.expenses AS expense
                           WHERE expense.id = action.target_id
                             AND expense.profile_id = action.profile_id
                       ))
                   OR (action.target_type = 'category_review' AND NOT EXISTS (
                           SELECT 1 FROM finance.category_proposals AS proposal
                           WHERE proposal.id = action.target_id
                             AND proposal.profile_id = action.profile_id
                       ))
                """,
            ),
        )
        for reason_code, query in checks:
            if self._count(connection, query):
                self._reject(reason_code)

    @staticmethod
    def _scalar(connection: Connection[tuple[object, ...]], query: str) -> object:
        """Return the sole scalar produced by an integrity query."""

        row = connection.execute(query).fetchone()
        if row is None:
            raise RestoreIntegrityError("database_result_missing")
        return row[0]

    @classmethod
    def _count(cls, connection: Connection[tuple[object, ...]], query: str) -> int:
        """Return one non-negative count from an integrity query."""

        value = cls._scalar(connection, query)
        if not isinstance(value, int) or value < 0:
            raise RestoreIntegrityError("database_count_invalid")
        return value

    @staticmethod
    def _reject(reason_code: str) -> None:
        """Record and raise one content-free integrity rejection."""

        _LOGGER.error("restore.integrity.rejected", reason_code=reason_code)
        raise RestoreIntegrityError(reason_code)
