"""Independent ingress and outbox loops over durable messaging persistence."""

from __future__ import annotations

from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from threading import Event
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from money_tracker.messaging import (
    TELEGRAM_UPDATE_RETENTION,
    ControlRevocationDraft,
    MessagingRepository,
    OutboxMessageDraft,
    RetryPolicy,
    downtime_gap,
)
from money_tracker.presentation import downtime_notice
from money_tracker.structured_logging import get_logger, log_context
from money_tracker.telegram.config import TelegramSettings
from money_tracker.telegram.contracts import IngestReport
from money_tracker.telegram.ingestion import TelegramUpdateIngestor
from money_tracker.telegram.transport import (
    TelegramTransport,
    TelegramTransportError,
)

_LOGGER = get_logger("money_tracker.telegram.gateway")
_OUTBOX_IDLE_SECONDS = 0.5


class Clock(Protocol):
    """Clock boundary shared by restart-safe gateway loops."""

    def now(self) -> datetime:
        """Return an aware current instant."""


class TelegramGateway:
    """Poll, durably ingest, and deliver without workflow/model dependencies.

    Attributes:
        Runtime dependencies are private and carry no in-memory correctness state;
        offsets, leases, attempts, and delivery results remain in PostgreSQL.
    """

    def __init__(
        self,
        *,
        settings: TelegramSettings,
        transport: TelegramTransport,
        repository: MessagingRepository,
        ingestor: TelegramUpdateIngestor,
        clock: Clock,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Assemble one environment-specific gateway process.

        Args:
            settings: Explicit test or production bot configuration.
            transport: Official Bot API boundary.
            repository: Durable offset, inbox, and outbox owner.
            ingestor: Authorization and normalization application service.
            clock: Aware runtime clock.
            retry_policy: Shared five-attempt delivery schedule.
        """

        self._settings = settings
        self._logger = _LOGGER.bind(
            bot_key=settings.bot_key,
            environment=settings.environment.value,
        )
        self._transport = transport
        self._repository = repository
        self._ingestor = ingestor
        self._clock = clock
        self._retry_policy = retry_policy or RetryPolicy()

    def prepare(self) -> None:
        """Validate the bot, preserve backlog, and initialize offset ownership."""

        now = self._clock.now()
        self._logger.debug("gateway.prepare.started")
        self._repository.ensure_bot(
            self._settings.bot_key,
            environment=self._settings.environment,
            now=now,
        )
        self._transport.validate_bot()
        self._transport.disable_webhook()
        self._announce_downtime(now=now)
        self._logger.info("gateway.ready")

    def _announce_downtime(self, *, now: datetime) -> None:
        """Warn the owner when Telegram may have expired updates while off.

        The Bot API discards undelivered updates after 24 hours and nothing
        local can recover them, so the only honest response is to say the gap
        exists and ask the owner to resend. The notice is keyed by the instant
        the gap started, which makes repeated restarts inside one outage
        produce one message rather than one per start.
        """

        bot_key = self._settings.bot_key
        binding = self._repository.owner_binding(bot_key)
        if binding is None:
            self._logger.debug("gateway.downtime.unpaired")
            return
        health = self._repository.health(bot_key)
        gap = downtime_gap(health, now=now)
        if gap is None or gap <= TELEGRAM_UPDATE_RETENTION:
            self._logger.debug(
                "gateway.downtime.within_retention",
                gap_seconds=None if gap is None else int(gap.total_seconds()),
            )
            return
        last_poll_at = health.last_poll_at
        assert last_poll_at is not None
        notice = downtime_notice(last_poll_at)
        self._repository.enqueue_notice(
            OutboxMessageDraft(
                id=uuid5(NAMESPACE_URL, f"money-tracker:downtime:{last_poll_at}"),
                bot_key=bot_key,
                telegram_chat_id=binding.telegram_chat_id,
                logical_key=f"downtime:{last_poll_at.isoformat()}",
                response_kind=notice.kind.value,
                text=notice.text,
                content_hash=notice.content_hash,
            ),
            now=now,
        )
        self._logger.warning(
            "gateway.downtime.exceeded_retention",
            gap_hours=int(gap.total_seconds() // 3600),
        )

    def run_once(self) -> IngestReport:
        """Commit one poll batch before callback acknowledgement or another poll.

        Returns:
            Content-free counters and accepted normalized values for the batch.
        """

        offset = self._repository.polling_offset(self._settings.bot_key)
        self._logger.debug("gateway.poll.started", offset=offset)
        updates = self._transport.get_updates(offset=offset)
        report = self._ingestor.ingest(
            bot_key=self._settings.bot_key,
            expected_offset=offset,
            updates=updates,
            now=self._clock.now(),
        )
        for callback_query_id in report.callback_query_ids:
            self._acknowledge_callback(callback_query_id)
        # An entirely empty long poll is the normal steady state, so it stays
        # at DEBUG; anything the batch actually committed is worth keeping.
        accepted = len(report.accepted)
        observed = accepted + report.rejected_count + report.unsupported_count
        if observed:
            self._logger.info(
                "gateway.ingest.committed",
                offset=offset,
                accepted=accepted,
                rejected=report.rejected_count,
                unsupported=report.unsupported_count,
            )
        else:
            self._logger.debug("gateway.ingest.empty", offset=offset)
        if report.rejected_count:
            self._logger.warning(
                "gateway.ingest.rejected", rejected=report.rejected_count
            )
        return report

    def deliver_outbox_once(self) -> bool:
        """Attempt the oldest eligible outbound work through the transport.

        The queue carries two operations. Sending a response learns its
        Telegram identifier from the call; revoking the controls on an earlier
        summary already knows the message it edits, so the same identifier is
        what the completed row records in both cases.

        Returns:
            True only when one unit of outbound work was durably completed.
        """

        now = self._clock.now()
        claim = self._repository.claim_next_outbox(
            self._settings.bot_key,
            now=now,
            lease=timedelta(
                seconds=self._settings.request_timeout_seconds
                + self._settings.connect_timeout_seconds
                + 5
            ),
            claim_token=uuid4(),
        )
        if claim is None:
            return False
        work = claim.message
        try:
            if isinstance(work, ControlRevocationDraft):
                self._transport.revoke_controls(work)
                delivered = work.target_message_id
            else:
                delivered = self._transport.send_message(work).telegram_message_id
        except TelegramTransportError as error:
            retry_at = None
            if error.transient and not self._retry_policy.exhausted(
                claim.attempt_count
            ):
                retry_at = self._retry_policy.retry_at(
                    now,
                    attempt_count=claim.attempt_count,
                )
                if error.retry_after_seconds is not None:
                    retry_at = max(
                        retry_at,
                        now + timedelta(seconds=error.retry_after_seconds),
                    )
            self._repository.fail_outbox(
                claim,
                error_code=error.code,
                transient=error.transient,
                retry_at=retry_at,
                now=now,
            )
            # A transient failure that still has attempts left is expected
            # operational noise; a permanent one, or an exhausted schedule, is
            # a dead letter that needs an owner, so it is reported as an error.
            fields: dict[str, object] = {
                "outbox_id": work.id,
                "reason_code": error.code,
                "transient": error.transient,
                "attempt_count": claim.attempt_count,
                "retry_at": retry_at,
            }
            if retry_at is None:
                self._logger.error("gateway.outbox.dead_lettered", **fields)
            else:
                self._logger.warning("gateway.outbox.retry_scheduled", **fields)
            return False
        self._repository.complete_outbox(
            claim,
            telegram_message_id=delivered,
            now=self._clock.now(),
        )
        self._logger.info(
            "gateway.outbox.delivered",
            outbox_id=work.id,
            telegram_message_id=delivered,
            attempt_count=claim.attempt_count,
            revocation=isinstance(work, ControlRevocationDraft),
        )
        return True

    def run_forever(self, stop: Event | None = None) -> None:
        """Run ingress and delivery concurrently with shared failure shutdown.

        Args:
            stop: Shutdown request owned by the caller, which is where signal
                handlers must be installed. Both loops check it between
                iterations, so a stop is honored after the current long poll or
                delivery rather than inside it. Defaults to a private event,
                leaving failure the only way the loops end.
        """

        self.prepare()
        stop = Event() if stop is None else stop
        self._logger.info("gateway.loops.starting")
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="money-tracker-telegram",
        ) as executor:
            futures = (
                executor.submit(self._run_ingress, stop),
                executor.submit(self._run_outbox, stop),
            )
            try:
                done, _ = wait(futures, return_when=FIRST_EXCEPTION)
                for future in done:
                    future.result()
            finally:
                stop.set()
                self._logger.info("gateway.loops.stopped")

    def close(self) -> None:
        """Release gateway transport resources."""

        self._logger.debug("gateway.transport.closing")
        self._transport.close()

    def _run_ingress(self, stop: Event) -> None:
        """Long poll with bounded exponential transport backoff."""

        # Each loop runs in its own thread and therefore starts from an empty
        # log context, so the binding that names the loop belongs here rather
        # than around the executor that submitted it.
        with log_context(loop="ingress"):
            failures = 0
            while not stop.is_set():
                try:
                    self.run_once()
                    failures = 0
                except TelegramTransportError as error:
                    if not error.transient:
                        self._logger.error(
                            "gateway.ingress.stopped", reason_code=error.code
                        )
                        raise
                    failures += 1
                    delay = self._retry_delay(
                        failures,
                        retry_after_seconds=error.retry_after_seconds,
                    )
                    self._logger.warning(
                        "gateway.ingress.retry_scheduled",
                        reason_code=error.code,
                        consecutive_failures=failures,
                        delay_seconds=round(delay, 1),
                    )
                    stop.wait(delay)

    def _run_outbox(self, stop: Event) -> None:
        """Continuously deliver independently of the long-poll wait."""

        with log_context(loop="outbox"):
            while not stop.is_set():
                delivered = self.deliver_outbox_once()
                if not delivered:
                    stop.wait(_OUTBOX_IDLE_SECONDS)

    def _acknowledge_callback(self, callback_query_id: str) -> None:
        """Best-effort callback acknowledgement after durable local commit."""

        try:
            self._transport.answer_callback_query(callback_query_id)
        except TelegramTransportError as error:
            # The update is already durably committed, so a failed
            # acknowledgement costs the owner a spinner, not a message.
            self._logger.warning(
                "gateway.callback.acknowledgement_failed",
                reason_code=error.code,
            )

    def _retry_delay(
        self,
        failures: int,
        *,
        retry_after_seconds: int | None,
    ) -> float:
        """Calculate a bounded transport delay without random private state."""

        if failures <= 0:
            raise ValueError("failure count must be positive")
        exponential = self._settings.retry_base_seconds * (2.0 ** (failures - 1))
        delay = min(exponential, self._settings.retry_maximum_seconds)
        if retry_after_seconds is not None:
            delay = max(delay, float(retry_after_seconds))
        return min(delay, self._settings.retry_maximum_seconds)
