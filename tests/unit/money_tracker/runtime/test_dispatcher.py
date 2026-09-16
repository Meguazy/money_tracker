"""Tests for the single router-to-specialist dispatcher."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

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
from tests.unit.money_tracker.tracing.memory_tracer import InMemoryTracer


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


@pytest.mark.parametrize("status", [DispatchStatus.APPROVED, DispatchStatus.REUSED])
def test_a_settled_review_carries_the_movement_it_posted(
    status: DispatchStatus,
) -> None:
    """Give the renderer the movement a settled review is about.

    Both settled branches post the movement, and the owner is shown that
    movement rather than the decision. Dropping the reference made every
    approval and every reuse commit its finance transaction and then fail while
    rendering, so the category existed, the movement was posted, and the owner
    was told nothing.
    """

    proposal_id = uuid4()
    expense_id = uuid4()
    dispatcher, _, _, _, _ = _dispatcher(
        _decision(WorkflowIntent.CATEGORY_FOLLOW_UP, pending_action_id=proposal_id),
        review={
            "proposal_id": str(proposal_id),
            "expense_id": str(expense_id),
            "outcome": status.value,
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

    assert result.status is status
    assert result.expense_id == expense_id


def test_an_untraced_dispatch_hands_graphs_only_their_thread() -> None:
    """Keep graph configuration exactly as it was before tracing existed."""

    message = _message("Quanto ho speso?")
    dispatcher, _expense, _movement, read, _review = _dispatcher(
        _decision(WorkflowIntent.QUERY),
        read={"outcome": "answered", "executed_tool": "category_spending"},
    )

    dispatcher.dispatch(message, context=DispatchContext(profile_id=uuid4()))

    (_state, config) = read.calls[0]
    assert config == {"configurable": {"thread_id": f"read:{message.source_reference}"}}


def test_a_traced_dispatch_records_the_work_unit_and_its_outcome() -> None:
    """Record the complete message, trusted context, versions, and outcome."""

    tracer = InMemoryTracer()
    profile_id = uuid4()
    message = _message("Quanto ho speso per gli animali?")
    dispatcher, _expense, _movement, read, _review = _dispatcher(
        _decision(WorkflowIntent.QUERY),
        read={
            "outcome": "answered",
            "executed_tool": "category_spending",
            "answer_json": json.dumps({"period": "2026-09"}),
        },
    )
    dispatcher.tracer = tracer
    dispatcher.trace_metadata = {"router_prompt": "candidate-router-it-v6"}

    dispatcher.dispatch(message, context=DispatchContext(profile_id=profile_id))

    (span,) = tracer.named("dispatch")
    assert span.metadata["router_prompt"] == "candidate-router-it-v6"
    assert isinstance(span.input, dict)
    assert span.input["message"]["text"] == "Quanto ho speso per gli animali?"
    assert span.input["context"]["profile_id"] == str(profile_id)
    assert isinstance(span.output, dict)
    assert span.output["executed_tool"] == "category_spending"
    assert span.output["answer"] == {"period": "2026-09"}
    assert tracer.graph_configs == ["router", "read"]
    (_state, config) = read.calls[0]
    assert config == {
        "configurable": {"thread_id": f"read:{message.source_reference}"},
        "callbacks": [tracer.handlers[1]],
    }


def test_a_failing_dispatch_marks_its_span_and_propagates() -> None:
    """Record a failure by exception class and leave the error to the caller."""

    tracer = InMemoryTracer()
    dispatcher, *_graphs = _dispatcher(_decision(WorkflowIntent.QUERY))
    dispatcher.router = _Graph({})
    dispatcher.tracer = tracer

    with pytest.raises(RuntimeError):
        dispatcher.dispatch(_message(), context=DispatchContext(profile_id=uuid4()))

    (span,) = tracer.named("dispatch")
    assert span.failure == ("exception", "RuntimeError")


def test_a_traced_read_records_its_decision_as_filterable_metadata() -> None:
    """Record routing, admitted tools, the admitted request, and grounding."""

    tracer = InMemoryTracer()
    message = _message("Quanto ho speso con la carta?")
    request = {"tool": "funding_class_split", "period": None, "category_name": None}
    dispatcher, *_graphs = _dispatcher(
        _decision(WorkflowIntent.QUERY),
        read={
            "outcome": DispatchStatus.ANSWERED.value,
            "tool_request_json": json.dumps(request),
            "executed_tool": ReadTool.FUNDING_CLASS_SPLIT.value,
            "answer_json": json.dumps({"period": "2026-09"}),
            "clarification_reason": None,
        },
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(message, context=DispatchContext(profile_id=uuid4()))

    (span,) = tracer.named("dispatch")
    assert span.metadata == {
        "routing_intent": "query",
        "routing_source": "model",
        "routing_reason_code": "model_classification",
        "routing_confidence": 0.99,
        "routing_policy_veto": False,
        "specialist": "read",
        "read_admitted_tools": sorted(
            ["budget_status", "category_spending", "funding_class_split"]
        ),
        "read_tool_request": request,
        "read_executed_tool": "funding_class_split",
        "read_grounded": True,
        "outcome_status": "answered",
        "thread_ids": [
            f"router:{message.source_reference}",
            f"read:{message.source_reference}",
        ],
    }


def test_a_traced_expense_records_how_its_allocations_were_resolved() -> None:
    """Record the converted answers, both allocation bases, and the review."""

    tracer = InMemoryTracer()
    proposal_id = uuid4()
    message = _message("Ho speso 12 euro per un corso")
    extraction = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "payment_parts": [],
    }
    fit = {"outcome": "proposal", "proposed_name": "Corsi"}
    dispatcher, *_graphs = _dispatcher(
        _decision(WorkflowIntent.EXPENSE),
        expense={
            "outcome": DispatchStatus.AWAITING_CATEGORY_APPROVAL.value,
            "extraction_json": json.dumps(extraction),
            "category_fit_json": json.dumps(fit),
            "expense_id": str(uuid4()),
            "proposal_id": str(proposal_id),
            "clarification_reason": None,
        },
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(message, context=DispatchContext(profile_id=uuid4()))

    metadata = tracer.named("dispatch")[0].metadata
    assert metadata["specialist"] == "expense"
    assert metadata["extraction"] == extraction
    assert metadata["category_fit"] == fit
    assert metadata["allocation_funding_basis"] == "profile_default"
    assert metadata["allocation_category_basis"] == "proposal"
    assert metadata["review_opened"] is True
    assert "resolution_reason" not in metadata
    assert metadata["thread_ids"] == [
        f"router:{message.source_reference}",
        f"expense:{message.source_reference}",
        f"category-review:{proposal_id}",
    ]
    assert tracer.graph_configs == ["router", "expense", "category-review"]


def test_an_unresolved_movement_records_its_reason_and_split_basis() -> None:
    """Name the resolution reason a clarification asks about."""

    tracer = InMemoryTracer()
    extraction = {
        "polarity": "expense",
        "payment_parts": [
            {"funding_source_text": "carta", "amount_minor_units": 500},
            {"funding_source_text": "buoni", "amount_minor_units": 400},
        ],
    }
    dispatcher, *_graphs = _dispatcher(
        _decision(WorkflowIntent.EXPENSE),
        expense={
            "outcome": DispatchStatus.CLARIFICATION.value,
            "extraction_json": json.dumps(extraction),
            "clarification_reason": "split_does_not_reconcile",
        },
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(_message("Ho speso 10"), context=DispatchContext(uuid4()))

    metadata = tracer.named("dispatch")[0].metadata
    assert metadata["resolution_reason"] == "split_does_not_reconcile"
    assert metadata["allocation_funding_basis"] == "stated_split"
    assert metadata["outcome_clarification_reason"] == "split_does_not_reconcile"


def test_a_policy_veto_is_distinguishable_from_other_clarifications() -> None:
    """Flag a valid classification that deterministic policy overruled."""

    tracer = InMemoryTracer()
    dispatcher, *_graphs = _dispatcher(
        RoutingDecision(
            intent=WorkflowIntent.CLARIFICATION,
            source=RoutingSource.MODEL,
            reason=RoutingReason.NO_CORRECTABLE_MOVEMENT,
            confidence=0.91,
        )
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(_message("Correggi"), context=DispatchContext(uuid4()))

    metadata = tracer.named("dispatch")[0].metadata
    assert metadata["routing_policy_veto"] is True
    assert metadata["routing_reason_code"] == "no_correctable_movement"
    assert metadata["outcome_status"] == "clarification"
    assert "specialist" not in metadata


def test_a_traced_movement_change_names_how_its_target_was_resolved() -> None:
    """Record that an action mapping, not routing, selected the movement."""

    tracer = InMemoryTracer()
    action_id = uuid4()
    movement_id = uuid4()
    dispatcher, *_graphs = _dispatcher(
        RoutingDecision(
            intent=WorkflowIntent.UNDO,
            source=RoutingSource.DETERMINISTIC,
            reason=RoutingReason.CALLBACK_ACTION,
            callback_action=CallbackAction.UNDO_EXPENSE,
            pending_action_id=action_id,
        ),
        movement={
            "outcome": DispatchStatus.VOIDED.value,
            "expense_id": str(movement_id),
        },
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(
        _message(
            "", callback_action=CallbackAction.UNDO_EXPENSE, pending_action_id=action_id
        ),
        context=DispatchContext(
            profile_id=uuid4(),
            movement_actions=(PendingMovementAction(action_id, movement_id),),
        ),
    )

    metadata = tracer.named("dispatch")[0].metadata
    assert metadata["specialist"] == "movement-change"
    assert metadata["movement_target_basis"] == "pending_action"
    assert metadata["movement_target_id"] == str(movement_id)
    assert metadata["routing_callback_action"] == "undo_expense"


def test_a_traced_review_resume_names_its_thread_and_decision() -> None:
    """Join a resumed review to the thread the original expense opened."""

    tracer = InMemoryTracer()
    proposal_id = uuid4()
    dispatcher, *_graphs = _dispatcher(
        _decision(WorkflowIntent.CATEGORY_FOLLOW_UP, pending_action_id=proposal_id),
        review={
            "proposal_id": str(proposal_id),
            "expense_id": str(uuid4()),
            "outcome": DispatchStatus.APPROVED.value,
            "settled_category_id": str(uuid4()),
        },
    )
    dispatcher.tracer = tracer

    dispatcher.dispatch(
        _message("Approva"),
        context=DispatchContext(
            profile_id=uuid4(),
            routing=RoutingContext(pending_category_review_ids=(proposal_id,)),
        ),
    )

    metadata = tracer.named("dispatch")[0].metadata
    assert metadata["specialist"] == "category-review"
    assert metadata["review_proposal_id"] == str(proposal_id)
    assert metadata["review_decision_action"] == "approve_category"
    assert metadata["review_resumed"] is True
    assert metadata["thread_ids"][-1] == f"category-review:{proposal_id}"


def test_a_failing_specialist_still_records_what_routing_decided() -> None:
    """Keep the routing facts that explain a specialist failure."""

    class _Failing:
        def invoke(self, value: object, config: object) -> object:
            raise RuntimeError("specialist failed")

    tracer = InMemoryTracer()
    dispatcher, *_graphs = _dispatcher(_decision(WorkflowIntent.QUERY))
    dispatcher.read = _Failing()
    dispatcher.tracer = tracer

    with pytest.raises(RuntimeError):
        dispatcher.dispatch(_message(), context=DispatchContext(profile_id=uuid4()))

    (span,) = tracer.named("dispatch")
    assert span.failure == ("exception", "RuntimeError")
    assert span.metadata["routing_intent"] == "query"
    assert span.metadata["specialist"] == "read"
    assert "outcome_status" not in span.metadata
