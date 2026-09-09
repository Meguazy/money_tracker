"""Operator-facing messaging operations that expose no private content.

AD-018 keeps administration separate from the gateway and the worker. The
operations here answer a person at a terminal rather than a durable work item,
which is why they may read live state that a logical response never can.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from money_tracker.messaging.ports import MessagingRepository

DEFAULT_PAIRING_LIFETIME = timedelta(minutes=10)
_CODE_BYTES = 16


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
        lifetime: timedelta = DEFAULT_PAIRING_LIFETIME,
    ) -> PairingCode:
        """Generate and store one short-lived, one-use pairing code.

        Any earlier unused code for the same bot is revoked, so exactly one
        code can ever be live. A bot that already has an owner is refused
        rather than offered a second binding.

        Args:
            bot_key: Bot and environment persistence scope.
            profile_id: Profile the resulting owner binding will own.
            lifetime: How long the code stays acceptable.

        Returns:
            The clear code and its expiry, for one-time operator display.

        Raises:
            ValueError: If the lifetime is not positive.
            MessagingConflictError: If the bot already has an owner binding.
        """

        if lifetime <= timedelta(0):
            raise ValueError("a pairing code must have a positive lifetime")
        now = self._clock.now()
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
        return PairingCode(code=code, expires_at=expires_at)


__all__ = [
    "DEFAULT_PAIRING_LIFETIME",
    "MessagingAdministration",
    "PairingCode",
]
