"""Behavior tests for operator-facing pairing code generation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from money_tracker.messaging import (
    DEFAULT_PAIRING_LIFETIME,
    MessagingAdministration,
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

    def create_pairing_code(self, **values: Any) -> None:
        """Record one stored pairing code."""

        self.stored.append(values)


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
    )

    assert pairing.expires_at == NOW + DEFAULT_PAIRING_LIFETIME
    assert DEFAULT_PAIRING_LIFETIME <= timedelta(minutes=15)


def test_two_codes_are_never_the_same() -> None:
    """Generate unpredictable codes rather than a guessable sequence."""

    service = _service(_Repository())

    first = service.create_pairing_code(bot_key="telegram-test", profile_id=PROFILE)
    second = service.create_pairing_code(bot_key="telegram-test", profile_id=PROFILE)

    assert first.code != second.code
    assert len(first.code) >= 20


def test_a_code_that_expires_immediately_is_refused() -> None:
    """Reject a lifetime that would make pairing impossible or unbounded."""

    with pytest.raises(ValueError, match="positive lifetime"):
        _service(_Repository()).create_pairing_code(
            bot_key="telegram-test",
            profile_id=PROFILE,
            lifetime=timedelta(0),
        )
