"""PostgreSQL adapter for durable Telegram messaging state."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import Connection, connect
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from money_tracker.messaging.contracts import (
    BotEnvironment,
    ControlRevocationDraft,
    DeadLetter,
    InboxClaim,
    InboxStatus,
    MessagingHealth,
    MessagingQueue,
    NormalizedTelegramUpdate,
    OutboundButton,
    OutboundWork,
    OutboxClaim,
    OutboxDeadLetter,
    OutboxMessageDraft,
    OutboxStatus,
    OwnerBinding,
    PendingAction,
    PendingActionDraft,
    PendingActionStatus,
    PendingActionTarget,
    RequeueOutcome,
    WorkflowResult,
    require_aware,
    validate_bot_key,
)
from money_tracker.messaging.errors import (
    MessagingConflictError,
    MessagingLeaseError,
)
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.persistence.messaging")

MAX_AUTOMATIC_ATTEMPTS = 5
OUTBOX_SEND_MESSAGE = "send_message"
OUTBOX_REVOKE_CONTROLS = "revoke_controls"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PostgresMessagingRepository:
    """Own durable Telegram offsets, queues, pairing, and correlations.

    The adapter deliberately exposes normalized records rather than Bot API
    payloads. Each mutating method opens one PostgreSQL transaction so callers
    cannot accidentally split the transaction boundaries accepted in AD-018.
    """

    def __init__(self, database_url: str) -> None:
        """Bind the adapter without exposing its credential in representations.

        Args:
            database_url: Dedicated PostgreSQL application database URL.

        Raises:
            ValueError: If the URL is not a PostgreSQL URL.
        """

        if not database_url.startswith("postgresql://"):
            raise ValueError("messaging repository requires a PostgreSQL URL")
        self._database_url = database_url

    def ensure_bot(
        self,
        bot_key: str,
        *,
        environment: BotEnvironment,
        now: datetime,
    ) -> None:
        """Create offset ownership or validate the existing environment."""

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO messaging.telegram_offsets "
                "(bot_key, environment, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (bot_key) DO NOTHING "
                "RETURNING environment",
                (bot_key, environment.value, now, now),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT environment FROM messaging.telegram_offsets "
                    "WHERE bot_key = %s",
                    (bot_key,),
                ).fetchone()
                if row is None or row["environment"] != environment.value:
                    raise MessagingConflictError(
                        "bot key belongs to a different environment"
                    )

    def polling_offset(self, bot_key: str) -> int:
        """Return the gateway-owned next Bot API offset."""

        validate_bot_key(bot_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT next_offset FROM messaging.telegram_offsets WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
        if row is None:
            raise LookupError("bot key is not initialized")
        return cast(int, row["next_offset"])

    def commit_ingest_batch(
        self,
        *,
        bot_key: str,
        expected_offset: int,
        observed_update_ids: tuple[int, ...],
        accepted_updates: tuple[NormalizedTelegramUpdate, ...],
        rejected_count: int,
        unsupported_count: int,
        now: datetime,
    ) -> int:
        """Atomically retain normalized work and acknowledge observed updates.

        Exact stale redeliveries are harmless. Any overlapping or divergent
        offset expectation is rejected so two gateway instances cannot silently
        transfer acknowledgement ownership.
        """

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        if expected_offset < 0 or rejected_count < 0 or unsupported_count < 0:
            raise ValueError("ingest counters and offset cannot be negative")
        if not observed_update_ids:
            raise ValueError("an ingest batch must contain observed updates")
        if tuple(sorted(set(observed_update_ids))) != observed_update_ids:
            raise ValueError("observed update identifiers must be unique and ordered")
        if len(accepted_updates) + rejected_count + unsupported_count != len(
            observed_update_ids
        ):
            raise ValueError("ingest disposition counts do not cover the batch")
        observed = set(observed_update_ids)
        for update in accepted_updates:
            if update.bot_key != bot_key or update.update_id not in observed:
                raise ValueError("accepted update is outside the ingest batch scope")

        next_offset = observed_update_ids[-1] + 1
        with self._connect() as connection:
            offset = self._lock_offset(connection, bot_key)
            current = cast(int, offset["next_offset"])
            if current != expected_offset:
                if current >= next_offset:
                    return current
                # Another gateway advanced the offset underneath this batch,
                # which means two gateways are polling the same bot.
                _LOGGER.error(
                    "messaging.offset_conflict",
                    bot_key=bot_key,
                    expected_offset=expected_offset,
                    stored_offset=current,
                )
                raise MessagingConflictError("persisted Telegram offset changed")
            for update in accepted_updates:
                self._insert_or_verify_inbox(connection, update)
            connection.execute(
                "UPDATE messaging.telegram_offsets SET next_offset = %s, "
                "last_observed_at = %s, last_poll_at = %s, "
                "rejected_update_count = rejected_update_count + %s, "
                "unsupported_update_count = unsupported_update_count + %s, "
                "updated_at = %s WHERE bot_key = %s",
                (
                    next_offset,
                    now,
                    now,
                    rejected_count,
                    unsupported_count,
                    now,
                    bot_key,
                ),
            )
        return next_offset

    def record_empty_poll(self, bot_key: str, *, now: datetime) -> None:
        """Record transport liveness without moving the durable offset."""

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE messaging.telegram_offsets SET last_poll_at = %s, "
                "updated_at = %s WHERE bot_key = %s",
                (now, now, bot_key),
            )
            if result.rowcount != 1:
                raise LookupError("bot key is not initialized")

    def claim_next_inbox(
        self,
        bot_key: str,
        *,
        now: datetime,
        lease: timedelta,
        claim_token: UUID,
    ) -> InboxClaim | None:
        """Lease the oldest non-terminal inbox item when it is eligible."""

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        if lease <= timedelta(0):
            raise ValueError("inbox lease must be positive")
        with self._connect() as connection:
            self._lock_offset(connection, bot_key)
            row = connection.execute(
                "SELECT * FROM messaging.telegram_inbox "
                "WHERE bot_key = %s AND status NOT IN ('completed', 'dead_letter') "
                "ORDER BY update_id FOR UPDATE LIMIT 1",
                (bot_key,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["status"] == InboxStatus.PROCESSING.value
                and row["claimed_until"] > now
            ):
                return None
            if (
                row["status"] == InboxStatus.RETRY.value
                and row["next_attempt_at"] > now
            ):
                return None
            claimed_until = now + lease
            connection.execute(
                "UPDATE messaging.telegram_inbox SET status = 'processing', "
                "attempt_count = attempt_count + 1, claim_token = %s, "
                "claimed_until = %s, last_error_code = NULL WHERE id = %s",
                (claim_token, claimed_until, row["id"]),
            )
            row["status"] = InboxStatus.PROCESSING.value
            row["attempt_count"] += 1
            row["claim_token"] = claim_token
            row["claimed_until"] = claimed_until
        return self._inbox_claim(row)

    def fail_inbox(
        self,
        claim: InboxClaim,
        *,
        error_code: str,
        transient: bool,
        retry_at: datetime | None,
        response: OutboxMessageDraft,
        now: datetime,
    ) -> None:
        """Commit a bounded retry or dead letter with its owner notice."""

        self._validate_failure(error_code, retry_at=retry_at, now=now)
        retry = transient and claim.attempt_count < MAX_AUTOMATIC_ATTEMPTS
        if retry and retry_at is None:
            raise ValueError("transient failure requires a retry timestamp")
        status = InboxStatus.RETRY if retry else InboxStatus.DEAD_LETTER
        next_attempt = retry_at if retry else now
        if response.bot_key != claim.update.bot_key:
            raise ValueError("failure response crosses the inbox bot scope")
        if response.telegram_chat_id != claim.update.telegram_chat_id:
            raise ValueError("failure response crosses the inbox chat scope")
        if response.inbox_id != claim.update.id:
            raise ValueError("failure response must target its inbox item")
        with self._connect() as connection:
            self._require_inbox_lease(connection, claim)
            self._insert_or_verify_outbox(connection, response, now)
            result = connection.execute(
                "UPDATE messaging.telegram_inbox SET status = %s, "
                "next_attempt_at = %s, claim_token = NULL, claimed_until = NULL, "
                "last_error_code = %s WHERE id = %s AND status = 'processing' "
                "AND claim_token = %s",
                (
                    status.value,
                    next_attempt,
                    error_code,
                    claim.update.id,
                    claim.claim_token,
                ),
            )
            if result.rowcount != 1:
                _LOGGER.warning(
                    "messaging.lease_expired",
                    bot_key=claim.update.bot_key,
                    inbox_id=claim.update.id,
                    operation="fail_inbox",
                )
                raise MessagingLeaseError("inbox lease is no longer current")

    def complete_inbox(
        self,
        claim: InboxClaim,
        *,
        workflow_status: str,
        workflow_result: WorkflowResult,
        response: OutboxMessageDraft,
        pending_action: PendingActionDraft | None,
        consumed_action_id: UUID | None,
        now: datetime,
        revocations: tuple[ControlRevocationDraft, ...] = (),
    ) -> UUID:
        """Commit result, logical response, correlation, and completion together.

        Revocations ride the same transaction because they exist only as a
        consequence of this response: a new summary is what makes the controls
        on an older one wrong. Committing them separately would allow a summary
        the owner can act on twice.
        """

        require_aware(now, field="now")
        if not workflow_status.strip():
            raise ValueError("workflow status cannot be blank")
        if response.bot_key != claim.update.bot_key:
            raise ValueError("outbox response crosses the inbox bot scope")
        for revocation in revocations:
            if revocation.bot_key != claim.update.bot_key:
                raise ValueError("revocation crosses the inbox bot scope")
            if revocation.telegram_chat_id != response.telegram_chat_id:
                raise ValueError("revocation crosses the response chat scope")
        if response.inbox_id not in {None, claim.update.id}:
            raise ValueError("outbox response targets another inbox item")
        if pending_action is not None and (
            pending_action.bot_key != response.bot_key
            or pending_action.outbox_id != response.id
            or pending_action.telegram_chat_id != response.telegram_chat_id
        ):
            raise ValueError("pending action crosses the response scope")
        with self._connect() as connection:
            self._require_inbox_lease(connection, claim)
            if consumed_action_id is not None:
                result = connection.execute(
                    "UPDATE messaging.telegram_pending_actions SET "
                    "status = 'consumed', consumed_at = %s WHERE id = %s "
                    "AND bot_key = %s AND telegram_user_id = %s "
                    "AND telegram_chat_id = %s AND status = 'active'",
                    (
                        now,
                        consumed_action_id,
                        claim.update.bot_key,
                        claim.update.telegram_user_id,
                        claim.update.telegram_chat_id,
                    ),
                )
                if result.rowcount != 1:
                    raise MessagingConflictError("pending action is not active")
            outbox_id = self._insert_or_verify_outbox(connection, response, now)
            for revocation in revocations:
                self._insert_or_verify_revocation(connection, revocation, now)
            if pending_action is not None:
                self._insert_or_verify_action(connection, pending_action, now)
            result = connection.execute(
                "UPDATE messaging.telegram_inbox SET status = 'completed', "
                "workflow_status = %s, workflow_result = %s, completed_at = %s, "
                "claim_token = NULL, claimed_until = NULL, last_error_code = NULL "
                "WHERE id = %s AND status = 'processing' AND claim_token = %s",
                (
                    workflow_status,
                    Jsonb(dict(workflow_result)),
                    now,
                    claim.update.id,
                    claim.claim_token,
                ),
            )
            if result.rowcount != 1:
                _LOGGER.warning(
                    "messaging.lease_expired",
                    bot_key=claim.update.bot_key,
                    inbox_id=claim.update.id,
                    operation="complete_inbox",
                )
                raise MessagingLeaseError("inbox lease is no longer current")
        return outbox_id

    def replay_inbox(self, bot_key: str, inbox_id: UUID, *, now: datetime) -> None:
        """Replay one dead letter only inside the selected bot scope."""

        self.requeue_inbox(
            bot_key,
            inbox_id,
            expected_replay_count=None,
            now=now,
        )

    def requeue_inbox(
        self,
        bot_key: str,
        inbox_id: UUID,
        *,
        expected_replay_count: int | None,
        now: datetime,
    ) -> RequeueOutcome:
        """Return one dead letter to the queue under the state it was chosen in.

        Pinning `expected_replay_count` makes the command the transition rather
        than the row. Without it a stale view can requeue an item a second time
        after the first requeue already failed again, which is a different
        decision than the one the caller made.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            inbox_id: Dead letter the operator chose to retry.
            expected_replay_count: Replay count the caller observed, or None to
                accept any dead letter, which is what the unattended operator
                command does.
            now: Aware instant the item becomes eligible again.

        Returns:
            Whether this call requeued the item or found its transition done.

        Raises:
            MessagingConflictError: If no dead letter in this bot scope is at
                the observed state. A foreign, missing, or already progressed
                item is deliberately indistinguishable.
        """

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        with self._connect() as connection:
            observed = connection.execute(
                "SELECT status, replay_count FROM messaging.telegram_inbox "
                "WHERE id = %s AND bot_key = %s FOR UPDATE",
                (inbox_id, bot_key),
            ).fetchone()
            outcome = self._requeue_outcome(
                observed,
                bot_key=bot_key,
                queue=MessagingQueue.INBOX,
                item_id=inbox_id,
                expected_replay_count=expected_replay_count,
            )
            if outcome is RequeueOutcome.APPLIED:
                connection.execute(
                    "UPDATE messaging.telegram_inbox SET status = 'pending', "
                    "attempt_count = 0, replay_count = replay_count + 1, "
                    "next_attempt_at = %s, last_error_code = NULL "
                    "WHERE id = %s AND bot_key = %s",
                    (now, inbox_id, bot_key),
                )
            return outcome

    @staticmethod
    def _requeue_outcome(
        observed: DictRow | None,
        *,
        bot_key: str,
        queue: MessagingQueue,
        item_id: UUID,
        expected_replay_count: int | None,
    ) -> RequeueOutcome:
        """Decide what a pinned requeue of one locked row may do.

        Both queues answer this the same way, and both refuse for the same
        reasons, so the refusal is recorded once here rather than by each
        caller. The record carries the observed state and counts but never the
        message the row belongs to.

        Args:
            observed: Locked status and replay count, or None when no row in
                this bot scope has that identifier.
            bot_key: Bot and environment persistence scope.
            queue: Which durable queue the item belongs to.
            item_id: Identifier the caller named.
            expected_replay_count: Replay count the caller observed, or None.

        Returns:
            Whether the caller may apply the requeue or already has.

        Raises:
            MessagingConflictError: If the observed state allows neither.
        """

        if observed is not None:
            status = cast(str, observed["status"])
            replay_count = cast(int, observed["replay_count"])
            pinned = expected_replay_count is not None
            if status == "dead_letter" and (
                not pinned or replay_count == expected_replay_count
            ):
                return RequeueOutcome.APPLIED
            if pinned and replay_count == cast(int, expected_replay_count) + 1:
                # The pinned transition is already durable, whatever the item
                # did afterwards, so repeating the request must not add a
                # second requeue the caller never asked for.
                return RequeueOutcome.REPLAYED
        _LOGGER.warning(
            "messaging.requeue_refused",
            bot_key=bot_key,
            queue=queue.value,
            item_id=item_id,
            expected_replay_count=expected_replay_count,
            observed_status=(
                None if observed is None else cast(str, observed["status"])
            ),
            observed_replay_count=(
                None if observed is None else cast(int, observed["replay_count"])
            ),
        )
        raise MessagingConflictError(
            f"only a {queue.value} dead letter can be requeued"
        )

    def _connect(self) -> Connection[DictRow]:
        """Open a dictionary-row PostgreSQL connection."""

        return connect(self._database_url, row_factory=dict_row)

    @staticmethod
    def _validate_failure(
        error_code: str,
        *,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        """Validate content-free failure metadata."""

        require_aware(now, field="now")
        if _ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("error code must be a safe bounded identifier")
        if retry_at is not None:
            require_aware(retry_at, field="retry_at")
            if retry_at <= now:
                raise ValueError("retry timestamp must be in the future")

    @staticmethod
    def _lock_offset(connection: Connection[DictRow], bot_key: str) -> DictRow:
        """Serialize one bot's gateway or sequential-worker ownership."""

        row = connection.execute(
            "SELECT * FROM messaging.telegram_offsets WHERE bot_key = %s FOR UPDATE",
            (bot_key,),
        ).fetchone()
        if row is None:
            raise LookupError("bot key is not initialized")
        return row

    @staticmethod
    def _require_inbox_lease(
        connection: Connection[DictRow], claim: InboxClaim
    ) -> None:
        """Lock and verify the exact inbox lease before a completion commit."""

        row = connection.execute(
            "SELECT status, claim_token FROM messaging.telegram_inbox "
            "WHERE id = %s FOR UPDATE",
            (claim.update.id,),
        ).fetchone()
        if (
            row is None
            or row["status"] != InboxStatus.PROCESSING.value
            or row["claim_token"] != claim.claim_token
        ):
            raise MessagingLeaseError("inbox lease is no longer current")

    @staticmethod
    def _insert_or_verify_inbox(
        connection: Connection[DictRow], update: NormalizedTelegramUpdate
    ) -> None:
        """Insert one update or prove an exact idempotent redelivery."""

        inserted = connection.execute(
            "INSERT INTO messaging.telegram_inbox "
            "(id, bot_key, update_id, source_reference, telegram_user_id, "
            "telegram_chat_id, telegram_message_id, received_at, accepted_at, "
            "text_content, callback_action, callback_query_id, "
            "reply_to_message_id, has_image, telegram_file_id, "
            "telegram_file_unique_id, file_media_type, file_size, next_attempt_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s) ON CONFLICT (bot_key, update_id) "
            "DO NOTHING RETURNING id",
            (
                update.id,
                update.bot_key,
                update.update_id,
                update.source_reference,
                update.telegram_user_id,
                update.telegram_chat_id,
                update.telegram_message_id,
                update.received_at,
                update.accepted_at,
                update.text,
                update.callback_action,
                update.callback_query_id,
                update.reply_to_message_id,
                update.has_image,
                update.telegram_file_id,
                update.telegram_file_unique_id,
                update.file_media_type,
                update.file_size,
                update.accepted_at,
            ),
        ).fetchone()
        if inserted is not None:
            return
        row = connection.execute(
            "SELECT id, source_reference, telegram_user_id, telegram_chat_id, "
            "telegram_message_id, received_at, text_content, callback_action, "
            "callback_query_id, reply_to_message_id, has_image, telegram_file_id, "
            "telegram_file_unique_id, file_media_type, file_size "
            "FROM messaging.telegram_inbox WHERE bot_key = %s AND update_id = %s",
            (update.bot_key, update.update_id),
        ).fetchone()
        expected = (
            update.id,
            update.source_reference,
            update.telegram_user_id,
            update.telegram_chat_id,
            update.telegram_message_id,
            update.received_at,
            update.text,
            update.callback_action,
            update.callback_query_id,
            update.reply_to_message_id,
            update.has_image,
            update.telegram_file_id,
            update.telegram_file_unique_id,
            update.file_media_type,
            update.file_size,
        )
        if row is None or tuple(row.values()) != expected:
            _LOGGER.error(
                "messaging.update_identity_conflict",
                bot_key=update.bot_key,
                update_id=update.update_id,
            )
            raise MessagingConflictError(
                "Telegram update identity has different normalized content"
            )

    @staticmethod
    def _inbox_claim(row: DictRow) -> InboxClaim:
        """Map a locked database row to a transport-neutral claim."""

        update = NormalizedTelegramUpdate(
            id=row["id"],
            bot_key=row["bot_key"],
            update_id=row["update_id"],
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            received_at=row["received_at"],
            accepted_at=row["accepted_at"],
            telegram_message_id=row["telegram_message_id"],
            text=row["text_content"],
            callback_action=row["callback_action"],
            callback_query_id=row["callback_query_id"],
            reply_to_message_id=row["reply_to_message_id"],
            has_image=row["has_image"],
            telegram_file_id=row["telegram_file_id"],
            telegram_file_unique_id=row["telegram_file_unique_id"],
            file_media_type=row["file_media_type"],
            file_size=row["file_size"],
        )
        return InboxClaim(
            update=update,
            status=InboxStatus(row["status"]),
            attempt_count=row["attempt_count"],
            replay_count=row["replay_count"],
            claim_token=row["claim_token"],
            claimed_until=row["claimed_until"],
        )

    def dead_letters(self, bot_key: str, *, limit: int) -> tuple[DeadLetter, ...]:
        """List failed work items in Telegram order, oldest first.

        The projection is deliberately content-free. An operator deciding
        whether to replay needs the identity, the attempts, and the reason
        code; the owner's text is none of their business even when they are
        the owner, because a terminal is not a private chat.

        Args:
            bot_key: Bot and environment persistence scope.
            limit: Maximum rows to return.

        Returns:
            Dead letters ordered by their Telegram update identity.

        Raises:
            ValueError: If the limit is not positive.
        """

        validate_bot_key(bot_key)
        if limit <= 0:
            raise ValueError("dead letter limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, update_id, source_reference, attempt_count, "
                "replay_count, last_error_code, accepted_at "
                "FROM messaging.telegram_inbox "
                "WHERE bot_key = %s AND status = 'dead_letter' "
                "ORDER BY update_id LIMIT %s",
                (bot_key, limit),
            ).fetchall()
        return tuple(
            DeadLetter(
                id=cast(UUID, row["id"]),
                update_id=cast(int, row["update_id"]),
                source_reference=cast(str, row["source_reference"]),
                attempt_count=cast(int, row["attempt_count"]),
                replay_count=cast(int, row["replay_count"]),
                last_error_code=cast("str | None", row["last_error_code"]),
                accepted_at=cast(datetime, row["accepted_at"]),
            )
            for row in rows
        )

    def outbox_dead_letters(
        self, bot_key: str, *, limit: int
    ) -> tuple[OutboxDeadLetter, ...]:
        """List failed logical responses without content or Telegram identity.

        Args:
            bot_key: Bot and environment persistence scope.
            limit: Maximum rows to return.

        Returns:
            Delivery dead letters ordered by their original creation time.

        Raises:
            ValueError: If the limit is not positive.
        """

        validate_bot_key(bot_key)
        if limit <= 0:
            raise ValueError("dead letter limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, response_kind, attempt_count, replay_count, "
                "last_error_code, created_at "
                "FROM messaging.telegram_outbox "
                "WHERE bot_key = %s AND status = 'dead_letter' "
                "ORDER BY created_at, id LIMIT %s",
                (bot_key, limit),
            ).fetchall()
        return tuple(
            OutboxDeadLetter(
                id=cast(UUID, row["id"]),
                response_kind=cast(str, row["response_kind"]),
                attempt_count=cast(int, row["attempt_count"]),
                replay_count=cast(int, row["replay_count"]),
                last_error_code=cast("str | None", row["last_error_code"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in rows
        )

    def replay_outbox(self, bot_key: str, outbox_id: UUID, *, now: datetime) -> None:
        """Return one scoped delivery dead letter to a fresh attempt cycle."""

        self.requeue_outbox(
            bot_key,
            outbox_id,
            expected_replay_count=None,
            now=now,
        )

    def requeue_outbox(
        self,
        bot_key: str,
        outbox_id: UUID,
        *,
        expected_replay_count: int | None,
        now: datetime,
    ) -> RequeueOutcome:
        """Start a fresh delivery cycle under the state the caller observed.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            outbox_id: Delivery dead letter the operator chose to resend.
            expected_replay_count: Manual resend count the caller observed, or
                None to accept any delivery dead letter.
            now: Aware instant the response becomes deliverable again.

        Returns:
            Whether this call started a cycle or found its transition done.

        Raises:
            MessagingConflictError: If no delivery dead letter in this bot
                scope is at the observed state.
        """

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        with self._connect() as connection:
            observed = connection.execute(
                "SELECT status, replay_count FROM messaging.telegram_outbox "
                "WHERE id = %s AND bot_key = %s FOR UPDATE",
                (outbox_id, bot_key),
            ).fetchone()
            outcome = self._requeue_outcome(
                observed,
                bot_key=bot_key,
                queue=MessagingQueue.OUTBOX,
                item_id=outbox_id,
                expected_replay_count=expected_replay_count,
            )
            if outcome is RequeueOutcome.APPLIED:
                connection.execute(
                    "UPDATE messaging.telegram_outbox SET status = 'pending', "
                    "attempt_count = 0, replay_count = replay_count + 1, "
                    "next_attempt_at = %s, claim_token = NULL, "
                    "claimed_until = NULL, last_error_code = NULL "
                    "WHERE id = %s AND bot_key = %s",
                    (now, outbox_id, bot_key),
                )
            return outcome

    def enqueue_notice(self, notice: OutboxMessageDraft, *, now: datetime) -> UUID:
        """Queue one owner message that no inbound work unit asked for.

        Pairing confirmations and downtime warnings are not answers to an inbox
        row, so they carry no `inbox_id`. Their logical key still makes them
        idempotent: a restart inside the same downtime episode re-derives the
        same key and adds no second message.

        Args:
            notice: Logical response to create, without an inbox reference.
            now: Aware creation instant.

        Returns:
            The identifier of the created or already existing message.

        Raises:
            ValueError: If the notice claims an inbox item.
            MessagingConflictError: If the key holds different content.
        """

        require_aware(now, field="now")
        if notice.inbox_id is not None:
            raise ValueError("an operator notice answers no inbox item")
        with self._connect() as connection:
            return self._insert_or_verify_outbox(connection, notice, now)

    def claim_next_outbox(
        self,
        bot_key: str,
        *,
        now: datetime,
        lease: timedelta,
        claim_token: UUID,
    ) -> OutboxClaim | None:
        """Lease the oldest non-terminal logical response when eligible."""

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        if lease <= timedelta(0):
            raise ValueError("outbox lease must be positive")
        with self._connect() as connection:
            self._lock_offset(connection, bot_key)
            row = connection.execute(
                "SELECT * FROM messaging.telegram_outbox WHERE bot_key = %s "
                "AND status NOT IN ('sent', 'dead_letter') "
                "ORDER BY created_at, id FOR UPDATE LIMIT 1",
                (bot_key,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["status"] == OutboxStatus.SENDING.value
                and row["claimed_until"] > now
            ):
                return None
            if (
                row["status"] == OutboxStatus.RETRY.value
                and row["next_attempt_at"] > now
            ):
                return None
            claimed_until = now + lease
            connection.execute(
                "UPDATE messaging.telegram_outbox SET status = 'sending', "
                "attempt_count = attempt_count + 1, claim_token = %s, "
                "claimed_until = %s, last_error_code = NULL WHERE id = %s",
                (claim_token, claimed_until, row["id"]),
            )
            row["status"] = OutboxStatus.SENDING.value
            row["attempt_count"] += 1
            row["claim_token"] = claim_token
            row["claimed_until"] = claimed_until
        return self._outbox_claim(row)

    def fail_outbox(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        transient: bool,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        """Schedule bounded delivery retry or create an outbox dead letter."""

        self._validate_failure(error_code, retry_at=retry_at, now=now)
        retry = transient and claim.attempt_count < MAX_AUTOMATIC_ATTEMPTS
        if retry and retry_at is None:
            raise ValueError("transient failure requires a retry timestamp")
        status = OutboxStatus.RETRY if retry else OutboxStatus.DEAD_LETTER
        next_attempt = retry_at if retry else now
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE messaging.telegram_outbox SET status = %s, "
                "next_attempt_at = %s, claim_token = NULL, claimed_until = NULL, "
                "last_error_code = %s WHERE id = %s AND status = 'sending' "
                "AND claim_token = %s",
                (
                    status.value,
                    next_attempt,
                    error_code,
                    claim.message.id,
                    claim.claim_token,
                ),
            )
            if result.rowcount != 1:
                raise MessagingLeaseError("outbox lease is no longer current")

    def complete_outbox(
        self,
        claim: OutboxClaim,
        *,
        telegram_message_id: int,
        now: datetime,
    ) -> None:
        """Persist a successful send and activate its reply correlation."""

        require_aware(now, field="now")
        if telegram_message_id <= 0:
            raise ValueError("Telegram message identifier must be positive")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE messaging.telegram_outbox SET status = 'sent', "
                "telegram_message_id = %s, sent_at = %s, claim_token = NULL, "
                "claimed_until = NULL, last_error_code = NULL WHERE id = %s "
                "AND status = 'sending' AND claim_token = %s",
                (telegram_message_id, now, claim.message.id, claim.claim_token),
            )
            if result.rowcount != 1:
                raise MessagingLeaseError("outbox lease is no longer current")
            connection.execute(
                "UPDATE messaging.telegram_pending_actions SET "
                "telegram_message_id = %s WHERE outbox_id = %s AND status = 'active'",
                (telegram_message_id, claim.message.id),
            )

    def create_pairing_code(
        self,
        *,
        code_id: UUID,
        bot_key: str,
        profile_id: UUID,
        code_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Revoke any older code and store one hashed one-use pairing code."""

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        require_aware(expires_at, field="expires_at")
        if expires_at <= now:
            raise ValueError("pairing code must expire in the future")
        if _SHA256.fullmatch(code_hash) is None:
            raise ValueError("pairing code hash must be lowercase SHA-256")
        with self._connect() as connection:
            self._lock_offset(connection, bot_key)
            existing = connection.execute(
                "SELECT 1 FROM messaging.telegram_owner_bindings WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
            if existing is not None:
                raise MessagingConflictError("bot already has an owner binding")
            connection.execute(
                "UPDATE messaging.telegram_pairing_codes SET revoked_at = %s "
                "WHERE bot_key = %s AND used_at IS NULL AND revoked_at IS NULL",
                (now, bot_key),
            )
            connection.execute(
                "INSERT INTO messaging.telegram_pairing_codes "
                "(id, bot_key, profile_id, code_hash, expires_at, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (code_id, bot_key, profile_id, code_hash, expires_at, now),
            )

    def pair_owner(
        self,
        *,
        bot_key: str,
        code_hash: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        confirmation: OutboxMessageDraft,
        now: datetime,
    ) -> OwnerBinding | None:
        """Consume a valid code and bind the sole authorized private owner.

        The profile comes from the code rather than from the caller, because
        the only party that can present the code is the owner the operator
        generated it for.

        Args:
            bot_key: Bot and environment persistence scope.
            code_hash: Lowercase SHA-256 of the code the sender typed.
            telegram_user_id: Sender to bind as the sole owner.
            telegram_chat_id: Private chat to bind as the sole owner chat.
            confirmation: Response queued in the same transaction so a paired
                owner learns that pairing worked.
            now: Aware pairing instant.

        Returns:
            The binding this call created, the identical existing binding when
            the same owner repeats a delivered `/pair`, or None when no live
            code matches and the sender is not the bound owner.

        Raises:
            ValueError: If identities, hash, or confirmation scope are invalid.
        """

        validate_bot_key(bot_key)
        require_aware(now, field="now")
        if telegram_user_id <= 0 or telegram_chat_id <= 0:
            raise ValueError("owner pairing requires positive private identities")
        if _SHA256.fullmatch(code_hash) is None:
            raise ValueError("pairing code hash must be lowercase SHA-256")
        if (
            confirmation.bot_key != bot_key
            or confirmation.telegram_chat_id != telegram_chat_id
            or confirmation.inbox_id is not None
        ):
            raise ValueError("pairing confirmation crosses the owner scope")
        with self._connect() as connection:
            self._lock_offset(connection, bot_key)
            existing = connection.execute(
                "SELECT * FROM messaging.telegram_owner_bindings WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
            if existing is not None:
                binding = self._owner_binding(existing)
                return (
                    binding
                    if (binding.telegram_user_id, binding.telegram_chat_id)
                    == (telegram_user_id, telegram_chat_id)
                    else None
                )
            code = connection.execute(
                "SELECT id, profile_id FROM messaging.telegram_pairing_codes "
                "WHERE bot_key = %s AND code_hash = %s "
                "AND used_at IS NULL AND revoked_at IS NULL AND expires_at > %s "
                "FOR UPDATE",
                (bot_key, code_hash, now),
            ).fetchone()
            if code is None:
                return None
            profile_id = cast(UUID, code["profile_id"])
            connection.execute(
                "UPDATE messaging.telegram_pairing_codes SET used_at = %s "
                "WHERE id = %s",
                (now, code["id"]),
            )
            connection.execute(
                "INSERT INTO messaging.telegram_owner_bindings "
                "(bot_key, profile_id, telegram_user_id, telegram_chat_id, paired_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (bot_key, profile_id, telegram_user_id, telegram_chat_id, now),
            )
            self._insert_or_verify_outbox(connection, confirmation, now)
        return OwnerBinding(
            bot_key=bot_key,
            profile_id=profile_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            paired_at=now,
        )

    def owner_binding(self, bot_key: str) -> OwnerBinding | None:
        """Return the trusted owner identity for one bot, if paired."""

        validate_bot_key(bot_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM messaging.telegram_owner_bindings WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
        return None if row is None else self._owner_binding(row)

    def resolve_callback_action(
        self,
        *,
        bot_key: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        action_id: UUID,
        action: str,
    ) -> PendingAction | None:
        """Resolve an active callback within its exact owner/chat/action scope."""

        validate_bot_key(bot_key)
        if not action.strip():
            raise ValueError("callback action cannot be blank")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM messaging.telegram_pending_actions WHERE id = %s "
                "AND bot_key = %s AND telegram_user_id = %s "
                "AND telegram_chat_id = %s AND status = 'active' "
                "AND %s = ANY(allowed_actions)",
                (action_id, bot_key, telegram_user_id, telegram_chat_id, action),
            ).fetchone()
        return None if row is None else self._pending_action(row)

    def resolve_reply_action(
        self,
        *,
        bot_key: str,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> PendingAction | None:
        """Resolve an unambiguous reply to a delivered active interaction."""

        validate_bot_key(bot_key)
        if telegram_message_id <= 0:
            raise ValueError("reply message identifier must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messaging.telegram_pending_actions "
                "WHERE bot_key = %s AND telegram_user_id = %s "
                "AND telegram_chat_id = %s AND telegram_message_id = %s "
                "AND status = 'active' ORDER BY created_at",
                (
                    bot_key,
                    telegram_user_id,
                    telegram_chat_id,
                    telegram_message_id,
                ),
            ).fetchall()
        if len(rows) != 1:
            return None
        return self._pending_action(rows[0])

    def health(self, bot_key: str) -> MessagingHealth:
        """Return content-free queue, offset, action, and dead-letter state.

        A bot whose persistence scope does not exist yet reports the same
        empty state a freshly created one does. Health is the first readiness
        check an operator runs on a migrated host, before `pair` creates that
        scope, and the database and schema it reports on are sound at that
        point. Every messaging table references `telegram_offsets(bot_key)`,
        so the absent row proves the counters below are zero rather than
        unknown.
        """

        validate_bot_key(bot_key)
        with self._connect() as connection:
            offset = connection.execute(
                "SELECT next_offset, last_observed_at, last_poll_at "
                "FROM messaging.telegram_offsets WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
            if offset is None:
                offset = {
                    "next_offset": 0,
                    "last_observed_at": None,
                    "last_poll_at": None,
                }
            inbox = connection.execute(
                "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, "
                "count(*) FILTER (WHERE status = 'retry') AS retrying, "
                "count(*) FILTER (WHERE status = 'dead_letter') AS dead, "
                "min(accepted_at) FILTER (WHERE status IN "
                "('pending', 'processing', 'retry')) AS oldest "
                "FROM messaging.telegram_inbox WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
            outbox = connection.execute(
                "SELECT count(*) FILTER (WHERE status = 'pending') AS pending, "
                "count(*) FILTER (WHERE status = 'retry') AS retrying, "
                "count(*) FILTER (WHERE status = 'dead_letter') AS dead, "
                "coalesce(sum(replay_count), 0) AS replay_count, "
                "min(created_at) FILTER (WHERE status IN "
                "('pending', 'sending', 'retry')) AS oldest "
                "FROM messaging.telegram_outbox WHERE bot_key = %s",
                (bot_key,),
            ).fetchone()
            actions = connection.execute(
                "SELECT count(*) AS active FROM messaging.telegram_pending_actions "
                "WHERE bot_key = %s AND status = 'active'",
                (bot_key,),
            ).fetchone()
        assert inbox is not None and outbox is not None and actions is not None
        return MessagingHealth(
            bot_key=bot_key,
            next_offset=offset["next_offset"],
            pending_inbox=inbox["pending"],
            retrying_inbox=inbox["retrying"],
            inbox_dead_letters=inbox["dead"],
            pending_outbox=outbox["pending"],
            retrying_outbox=outbox["retrying"],
            outbox_dead_letters=outbox["dead"],
            active_actions=actions["active"],
            last_observed_at=offset["last_observed_at"],
            last_poll_at=offset["last_poll_at"],
            oldest_inbox_at=inbox["oldest"],
            oldest_outbox_at=outbox["oldest"],
        )

    def text_delivery_latencies(
        self,
        bot_key: str,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[int, ...]:
        """Return successful text input-to-send durations in acceptance order.

        Failure notices and media work are excluded. A retry may create both a
        recoverable notice and a final response for one inbox item, so filtering
        response kinds is required to count the completed workflow once.

        Control revocations are excluded for the same reason and matter more:
        one work unit can queue several, they are delivered after the answer
        the owner is waiting for, and counting them would silently add slower
        samples that no owner ever waited on.

        Args:
            bot_key: Bot and environment persistence scope.
            since: Inclusive accepted-at lower bound.
            limit: Maximum successful workflow responses to return.

        Returns:
            Millisecond durations ordered by accepted input.

        Raises:
            ValueError: If the limit is not positive or since is naive.
        """

        validate_bot_key(bot_key)
        require_aware(since, field="since")
        if limit <= 0:
            raise ValueError("latency sample limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT round(extract(epoch FROM (o.sent_at - i.accepted_at)) "
                "* 1000)::bigint AS latency_ms "
                "FROM messaging.telegram_inbox AS i "
                "JOIN messaging.telegram_outbox AS o ON o.inbox_id = i.id "
                "WHERE i.bot_key = %s AND i.accepted_at >= %s "
                "AND i.text_content IS NOT NULL AND NOT i.has_image "
                "AND i.status = 'completed' AND o.status = 'sent' "
                "AND o.operation = %s "
                "AND o.response_kind NOT IN "
                "('recoverable_failure', 'terminal_failure') "
                "AND o.sent_at >= i.accepted_at "
                "ORDER BY i.accepted_at, i.update_id LIMIT %s",
                (bot_key, since, OUTBOX_SEND_MESSAGE, limit),
            ).fetchall()
        return tuple(cast(int, row["latency_ms"]) for row in rows)

    @staticmethod
    def _owner_binding(row: DictRow) -> OwnerBinding:
        """Map an owner binding row to its validated contract."""

        return OwnerBinding(
            bot_key=row["bot_key"],
            profile_id=row["profile_id"],
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            paired_at=row["paired_at"],
        )

    @staticmethod
    def _pending_action(row: DictRow) -> PendingAction:
        """Map a pending interaction row to its validated contract."""

        return PendingAction(
            id=row["id"],
            bot_key=row["bot_key"],
            profile_id=row["profile_id"],
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            outbox_id=row["outbox_id"],
            target_type=PendingActionTarget(row["target_type"]),
            target_id=row["target_id"],
            allowed_actions=tuple(row["allowed_actions"]),
            status=PendingActionStatus(row["status"]),
            telegram_message_id=row["telegram_message_id"],
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
        )

    @staticmethod
    def _outbox_claim(row: DictRow) -> OutboxClaim:
        """Map a locked outbox row to transport-neutral outbound work."""

        message: OutboundWork
        if row["operation"] == OUTBOX_REVOKE_CONTROLS:
            message = ControlRevocationDraft(
                id=row["id"],
                bot_key=row["bot_key"],
                telegram_chat_id=row["telegram_chat_id"],
                logical_key=row["logical_key"],
                target_message_id=row["target_message_id"],
                content_hash=row["content_hash"],
                inbox_id=row["inbox_id"],
            )
            return OutboxClaim(
                message=message,
                status=OutboxStatus(row["status"]),
                attempt_count=row["attempt_count"],
                claim_token=row["claim_token"],
                claimed_until=row["claimed_until"],
            )
        buttons = tuple(
            OutboundButton(
                label=cast(str, button["label"]),
                callback_data=cast(str, button["callback_data"]),
            )
            for button in cast(list[dict[str, object]], row["buttons"])
        )
        message = OutboxMessageDraft(
            id=row["id"],
            bot_key=row["bot_key"],
            telegram_chat_id=row["telegram_chat_id"],
            logical_key=row["logical_key"],
            response_kind=row["response_kind"],
            text=row["text_content"],
            content_hash=row["content_hash"],
            buttons=buttons,
            inbox_id=row["inbox_id"],
            reply_to_message_id=row["reply_to_message_id"],
        )
        return OutboxClaim(
            message=message,
            status=OutboxStatus(row["status"]),
            attempt_count=row["attempt_count"],
            claim_token=row["claim_token"],
            claimed_until=row["claimed_until"],
        )

    @staticmethod
    def _insert_or_verify_outbox(
        connection: Connection[DictRow],
        response: OutboxMessageDraft,
        now: datetime,
    ) -> UUID:
        """Insert one logical response or verify an exact idempotent replay."""

        buttons = [
            {"label": button.label, "callback_data": button.callback_data}
            for button in response.buttons
        ]
        inserted = connection.execute(
            "INSERT INTO messaging.telegram_outbox "
            "(id, bot_key, inbox_id, telegram_chat_id, logical_key, response_kind, "
            "text_content, buttons, reply_to_message_id, content_hash, "
            "next_attempt_at, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (bot_key, logical_key) DO NOTHING RETURNING id",
            (
                response.id,
                response.bot_key,
                response.inbox_id,
                response.telegram_chat_id,
                response.logical_key,
                response.response_kind,
                response.text,
                Jsonb(buttons),
                response.reply_to_message_id,
                response.content_hash,
                now,
                now,
            ),
        ).fetchone()
        if inserted is not None:
            return cast(UUID, inserted["id"])
        row = connection.execute(
            "SELECT id, inbox_id, telegram_chat_id, response_kind, text_content, "
            "buttons, reply_to_message_id, content_hash "
            "FROM messaging.telegram_outbox WHERE bot_key = %s AND logical_key = %s",
            (response.bot_key, response.logical_key),
        ).fetchone()
        expected = (
            response.id,
            response.inbox_id,
            response.telegram_chat_id,
            response.response_kind,
            response.text,
            buttons,
            response.reply_to_message_id,
            response.content_hash,
        )
        if row is None or tuple(row.values()) != expected:
            raise MessagingConflictError(
                "logical outbox identity has different response content"
            )
        return cast(UUID, row["id"])

    @staticmethod
    def _insert_or_verify_revocation(
        connection: Connection[DictRow],
        revocation: ControlRevocationDraft,
        now: datetime,
    ) -> UUID:
        """Insert one revocation or verify an exact idempotent replay.

        The logical key names the target message, so a replayed work unit
        queues the same revocation rather than a second one, exactly as a
        replayed response reuses its logical row.
        """

        inserted = connection.execute(
            "INSERT INTO messaging.telegram_outbox "
            "(id, bot_key, inbox_id, telegram_chat_id, logical_key, operation, "
            "response_kind, text_content, target_message_id, content_hash, "
            "next_attempt_at, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, %s) "
            "ON CONFLICT (bot_key, logical_key) DO NOTHING RETURNING id",
            (
                revocation.id,
                revocation.bot_key,
                revocation.inbox_id,
                revocation.telegram_chat_id,
                revocation.logical_key,
                OUTBOX_REVOKE_CONTROLS,
                OUTBOX_REVOKE_CONTROLS,
                revocation.target_message_id,
                revocation.content_hash,
                now,
                now,
            ),
        ).fetchone()
        if inserted is not None:
            return cast(UUID, inserted["id"])
        row = connection.execute(
            "SELECT id, operation, telegram_chat_id, target_message_id, content_hash "
            "FROM messaging.telegram_outbox WHERE bot_key = %s AND logical_key = %s",
            (revocation.bot_key, revocation.logical_key),
        ).fetchone()
        expected = (
            revocation.id,
            OUTBOX_REVOKE_CONTROLS,
            revocation.telegram_chat_id,
            revocation.target_message_id,
            revocation.content_hash,
        )
        if row is None or tuple(row.values()) != expected:
            raise MessagingConflictError(
                "logical outbox identity has different revocation content"
            )
        return cast(UUID, row["id"])

    def delivered_control_messages(
        self,
        *,
        bot_key: str,
        target_type: PendingActionTarget,
        target_id: UUID,
        excluding_outbox_id: UUID,
    ) -> tuple[int, ...]:
        """Return delivered messages still offering controls for one target.

        Only sent rows have a Telegram identifier to edit, and only rows that
        carried buttons ever showed controls worth removing. The response just
        committed is excluded because it is the one that is still correct.

        The target is named rather than assumed, because a settled category
        review supersedes the controls on the review message as much as on any
        earlier movement summary, and those are different targets.

        Args:
            bot_key: Selected bot persistence scope.
            target_type: Whether the superseded controls acted on a movement or
                a category review.
            target_id: Movement or review whose earlier controls are superseded.
            excluding_outbox_id: Response that supersedes them.

        Returns:
            Ascending Telegram message identifiers to revoke.
        """

        validate_bot_key(bot_key)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT outbox.telegram_message_id AS message_id "
                "FROM messaging.telegram_pending_actions AS action "
                "JOIN messaging.telegram_outbox AS outbox "
                "ON outbox.id = action.outbox_id "
                "WHERE action.bot_key = %s AND action.target_type = %s "
                "AND action.target_id = %s AND outbox.status = 'sent' "
                "AND outbox.operation = %s "
                "AND jsonb_array_length(outbox.buttons) > 0 "
                "AND outbox.id <> %s "
                "ORDER BY message_id",
                (
                    bot_key,
                    target_type.value,
                    target_id,
                    OUTBOX_SEND_MESSAGE,
                    excluding_outbox_id,
                ),
            ).fetchall()
        return tuple(cast(int, row["message_id"]) for row in rows)

    @staticmethod
    def _insert_or_verify_action(
        connection: Connection[DictRow],
        action: PendingActionDraft,
        now: datetime,
    ) -> None:
        """Insert a pending action or verify the exact replayed mapping."""

        inserted = connection.execute(
            "INSERT INTO messaging.telegram_pending_actions "
            "(id, bot_key, profile_id, telegram_user_id, telegram_chat_id, "
            "outbox_id, target_type, target_id, allowed_actions, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (
                action.id,
                action.bot_key,
                action.profile_id,
                action.telegram_user_id,
                action.telegram_chat_id,
                action.outbox_id,
                action.target_type.value,
                action.target_id,
                list(action.allowed_actions),
                now,
            ),
        ).fetchone()
        if inserted is not None:
            return
        row = connection.execute(
            "SELECT bot_key, profile_id, telegram_user_id, telegram_chat_id, "
            "outbox_id, target_type, target_id, allowed_actions "
            "FROM messaging.telegram_pending_actions WHERE id = %s",
            (action.id,),
        ).fetchone()
        expected = (
            action.bot_key,
            action.profile_id,
            action.telegram_user_id,
            action.telegram_chat_id,
            action.outbox_id,
            action.target_type.value,
            action.target_id,
            list(action.allowed_actions),
        )
        if row is None or tuple(row.values()) != expected:
            raise MessagingConflictError(
                "pending action identity has different target content"
            )


__all__ = [
    "MAX_AUTOMATIC_ATTEMPTS",
    "OUTBOX_REVOKE_CONTROLS",
    "OUTBOX_SEND_MESSAGE",
    "MessagingConflictError",
    "MessagingLeaseError",
    "PostgresMessagingRepository",
]
