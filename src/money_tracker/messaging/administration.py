"""Operator-facing messaging operations that expose no private content.

AD-018 keeps administration separate from the gateway and the worker. The
operations here answer a person at a terminal rather than a durable work item,
which is why they may read live state that a logical response never can.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from money_tracker.messaging.contracts import (
    BotEnvironment,
    DeadLetter,
    MessagingHealth,
    OutboxDeadLetter,
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
    """Content-free warm text delivery latency summary.

    Attributes:
        sample_count: Successful text workflows found in the selected window.
        warmup_excluded: Number of earliest successes removed before p95.
        warm_sample_count: Samples used by the warm p95.
        warm_p95_ms: Nearest-rank warm p95, or None without a warm sample.
        maximum_ms: Slowest warm sample, or None without a warm sample.
    """

    sample_count: int
    warmup_excluded: int
    warm_sample_count: int
    warm_p95_ms: int | None
    maximum_ms: int | None


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

    def replay(self, bot_key: str, inbox_id: UUID) -> None:
        """Return one dead letter to the queue with its identity intact.

        Replay preserves the source reference, so the finance command keys the
        work unit already derived stay the same. A replayed item that already
        committed a mutation therefore returns the first result rather than
        applying a second one, which is what makes this safe to offer at all.

        Args:
            bot_key: Bot and environment persistence scope selected by config.
            inbox_id: Dead letter an operator chose to retry.

        Raises:
            MessagingConflictError: If the item is not a dead letter.
        """

        # An operator reaching for replay means something already failed
        # five times, so the action is recorded as a deliberate intervention.
        _LOGGER.warning("messaging.inbox.replayed", bot_key=bot_key, inbox_id=inbox_id)
        self._repository.replay_inbox(bot_key, inbox_id, now=self._clock.now())

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

        _LOGGER.warning("messaging.outbox.resent", bot_key=bot_key, outbox_id=outbox_id)
        self._repository.replay_outbox(bot_key, outbox_id, now=self._clock.now())

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

        durations = self._repository.text_delivery_latencies(
            bot_key,
            since=since,
            limit=limit,
        )
        warmup_excluded = min(1, len(durations))
        warm = durations[warmup_excluded:]
        if not warm:
            return TextLatencyReport(
                sample_count=len(durations),
                warmup_excluded=warmup_excluded,
                warm_sample_count=0,
                warm_p95_ms=None,
                maximum_ms=None,
            )
        ordered = sorted(warm)
        rank = math.ceil(0.95 * len(ordered))
        return TextLatencyReport(
            sample_count=len(durations),
            warmup_excluded=warmup_excluded,
            warm_sample_count=len(warm),
            warm_p95_ms=ordered[rank - 1],
            maximum_ms=ordered[-1],
        )


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
    "TELEGRAM_UPDATE_RETENTION",
    "MessagingAdministration",
    "PairingCode",
    "TextLatencyReport",
    "downtime_gap",
]
