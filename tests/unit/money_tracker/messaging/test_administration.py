"""Behavior tests for operator-facing pairing code generation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from money_tracker.messaging import (
    DEFAULT_PAIRING_LIFETIME,
    TELEGRAM_UPDATE_RETENTION,
    BotEnvironment,
    DeadLetter,
    MessagingAdministration,
    MessagingHealth,
    OutboxDeadLetter,
    downtime_gap,
)

NOW = datetime(2026, 9, 9, 8, tzinfo=UTC)
PROFILE = UUID("00000000-0000-0000-0000-0000000000aa")


class _Clock:
    """Fixed aware administration clock."""

    def now(self) -> datetime:
        """Return the deterministic test instant."""

        return NOW


class _Ids:
    """Deterministic identifier provider."""

    def new(self) -> UUID:
        """Return a fresh identifier."""

        return uuid4()


class _Repository:
    """Records what the administration service asked persistence to store."""

    def __init__(self) -> None:
        self.stored: list[dict[str, Any]] = []
        self.ensured: list[tuple[str, Any]] = []
        self.limits: list[int] = []
        self.outbox_limits: list[int] = []
        self.replayed: list[tuple[str, UUID, datetime]] = []
        self.resent: list[tuple[str, UUID, datetime]] = []
        self.latencies = (9_999, 1_000, 2_000, 3_000, 4_000)

    def ensure_bot(self, bot_key: str, **values: Any) -> None:
        """Record the persistence scope the pairing code needs."""

        self.ensured.append((bot_key, values["environment"]))

    def create_pairing_code(self, **values: Any) -> None:
        """Record one stored pairing code."""

        if not self.ensured:
            raise LookupError("bot key is not initialized")
        self.stored.append(values)

    def health(self, bot_key: str) -> MessagingHealth:
        """Return the recorded health snapshot."""

        return _health()

    def dead_letters(self, bot_key: str, *, limit: int) -> tuple[DeadLetter, ...]:
        """Return one synthetic failed item and record the requested limit."""

        self.limits.append(limit)
        return (
            DeadLetter(
                id=UUID("00000000-0000-0000-0000-0000000000d1"),
                update_id=7,
                source_reference="telegram:telegram-test:7",
                attempt_count=5,
                replay_count=0,
                last_error_code="model_unavailable",
                accepted_at=NOW,
            ),
        )

    def replay_inbox(self, bot_key: str, inbox_id: UUID, *, now: datetime) -> None:
        """Record one replay request."""

        self.replayed.append((bot_key, inbox_id, now))

    def outbox_dead_letters(
        self, bot_key: str, *, limit: int
    ) -> tuple[OutboxDeadLetter, ...]:
        """Return one synthetic delivery failure and record the limit."""

        self.outbox_limits.append(limit)
        return (
            OutboxDeadLetter(
                id=UUID("00000000-0000-0000-0000-0000000000d2"),
                response_kind="draft",
                attempt_count=5,
                replay_count=1,
                last_error_code="telegram_unavailable",
                created_at=NOW,
            ),
        )

    def replay_outbox(self, bot_key: str, outbox_id: UUID, *, now: datetime) -> None:
        """Record one manual delivery retry request."""

        self.resent.append((bot_key, outbox_id, now))

    def text_delivery_latencies(
        self, bot_key: str, *, since: datetime, limit: int
    ) -> tuple[int, ...]:
        """Return synthetic successful text workflow durations."""

        return self.latencies[:limit]


def _service(repository: _Repository) -> MessagingAdministration:
    """Bind the production service to the recording boundary."""

    return MessagingAdministration(
        repository,  # type: ignore[arg-type]
        clock=_Clock(),
        ids=_Ids(),
    )


def test_only_the_hash_of_a_generated_code_is_stored() -> None:
    """Keep the secret out of persistence while still being able to check it."""

    repository = _Repository()

    pairing = _service(repository).create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
    )

    stored = repository.stored[0]
    assert stored["code_hash"] == hashlib.sha256(pairing.code.encode()).hexdigest()
    assert pairing.code not in str(stored)
    assert stored["profile_id"] == PROFILE
    assert stored["bot_key"] == "telegram-test"


def test_a_code_expires_after_the_requested_lifetime() -> None:
    """Bound how long a one-use secret stays acceptable."""

    repository = _Repository()

    pairing = _service(repository).create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
        lifetime=timedelta(minutes=2),
    )

    assert pairing.expires_at == NOW + timedelta(minutes=2)
    assert repository.stored[0]["expires_at"] == pairing.expires_at


def test_the_default_lifetime_is_short() -> None:
    """Prefer regenerating a code over leaving one live for a long time."""

    repository = _Repository()

    pairing = _service(repository).create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
    )

    assert pairing.expires_at == NOW + DEFAULT_PAIRING_LIFETIME
    assert DEFAULT_PAIRING_LIFETIME <= timedelta(minutes=15)


def test_two_codes_are_never_the_same() -> None:
    """Generate unpredictable codes rather than a guessable sequence."""

    service = _service(_Repository())

    first = service.create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
    )
    second = service.create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
    )

    assert first.code != second.code
    assert len(first.code) >= 20


def test_a_code_that_expires_immediately_is_refused() -> None:
    """Reject a lifetime that would make pairing impossible or unbounded."""

    with pytest.raises(ValueError, match="positive lifetime"):
        _service(_Repository()).create_pairing_code(
            bot_key="telegram-test",
            profile_id=PROFILE,
            environment=BotEnvironment.TEST,
            lifetime=timedelta(0),
        )


def _health(last_poll_at: datetime | None = NOW) -> MessagingHealth:
    """Build one content-free health snapshot."""

    return MessagingHealth(
        bot_key="telegram-test",
        next_offset=12,
        pending_inbox=1,
        retrying_inbox=0,
        inbox_dead_letters=2,
        pending_outbox=0,
        retrying_outbox=0,
        outbox_dead_letters=0,
        active_actions=1,
        last_observed_at=NOW,
        last_poll_at=last_poll_at,
        oldest_inbox_at=NOW,
        oldest_outbox_at=None,
    )


def test_health_is_reported_without_message_content() -> None:
    """Give an operator counts and offsets, never what the owner wrote."""

    health = _service(_Repository()).health("telegram-test")

    assert health.inbox_dead_letters == 2
    assert health.next_offset == 12


def test_dead_letters_carry_identity_reason_and_no_content() -> None:
    """Show exactly what deciding on a replay needs."""

    repository = _Repository()

    items = _service(repository).dead_letters("telegram-test")

    assert items[0].last_error_code == "model_unavailable"
    assert items[0].source_reference == "telegram:telegram-test:7"
    assert repository.limits == [50]


def test_replay_names_the_selected_item_and_the_instant() -> None:
    """Return one chosen dead letter to the queue, not the whole backlog."""

    repository = _Repository()
    inbox_id = UUID("00000000-0000-0000-0000-0000000000d1")

    _service(repository).replay("telegram-test", inbox_id)

    assert repository.replayed == [("telegram-test", inbox_id, NOW)]


def test_outbox_dead_letters_expose_only_safe_delivery_metadata() -> None:
    """List delivery identity and counters without message or chat content."""

    repository = _Repository()

    items = _service(repository).outbox_dead_letters("telegram-test")

    assert items[0].response_kind == "draft"
    assert items[0].last_error_code == "telegram_unavailable"
    assert repository.outbox_limits == [50]
    assert "Risposta" not in str(items[0])


def test_resend_names_one_selected_outbox_dead_letter() -> None:
    """Start a new bounded delivery cycle only for the chosen message."""

    repository = _Repository()
    outbox_id = UUID("00000000-0000-0000-0000-0000000000d2")

    _service(repository).resend("telegram-test", outbox_id)

    assert repository.resent == [("telegram-test", outbox_id, NOW)]


def test_text_latency_excludes_warmup_and_uses_nearest_rank_p95() -> None:
    """Summarize only warm durations without exposing individual messages."""

    report = _service(_Repository()).text_latency_report(
        "telegram-test",
        since=NOW - timedelta(minutes=1),
    )

    assert report.sample_count == 5
    assert report.warmup_excluded == 1
    assert report.warm_sample_count == 4
    assert report.warm_p95_ms == 4_000
    assert report.maximum_ms == 4_000


def test_text_latency_does_not_report_p95_from_warmup_alone() -> None:
    """Avoid presenting one cold observation as a warm distribution."""

    repository = _Repository()
    repository.latencies = (9_999,)

    report = _service(repository).text_latency_report(
        "telegram-test",
        since=NOW - timedelta(minutes=1),
    )

    assert report.sample_count == 1
    assert report.warmup_excluded == 1
    assert report.warm_sample_count == 0
    assert report.warm_p95_ms is None
    assert report.maximum_ms is None


def test_a_bot_that_never_polled_reports_no_downtime() -> None:
    """Report no gap rather than an infinite one before the first poll."""

    assert downtime_gap(_health(last_poll_at=None), now=NOW) is None


def test_a_long_outage_exceeds_the_telegram_update_retention() -> None:
    """Detect the window in which Telegram may have discarded updates."""

    short = downtime_gap(_health(), now=NOW + timedelta(hours=23))
    long = downtime_gap(_health(), now=NOW + timedelta(hours=30))

    assert short is not None and short <= TELEGRAM_UPDATE_RETENTION
    assert long is not None and long > TELEGRAM_UPDATE_RETENTION


def test_a_clock_that_moved_backwards_reports_no_negative_gap() -> None:
    """Never turn a corrected clock into a nonsensical negative outage."""

    gap = downtime_gap(_health(), now=NOW - timedelta(hours=1))

    assert gap == timedelta(0)


def test_pairing_creates_the_bot_scope_it_needs() -> None:
    """Let pairing be the first thing done with a bot, before the gateway runs."""

    repository = _Repository()

    _service(repository).create_pairing_code(
        bot_key="telegram-test",
        profile_id=PROFILE,
        environment=BotEnvironment.TEST,
    )

    assert repository.ensured == [("telegram-test", BotEnvironment.TEST)]
    assert repository.stored[0]["bot_key"] == "telegram-test"
