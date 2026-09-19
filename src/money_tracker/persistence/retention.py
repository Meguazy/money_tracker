"""Age-based deletion of concluded runtime owner content in application PostgreSQL.

The messaging queues and workflow checkpoints hold owner text, such as the
original Telegram message, the rendered reply, and graph state, for as long as
their rows exist. None of it is authoritative: finance records, audit history,
and command idempotency live elsewhere and are never touched here. The owner
decided on 2026-09-19 that concluded runtime content is deleted, not redacted,
30 days after it concluded, which is the same window as runtime traces.

Retention deletes only what can no longer be acted on:

- consumed or cancelled pending actions;
- sent outbox messages that no remaining pending action refers to;
- completed inbox items whose every response is either deleted or still
  carrying an active control, which is detached from the item it answered;
- checkpoint threads whose source work unit is terminal, or whose category
  review is no longer pending.

Active controls, paused reviews, dead letters, undelivered responses, and
threads of a family this module does not recognise are always kept. A dry run
executes the same statements in the same transaction and rolls it back, so the
reported plan is exactly what a confirmed run would delete at that instant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from psycopg import Connection, DataError, Error, OperationalError, connect, errors

from money_tracker.persistence.schema import APPLICATION_SCHEMA_REVISION
from money_tracker.structured_logging import get_logger
from money_tracker.workflows.threads import (
    SOURCE_KEYED_NAMESPACES,
    THREAD_SEPARATOR,
    ThreadNamespace,
)

_LOGGER = get_logger(__name__)

RETENTION_DAYS: Final = 30
"""Owner-decided retention of concluded runtime content (2026-09-19)."""

MINIMUM_RETENTION: Final = timedelta(days=1)
"""Shortest accepted window, which keeps Telegram's 24-hour backlog intact."""

LOCK_TIMEOUT: Final = "5s"
"""Longest wait for a row another process holds before the run gives up."""

_MESSAGING_TABLES: Final = (
    "messaging.telegram_pending_actions",
    "messaging.telegram_outbox",
    "messaging.telegram_inbox",
)
_CHECKPOINT_TABLES: Final = (
    "workflow.checkpoint_writes",
    "workflow.checkpoint_blobs",
    "workflow.checkpoints",
)


class RuntimeRetentionError(RuntimeError):
    """A retention run was refused or failed without deleting anything.

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


@dataclass(frozen=True, slots=True)
class DeletedRuntimeRows:
    """Rows one run deleted, or would delete in a dry run.

    Attributes:
        pending_actions: Consumed or cancelled Telegram control correlations.
        outbox_messages: Sent Telegram responses and control revocations.
        inbox_items: Completed inbound Telegram updates.
        detached_outbox_messages: Retained responses carrying an active
            control whose link to a deleted inbound item was cleared.
        checkpoint_threads: Workflow threads removed whole.
        checkpoints: Checkpoint rows of those threads.
        checkpoint_blobs: Serialized channel values of those threads.
        checkpoint_writes: Pending task writes of those threads.
    """

    pending_actions: int
    outbox_messages: int
    inbox_items: int
    detached_outbox_messages: int
    checkpoint_threads: int
    checkpoints: int
    checkpoint_blobs: int
    checkpoint_writes: int

    @property
    def total(self) -> int:
        """Return how many rows the run removed, excluding detached links."""

        return (
            self.pending_actions
            + self.outbox_messages
            + self.inbox_items
            + self.checkpoints
            + self.checkpoint_blobs
            + self.checkpoint_writes
        )


@dataclass(frozen=True, slots=True)
class RetainedRuntimeRows:
    """Runtime rows older than the cutoff that the policy deliberately kept.

    Each count is taken after the deletion, in the same transaction, so it is
    the owner content that will outlive the window and needs an explanation:
    a dead letter, an undelivered response, an active control, a paused
    review, or a thread of an unrecognised family.

    Attributes:
        inbox_items: Inbound updates accepted before the cutoff.
        outbox_messages: Responses created before the cutoff.
        pending_actions: Control correlations created before the cutoff.
        checkpoint_threads: Threads whose newest checkpoint precedes it.
    """

    inbox_items: int
    outbox_messages: int
    pending_actions: int
    checkpoint_threads: int


@dataclass(frozen=True, slots=True)
class RuntimeRetentionResult:
    """Content-free outcome of one retention run.

    Attributes:
        cutoff: Instant before which concluded content was selected.
        dry_run: Whether the transaction was rolled back.
        deleted: Rows removed, or that would be removed in a dry run.
        retained: Rows older than the cutoff that the policy kept.
        checkpoint_store_present: Whether LangGraph checkpoint tables exist.
        vacuumed: Whether the affected tables were vacuumed after commit.
    """

    cutoff: datetime
    dry_run: bool
    deleted: DeletedRuntimeRows
    retained: RetainedRuntimeRows
    checkpoint_store_present: bool
    vacuumed: bool


_DELETE_PENDING_ACTIONS: Final = """
    DELETE FROM messaging.telegram_pending_actions
     WHERE status IN ('consumed', 'cancelled')
       AND coalesce(consumed_at, created_at) < %(cutoff)s
"""

# A pending action refers to the message that offered it. Once every such
# action is gone, a delivered message is only history.
_DELETE_OUTBOX: Final = """
    DELETE FROM messaging.telegram_outbox AS outbox
     WHERE outbox.status = 'sent'
       AND outbox.sent_at < %(cutoff)s
       AND NOT EXISTS (
           SELECT 1 FROM messaging.telegram_pending_actions AS action
            WHERE action.outbox_id = outbox.id
       )
"""

# An inbound item may go once nothing it produced still needs it. A response
# kept for an active control does not: it is kept for its Telegram message
# identifier, never for the text that prompted it, so its link is cleared.
# Anything undelivered, retrying, or dead-lettered keeps the item whole.
_DETACH_OUTBOX: Final = """
    UPDATE messaging.telegram_outbox AS outbox
       SET inbox_id = NULL
      FROM messaging.telegram_inbox AS inbox
     WHERE outbox.inbox_id = inbox.id
       AND inbox.status = 'completed'
       AND inbox.completed_at < %(cutoff)s
       AND NOT EXISTS (
           SELECT 1 FROM messaging.telegram_outbox AS blocking
            WHERE blocking.inbox_id = inbox.id
              AND NOT (
                  blocking.status = 'sent'
                  AND EXISTS (
                      SELECT 1 FROM messaging.telegram_pending_actions AS action
                       WHERE action.outbox_id = blocking.id
                         AND action.status = 'active'
                  )
              )
       )
"""

_DELETE_INBOX: Final = """
    DELETE FROM messaging.telegram_inbox AS inbox
     WHERE inbox.status = 'completed'
       AND inbox.completed_at < %(cutoff)s
       AND NOT EXISTS (
           SELECT 1 FROM messaging.telegram_outbox AS outbox
            WHERE outbox.inbox_id = inbox.id
       )
"""

# `ts` is written by LangGraph into every checkpoint. A source-keyed thread can
# only be continued by a retry of its work unit, so it is terminal once no
# unfinished inbox item carries its reference; a thread without any inbox item
# came from a local command and has nothing that could resume it. A review is
# terminal once its proposal is no longer pending. Any other family is kept.
_EXPIRED_THREADS: Final = """
    WITH threads AS (
        SELECT thread_id,
               split_part(thread_id, %(separator)s, 1) AS namespace,
               substr(thread_id, strpos(thread_id, %(separator)s) + 1) AS reference,
               max((checkpoint ->> 'ts')::timestamptz) AS last_checkpoint_at
          FROM workflow.checkpoints
         GROUP BY thread_id
    )
    SELECT thread_id FROM threads
     WHERE last_checkpoint_at < %(cutoff)s
       AND strpos(thread_id, %(separator)s) > 0
       AND (
           (namespace = ANY(%(source_keyed)s) AND NOT EXISTS (
               SELECT 1 FROM messaging.telegram_inbox AS inbox
                WHERE inbox.source_reference = threads.reference
                  AND inbox.status <> 'completed'
           ))
           OR (namespace = %(review)s AND NOT EXISTS (
               SELECT 1 FROM finance.category_proposals AS proposal
                WHERE proposal.id::text = threads.reference
                  AND proposal.status = 'pending'
           ))
       )
     ORDER BY thread_id
"""

_RETAINED: Final = """
    SELECT
        (SELECT count(*) FROM messaging.telegram_inbox
          WHERE accepted_at < %(cutoff)s),
        (SELECT count(*) FROM messaging.telegram_outbox
          WHERE created_at < %(cutoff)s),
        (SELECT count(*) FROM messaging.telegram_pending_actions
          WHERE created_at < %(cutoff)s)
"""

_RETAINED_THREADS: Final = """
    SELECT count(*) FROM (
        SELECT thread_id FROM workflow.checkpoints
         GROUP BY thread_id
        HAVING max((checkpoint ->> 'ts')::timestamptz) < %(cutoff)s
    ) AS old_threads
"""


def _utc_now() -> datetime:
    """Return the current aware instant."""

    return datetime.now(UTC)


def _count(value: object) -> int:
    """Return one non-negative aggregate PostgreSQL produced."""

    if not isinstance(value, int) or value < 0:
        raise RuntimeRetentionError("database_count_invalid")
    return value


def _facts(result: RuntimeRetentionResult) -> dict[str, object]:
    """Flatten a result into content-free journal fields."""

    return {
        "pending_actions": result.deleted.pending_actions,
        "outbox_messages": result.deleted.outbox_messages,
        "inbox_items": result.deleted.inbox_items,
        "detached_outbox_messages": result.deleted.detached_outbox_messages,
        "checkpoint_threads": result.deleted.checkpoint_threads,
        "deleted_rows": result.deleted.total,
        "retained_inbox_items": result.retained.inbox_items,
        "retained_outbox_messages": result.retained.outbox_messages,
        "retained_pending_actions": result.retained.pending_actions,
        "retained_checkpoint_threads": result.retained.checkpoint_threads,
        "checkpoint_store_present": result.checkpoint_store_present,
        "vacuumed": result.vacuumed,
    }


class PostgresRuntimeRetention:
    """Delete concluded messaging and checkpoint content older than a window."""

    def __init__(
        self,
        database_url: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Bind retention to one application database.

        Args:
            database_url: Credential-bearing PostgreSQL URL; never logged.
            clock: Source of the current aware instant the window counts from.

        Raises:
            ValueError: If the URL is not a PostgreSQL URL.
        """

        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("runtime retention requires a PostgreSQL URL")
        self._database_url = database_url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        self._clock = clock

    def run(self, *, retention: timedelta, confirm: bool) -> RuntimeRetentionResult:
        """Select, and optionally delete, concluded content older than a window.

        Args:
            retention: How long concluded content is kept; at least one day.
            confirm: Commit the deletion when true; roll it back otherwise.

        Returns:
            Content-free counts of what was deleted and what was retained.

        Raises:
            RuntimeRetentionError: If the window, clock, schema, or database
                refuses the run. Nothing is deleted in that case.
        """

        if retention < MINIMUM_RETENTION:
            raise RuntimeRetentionError("retention_too_short")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeRetentionError("clock_not_aware")
        cutoff = now.astimezone(UTC) - retention
        with _LOGGER.timed("runtime_retention.run", dry_run=not confirm) as outcome:
            result = self._transaction(cutoff, confirm=confirm)
            if confirm and result.deleted.total:
                result = replace(result, vacuumed=self._vacuum(result))
            outcome.update(_facts(result))
        return result

    def _transaction(
        self, cutoff: datetime, *, confirm: bool
    ) -> RuntimeRetentionResult:
        """Run the deletions in one transaction, then commit or roll back.

        Args:
            cutoff: Instant before which concluded content is selected.
            confirm: Commit when true; roll back for a dry run.

        Returns:
            Counts observed inside the transaction.

        Raises:
            RuntimeRetentionError: With a stable reason for every database
                failure. The transaction is rolled back and nothing changes.
        """

        try:
            with connect(self._database_url) as connection:
                # A held row means the gateway or worker is using it; waiting
                # would delay them, and the next daily run retries anyway.
                connection.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                self._require_schema(connection)
                result = self._delete(connection, cutoff, dry_run=not confirm)
                if confirm:
                    connection.commit()
                else:
                    connection.rollback()
                return result
        except errors.LockNotAvailable as error:
            raise RuntimeRetentionError("database_busy", transient=True) from error
        except errors.UndefinedTable as error:
            raise RuntimeRetentionError("database_schema_incompatible") from error
        except OperationalError as error:
            raise RuntimeRetentionError(
                "database_unavailable", transient=True
            ) from error
        except DataError as error:
            raise RuntimeRetentionError("checkpoint_data_invalid") from error
        except Error as error:
            raise RuntimeRetentionError("database_operation_failed") from error

    @staticmethod
    def _require_schema(connection: Connection[tuple[object, ...]]) -> None:
        """Refuse a database whose tables these statements were not written for."""

        row = connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()
        if row is None or row[0] != APPLICATION_SCHEMA_REVISION:
            raise RuntimeRetentionError("database_schema_incompatible")

    def _delete(
        self,
        connection: Connection[tuple[object, ...]],
        cutoff: datetime,
        *,
        dry_run: bool,
    ) -> RuntimeRetentionResult:
        """Run every deletion in dependency order inside the open transaction."""

        parameters = {"cutoff": cutoff}
        pending_actions = connection.execute(
            _DELETE_PENDING_ACTIONS, parameters
        ).rowcount
        outbox_messages = connection.execute(_DELETE_OUTBOX, parameters).rowcount
        detached = connection.execute(_DETACH_OUTBOX, parameters).rowcount
        inbox_items = connection.execute(_DELETE_INBOX, parameters).rowcount
        store_present = self._checkpoint_store_present(connection)
        threads: list[str] = []
        checkpoint_rows = (0, 0, 0)
        if store_present:
            threads = [
                str(row[0])
                for row in connection.execute(
                    _EXPIRED_THREADS,
                    {
                        **parameters,
                        "separator": THREAD_SEPARATOR,
                        "source_keyed": sorted(
                            namespace.value for namespace in SOURCE_KEYED_NAMESPACES
                        ),
                        "review": ThreadNamespace.CATEGORY_REVIEW.value,
                    },
                ).fetchall()
            ]
            checkpoint_rows = self._delete_threads(connection, threads)
        retained = connection.execute(_RETAINED, parameters).fetchone()
        retained_threads = (
            connection.execute(_RETAINED_THREADS, parameters).fetchone()
            if store_present
            else (0,)
        )
        if retained is None or retained_threads is None:
            raise RuntimeRetentionError("database_result_missing")
        return RuntimeRetentionResult(
            cutoff=cutoff,
            dry_run=dry_run,
            deleted=DeletedRuntimeRows(
                pending_actions=pending_actions,
                outbox_messages=outbox_messages,
                inbox_items=inbox_items,
                detached_outbox_messages=detached,
                checkpoint_threads=len(threads),
                checkpoints=checkpoint_rows[0],
                checkpoint_blobs=checkpoint_rows[1],
                checkpoint_writes=checkpoint_rows[2],
            ),
            retained=RetainedRuntimeRows(
                inbox_items=_count(retained[0]),
                outbox_messages=_count(retained[1]),
                pending_actions=_count(retained[2]),
                checkpoint_threads=_count(retained_threads[0]),
            ),
            checkpoint_store_present=store_present,
            vacuumed=False,
        )

    @staticmethod
    def _checkpoint_store_present(connection: Connection[tuple[object, ...]]) -> bool:
        """Report whether checkpoint bootstrap has created its tables."""

        row = connection.execute(
            "SELECT to_regclass('workflow.checkpoints') IS NOT NULL"
        ).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _delete_threads(
        connection: Connection[tuple[object, ...]], threads: list[str]
    ) -> tuple[int, int, int]:
        """Remove whole threads the way the LangGraph saver's own delete does."""

        if not threads:
            return 0, 0, 0
        checkpoints = connection.execute(
            "DELETE FROM workflow.checkpoints WHERE thread_id = ANY(%s)", (threads,)
        ).rowcount
        blobs = connection.execute(
            "DELETE FROM workflow.checkpoint_blobs WHERE thread_id = ANY(%s)",
            (threads,),
        ).rowcount
        writes = connection.execute(
            "DELETE FROM workflow.checkpoint_writes WHERE thread_id = ANY(%s)",
            (threads,),
        ).rowcount
        return checkpoints, blobs, writes

    def _vacuum(self, result: RuntimeRetentionResult) -> bool:
        """Reclaim deleted tuples promptly instead of waiting for autovacuum.

        Deletion has already committed, so a failure here is a degraded outcome
        rather than a failed run: autovacuum reclaims the space later.

        Args:
            result: Committed outcome deciding whether checkpoint tables exist.

        Returns:
            Whether every affected table was vacuumed.
        """

        tables = _MESSAGING_TABLES + (
            _CHECKPOINT_TABLES if result.checkpoint_store_present else ()
        )
        try:
            with connect(self._database_url, autocommit=True) as connection:
                for table in tables:
                    connection.execute(f"VACUUM (ANALYZE) {table}")
        except Error as error:
            _LOGGER.warning(
                "runtime_retention.vacuum.failed",
                reason_code="vacuum_failed",
                error_type=type(error).__name__,
                sqlstate=error.sqlstate,
            )
            return False
        return True


__all__ = [
    "LOCK_TIMEOUT",
    "MINIMUM_RETENTION",
    "RETENTION_DAYS",
    "DeletedRuntimeRows",
    "PostgresRuntimeRetention",
    "RetainedRuntimeRows",
    "RuntimeRetentionError",
    "RuntimeRetentionResult",
]
