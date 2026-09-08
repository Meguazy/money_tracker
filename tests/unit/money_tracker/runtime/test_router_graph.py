"""Tests for the compiled root-router graph and its two halves."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from money_tracker.runtime import CLASSIFY_NODE, PRE_ROUTE_NODE, build_router_graph
from money_tracker.workflows import (
    ROUTER,
    CallbackAction,
    ChannelMessage,
    RepairRequest,
    RoutingContext,
    RoutingReason,
    RoutingSource,
    SupportedCommand,
    WorkflowIntent,
    default_prompt,
    initial_router_state,
    routing_decision_from_state,
)

RECEIVED_AT = datetime(2026, 9, 2, 21, 30, tzinfo=UTC)


@dataclass
class ScriptedModel:
    """A real port implementation replaying a fixed script of completions."""

    completions: list[str] = field(default_factory=list)
    calls: int = 0

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: RepairRequest | None = None,
    ) -> str:
        """Return the next scripted completion and count the request."""

        self.calls += 1
        return self.completions[self.calls - 1]


def _message(**overrides: object) -> ChannelMessage:
    """Build one normalized inbound message with explicit overrides."""

    fields: dict[str, object] = {
        "source_reference": "channel:inbox:1",
        "received_at": RECEIVED_AT,
    }
    fields.update(overrides)
    return ChannelMessage(**fields)  # type: ignore[arg-type]


def _classification(intent: str, confidence: float = 0.95) -> str:
    """Render one schema-valid classification completion."""

    return json.dumps({"intent": intent, "confidence": confidence, "evidence": "testo"})


def _run(
    message: ChannelMessage,
    *,
    model: ScriptedModel,
    context: RoutingContext | None = None,
    minimum_confidence: float = 0.9,
) -> dict[str, object]:
    """Invoke the compiled graph once and return its final state."""

    graph = build_router_graph(
        model=model,
        instructions=default_prompt(ROUTER).text,
        minimum_confidence=minimum_confidence,
    )
    return dict(
        graph.invoke(initial_router_state(message, context=context or RoutingContext()))
    )


def test_graph_exposes_its_two_halves_as_named_nodes() -> None:
    """Keep the deterministic and model halves separately addressable."""

    assert PRE_ROUTE_NODE == "pre_route"
    assert CLASSIFY_NODE == "classify"


def test_deterministic_route_never_reaches_the_model() -> None:
    """Spend no inference call on a decision the channel already stated."""

    model = ScriptedModel()

    state = _run(_message(has_image=True), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 0
    assert decision is not None
    assert decision.intent is WorkflowIntent.RECEIPT
    assert decision.source is RoutingSource.DETERMINISTIC


def test_supported_command_decides_without_the_model() -> None:
    """Execute a stated command through the deterministic half only."""

    model = ScriptedModel()

    state = _run(_message(text="/status"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 0
    assert decision is not None
    assert decision.command is SupportedCommand.STATUS


def test_callback_decides_without_the_model() -> None:
    """Honor an explicit owner decision through the deterministic half."""

    pending = uuid4()
    model = ScriptedModel()

    state = _run(
        _message(
            callback_action=CallbackAction.APPROVE_CATEGORY,
            pending_action_id=pending,
        ),
        model=model,
    )
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 0
    assert decision is not None
    assert decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert decision.pending_action_id == pending
    assert decision.callback_action is CallbackAction.APPROVE_CATEGORY


def test_uncorrelated_callback_clarifies_without_the_model() -> None:
    """Keep the decision's own callback distinct from the message's."""

    model = ScriptedModel()

    state = _run(_message(callback_action=CallbackAction.APPROVE_CATEGORY), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 0
    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.UNCORRELATED_CALLBACK
    assert decision.callback_action is None


def test_free_text_reaches_the_model_and_is_accepted() -> None:
    """Classify prose the deterministic half cannot resolve."""

    model = ScriptedModel([_classification("expense")])

    state = _run(_message(text="ho speso 12 euro"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 1
    assert decision is not None
    assert decision.intent is WorkflowIntent.EXPENSE
    assert decision.source is RoutingSource.MODEL


def test_graph_reimposes_policy_on_the_model_answer() -> None:
    """Veto a route the runtime facts do not support."""

    model = ScriptedModel([_classification("category_follow_up")])

    state = _run(_message(text="usa la categoria Animali"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.NO_PENDING_REVIEW


def test_graph_applies_the_supplied_confidence_bar() -> None:
    """Use the bar the caller stated rather than an implied default."""

    model = ScriptedModel([_classification("expense", 0.5)])

    state = _run(_message(text="forse ho speso"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.LOW_CONFIDENCE


def test_graph_repairs_one_invalid_completion() -> None:
    """Recover a malformed answer inside the graph without asking the owner."""

    model = ScriptedModel(["not json", _classification("query")])

    state = _run(_message(text="quanto ho speso"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 2
    assert decision is not None
    assert decision.intent is WorkflowIntent.QUERY


def test_exhausted_retry_budget_becomes_clarification() -> None:
    """Fall back to a question instead of failing the invocation."""

    model = ScriptedModel(["not json", "still not json"])

    state = _run(_message(text="quanto ho speso"), model=model)
    decision = routing_decision_from_state(state)  # type: ignore[arg-type]

    assert model.calls == 2
    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.INVALID_MODEL_OUTPUT
    assert decision.source is RoutingSource.MODEL


def test_graph_requires_an_explicit_confidence_bar() -> None:
    """Refuse to imply an uncalibrated production threshold."""

    with pytest.raises(TypeError):
        build_router_graph(  # type: ignore[call-arg]
            model=ScriptedModel(),
            instructions=default_prompt(ROUTER).text,
        )


def test_final_state_holds_only_small_serializable_values() -> None:
    """Hold the Step 3 state requirement on a real graph invocation."""

    model = ScriptedModel([_classification("advice")])

    state = _run(_message(text="sto spendendo troppo?"), model=model)

    assert json.loads(json.dumps(state)) == state
