"""Channel-independent contracts for routing one work unit to a specialist.

The dispatcher returns one compact typed outcome whichever specialist runs.  A
transport can therefore persist and render the result without inspecting a
LangGraph state mapping or learning which nodes produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from money_tracker.domain.errors import DomainError
from money_tracker.workflows.messages import CallbackAction, ChannelMessage
from money_tracker.workflows.review import ReviewDecision
from money_tracker.workflows.routing import RoutingContext, RoutingDecision
from money_tracker.workflows.tools import ReadTool


class DispatchStatus(StrEnum):
    """Terminal or paused status returned by the workflow dispatcher."""

    ROUTED = "routed"
    DRAFTED = "drafted"
    AWAITING_CATEGORY_APPROVAL = "awaiting_category_approval"
    ANSWERED = "answered"
    CORRECTED = "corrected"
    VOIDED = "voided"
    APPROVED = "approved"
    REUSED = "reused"
    CANCELLED = "cancelled"
    CLARIFICATION = "clarification"


class DispatchReason(StrEnum):
    """Dispatcher-level reasons not owned by an individual specialist."""

    REVIEW_DECISION_REQUIRED = "review_decision_required"
    REVIEW_NOT_PENDING = "review_not_pending"
    MOVEMENT_TARGET_REQUIRED = "movement_target_required"


@dataclass(frozen=True, slots=True)
class PendingMovementAction:
    """Trusted correlation from a pending interface action to a movement.

    Attributes:
        action_id: Stable action reference carried by a callback.
        movement_id: Stable movement reference resolved by the runtime.
    """

    action_id: UUID
    movement_id: UUID


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Trusted owner scope and correlations used during dispatch.

    Attributes:
        profile_id: Owner profile allowed to read or mutate finance data.  None
            permits routing-only diagnostics while preventing specialist access.
        routing: Repository-derived facts bounding model routing decisions.
        movement_actions: Pending callback references mapped to their exact
            movement.  Free-text corrections use ``routing``'s latest movement.
    """

    profile_id: UUID | None = None
    routing: RoutingContext = RoutingContext()
    movement_actions: tuple[PendingMovementAction, ...] = ()

    def movement_for_action(self, action_id: UUID) -> UUID | None:
        """Resolve one callback action without guessing a movement.

        Args:
            action_id: Stable pending action reference from the message.

        Returns:
            The correlated movement, or None when the action is stale or foreign.
        """

        return next(
            (
                item.movement_id
                for item in self.movement_actions
                if item.action_id == action_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Typed transport-neutral result of one routed work unit.

    Attributes:
        routing: Accepted root routing decision.
        status: Terminal or paused workflow result.
        expense_id: Stored, corrected, or voided movement reference.
        proposal_id: Pending category proposal reference.
        settled_category_id: Category selected by an owner review.
        executed_tool: Read-only tool whose output supplied the answer facts.
        facts: JSON-compatible facts produced by the admitted read tool.
        clarification_reason: Stable reason selecting a later presentation.
    """

    routing: RoutingDecision
    status: DispatchStatus
    expense_id: UUID | None = None
    proposal_id: UUID | None = None
    settled_category_id: UUID | None = None
    executed_tool: ReadTool | None = None
    facts: dict[str, Any] | None = None
    clarification_reason: str | None = None


_NAMED_CATEGORY = re.compile(r"^usa\s+la\s+categoria\s+(.+)$", re.IGNORECASE)


def review_decision_from_message(message: ChannelMessage) -> ReviewDecision | None:
    """Interpret a correlated category follow-up as one explicit review branch.

    Callback actions are already typed by the channel boundary.  The small text
    grammar exists for the Italian acceptance examples and deliberately accepts
    only direct branch statements; unrelated prose is not guessed into a write.

    Args:
        message: Correlated normalized work unit routed to category follow-up.

    Returns:
        The validated decision, or None when the message does not state one.
    """

    action = message.callback_action
    if action is not None:
        name = (
            message.normalized_text
            if action is CallbackAction.USE_NAMED_EXISTING
            else None
        )
        try:
            return ReviewDecision(action, name)
        except DomainError:
            return None

    text = message.normalized_text
    folded = text.casefold().replace("’", "'")
    fixed = {
        "approva": CallbackAction.APPROVE_CATEGORY,
        "usa una delle categorie esistenti": CallbackAction.USE_BEST_EXISTING,
        "proponine un'altra": CallbackAction.REPROPOSE_CATEGORY,
        "annulla": CallbackAction.CANCEL_REVIEW,
    }
    if folded in fixed:
        return ReviewDecision(fixed[folded])
    match = _NAMED_CATEGORY.fullmatch(text)
    if match is None:
        return None
    return ReviewDecision(CallbackAction.USE_NAMED_EXISTING, match.group(1))


__all__ = [
    "DispatchContext",
    "DispatchOutcome",
    "DispatchReason",
    "DispatchStatus",
    "PendingMovementAction",
    "review_decision_from_message",
]
