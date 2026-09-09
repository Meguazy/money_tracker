"""Owner-facing rendering of typed workflow outcomes.

Presentation is deliberately its own package. A renderer decides only what the
owner reads and which controls exist; it never persists, dispatches, calls a
model, or talks to a transport, so the same typed outcome can later feed the
dashboard without moving any of this logic.
"""

from money_tracker.presentation.telegram import (
    MovementView,
    OwnerResponse,
    ResponseKind,
    ResponseTarget,
    TelegramResponseRenderer,
    action_code,
    action_for_code,
    pairing_confirmation,
)

__all__ = [
    "MovementView",
    "OwnerResponse",
    "ResponseKind",
    "ResponseTarget",
    "TelegramResponseRenderer",
    "action_code",
    "action_for_code",
    "pairing_confirmation",
]
