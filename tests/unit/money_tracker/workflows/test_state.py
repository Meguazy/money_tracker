"""Tests for the versioned router state and its exhaustive conversions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from money_tracker.workflows import (
    WORKFLOW_STATE_VERSION,
    CallbackAction,
    ChannelMessage,
    RouterState,
    RoutingContext,
    RoutingReason,
    RoutingSource,
    SupportedCommand,
    WorkflowIntent,
    channel_message_from_state,
    initial_review_state,
    initial_router_state,
    pre_route,
    routing_context_from_state,
    routing_decision_from_state,
    routing_update,
)

RECEIVED_AT = datetime(2026, 9, 2, 21, 30, tzinfo=UTC)
PRIMITIVES = (str, int, float, bool)


def _message(**overrides: object) -> ChannelMessage:
    """Build one normalized inbound message with explicit overrides."""

    fields: dict[str, object] = {
        "source_reference": "channel:inbox:1",
        "received_at": RECEIVED_AT,
    }
    fields.update(overrides)
    return ChannelMessage(**fields)  # type: ignore[arg-type]


def _assert_only_small_serializable_values(state: RouterState) -> None:
    """Assert the Step 3 state requirement on one concrete state."""

    for key, value in state.items():
        if isinstance(value, list):
            assert all(isinstance(item, str) for item in value), key
            continue
        assert value is None or isinstance(value, PRIMITIVES), key


def test_state_version_is_pinned() -> None:
    """Keep a resumed thread anchored to the shape it was created under."""

    assert WORKFLOW_STATE_VERSION == "v1"
    assert (
        initial_router_state(_message(text="ciao"), context=RoutingContext())[
            "state_version"
        ]
        == "v1"
    )


def test_initial_state_holds_only_small_serializable_values() -> None:
    """Keep domain data in repositories and identifiers as stable references."""

    state = initial_router_state(
        _message(
            text="ho speso 12 euro",
            callback_action=CallbackAction.APPROVE_CATEGORY,
            pending_action_id=uuid4(),
            reply_to_action_id=uuid4(),
            has_image=True,
        ),
        context=RoutingContext(
            pending_category_review_ids=(uuid4(), uuid4()),
            latest_correctable_movement_id=uuid4(),
        ),
    )

    _assert_only_small_serializable_values(state)


def test_decided_state_holds_only_small_serializable_values() -> None:
    """Hold the requirement after routing has contributed its outcome."""

    message = _message(text="/status")
    state = initial_router_state(message, context=RoutingContext())
    decision = pre_route(message)
    assert decision is not None
    state.update(routing_update(decision))

    _assert_only_small_serializable_values(state)


def test_state_is_json_serializable_end_to_end() -> None:
    """Prove the state survives a plain JSON round trip unchanged."""

    message = _message(
        text="approva",
        callback_action=CallbackAction.APPROVE_CATEGORY,
        pending_action_id=uuid4(),
    )
    state = initial_router_state(
        message,
        context=RoutingContext(pending_category_review_ids=(uuid4(),)),
    )
    decision = pre_route(message)
    assert decision is not None
    state.update(routing_update(decision))

    assert json.loads(json.dumps(state)) == state


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "ho speso 12 euro"},
        {"text": "  ho   speso 12 euro  ", "reply_to_action_id": uuid4()},
        {"has_image": True},
        {"has_image": True, "text": "scontrino della spesa"},
        {
            "callback_action": CallbackAction.USE_NAMED_EXISTING,
            "pending_action_id": uuid4(),
        },
    ],
)
def test_message_round_trips_through_state(overrides: dict[str, object]) -> None:
    """Reconstruct the exact inbound work unit a resumed thread started from."""

    message = _message(**overrides)

    restored = channel_message_from_state(
        initial_router_state(message, context=RoutingContext())
    )

    assert restored == message


def test_context_round_trips_through_state() -> None:
    """Keep trusted routing facts intact across a resume."""

    context = RoutingContext(
        pending_category_review_ids=(uuid4(), uuid4()),
        latest_correctable_movement_id=uuid4(),
    )

    restored = routing_context_from_state(
        initial_router_state(_message(text="ciao"), context=context)
    )

    assert restored == context


def test_empty_context_round_trips_through_state() -> None:
    """Keep the absent-facts case distinguishable from a populated one."""

    restored = routing_context_from_state(
        initial_router_state(_message(text="ciao"), context=RoutingContext())
    )

    assert restored == RoutingContext()


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "/pair ABC123"},
        {"has_image": True},
        {"text": "/unknown"},
        {"text": "   "},
        {
            "callback_action": CallbackAction.UNDO_EXPENSE,
            "pending_action_id": uuid4(),
        },
        {"callback_action": CallbackAction.APPROVE_CATEGORY},
    ],
)
def test_deterministic_decision_round_trips_through_state(
    overrides: dict[str, object],
) -> None:
    """Reconstruct every deterministic outcome from a checkpointed thread."""

    message = _message(**overrides)
    decision = pre_route(message)
    assert decision is not None
    state = initial_router_state(message, context=RoutingContext())
    state.update(routing_update(decision))

    assert routing_decision_from_state(state) == decision


def test_undecided_state_reports_no_decision() -> None:
    """Distinguish a thread awaiting classification from a decided one."""

    state = initial_router_state(
        _message(text="ho speso 12 euro"), context=RoutingContext()
    )

    assert pre_route(_message(text="ho speso 12 euro")) is None
    assert routing_decision_from_state(state) is None


def test_model_decision_round_trips_with_its_correlated_references() -> None:
    """Keep confidence and correlated references intact across a resume."""

    from money_tracker.workflows import RouteClassification, accept_classification

    pending = uuid4()
    message = _message(text="usa la categoria Animali")
    decision = accept_classification(
        RouteClassification(
            intent=WorkflowIntent.CATEGORY_FOLLOW_UP,
            confidence=0.93,
            evidence="usa la categoria Animali",
        ),
        message=message,
        context=RoutingContext(pending_category_review_ids=(pending,)),
        minimum_confidence=0.9,
    )
    state = initial_router_state(
        message, context=RoutingContext(pending_category_review_ids=(pending,))
    )
    state.update(routing_update(decision))

    restored = routing_decision_from_state(state)

    assert restored == decision
    assert restored is not None
    assert restored.source is RoutingSource.MODEL
    assert restored.confidence == 0.93
    assert restored.pending_action_id == pending


def test_command_decision_keeps_its_command_but_no_argument() -> None:
    """Carry the recognized command across a resume without its argument."""

    message = _message(text="/pair SECRET-CODE")
    decision = pre_route(message)
    assert decision is not None
    state = initial_router_state(message, context=RoutingContext())
    state.update(routing_update(decision))

    restored = routing_decision_from_state(state)

    assert restored is not None
    assert restored.command is SupportedCommand.PAIR
    assert "SECRET-CODE" not in json.dumps(routing_update(decision))


def test_decided_state_missing_its_own_fields_is_rejected() -> None:
    """Fail loudly on a state that claims a decision it cannot describe."""

    state = RouterState(
        intent=WorkflowIntent.QUERY.value,
        routing_source=None,
        routing_reason=RoutingReason.MODEL_CLASSIFICATION.value,
        routing_confidence=None,
        callback_action=None,
        decision_callback_action=None,
        command=None,
        resolved_pending_action_id=None,
        target_movement_id=None,
    )

    with pytest.raises(KeyError, match="routing_source"):
        routing_decision_from_state(state)


def test_a_review_requires_a_durable_source_reference() -> None:
    """Keep a paused review correlatable to the record that opened it."""

    with pytest.raises(ValueError, match="source reference"):
        initial_review_state(
            profile_id=uuid4(),
            proposal_id=uuid4(),
            expense_id=uuid4(),
            source_reference="   ",
        )
