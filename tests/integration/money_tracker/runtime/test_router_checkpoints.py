"""Real PostgreSQL checkpoint tests for the compiled root-router graph."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.runtime import build_router_graph
from money_tracker.workflows import (
    ROUTER,
    CallbackAction,
    ChannelMessage,
    RepairRequest,
    RoutingContext,
    RoutingSource,
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
        "source_reference": f"integration:router:{uuid4()}",
        "received_at": RECEIVED_AT,
    }
    fields.update(overrides)
    return ChannelMessage(**fields)  # type: ignore[arg-type]


@pytest.mark.postgres
def test_deterministic_decision_survives_a_restart(postgres_url: str) -> None:
    """Resume a routed thread from real PostgreSQL after a fresh connection.

    The deterministic half decides without any inference call, so this covers
    persistence and resume with no model service involved at all.
    """

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    thread = str(uuid4())
    configuration = {"configurable": {"thread_id": thread}}
    pending = uuid4()
    message = _message(
        callback_action=CallbackAction.APPROVE_CATEGORY,
        pending_action_id=pending,
    )

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        graph = build_router_graph(
            model=ScriptedModel(),
            instructions=default_prompt(ROUTER).text,
            minimum_confidence=0.9,
            checkpointer=checkpointer,
        )
        written = graph.invoke(
            initial_router_state(message, context=RoutingContext()),
            configuration,  # type: ignore[arg-type]
        )

    with open_postgres_checkpointer(postgres_url) as reopened:
        restarted = build_router_graph(
            model=ScriptedModel(),
            instructions=default_prompt(ROUTER).text,
            minimum_confidence=0.9,
            checkpointer=reopened,
        )
        resumed = restarted.get_state(configuration).values  # type: ignore[arg-type]

    decision = routing_decision_from_state(resumed)

    assert resumed == dict(written)
    assert decision is not None
    assert decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert decision.source is RoutingSource.DETERMINISTIC
    assert decision.pending_action_id == pending
    assert decision.callback_action is CallbackAction.APPROVE_CATEGORY


@pytest.mark.postgres
def test_checkpointed_state_stays_json_primitive(postgres_url: str) -> None:
    """Prove a resumed checkpoint carries no reconstructed rich objects.

    A tuple written into state would come back as a list, and a datetime or
    UUID would come back as a rich object under a permissive serializer. This
    asserts the persisted shape is exactly what the state contract promises.
    """

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    thread = str(uuid4())
    configuration = {"configurable": {"thread_id": thread}}
    context = RoutingContext(
        pending_category_review_ids=(uuid4(), uuid4()),
        latest_correctable_movement_id=uuid4(),
    )

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        graph = build_router_graph(
            model=ScriptedModel(
                [
                    json.dumps(
                        {
                            "intent": "query",
                            "confidence": 0.96,
                            "evidence": "quanto ho speso",
                        }
                    )
                ]
            ),
            instructions=default_prompt(ROUTER).text,
            minimum_confidence=0.9,
            checkpointer=checkpointer,
        )
        graph.invoke(
            initial_router_state(
                _message(text="quanto ho speso per gli abbonamenti"),
                context=context,
            ),
            configuration,  # type: ignore[arg-type]
        )

    with open_postgres_checkpointer(postgres_url) as reopened:
        restarted = build_router_graph(
            model=ScriptedModel(),
            instructions=default_prompt(ROUTER).text,
            minimum_confidence=0.9,
            checkpointer=reopened,
        )
        resumed = restarted.get_state(configuration).values  # type: ignore[arg-type]

    assert json.loads(json.dumps(resumed)) == resumed
    assert isinstance(resumed["pending_category_review_ids"], list)
    decision = routing_decision_from_state(resumed)
    assert decision is not None
    assert decision.intent is WorkflowIntent.QUERY


@pytest.mark.postgres
@pytest.mark.inference
def test_real_local_model_classifies_an_italian_expense(
    postgres_url: str,
) -> None:
    """Route a real Italian message through the unpromoted candidate boundary.

    This exercises the strict `json_schema` mechanism the Step 1 capability
    screen validated. It asserts only that the boundary produces a schema-valid
    decision: routing accuracy is an evaluation gate on a pinned dataset, not a
    property one example can establish.
    """

    if os.getenv("RUN_INFERENCE_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_INFERENCE_INTEGRATION_TESTS=1 for real inference")

    from money_tracker.runtime import LocalStructuredOutputModel

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    configuration = {"configurable": {"thread_id": str(uuid4())}}

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        graph = build_router_graph(
            model=LocalStructuredOutputModel(),
            instructions=default_prompt(ROUTER).text,
            minimum_confidence=0.0,
            checkpointer=checkpointer,
        )
        state = graph.invoke(
            initial_router_state(
                _message(text="Ho appena speso 12 euro per i croccantini dei gatti"),
                context=RoutingContext(),
            ),
            configuration,  # type: ignore[arg-type]
        )

    decision = routing_decision_from_state(dict(state))  # type: ignore[arg-type]

    assert decision is not None
    assert decision.source is RoutingSource.MODEL
    assert decision.confidence is not None
