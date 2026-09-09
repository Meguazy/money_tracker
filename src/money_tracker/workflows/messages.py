"""Channel-agnostic inbound work unit consumed by the root router.

The router must behave identically whatever transport delivered a message, so
this module owns the normalized shape rather than any transport adapter. It
carries no transport credential, no raw update payload, and no media bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from money_tracker.domain.errors import DomainError


class SupportedCommand(StrEnum):
    """Deterministic channel command recognized without a model.

    Attributes:
        PAIR: Bind the sole owner identity to a one-use pairing code.
        HELP: Describe the supported interactions.
        STATUS: Report local service and backlog health.
    """

    PAIR = "pair"
    HELP = "help"
    STATUS = "status"


class CallbackAction(StrEnum):
    """Explicit owner decision delivered by an interface control.

    Every value corresponds to a branch the product baseline requires, so an
    unrecognized callback is a routing failure rather than free text.

    Attributes:
        APPROVE_CATEGORY: Create the proposed category and post atomically.
        USE_BEST_EXISTING: Reuse the best adequate existing category instead.
        USE_NAMED_EXISTING: Resolve an owner-named existing category.
        REPROPOSE_CATEGORY: Reject the candidate and request a distinct one.
        CANCEL_REVIEW: Abandon the draft without creating anything.
        POST_EXPENSE: Record a draft the owner confirmed, making it count.
        MODIFY_EXPENSE: Replace the editable fields of a movement.
        UNDO_EXPENSE: Void a movement without deleting it.
    """

    APPROVE_CATEGORY = "approve_category"
    USE_BEST_EXISTING = "use_best_existing"
    USE_NAMED_EXISTING = "use_named_existing"
    REPROPOSE_CATEGORY = "repropose_category"
    CANCEL_REVIEW = "cancel_review"
    POST_EXPENSE = "post_expense"
    MODIFY_EXPENSE = "modify_expense"
    UNDO_EXPENSE = "undo_expense"


CATEGORY_REVIEW_ACTIONS = frozenset(
    {
        CallbackAction.APPROVE_CATEGORY,
        CallbackAction.USE_BEST_EXISTING,
        CallbackAction.USE_NAMED_EXISTING,
        CallbackAction.REPROPOSE_CATEGORY,
        CallbackAction.CANCEL_REVIEW,
    }
)
"""Callback actions owned by the paused owner category review."""


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """One normalized inbound unit of work for the root router.

    Attributes:
        source_reference: Stable non-sensitive reference to the durable inbound
            record, used for audit and idempotency correlation.
        received_at: Aware timestamp the channel assigned to the message, which
            anchors resolution of relative Italian dates.
        text: Owner text when present, including an image caption.
        callback_action: Explicit decision when an interface control was used.
        pending_action_id: Stable pending-action reference a callback answers.
        reply_to_action_id: Stable pending-action reference a free-text reply
            addresses, when the channel reports one.
        has_image: Whether the message carries image media.
    """

    source_reference: str
    received_at: datetime
    text: str | None = None
    callback_action: CallbackAction | None = None
    pending_action_id: UUID | None = None
    reply_to_action_id: UUID | None = None
    has_image: bool = False

    def __post_init__(self) -> None:
        """Reject a message the router could not process deterministically.

        Raises:
            DomainError: If the source reference is blank, the timestamp is
                naive, or the message carries no processable content.
        """

        if not self.source_reference.strip():
            raise DomainError("channel message requires a source reference")
        if self.received_at.tzinfo is None or (self.received_at.utcoffset() is None):
            raise DomainError("channel message timestamp must be time-zone aware")
        if self.text is None and self.callback_action is None and not self.has_image:
            raise DomainError("channel message carries no processable content")

    @property
    def normalized_text(self) -> str:
        """Return the owner text with collapsed whitespace, possibly empty."""

        if self.text is None:
            return ""
        return " ".join(self.text.split())
