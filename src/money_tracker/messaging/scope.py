"""Non-secret bot persistence scope shared by every messaging process.

The scope answers one question: which bot's durable state does this process
own. It is deliberately separate from the Telegram credential settings, because
a process that only reads or requeues messaging rows must never be required to
hold a Bot API token in order to know which rows are its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from money_tracker.messaging.contracts import BotEnvironment, validate_bot_key

TELEGRAM_ENVIRONMENT_ENV = "MONEY_TRACKER_TELEGRAM_ENVIRONMENT"


@dataclass(frozen=True, slots=True)
class MessagingScope:
    """One bot environment and the stable key that scopes its durable rows.

    Attributes:
        environment: Explicit production or test bot selection.
        bot_key: Stable non-secret persistence scope derived from it.
    """

    environment: BotEnvironment
    bot_key: str

    def __post_init__(self) -> None:
        """Reject a key that no messaging table would accept."""

        validate_bot_key(self.bot_key)

    @classmethod
    def for_environment(cls, environment: BotEnvironment) -> MessagingScope:
        """Derive the persistence scope owned by one bot environment.

        Args:
            environment: Explicitly selected bot environment.

        Returns:
            The environment and its derived stable bot key.
        """

        return cls(
            environment=environment,
            bot_key=f"telegram-{environment.value}",
        )

    @classmethod
    def from_env(cls) -> MessagingScope:
        """Select exactly one bot environment without reading a credential.

        Returns:
            The scope every messaging read and requeue is confined to.

        Raises:
            RuntimeError: If no explicit environment is configured.
        """

        raw = os.getenv(TELEGRAM_ENVIRONMENT_ENV, "").strip()
        try:
            environment = BotEnvironment(raw)
        except ValueError as error:
            raise RuntimeError(
                f"{TELEGRAM_ENVIRONMENT_ENV} must be explicitly 'production' or 'test'"
            ) from error
        return cls.for_environment(environment)


__all__ = ["TELEGRAM_ENVIRONMENT_ENV", "MessagingScope"]
