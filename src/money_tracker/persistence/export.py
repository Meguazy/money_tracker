"""Portable, snapshot-consistent export of authoritative finance records.

The export is an owner-controlled portability surface, not a database dump or
backup. It reads one profile through explicit, versioned projections inside a
repeatable-read transaction and writes newline-delimited JSON to a new private
file. Every row is self-describing and keeps stable identifiers, integer minor
units, ISO currency, and audit provenance.

Receipt originals, durable messaging, workflow checkpoints, and command
idempotency records are intentionally absent. They are operational or recovery
state rather than portable finance records. Receipt metadata and content hashes
are included so an owner can relate the export to separately retained media.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO, Final, Protocol
from uuid import UUID

from psycopg import connect
from psycopg.rows import dict_row

from money_tracker.persistence.schema import APPLICATION_SCHEMA_REVISION
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger(__name__)

FORMAT_NAME: Final = "money-tracker.finance"
"""Stable identifier of the portable finance format."""

FORMAT_VERSION: Final = 1
"""Current portable finance format version."""

SUPPORTED_DATABASE_REVISION: Final = APPLICATION_SCHEMA_REVISION
"""Exact schema revision whose projections define format version one."""


class FinanceExportError(RuntimeError):
    """A finance export was refused or failed safely.

    Attributes:
        reason_code: Stable machine-readable explanation without owner content.
        transient: Whether retrying after an external-state change may succeed.
    """

    def __init__(self, reason_code: str, *, transient: bool = False) -> None:
        """Bind safe failure facts.

        Args:
            reason_code: Stable machine-readable explanation.
            transient: Whether retrying later could succeed.
        """

        super().__init__(reason_code)
        self.reason_code = reason_code
        self.transient = transient


class _Digest(Protocol):
    """Minimal hashing contract needed while streaming the export."""

    def update(self, data: bytes) -> object:
        """Add bytes to the running digest."""


@dataclass(frozen=True, slots=True)
class FinanceExportResult:
    """Safe metadata describing a completed finance export.

    Attributes:
        profile_id: Profile whose records were exported.
        record_count: Data records written after the manifest.
        byte_count: Complete output-file size.
        sha256: SHA-256 digest prefixed with its algorithm.
        record_counts: Number of records written for each entity type.
    """

    profile_id: UUID
    record_count: int
    byte_count: int
    sha256: str
    record_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _Projection:
    """One explicitly ordered export projection."""

    record_type: str
    query: str


_PROJECTIONS: Final[tuple[_Projection, ...]] = (
    _Projection(
        "profile",
        """
        SELECT id, locale, timezone, currency, default_funding_source_id,
               active, created_at, updated_at
          FROM finance.profiles
         WHERE id = %(profile_id)s
         ORDER BY id
        """,
    ),
    _Projection(
        "category",
        """
        SELECT id, profile_id, name, description, parent_id, status, seeded,
               taxonomy_version, version, created_at, updated_at
          FROM finance.categories
         WHERE profile_id = %(profile_id)s
         ORDER BY parent_id NULLS FIRST, id
        """,
    ),
    _Projection(
        "category_alias",
        """
        SELECT alias.category_id, alias.alias
          FROM finance.category_aliases AS alias
          JOIN finance.categories AS category ON category.id = alias.category_id
         WHERE category.profile_id = %(profile_id)s
         ORDER BY alias.category_id, alias.normalized_alias
        """,
    ),
    _Projection(
        "funding_source",
        """
        SELECT id, profile_id, name, funding_class, balance_mode, status,
               is_default, version, created_at, updated_at
          FROM finance.funding_sources
         WHERE profile_id = %(profile_id)s
         ORDER BY id
        """,
    ),
    _Projection(
        "receipt",
        """
        SELECT id, profile_id, content_sha256, media_type, byte_size, stored_at,
               source_reference, status, original_filename, version
          FROM finance.receipts
         WHERE profile_id = %(profile_id)s
         ORDER BY id
        """,
    ),
    _Projection(
        "expense",
        """
        SELECT id, profile_id, amount_minor, currency, occurred_at, description,
               merchant, status, source_reference, idempotency_key,
               original_expense_id, receipt_id, version, created_at, updated_at
          FROM finance.expenses
         WHERE profile_id = %(profile_id)s
         ORDER BY occurred_at, id
        """,
    ),
    _Projection(
        "category_allocation",
        """
        SELECT allocation.expense_id, allocation.category_id,
               allocation.amount_minor, allocation.currency,
               allocation.provenance, allocation.evidence,
               allocation.owner_override, allocation.receipt_line_id
          FROM finance.category_allocations AS allocation
          JOIN finance.expenses AS expense ON expense.id = allocation.expense_id
         WHERE expense.profile_id = %(profile_id)s
         ORDER BY allocation.expense_id, allocation.category_id
        """,
    ),
    _Projection(
        "payment_allocation",
        """
        SELECT allocation.expense_id, allocation.funding_source_id,
               allocation.amount_minor, allocation.currency
          FROM finance.payment_allocations AS allocation
          JOIN finance.expenses AS expense ON expense.id = allocation.expense_id
         WHERE expense.profile_id = %(profile_id)s
         ORDER BY allocation.expense_id, allocation.funding_source_id
        """,
    ),
    _Projection(
        "wallet_entry",
        """
        SELECT id, profile_id, funding_source_id, entry_type, amount_minor,
               currency, occurred_at, expires_on, expense_id, note, created_at
          FROM finance.wallet_entries
         WHERE profile_id = %(profile_id)s
         ORDER BY occurred_at, id
        """,
    ),
    _Projection(
        "monthly_budget",
        """
        SELECT id, profile_id, category_id, target_minor, currency, version,
               created_at, updated_at
          FROM finance.monthly_budgets
         WHERE profile_id = %(profile_id)s
         ORDER BY id
        """,
    ),
    _Projection(
        "monthly_budget_override",
        """
        SELECT override.budget_id, override.year, override.month,
               override.target_minor, override.currency
          FROM finance.monthly_budget_overrides AS override
          JOIN finance.monthly_budgets AS budget ON budget.id = override.budget_id
         WHERE budget.profile_id = %(profile_id)s
         ORDER BY override.budget_id, override.year, override.month
        """,
    ),
    _Projection(
        "category_proposal",
        """
        SELECT id, profile_id, proposed_name, description, parent_id, rationale,
               alternative_category_ids, excluded_names, source_reference,
               status, created_at, updated_at
          FROM finance.category_proposals
         WHERE profile_id = %(profile_id)s
         ORDER BY created_at, id
        """,
    ),
    _Projection(
        "audit_event",
        """
        SELECT id, profile_id, aggregate_type, aggregate_id, action, actor,
               source_reference, occurred_at, prior_version, current_version,
               reason
          FROM finance.audit_events
         WHERE profile_id = %(profile_id)s
         ORDER BY occurred_at, id
        """,
    ),
)


class PostgresFinanceExporter:
    """Read versioned finance projections from application PostgreSQL."""

    def __init__(
        self,
        database_url: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind the exporter to one application database.

        Args:
            database_url: Credential-bearing PostgreSQL URL kept out of output
                and diagnostics.
            clock: Aware instant source for the manifest.

        Raises:
            ValueError: If the URL is not PostgreSQL.
        """

        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("finance exporter requires a PostgreSQL URL")
        self._database_url = database_url
        self._clock = clock or (lambda: datetime.now(UTC))

    def export(
        self,
        profile_id: UUID,
        destination: Path,
        *,
        forbidden_root: Path,
    ) -> FinanceExportResult:
        """Export one profile to a new private JSON Lines file.

        Args:
            profile_id: Stable profile selected by the trusted operator.
            destination: Absolute path to a file that does not exist.
            forbidden_root: Checkout root the export must remain outside.

        Returns:
            Safe counts and integrity metadata for the completed file.

        Raises:
            FinanceExportError: If the destination, schema, profile, database,
                or write cannot be used safely.
        """

        _require_private_destination(destination, forbidden_root=forbidden_root)
        exported_at = self._clock()
        if exported_at.tzinfo is None or exported_at.utcoffset() is None:
            raise FinanceExportError("export_clock_naive")
        try:
            revision, records = self._read_snapshot(profile_id)
        except FinanceExportError:
            raise
        except Exception:
            _LOGGER.exception(
                "finance_export.database.failed",
                profile_id=profile_id,
            )
            raise FinanceExportError("database_failed", transient=True) from None
        counts = {
            projection.record_type: len(records[projection.record_type])
            for projection in _PROJECTIONS
        }
        if counts["profile"] != 1:
            raise FinanceExportError("profile_not_found")
        manifest = {
            "type": "manifest",
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "database_revision": revision,
            "exported_at": _json_value(exported_at),
            "profile_id": str(profile_id),
            "record_counts": counts,
        }
        descriptor = _open_private_destination(destination)
        digest = hashlib.sha256()
        byte_count = 0
        record_count = sum(counts.values())
        try:
            with os.fdopen(descriptor, "wb") as handle:
                byte_count += _write_line(handle, digest, manifest)
                for projection in _PROJECTIONS:
                    for record in records[projection.record_type]:
                        byte_count += _write_line(
                            handle,
                            digest,
                            {
                                "type": projection.record_type,
                                "data": _json_value(record),
                            },
                        )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            try:
                destination.unlink(missing_ok=True)
            except OSError as cleanup_error:
                _LOGGER.error(
                    "finance_export.partial_cleanup.failed",
                    profile_id=profile_id,
                    error_type=type(cleanup_error).__name__,
                    errno=cleanup_error.errno,
                )
                raise FinanceExportError("export_cleanup_failed") from None
            _LOGGER.error(
                "finance_export.write.failed",
                profile_id=profile_id,
                error_type=type(error).__name__,
                errno=error.errno,
            )
            raise FinanceExportError("export_write_failed", transient=True) from None
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        _LOGGER.info(
            "finance_export.completed",
            profile_id=profile_id,
            format_version=FORMAT_VERSION,
            record_count=record_count,
            byte_count=byte_count,
        )
        return FinanceExportResult(
            profile_id=profile_id,
            record_count=record_count,
            byte_count=byte_count,
            sha256=f"sha256:{digest.hexdigest()}",
            record_counts=counts,
        )

    def _read_snapshot(
        self, profile_id: UUID
    ) -> tuple[str, dict[str, list[dict[str, object]]]]:
        """Read all version-one projections in one repeatable-read transaction."""

        with connect(self._database_url, row_factory=dict_row) as connection:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            revision_row = connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone()
            if revision_row is None:
                raise FinanceExportError("database_schema_missing")
            revision = str(revision_row["version_num"])
            if revision != SUPPORTED_DATABASE_REVISION:
                raise FinanceExportError("database_schema_unsupported")
            records = {
                projection.record_type: list(
                    connection.execute(
                        projection.query,
                        {"profile_id": profile_id},
                    ).fetchall()
                )
                for projection in _PROJECTIONS
            }
        return revision, records


def _require_private_destination(destination: Path, *, forbidden_root: Path) -> None:
    """Validate a new owner-only destination outside the checkout."""

    if not destination.is_absolute():
        raise FinanceExportError("export_path_not_absolute")
    if destination.exists() or destination.is_symlink():
        raise FinanceExportError("export_path_exists")
    try:
        resolved_parent = destination.parent.resolve(strict=True)
    except OSError:
        raise FinanceExportError("export_parent_missing") from None
    if not resolved_parent.is_dir():
        raise FinanceExportError("export_parent_not_directory")
    root = forbidden_root.resolve()
    if resolved_parent == root or root in resolved_parent.parents:
        raise FinanceExportError("export_inside_repository")
    status = resolved_parent.stat()
    if status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise FinanceExportError("export_parent_not_private")


def _open_private_destination(destination: Path) -> int:
    """Open a new mode-600 destination without following a final symlink."""

    try:
        return os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        raise FinanceExportError("export_path_exists") from None
    except OSError:
        raise FinanceExportError("export_open_failed") from None


def _write_line(
    handle: BinaryIO,
    digest: _Digest,
    value: Mapping[str, object],
) -> int:
    """Write one canonical JSON line and update its digest."""

    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    handle.write(encoded)
    digest.update(encoded)
    return len(encoded)


def _json_value(value: object) -> object:
    """Convert PostgreSQL values recursively into portable JSON values."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise FinanceExportError("database_timestamp_naive")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise FinanceExportError("database_value_unsupported")


__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "FinanceExportError",
    "FinanceExportResult",
    "PostgresFinanceExporter",
]
