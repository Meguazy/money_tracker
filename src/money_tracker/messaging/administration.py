"""Operator-facing messaging operations that expose no private content.

AD-018 keeps administration separate from the gateway and the worker. The
operations here answer a person at a terminal rather than a durable work item,
which is why they may read live state that a logical response never can.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from money_tracker.latency import LatencySummary, summarize_latencies
from money_tracker.messaging.contracts import (
    BotEnvironment,
    DeadLetter,
    MessagingHealth,
    MessagingQueue,
    OutboxDeadLetter,
    RequeuedItem,
    TextDeliverySample,
)
from money_tracker.messaging.ports import MessagingRepository
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.messaging.administration")

DEFAULT_PAIRING_LIFETIME = timedelta(minutes=10)
TELEGRAM_UPDATE_RETENTION = timedelta(hours=24)
"""How long the Bot API keeps an undelivered update, per its documentation."""

_CODE_BYTES = 16
_DEAD_LETTER_LIMIT = 50
_LATENCY_SAMPLE_LIMIT = 200

INBOX_REQUEUE_COMMAND = "messaging-replay"
OUTBOX_REQUEUE_COMMAND = "messaging-resend"


def requeue_command_key(
    command: str,
    item_id: UUID,
    observed_replay_count: int,
) -> str:
    """Build the stable key that names one requeue of one dead letter.

    A requeue is not a finance write and claims no command table: the durable
    pin is the row's own replay count, checked inside the single messaging
    transaction that requeues it. The key exists so a caller states which
    transition it intends, and so two callers looking at the same displayed
    state cannot ask for two different ones.

    Args:
        command: Requeue family, either inbox replay or outbox resend.
        item_id: Dead letter the caller selected.
        observed_replay_count: Replay count shown when the caller decided.

    Returns:
        The canonical key for that one transition.
    """

    return f"{command}:{item_id}:{observed_replay_count}"


class Clock(Protocol):
    """Aware clock boundary shared by the messaging services."""

    def now(self) -> datetime:
        """Return an aware current instant."""


class IdGenerator(Protocol):
    """Stable identifier provider for durable administrative records."""

    def new(self) -> UUID:
        """Return a fresh identifier."""


@dataclass(frozen=True, slots=True)
class PairingCode:
    """One pairing code in the only form the operator ever sees it.

    The clear value is returned once and never stored: persistence keeps only
    its SHA-256. Losing it therefore means generating another code, which is
    the intended trade rather than a limitation to work around.

    Attributes:
        code: Clear one-use code the owner types into the private bot chat.
        expires_at: Instant after which the code stops being accepted.
    """

    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TextLatencyReport:
    """Content-free warm text delivery latency summary and its breakdown.

    The first four latency fields are the NFR-PER-001 measurement as Step 4
    defined it. The breakdown splits the same warm samples at the worker's
    completion commit, so a slow total can be attributed to the worker side,
    which includes waiting while the worker or model was unavailable, or to the
    gateway side, which includes waiting for delivery.

    Attributes:
        sample_count: Successful text workflows found in the selected window.
        warmup_excluded: Number of earliest successes removed before p95.
        warm_sample_count: Samples used by the warm p95.
        warm_p95_ms: Nearest-rank warm p95, or None without a warm sample.
        maximum_ms: Slowest warm sample, or None without a warm sample.
        total: Warm acceptance-to-send distribution.
        completion: Warm acceptance-to-completion distribution.
        delivery: Warm completion-to-send distribution.
        retried_sample_count: Warm samples that needed more than one attempt
            or an operator replay.
        by_response_kind: Warm acceptance-to-send distribution per response
            kind.
    """

    sample_count: int
    warmup_excluded: int
    warm_sample_count: int
    warm_p95_ms: int | None
    maximum_ms: int | None
    total: LatencySummary | None = None
    completion: LatencySummary | None = None
    delivery: LatencySummary | None = None
    retried_sample_count: int = 0
    by_response_kind: Mapping[str, LatencySummary] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessagingOperationsSnapshot:
    """Everything an operator surface may know about one bot at one instant.

    The snapshot is content-free by construction: every value in it is a count,
    an age, a bounded reason code, or a stable identifier. Nothing an owner
    wrote can reach it, which is what lets the same value be rendered in a
    browser and summarized in a journal without a separate privacy decision.

    Attributes:
        environment: Bot environment this durable state belongs to.
        owner_paired: Whether the sole owner binding exists.
        health: Queue depths, ages, offset, and active correlations.
        downtime_exceeds_update_retention: Whether the gap since the last poll
            is longer than the Bot API keeps undelivered updates.
        inbox_dead_letters: Failed work items, oldest Telegram identity first.
        outbox_dead_letters: Failed logical responses, oldest first.
    """

    environment: BotEnvironment
    owner_paired: bool
    health: MessagingHealth
    downtime_exceeds_update_retention: bool
    inbox_dead_letters: tuple[DeadLetter, ...]
    outbox_dead_letters: tuple[OutboxDeadLetter, ...]


class MessagingAdministration:
    """Perform bounded operator actions against durable messaging state.

    Attributes:
        The service holds no state of its own. It exists so that operator
        policy, such as how long a pairing code lives, is expressed once rather
        than restated by every entry point.
    """

    def __init__(
        self,
        repository: MessagingRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind the durable messaging port and the runtime boundaries.

        Args:
            repository: Owner of pairing, binding, and queue state.
            clock: Aware runtime clock.
            ids: Stable identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def create_pairing_code(
        self,
        *,
        bot_key: str,
        profile_id: UUID,
        environment: BotEnvironment,
        lifetime: timedelta = DEFAULT_PAIRING_LIFETIME,
    ) -> PairingCode:
        """Generate and store one short-lived, one-use pairing code.

        The bot's persistence scope is created first when it does not exist.
        Pairing is the first thing an operator does with a new bot, before the
        gateway has ever run, so requiring the scope to already be there would
        make the documented setup order impossible. Doing it here also refuses
        a bot key that belongs to the other environment.

        Any earlier unused code for the same bot is revoked, so exactly one
        code can ever be live. A bot that already has an owner is refused
        rather than offered a second binding.

        Args:
            bot_key: Bot and environment persistence scope.
            profile_id: Profile the resulting owner binding will own.
            environment: Environment this bot key must belong to.
            lifetime: How long the code stays acceptable.

        Returns:
            The clear code and its expiry, for one-time operator display.

        Raises:
            ValueError: If the lifetime is not positive.
            MessagingConflictError: If the bot key belongs to another
                environment, or the bot already has an owner binding.
        """

        if lifetime <= timedelta(0):
            _LOGGER.error("pairing.lifetime_invalid", bot_key=bot_key)
            raise ValueError("a pairing code must have a positive lifetime")
        now = self._clock.now()
        self._repository.ensure_bot(bot_key, environment=environment, now=now)
        code = secrets.token_urlsafe(_CODE_BYTES)
        expires_at = now + lifetime
        self._repository.create_pairing_code(
            code_id=self._ids.new(),
            bot_key=bot_key,
            profile_id=profile_id,
            code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
            now=now,
        )
        # The code itself is a one-use secret and never reaches a record;
        # that a code was issued, and when it stops working, is what an
        # operator needs from the journal.
        _LOGGER.info(
            "pairing.code_issued",
            bot_key=bot_key,
            profile_id=profile_id,
            environment=environment.value,
            expires_at=expires_at,
        )
        return PairingCode(code=code, expires_at=expires_at)

    def health(self, bot_key: str) -> MessagingHealth:
        """Return the content-free queue and offset summary for an operator.

        Args:
            bot_key: Bot and environment persistence scope.

        Returns:
            Counts, ages, and offset state carrying no message content.
        """

        return self._repository.health(bot_key)

    def operations_snapshot(
        self,
        bot_key: str,
        *,
        environment: BotEnvironment,
        limit: int = _DEAD_LETTER_LIMIT,
    ) -> MessagingOperationsSnapshot:
        """Compose the one content-free view an operator surface needs.

        Queue state and both failure queues are read together because a count
        without its failed items cannot be acted on, and a failed item without
        the queue it sits in cannot be judged. Whether the downtime gap matters
        is operator policy, so it is decided here rather than by each caller.

        Args:
            bot_key: Bot and environment persistence scope.
            environment: Trusted environment that scope belongs to.
            limit: Maximum dead letters to include per queue.

        Returns:
            Counts, ages, offset state, pairing state, and both failure queues.
        """

        health = self._repository.health(bot_key)
        gap = downtime_gap(health, now=self._clock.now())
        return MessagingOperationsSnapshot(
            environment=environment,
            owner_paired=self.owner_paired(bot_key),
            health=health,
            downtime_exceeds_update_retention=(
                gap is not None and gap > TELEGRAM_UPDATE_RETENTION
            ),
            inbox_dead_letters=self.dead_letters(bot_key, limit=limit),
            outbox_dead_letters=self.outbox_dead_letters(bot_key, limit=limit),
        )

    def dead_letters(
        self,
        bot_key: str,
        *,
        limit: int = _DEAD_LETTER_LIMIT,
    ) -> tuple[DeadLetter, ...]:
        """List failed work items in the order they arrived.

        Args:
            bot_key: Bot and environment persistence scope.
            limit: Maximum rows to return.

        Returns:
            Dead letters carrying identity, attempts, and reason codes only.
        """

        return self._repository.dead_letters(bot_key, limit=limit)

    def owner_paired(self, bot_key: str) -> bool:
        """Report whether one bot already has its sole owner binding.

        The binding itself carries Telegram identities, so callers that only
        need to know whether pairing happened get the answer rather than the
        record.

        Args:
            bot_key: Bot and environment persistence scope.

        Returns:
            True when the bot has an owner binding.
        """

        return self._repository.owner_binding(bot_key) is not None

    def replay(self, bot_key: str, inbox_id: UUID) -> None:
        """Return one dead letter to the queue with its identity intact.

        This is the unattended operator form: it accepts whichever dead letter
        the named identifier resolves to. A caller that decided from a listing
        should pin that listing with `requeue_inbox` instead.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            inbox_id: Dead letter an operator chose to retry.

        Raises:
            MessagingConflictError: If the item is not a dead letter.
        """

        self.requeue_inbox(bot_key, inbox_id, expected_replay_count=None)

    def requeue_inbox(
        self,
        bot_key: str,
        inbox_id: UUID,
        *,
        expected_replay_count: int | None,
    ) -> RequeuedItem:
        """Requeue one dead letter under the state the caller decided from.

        Replay preserves the source reference, so the finance command keys the
        work unit already derived stay the same. A replayed item that already
        committed a mutation therefore returns the first result rather than
        applying a second one, which is what makes this safe to offer at all.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            inbox_id: Dead letter the caller chose to retry.
            expected_replay_count: Replay count shown when the caller decided,
                or None to accept any dead letter.

        Returns:
            Whether this call requeued the item or replayed a done transition.

        Raises:
            MessagingConflictError: If no dead letter is at that state.
        """

        outcome = self._repository.requeue_inbox(
            bot_key,
            inbox_id,
            expected_replay_count=expected_replay_count,
            now=self._clock.now(),
        )
        # Reaching for a replay means something already failed five times, so
        # the intervention is recorded with what it actually did.
        _LOGGER.warning(
            "messaging.inbox.replayed",
            bot_key=bot_key,
            inbox_id=inbox_id,
            expected_replay_count=expected_replay_count,
            outcome=outcome.value,
        )
        return RequeuedItem(
            id=inbox_id,
            queue=MessagingQueue.INBOX,
            outcome=outcome,
        )

    def outbox_dead_letters(
        self,
        bot_key: str,
        *,
        limit: int = _DEAD_LETTER_LIMIT,
    ) -> tuple[OutboxDeadLetter, ...]:
        """List failed logical responses without exposing their content.

        Args:
            bot_key: Bot and environment persistence scope.
            limit: Maximum rows to return.

        Returns:
            Delivery failures carrying stable identity and safe metadata only.
        """

        return self._repository.outbox_dead_letters(bot_key, limit=limit)

    def resend(self, bot_key: str, outbox_id: UUID) -> None:
        """Return one delivery dead letter to a fresh bounded attempt cycle.

        Telegram cannot deduplicate sendMessage calls. An operator must
        therefore request this explicitly after deciding that another physical
        message is preferable to leaving an ambiguous delivery unresolved.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            outbox_id: Delivery dead letter selected by the operator.

        Raises:
            MessagingConflictError: If the item is not an outbox dead letter.
        """

        self.requeue_outbox(bot_key, outbox_id, expected_replay_count=None)

    def requeue_outbox(
        self,
        bot_key: str,
        outbox_id: UUID,
        *,
        expected_replay_count: int | None,
    ) -> RequeuedItem:
        """Resend one delivery dead letter under the observed state.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            outbox_id: Delivery dead letter the caller chose to resend.
            expected_replay_count: Manual resend count shown when the caller
                decided, or None to accept any delivery dead letter.

        Returns:
            Whether this call started a cycle or replayed a done transition.

        Raises:
            MessagingConflictError: If no delivery dead letter is at that state.
        """

        outcome = self._repository.requeue_outbox(
            bot_key,
            outbox_id,
            expected_replay_count=expected_replay_count,
            now=self._clock.now(),
        )
        _LOGGER.warning(
            "messaging.outbox.resent",
            bot_key=bot_key,
            outbox_id=outbox_id,
            expected_replay_count=expected_replay_count,
            outcome=outcome.value,
        )
        return RequeuedItem(
            id=outbox_id,
            queue=MessagingQueue.OUTBOX,
            outcome=outcome,
        )

    def text_latency_report(
        self,
        bot_key: str,
        *,
        since: datetime,
        limit: int = _LATENCY_SAMPLE_LIMIT,
    ) -> TextLatencyReport:
        """Summarize warm owner-input to Bot-API-send latency.

        The first successful observation is a warm-up and is excluded. The
        remaining p95 uses nearest-rank selection, matching the evaluation
        convention without exposing message text, identities, or timestamps.

        Args:
            bot_key: Bot and environment persistence scope.
            since: Inclusive start of the acceptance sample window.
            limit: Maximum successful workflows to include.

        Returns:
            Counts and warm latency statistics with no private content.
        """

        samples = self._repository.text_delivery_samples(
            bot_key,
            since=since,
            limit=limit,
        )
        warmup_excluded = min(1, len(samples))
        warm = samples[warmup_excluded:]
        total = summarize_latencies(sample.total_ms for sample in warm)
        if total is None:
            return TextLatencyReport(
                sample_count=len(samples),
                warmup_excluded=warmup_excluded,
                warm_sample_count=0,
                warm_p95_ms=None,
                maximum_ms=None,
            )
        return TextLatencyReport(
            sample_count=len(samples),
            warmup_excluded=warmup_excluded,
            warm_sample_count=len(warm),
            warm_p95_ms=total.p95_ms,
            maximum_ms=total.maximum_ms,
            total=total,
            completion=summarize_latencies(sample.completion_ms for sample in warm),
            delivery=summarize_latencies(sample.delivery_ms for sample in warm),
            retried_sample_count=sum(
                1 for sample in warm if sample.attempts > 1 or sample.replayed
            ),
            by_response_kind=_by_response_kind(warm),
        )


def _by_response_kind(
    samples: Sequence[TextDeliverySample],
) -> dict[str, LatencySummary]:
    """Group warm totals by the stable kind of response the owner received."""

    grouped: dict[str, list[int]] = {}
    for sample in samples:
        grouped.setdefault(sample.response_kind, []).append(sample.total_ms)
    return {
        kind: summary
        for kind, values in sorted(grouped.items())
        if (summary := summarize_latencies(values)) is not None
    }


def downtime_gap(health: MessagingHealth, *, now: datetime) -> timedelta | None:
    """Return how long the gateway was not listening, when that is knowable.

    A bot that has never polled has no gap to report rather than an infinite
    one: there is no earlier moment when a message could have been missed.

    Args:
        health: Content-free messaging state.
        now: Aware current instant.

    Returns:
        The elapsed time since the last successful poll, or None.
    """

    if health.last_poll_at is None:
        return None
    return max(now - health.last_poll_at, timedelta(0))


__all__ = [
    "DEFAULT_PAIRING_LIFETIME",
    "INBOX_REQUEUE_COMMAND",
    "OUTBOX_REQUEUE_COMMAND",
    "TELEGRAM_UPDATE_RETENTION",
    "MessagingAdministration",
    "MessagingOperationsSnapshot",
    "PairingCode",
    "TextLatencyReport",
    "downtime_gap",
    "requeue_command_key",
]
