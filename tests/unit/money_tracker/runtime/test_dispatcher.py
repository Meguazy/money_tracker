"""Tests for the single router-to-specialist dispatcher."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from money_tracker.runtime import WorkflowDispatcher
from money_tracker.workflows import (
    CallbackAction,
    ChannelMessage,
    DispatchContext,
    DispatchReason,
    DispatchStatus,
    PendingMovementAction,
    ReadTool,
    RoutingContext,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
    WorkflowIntent,
    routing_update,
)


@dataclass
class _Graph:
    """Lightweight graph boundary returning production-shaped state."""

    result: dict[str, Any]
    calls: list[tuple[Any, object]] = field(default_factory=list)

    def invoke(self, value: Any, config: object) -> dict[str, Any]:
        """Record an invocation and return a fresh state mapping."""

        self.calls.append((value, config))
        return dict(self.result)


def _message(text: str = "testo", **values: object) -> ChannelMessage:
    """Build one channel-neutral work unit."""

    return ChannelMessage(
        source_reference=f"test:dispatch:{uuid4()}",
        received_at=datetime(2026, 9, 8, tzinfo=UTC),
        text=text,
        **values,
    )


def _decision(intent: WorkflowIntent, **values: object) -> RoutingDecision:
    """Build an accepted model routing decision."""

    return RoutingDecision(
        intent=intent,
        source=RoutingSource.MODEL,
        reason=RoutingReason.MODEL_CLASSIFICATION,
        confidence=0.99,
        **values,
    )


def _dispatcher(
    decision: RoutingDecision,
    *,
    expense: dict[str, Any] | None = None,
    movement: dict[str, Any] | None = None,
    read: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> tuple[WorkflowDispatcher, _Graph, _Graph, _Graph, _Graph]:
    """Assemble the production dispatcher over contract-faithful graph fakes."""

    expense_graph = _Graph(expense or {})
    movement_graph = _Graph(movement or {})
    read_graph = _Graph(read or {})
    review_graph = _Graph(review or {})
    dispatcher = WorkflowDispatcher(
        router=_Graph(dict(routing_update(decision))),
        expense=expense_graph,
        movement_change=movement_graph,
        read=read_graph,
        review=review_graph,
    )
    return dispatcher, expense_graph, movement_graph, read_graph, review_graph


def test_expense_proposal_opens_the_persistent_review() -> None:
    """Dispatch movement creation and initialize the exact proposal checkpoint."""

    expense_id = uuid4()
    proposal_id = uuid4()
    dispatcher, expense, _, _, review = _dispatcher(
        _decision(WorkflowIntent.EXPENSE),
        expense={
            "outcome": DispatchStatus.AWAITING_CATEGORY_APPROVAL.value,
            "expense_id": str(expense_id),
            "proposal_id": str(proposal_id),
            "clarification_reason": None,
        },
    )

    result = dispatcher.dispatch(
        _message("Ho speso 12 euro"), context=DispatchContext(profile_id=uuid4())
    )

    assert result.status is DispatchStatus.AWAITING_CATEGORY_APPROVAL
    assert result.expense_id == expense_id
    assert result.proposal_id == proposal_id
    assert len(expense.calls) == 1
    assert len(review.calls) == 1
    assert review.calls[0][0]["proposal_id"] == str(proposal_id)


def test_query_reaches_the_read_specialist_and_returns_tool_facts() -> None:
    """Expose grounded read facts without leaking a LangGraph state mapping."""

    dispatcher, _, _, read, _ = _dispatcher(
        _decision(WorkflowIntent.QUERY),
        read={
            "outcome": DispatchStatus.ANSWERED.value,
            "executed_tool": ReadTool.FUNDING_CLASS_SPLIT.value,
            "answer_json": json.dumps({"period": "2026-09", "total": "12.00"}),
            "clarification_reason": None,
        },
    )

    result = dispatcher.dispatch(
        _message("Quanto ho speso?"), context=DispatchContext(profile_id=uuid4())
    )

    assert result.status is DispatchStatus.ANSWERED
    assert result.executed_tool is ReadTool.FUNDING_CLASS_SPLIT
    assert result.facts == {"period": "2026-09", "total": "12.00"}
    assert len(read.calls) == 1


def test_undo_callback_uses_the_trusted_action_mapping() -> None:
    """Resolve an explicit action reference before invoking a mutation graph."""

    action_id = uuid4()
    movement_id = uuid4()
    decision = RoutingDecision(
        intent=WorkflowIntent.UNDO,
        source=RoutingSource.DETERMINISTIC,
        reason=RoutingReason.CALLBACK_ACTION,
        callback_action=CallbackAction.UNDO_EXPENSE,
        pending_action_id=action_id,
    )
    dispatcher, _, movement, _, _ = _dispatcher(
        decision,
        movement={
            "outcome": DispatchStatus.VOIDED.value,
            "expense_id": str(movement_id),
            "clarification_reason": None,
        },
    )
    context = DispatchContext(
        profile_id=uuid4(),
        movement_actions=(PendingMovementAction(action_id, movement_id),),
    )

    result = dispatcher.dispatch(
        _message(
            "", callback_action=CallbackAction.UNDO_EXPENSE, pending_action_id=action_id
        ),
        context=context,
    )

    assert result.status is DispatchStatus.VOIDED
    assert movement.calls[0][0]["target_movement_id"] == str(movement_id)


def test_stale_category_callback_never_reaches_the_review() -> None:
    """Veto a callback whose action is no longer pending in trusted context."""

    proposal_id = uuid4()
    decision = RoutingDecision(
        intent=WorkflowIntent.CATEGORY_FOLLOW_UP,
        source=RoutingSource.DETERMINISTIC,
        reason=RoutingReason.CALLBACK_ACTION,
        callback_action=CallbackAction.APPROVE_CATEGORY,
        pending_action_id=proposal_id,
    )
    dispatcher, _, _, _, review = _dispatcher(decision)

    result = dispatcher.dispatch(
        _message(
            "",
            callback_action=CallbackAction.APPROVE_CATEGORY,
            pending_action_id=proposal_id,
        ),
        context=DispatchContext(profile_id=uuid4()),
    )

    assert result.status is DispatchStatus.CLARIFICATION
    assert result.clarification_reason == DispatchReason.REVIEW_NOT_PENDING.value
    assert review.calls == []


def test_correlated_text_follow_up_resumes_the_review() -> None:
    """Turn an explicit acceptance phrase into the review graph's typed resume."""

    proposal_id = uuid4()
    dispatcher, _, _, _, review = _dispatcher(
        _decision(WorkflowIntent.CATEGORY_FOLLOW_UP, pending_action_id=proposal_id),
        review={
            "proposal_id": str(proposal_id),
            "outcome": DispatchStatus.APPROVED.value,
            "settled_category_id": str(uuid4()),
            "clarification_reason": None,
        },
    )

    result = dispatcher.dispatch(
        _message("Approva"),
        context=DispatchContext(
            profile_id=uuid4(),
            routing=RoutingContext(pending_category_review_ids=(proposal_id,)),
        ),
    )

    assert result.status is DispatchStatus.APPROVED
    assert len(review.calls) == 1


def test_routing_only_mode_does_not_open_a_specialist() -> None:
    """Preserve the administration diagnostic when no profile scope is supplied."""

    dispatcher, expense, movement, read, review = _dispatcher(
        _decision(WorkflowIntent.ADVICE)
    )

    result = dispatcher.dispatch(
        _message("Dammi un consiglio"), context=DispatchContext()
    )

    assert result.status is DispatchStatus.ROUTED
    assert expense.calls == movement.calls == read.calls == review.calls == []


def test_posting_callback_reaches_the_movement_specialist() -> None:
    """Send an explicit Registra to the same trusted target resolution."""

    action_id = uuid4()
    movement_id = uuid4()
    decision = RoutingDecision(
        intent=WorkflowIntent.CORRECTION,
        source=RoutingSource.DETERMINISTIC,
        reason=RoutingReason.CALLBACK_ACTION,
        callback_action=CallbackAction.POST_EXPENSE,
        pending_action_id=action_id,
    )
    dispatcher, _, movement, _, _ = _dispatcher(
        decision,
        movement={
            "outcome": DispatchStatus.POSTED.value,
            "expense_id": str(movement_id),
            "clarification_reason": None,
        },
    )
    context = DispatchContext(
        profile_id=uuid4(),
        movement_actions=(PendingMovementAction(action_id, movement_id),),
    )

    result = dispatcher.dispatch(
        _message(
            "", callback_action=CallbackAction.POST_EXPENSE, pending_action_id=action_id
        ),
        context=context,
    )

    assert result.status is DispatchStatus.POSTED
    assert result.expense_id == movement_id
    assert movement.calls[0][0]["routed_intent"] == WorkflowIntent.CORRECTION.value
    assert movement.calls[0][0]["callback_action"] == CallbackAction.POST_EXPENSE.value


def test_an_uncorrelated_posting_callback_records_nothing() -> None:
    """Refuse to record whatever happens to be recent when the control is stale."""

    dispatcher, _, movement, _, _ = _dispatcher(
        RoutingDecision(
            intent=WorkflowIntent.CLARIFICATION,
            source=RoutingSource.DETERMINISTIC,
            reason=RoutingReason.UNCORRELATED_CALLBACK,
        )
    )

    result = dispatcher.dispatch(
        _message("", callback_action=CallbackAction.POST_EXPENSE),
        context=DispatchContext(profile_id=uuid4()),
    )

    assert result.status is DispatchStatus.CLARIFICATION
    assert movement.calls == []
