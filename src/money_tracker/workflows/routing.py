"""Typed root routing: deterministic pre-routing and classification guards.

Routing has two halves with different reliability requirements.

Deterministic pre-routing handles everything the channel already states without
interpretation: an explicit interface decision, an attached image, a supported
command, and empty input. The evaluation matrix requires this half to be 100%
reproducible, so it never consults a model and never depends on wording.

The remaining free text is classified by a model, and `accept_classification`
re-imposes deterministic policy on the result. A classification cannot invent a
correlation target, continue a review that is not pending, correct a movement
that does not exist, or pass an uncalibrated confidence bar. Each veto falls
back to clarification, which is the only safe default: it asks instead of
writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from money_tracker.workflows.contracts import RouteClassification
from money_tracker.workflows.intents import (
    DETERMINISTIC_ONLY_INTENTS,
    WorkflowIntent,
)
from money_tracker.workflows.messages import (
    CATEGORY_REVIEW_ACTIONS,
    CallbackAction,
    ChannelMessage,
    SupportedCommand,
)

_COMMAND_PREFIX = "/"


class RoutingSource(StrEnum):
    """Which half of the router produced a decision.

    Attributes:
        DETERMINISTIC: Assigned from stated channel facts without a model.
        MODEL: Assigned by a validated model classification.
    """

    DETERMINISTIC = "deterministic"
    MODEL = "model"


class RoutingReason(StrEnum):
    """Stable machine-readable justification for one routing decision.

    These codes are safe to log, trace, and assert on. They deliberately carry
    no owner text, so routing telemetry cannot leak message content.

    Attributes:
        CALLBACK_ACTION: An interface control stated the decision.
        UNCORRELATED_CALLBACK: A callback arrived without a pending action.
        IMAGE_ATTACHMENT: The message carries image media for review.
        SUPPORTED_COMMAND: The text names a supported command.
        UNKNOWN_COMMAND: The text names no supported command.
        EMPTY_INPUT: No image, callback, or non-blank text was present.
        MODEL_CLASSIFICATION: A validated classification was accepted.
        LOW_CONFIDENCE: The classification did not meet the confidence bar.
        NO_PENDING_REVIEW: No paused category review exists to continue.
        AMBIGUOUS_PENDING_REVIEW: Several reviews are pending and uncorrelated.
        NO_CORRECTABLE_MOVEMENT: No movement is available to correct or void.
        INVALID_MODEL_OUTPUT: The model could not satisfy the routing schema
            within its retry budget.
    """

    CALLBACK_ACTION = "callback_action"
    UNCORRELATED_CALLBACK = "uncorrelated_callback"
    IMAGE_ATTACHMENT = "image_attachment"
    SUPPORTED_COMMAND = "supported_command"
    UNKNOWN_COMMAND = "unknown_command"
    EMPTY_INPUT = "empty_input"
    MODEL_CLASSIFICATION = "model_classification"
    LOW_CONFIDENCE = "low_confidence"
    NO_PENDING_REVIEW = "no_pending_review"
    AMBIGUOUS_PENDING_REVIEW = "ambiguous_pending_review"
    NO_CORRECTABLE_MOVEMENT = "no_correctable_movement"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Trusted runtime facts that bound a model routing decision.

    The worker derives every field from repositories inside the owner's profile
    scope. A model cannot supply them, so a classification can be vetoed
    against reality instead of being taken at its word.

    Attributes:
        pending_category_review_ids: Stable pending-action references for
            category reviews currently awaiting this owner.
        latest_correctable_movement_id: Most recent movement the owner may
            correct or void, or None when no such movement exists.
    """

    pending_category_review_ids: tuple[UUID, ...] = ()
    latest_correctable_movement_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The single destination selected for one inbound message.

    Attributes:
        intent: Selected workflow destination.
        source: Which half of the router decided.
        reason: Stable justification code safe for logs and traces.
        command: Supported command when the intent is a channel command.
        callback_action: Owner decision when an interface control was used.
        pending_action_id: Correlated pending action for a review follow-up.
        target_movement_id: Correlated movement for a correction or undo.
        confidence: Model self-report when the model decided, else None.
    """

    intent: WorkflowIntent
    source: RoutingSource
    reason: RoutingReason
    command: SupportedCommand | None = None
    callback_action: CallbackAction | None = None
    pending_action_id: UUID | None = None
    target_movement_id: UUID | None = None
    confidence: float | None = None


def parse_command(text: str) -> SupportedCommand | None:
    """Recognize a supported command in owner text.

    A channel may append a bot mention to a command in a shared conversation,
    so a single `@suffix` is stripped before matching. Matching is otherwise
    exact and case-insensitive; no command is inferred from prose.

    Args:
        text: Owner text, already whitespace-normalized or raw.

    Returns:
        The recognized command, or None when the text names none.
    """

    candidate = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not candidate.startswith(_COMMAND_PREFIX):
        return None
    name = candidate[len(_COMMAND_PREFIX) :].split("@", maxsplit=1)[0].casefold()
    try:
        return SupportedCommand(name)
    except ValueError:
        return None


def pre_route(message: ChannelMessage) -> RoutingDecision | None:
    """Decide the route from stated channel facts alone.

    Precedence is fixed and total. An explicit interface decision outranks
    everything because the owner already chose. Image media outranks its own
    caption so that a caption can never turn a review-only receipt into a
    posting path. A command outranks free text. Blank remaining input is
    clarified rather than sent to a model.

    Args:
        message: Normalized inbound work unit.

    Returns:
        A deterministic decision, or None when free text still requires model
        classification.
    """

    if message.callback_action is not None:
        return _route_callback(message.callback_action, message.pending_action_id)
    if message.has_image:
        return RoutingDecision(
            intent=WorkflowIntent.RECEIPT,
            source=RoutingSource.DETERMINISTIC,
            reason=RoutingReason.IMAGE_ATTACHMENT,
        )
    text = message.normalized_text
    if not text:
        return clarify(RoutingReason.EMPTY_INPUT, RoutingSource.DETERMINISTIC)
    if text.startswith(_COMMAND_PREFIX):
        command = parse_command(text)
        if command is None:
            return clarify(RoutingReason.UNKNOWN_COMMAND, RoutingSource.DETERMINISTIC)
        return RoutingDecision(
            intent=WorkflowIntent.COMMAND,
            source=RoutingSource.DETERMINISTIC,
            reason=RoutingReason.SUPPORTED_COMMAND,
            command=command,
        )
    return None


def accept_classification(
    classification: RouteClassification,
    *,
    message: ChannelMessage,
    context: RoutingContext,
    minimum_confidence: float,
) -> RoutingDecision:
    """Re-impose deterministic policy on one model classification.

    The caller must state `minimum_confidence` explicitly. No production
    routing threshold is promoted yet: the value is calibrated on the pinned
    routing dataset and recorded in the experiment decision log before any
    runtime uses it, so this function refuses to imply a default.

    Args:
        classification: Parsed and schema-valid model output.
        message: The message that produced the classification.
        context: Trusted runtime facts used to veto impossible routes.
        minimum_confidence: Inclusive confidence bar in the unit interval.

    Returns:
        The accepted decision, or a clarification decision when policy vetoed
        the requested route.

    Raises:
        ValueError: If the confidence bar is outside the unit interval, or the
            classification requests a deterministic-only route.
    """

    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum confidence must lie in the unit interval")
    if classification.intent in DETERMINISTIC_ONLY_INTENTS:
        raise ValueError(
            f"intent {classification.intent.value} is assigned deterministically"
        )
    if classification.confidence < minimum_confidence:
        return clarify(
            RoutingReason.LOW_CONFIDENCE,
            RoutingSource.MODEL,
            confidence=classification.confidence,
        )
    if classification.intent is WorkflowIntent.CATEGORY_FOLLOW_UP:
        return _route_category_follow_up(classification, message, context)
    if classification.intent in {WorkflowIntent.CORRECTION, WorkflowIntent.UNDO}:
        return _route_movement_change(classification, context)
    return RoutingDecision(
        intent=classification.intent,
        source=RoutingSource.MODEL,
        reason=RoutingReason.MODEL_CLASSIFICATION,
        confidence=classification.confidence,
    )


def _route_callback(
    action: CallbackAction,
    pending_action_id: UUID | None,
) -> RoutingDecision:
    """Route an explicit owner decision to the workflow that owns it.

    A callback that names no pending action cannot be correlated to the review
    or movement it answers, so it is clarified instead of applied to whatever
    happens to be pending.
    """

    if pending_action_id is None:
        return clarify(RoutingReason.UNCORRELATED_CALLBACK, RoutingSource.DETERMINISTIC)
    if action in CATEGORY_REVIEW_ACTIONS:
        intent = WorkflowIntent.CATEGORY_FOLLOW_UP
    elif action in {CallbackAction.MODIFY_EXPENSE, CallbackAction.POST_EXPENSE}:
        intent = WorkflowIntent.CORRECTION
    else:
        intent = WorkflowIntent.UNDO
    return RoutingDecision(
        intent=intent,
        source=RoutingSource.DETERMINISTIC,
        reason=RoutingReason.CALLBACK_ACTION,
        callback_action=action,
        pending_action_id=pending_action_id,
    )


def _route_category_follow_up(
    classification: RouteClassification,
    message: ChannelMessage,
    context: RoutingContext,
) -> RoutingDecision:
    """Continue a paused review only when exactly one is correlated.

    A follow-up must name the review it answers. An explicit reply reference
    wins; a single pending review needs none; several uncorrelated reviews are
    ambiguous and are clarified rather than resolved by guesswork.
    """

    pending = context.pending_category_review_ids
    if not pending:
        return clarify(
            RoutingReason.NO_PENDING_REVIEW,
            RoutingSource.MODEL,
            confidence=classification.confidence,
        )
    if message.reply_to_action_id is not None:
        if message.reply_to_action_id not in pending:
            return clarify(
                RoutingReason.NO_PENDING_REVIEW,
                RoutingSource.MODEL,
                confidence=classification.confidence,
            )
        correlated = message.reply_to_action_id
    elif len(pending) == 1:
        correlated = pending[0]
    else:
        return clarify(
            RoutingReason.AMBIGUOUS_PENDING_REVIEW,
            RoutingSource.MODEL,
            confidence=classification.confidence,
        )
    return RoutingDecision(
        intent=WorkflowIntent.CATEGORY_FOLLOW_UP,
        source=RoutingSource.MODEL,
        reason=RoutingReason.MODEL_CLASSIFICATION,
        pending_action_id=correlated,
        confidence=classification.confidence,
    )


def _route_movement_change(
    classification: RouteClassification,
    context: RoutingContext,
) -> RoutingDecision:
    """Correct or void only a movement the runtime actually resolved."""

    target = context.latest_correctable_movement_id
    if target is None:
        return clarify(
            RoutingReason.NO_CORRECTABLE_MOVEMENT,
            RoutingSource.MODEL,
            confidence=classification.confidence,
        )
    return RoutingDecision(
        intent=classification.intent,
        source=RoutingSource.MODEL,
        reason=RoutingReason.MODEL_CLASSIFICATION,
        target_movement_id=target,
        confidence=classification.confidence,
    )


def clarify(
    reason: RoutingReason,
    source: RoutingSource,
    *,
    confidence: float | None = None,
) -> RoutingDecision:
    """Build the safe fallback decision that asks instead of acting.

    Every routing veto and every boundary failure ends here, so the safe
    default is one named operation rather than a shape each caller rebuilds.

    Args:
        reason: Stable justification code for the fallback.
        source: Which half of the router reached it.
        confidence: Model self-report when a classification was involved.

    Returns:
        A clarification decision that mutates nothing.
    """

    return RoutingDecision(
        intent=WorkflowIntent.CLARIFICATION,
        source=source,
        reason=reason,
        confidence=confidence,
    )
