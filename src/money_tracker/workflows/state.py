"""Versioned root-router workflow state and its conversions.

Step 3 requires graph state to hold only small serializable values and stable
references, with domain data left in repositories. This module is the single
place that decides what enters a checkpoint, so that requirement is enforceable
by inspection and by test rather than by convention.

Every value is a JSON primitive or a list of primitives. Identifiers are stored
as strings and timestamps as ISO text, which keeps the state safe under the
strict msgpack serializer the checkpointer is configured with and survives a
round trip through PostgreSQL unchanged. No domain aggregate, money value,
media reference, service object, or credential is representable here.

Owner message text is present because the classification step needs it. That
text is not new exposure: the durable channel inbox already stores the raw
update, and `source_reference` points at that record.

Conversions are exhaustive in both directions and are tested by round trip, so
adding a routing field cannot silently fail to reach a resumed thread.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TypedDict
from uuid import UUID

from money_tracker.workflows.intents import WorkflowIntent
from money_tracker.workflows.messages import (
    CallbackAction,
    ChannelMessage,
    SupportedCommand,
)
from money_tracker.workflows.routing import (
    RoutingContext,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
)

WORKFLOW_STATE_VERSION = "v1"
"""Version of the root-router state shape.

A thread interrupted under one version is resumed under that version, so a
breaking change requires a new value and a documented migration for pending
threads rather than an edit in place.
"""


class RouterState(TypedDict, total=False):
    """Root-router state as it is checkpointed.

    Keys are individually optional because graph nodes contribute partial
    updates that the runtime merges. Accessors below require what they read.

    Attributes:
        state_version: Shape version this thread was created under.
        source_reference: Stable reference to the durable inbound record.
        received_at: ISO timestamp the channel assigned to the message.
        text: Owner text when present, including an image caption.
        callback_action: Explicit interface decision value when one was used.
        pending_action_id: Pending-action reference a callback answers.
        reply_to_action_id: Pending-action reference a free-text reply answers.
        has_image: Whether the message carried image media.
        pending_category_review_ids: Reviews awaiting this owner.
        latest_correctable_movement_id: Movement available to correct or void.
        intent: Selected destination once routing has decided.
        routing_source: Which half of the router decided.
        routing_reason: Stable justification code for the decision.
        routing_confidence: Model self-report when the model decided.
        command: Supported command when the intent is a channel command.
        decision_callback_action: Interface decision the router acted on. This
            is not the message's own action: an uncorrelated callback is
            clarified, so the message carries an action the decision does not.
        resolved_pending_action_id: Correlated pending action for a follow-up.
        target_movement_id: Correlated movement for a lifecycle change.
    """

    state_version: str
    source_reference: str
    received_at: str
    text: str | None
    callback_action: str | None
    pending_action_id: str | None
    reply_to_action_id: str | None
    has_image: bool
    pending_category_review_ids: list[str]
    latest_correctable_movement_id: str | None
    intent: str | None
    routing_source: str | None
    routing_reason: str | None
    routing_confidence: float | None
    command: str | None
    decision_callback_action: str | None
    resolved_pending_action_id: str | None
    target_movement_id: str | None


def initial_router_state(
    message: ChannelMessage,
    *,
    context: RoutingContext,
) -> RouterState:
    """Build the state one inbound message and its trusted context start from.

    Args:
        message: Normalized inbound work unit.
        context: Trusted runtime facts the model may not choose.

    Returns:
        A complete initial state with no routing outcome yet.
    """

    return RouterState(
        state_version=WORKFLOW_STATE_VERSION,
        source_reference=message.source_reference,
        received_at=message.received_at.isoformat(),
        text=message.text,
        callback_action=(
            message.callback_action.value
            if message.callback_action is not None
            else None
        ),
        pending_action_id=_optional_str(message.pending_action_id),
        reply_to_action_id=_optional_str(message.reply_to_action_id),
        has_image=message.has_image,
        pending_category_review_ids=[
            str(item) for item in context.pending_category_review_ids
        ],
        latest_correctable_movement_id=_optional_str(
            context.latest_correctable_movement_id
        ),
        intent=None,
        routing_source=None,
        routing_reason=None,
        routing_confidence=None,
        command=None,
        decision_callback_action=None,
        resolved_pending_action_id=None,
        target_movement_id=None,
    )


def channel_message_from_state(state: RouterState) -> ChannelMessage:
    """Reconstruct the inbound work unit a resumed thread was created from.

    Args:
        state: Checkpointed router state.

    Returns:
        The normalized inbound message.

    Raises:
        KeyError: If the state omits a field the message requires.
        DomainError: If the stored message no longer satisfies its contract.
    """

    callback = state["callback_action"]
    return ChannelMessage(
        source_reference=state["source_reference"],
        received_at=datetime.fromisoformat(state["received_at"]),
        text=state["text"],
        callback_action=CallbackAction(callback) if callback is not None else None,
        pending_action_id=_optional_uuid(state["pending_action_id"]),
        reply_to_action_id=_optional_uuid(state["reply_to_action_id"]),
        has_image=state["has_image"],
    )


def routing_context_from_state(state: RouterState) -> RoutingContext:
    """Reconstruct the trusted routing context stored with a thread.

    Args:
        state: Checkpointed router state.

    Returns:
        The trusted runtime facts recorded when the thread started.

    Raises:
        KeyError: If the state omits a context field.
    """

    return RoutingContext(
        pending_category_review_ids=tuple(
            UUID(item) for item in state["pending_category_review_ids"]
        ),
        latest_correctable_movement_id=_optional_uuid(
            state["latest_correctable_movement_id"]
        ),
    )


def routing_update(decision: RoutingDecision) -> RouterState:
    """Build the partial state update one routing decision contributes.

    Args:
        decision: The selected destination for this message.

    Returns:
        Only the keys the decision determines.
    """

    return RouterState(
        intent=decision.intent.value,
        routing_source=decision.source.value,
        routing_reason=decision.reason.value,
        routing_confidence=decision.confidence,
        command=decision.command.value if decision.command is not None else None,
        decision_callback_action=(
            decision.callback_action.value
            if decision.callback_action is not None
            else None
        ),
        resolved_pending_action_id=_optional_str(decision.pending_action_id),
        target_movement_id=_optional_str(decision.target_movement_id),
    )


def routing_decision_from_state(state: RouterState) -> RoutingDecision | None:
    """Reconstruct the routing decision a thread reached, when it reached one.

    The decision's callback action is stored separately from the message's own.
    They differ whenever a callback arrives without a pending action: the
    message names an action, but the router clarifies instead of acting on it.

    Args:
        state: Checkpointed router state.

    Returns:
        The decision, or None while routing has not decided yet.

    Raises:
        KeyError: If a decided state omits one of its own fields.
    """

    intent = state.get("intent")
    if intent is None:
        return None
    callback = state["decision_callback_action"]
    command = state["command"]
    return RoutingDecision(
        intent=WorkflowIntent(intent),
        source=RoutingSource(_require(state["routing_source"], "routing_source")),
        reason=RoutingReason(_require(state["routing_reason"], "routing_reason")),
        command=SupportedCommand(command) if command is not None else None,
        callback_action=CallbackAction(callback) if callback is not None else None,
        pending_action_id=_optional_uuid(state["resolved_pending_action_id"]),
        target_movement_id=_optional_uuid(state["target_movement_id"]),
        confidence=state["routing_confidence"],
    )


def _require(value: str | None, field: str) -> str:
    """Require a field a decided state must carry.

    Args:
        value: Stored value.
        field: State key, used only to name the defect.

    Returns:
        The present value.

    Raises:
        KeyError: If a decided state omits the field.
    """

    if value is None:
        raise KeyError(f"decided router state requires {field}")
    return value


def _optional_str(value: UUID | None) -> str | None:
    """Render an optional identifier as stable text."""

    return None if value is None else str(value)


def _optional_uuid(value: str | None) -> UUID | None:
    """Read an optional identifier back from stable text."""

    return None if value is None else UUID(value)


class ExpenseOutcome(StrEnum):
    """How one expense or refund workflow run ended.

    Attributes:
        DRAFTED: A complete draft movement was stored.
        AWAITING_CATEGORY_APPROVAL: A draft was stored without a purpose, and a
            candidate category is pending owner approval.
        CLARIFICATION: Nothing was stored and one targeted question is owed.
    """

    DRAFTED = "drafted"
    AWAITING_CATEGORY_APPROVAL = "awaiting_category_approval"
    CLARIFICATION = "clarification"


class ExpenseWorkflowState(TypedDict, total=False):
    """Expense or refund workflow state as it is checkpointed.

    The same rule as the router state applies: JSON primitives and stable
    string references only. The stored movement lives in the repository and is
    referenced here by identifier, never embedded.

    The profile scope is present because repository reads need it, and it
    arrives from trusted runtime context. No model-facing schema has a field
    for it, so a model cannot influence which owner's data is read or written.

    Attributes:
        state_version: Shape version this thread was created under.
        profile_id: Trusted owner scope injected by the runtime.
        source_reference: Stable reference to the durable inbound record.
        received_at: ISO timestamp anchoring relative Italian dates.
        text: Owner text the boundaries interpret.
        routed_intent: Whether the router selected the expense or refund route.
        extraction_json: The extraction boundary's answer, carried forward as
            its own wire form so a resumed thread never re-asks the model.
        category_fit_json: The purpose boundary's answer, carried the same way.
        outcome: How the run ended, once it has ended.
        expense_id: Stored draft movement, when one was stored.
        proposal_id: Pending category candidate, when one was submitted.
        clarification_reason: Stable reason selecting the question to ask.
    """

    state_version: str
    profile_id: str
    source_reference: str
    received_at: str
    text: str
    routed_intent: str
    extraction_json: str | None
    category_fit_json: str | None
    outcome: str | None
    expense_id: str | None
    proposal_id: str | None
    clarification_reason: str | None


def initial_expense_state(
    message: ChannelMessage,
    *,
    profile_id: UUID,
    routed_intent: WorkflowIntent,
) -> ExpenseWorkflowState:
    """Build the state one routed expense or refund message starts from.

    Args:
        message: Normalized inbound work unit carrying owner text.
        profile_id: Trusted owner scope.
        routed_intent: The route the router selected.

    Returns:
        A complete initial state with no outcome yet.

    Raises:
        ValueError: If the route is not a movement route, or the message
            carries no text for the boundaries to interpret.
    """

    if routed_intent not in {WorkflowIntent.EXPENSE, WorkflowIntent.REFUND}:
        raise ValueError("expense workflow accepts only a movement route")
    text = message.normalized_text
    if not text:
        raise ValueError("expense workflow requires owner text")
    return ExpenseWorkflowState(
        state_version=WORKFLOW_STATE_VERSION,
        profile_id=str(profile_id),
        source_reference=message.source_reference,
        received_at=message.received_at.isoformat(),
        text=text,
        routed_intent=routed_intent.value,
        extraction_json=None,
        category_fit_json=None,
        outcome=None,
        expense_id=None,
        proposal_id=None,
        clarification_reason=None,
    )


def expense_message_from_state(state: ExpenseWorkflowState) -> ChannelMessage:
    """Reconstruct the inbound record a resumed movement thread started from.

    Args:
        state: Checkpointed expense workflow state.

    Returns:
        The normalized inbound message.

    Raises:
        KeyError: If the state omits a field the message requires.
    """

    return ChannelMessage(
        source_reference=state["source_reference"],
        received_at=datetime.fromisoformat(state["received_at"]),
        text=state["text"],
    )


class MovementChangeOutcome(StrEnum):
    """How one correction, posting, or undo specialist run ended."""

    CORRECTED = "corrected"
    POSTED = "posted"
    VOIDED = "voided"
    CLARIFICATION = "clarification"


class MovementChangeReason(StrEnum):
    """Why a lifecycle change stopped without mutating finance data."""

    NO_CORRECTABLE_MOVEMENT = "no_correctable_movement"
    CORRECTION_DETAILS_REQUIRED = "correction_details_required"
    MOVEMENT_NOT_CORRECTABLE = "movement_not_correctable"
    MOVEMENT_NOT_POSTABLE = "movement_not_postable"


class MovementChangeState(TypedDict, total=False):
    """Correction or undo state containing only primitives and stable references.

    Attributes:
        state_version: Shape version this thread was created under.
        profile_id: Trusted owner scope injected by the runtime.
        source_reference: Stable inbound work reference used for idempotency.
        received_at: ISO channel timestamp anchoring relative dates.
        text: Owner correction text, or empty text for an explicit undo action.
        routed_intent: Correction or undo.
        target_movement_id: Runtime-resolved movement reference, when available.
        extraction_json: Correction extraction carried across resume.
        category_fit_json: Correction purpose judgment carried across resume.
        outcome: Terminal specialist result.
        expense_id: Corrected or voided movement reference.
        clarification_reason: Stable reason selecting the next question.
    """

    state_version: str
    profile_id: str
    source_reference: str
    received_at: str
    text: str
    routed_intent: str
    target_movement_id: str | None
    extraction_json: str | None
    category_fit_json: str | None
    outcome: str | None
    expense_id: str | None
    clarification_reason: str | None


def initial_movement_change_state(
    message: ChannelMessage,
    *,
    profile_id: UUID,
    routed_intent: WorkflowIntent,
    target_movement_id: UUID | None,
) -> MovementChangeState:
    """Build initial state for a routed correction, posting, or undo.

    Args:
        message: Normalized inbound work unit.
        profile_id: Trusted owner scope.
        routed_intent: Correction or undo route selected by the router.
        target_movement_id: Movement resolved by trusted runtime context.

    Returns:
        A complete primitive state. A missing target is retained so the graph
        can return a typed clarification without touching a repository.

    Raises:
        ValueError: If another route is supplied.
    """

    if routed_intent not in {
        WorkflowIntent.CORRECTION,
        WorkflowIntent.POSTING,
        WorkflowIntent.UNDO,
    }:
        raise ValueError(
            "movement change workflow accepts correction, posting, or undo"
        )
    return MovementChangeState(
        state_version=WORKFLOW_STATE_VERSION,
        profile_id=str(profile_id),
        source_reference=message.source_reference,
        received_at=message.received_at.isoformat(),
        text=message.normalized_text,
        routed_intent=routed_intent.value,
        target_movement_id=_optional_str(target_movement_id),
        extraction_json=None,
        category_fit_json=None,
        outcome=None,
        expense_id=None,
        clarification_reason=None,
    )


def movement_change_message_from_state(state: MovementChangeState) -> ChannelMessage:
    """Reconstruct the normalized work unit for a resumed change thread."""

    return ChannelMessage(
        source_reference=state["source_reference"],
        received_at=datetime.fromisoformat(state["received_at"]),
        text=state["text"],
    )


class ReviewOutcome(StrEnum):
    """How one owner category review ended, once it has ended.

    A re-proposal is deliberately absent: it does not end the review, it opens
    the next candidate and pauses again.

    Attributes:
        APPROVED: The candidate became a taxonomy node and the movement posted.
        REUSED: An existing category received the purpose and the movement
            posted.
        CANCELLED: The review ended with no node and the draft was discarded.
        CLARIFICATION: The decision could not be applied and the review is
            still pending.
    """

    APPROVED = "approved"
    REUSED = "reused"
    CANCELLED = "cancelled"
    CLARIFICATION = "clarification"


class ReviewReason(StrEnum):
    """Why an owner decision could not be applied to a review.

    Like the resolution reasons, these are stable machine-readable codes
    carrying no owner text, so a channel can select one question without
    parsing prose.

    Attributes:
        UNKNOWN_CATEGORY: The named category matches no active name or alias.
        NO_BEST_EXISTING: The review examined no alternative to reuse.
        PROPOSALS_EXHAUSTED: The candidate loop reached its cap, so existing
            categories and manual entry are offered instead.
        INVALID_CANDIDATE: A replacement candidate was refused by taxonomy
            governance.
        ALREADY_SETTLED: The review had already received a decision.
    """

    UNKNOWN_CATEGORY = "unknown_category"
    NO_BEST_EXISTING = "no_best_existing"
    PROPOSALS_EXHAUSTED = "proposals_exhausted"
    INVALID_CANDIDATE = "invalid_candidate"
    ALREADY_SETTLED = "already_settled"


class ReviewWorkflowState(TypedDict, total=False):
    """Owner category review state as it is checkpointed.

    The same rule as the other workflow states applies: JSON primitives and
    stable string references only. The proposal, the movement, and the taxonomy
    stay in repositories and are referenced here by identifier.

    A paused review may wait indefinitely, so this state is the only thing that
    survives between the question and the owner's answer. It deliberately does
    not carry the candidate's name or rationale: those live in the stored
    proposal, which is authoritative and may have been replaced by a successor.

    Attributes:
        state_version: Shape version this thread was created under.
        profile_id: Trusted owner scope injected by the runtime.
        source_reference: Stable reference to the durable inbound record.
        proposal_id: Pending candidate currently awaiting the owner.
        expense_id: Draft movement the review is holding.
        decision_action: The branch the owner selected, once they have.
        decision_category_name: Name the owner typed, on the named branch only.
        outcome: How the review ended, once it has ended.
        settled_category_id: Category the purpose was assigned to, when one
            was.
        clarification_reason: Stable reason selecting the question to ask.
    """

    state_version: str
    profile_id: str
    source_reference: str
    proposal_id: str
    expense_id: str
    decision_action: str | None
    decision_category_name: str | None
    outcome: str | None
    settled_category_id: str | None
    clarification_reason: str | None


def initial_review_state(
    *,
    profile_id: UUID,
    proposal_id: UUID,
    expense_id: UUID,
    source_reference: str,
) -> ReviewWorkflowState:
    """Build the state one paused owner category review starts from.

    Args:
        profile_id: Trusted owner scope.
        proposal_id: Pending candidate the owner will decide on.
        expense_id: Draft movement the review is holding.
        source_reference: Stable reference to the durable inbound record.

    Returns:
        A complete initial state with no outcome yet.

    Raises:
        ValueError: If the source reference is blank.
    """

    if not source_reference.strip():
        raise ValueError("category review requires a source reference")
    return ReviewWorkflowState(
        state_version=WORKFLOW_STATE_VERSION,
        profile_id=str(profile_id),
        source_reference=source_reference,
        proposal_id=str(proposal_id),
        expense_id=str(expense_id),
        decision_action=None,
        decision_category_name=None,
        outcome=None,
        settled_category_id=None,
        clarification_reason=None,
    )


class ReadOutcome(StrEnum):
    """How one read-only question or advice run ended.

    Attributes:
        ANSWERED: A registered tool executed and produced figures.
        CLARIFICATION: Nothing was read and one targeted question is owed.
    """

    ANSWERED = "answered"
    CLARIFICATION = "clarification"


class ReadReason(StrEnum):
    """Why a read-only run could not produce an answer.

    Attributes:
        MODEL_OUTPUT_INVALID: The selection boundary never returned a valid
            request within its retry budget.
        TOOL_NOT_REGISTERED: The requested tool is outside the calling route's
            registry.
        UNKNOWN_CATEGORY: The named category matches no active name or alias.
        BUDGET_TOOL_UNAVAILABLE: The message asks about one category's budget on
            a route whose registry has no budget tool, so no admitted tool can
            answer the question that was asked.
    """

    MODEL_OUTPUT_INVALID = "invalid_model_output"
    TOOL_NOT_REGISTERED = "tool_not_registered"
    UNKNOWN_CATEGORY = "unknown_category"
    BUDGET_TOOL_UNAVAILABLE = "budget_tool_unavailable"


class ReadWorkflowState(TypedDict, total=False):
    """Read-only question or advice state as it is checkpointed.

    The same rule as the other workflow states applies: JSON primitives and
    stable string references only. The figures are stored as their own compact
    JSON so a resumed thread reports what it read rather than reading again,
    and so a reviewer can see that every number came from a tool result.

    Attributes:
        state_version: Shape version this thread was created under.
        profile_id: Trusted owner scope injected by the runtime.
        source_reference: Stable reference to the durable inbound record.
        received_at: ISO timestamp anchoring the month in progress.
        text: Owner text the selection boundary interprets.
        routed_intent: Whether the router selected the query or advice route.
        tool_request_json: The selection boundary's answer, carried forward as
            its own wire form.
        executed_tool: The tool deterministic acceptance admitted and ran.
        answer_json: Figures the tool produced, as JSON primitives.
        outcome: How the run ended, once it has ended.
        clarification_reason: Stable reason selecting the question to ask.
    """

    state_version: str
    profile_id: str
    source_reference: str
    received_at: str
    text: str
    routed_intent: str
    tool_request_json: str | None
    executed_tool: str | None
    answer_json: str | None
    outcome: str | None
    clarification_reason: str | None


def initial_read_state(
    message: ChannelMessage,
    *,
    profile_id: UUID,
    routed_intent: WorkflowIntent,
) -> ReadWorkflowState:
    """Build the state one routed question or advice message starts from.

    Args:
        message: Normalized inbound work unit carrying owner text.
        profile_id: Trusted owner scope.
        routed_intent: The route the router selected.

    Returns:
        A complete initial state with no outcome yet.

    Raises:
        ValueError: If the route is not read-only, or the message carries no
            text for the selection boundary to interpret.
    """

    if routed_intent not in {WorkflowIntent.QUERY, WorkflowIntent.ADVICE}:
        raise ValueError("read workflow accepts only a query or advice route")
    text = message.normalized_text
    if not text:
        raise ValueError("read workflow requires owner text")
    return ReadWorkflowState(
        state_version=WORKFLOW_STATE_VERSION,
        profile_id=str(profile_id),
        source_reference=message.source_reference,
        received_at=message.received_at.isoformat(),
        text=text,
        routed_intent=routed_intent.value,
        tool_request_json=None,
        executed_tool=None,
        answer_json=None,
        outcome=None,
        clarification_reason=None,
    )


def read_message_from_state(state: ReadWorkflowState) -> ChannelMessage:
    """Reconstruct the inbound record a resumed read thread started from.

    Args:
        state: Checkpointed read workflow state.

    Returns:
        The normalized inbound message.

    Raises:
        KeyError: If the state omits a field the message requires.
    """

    return ChannelMessage(
        source_reference=state["source_reference"],
        received_at=datetime.fromisoformat(state["received_at"]),
        text=state["text"],
    )
