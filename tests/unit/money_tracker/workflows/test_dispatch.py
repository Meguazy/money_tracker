"""Tests for transport-neutral dispatch contracts."""

from datetime import UTC, datetime
from uuid import uuid4

from money_tracker.workflows import (
    CallbackAction,
    ChannelMessage,
    DispatchContext,
    PendingMovementAction,
    review_decision_from_message,
)


def _message(text: str, **values: object) -> ChannelMessage:
    """Build a normalized category response."""

    return ChannelMessage(
        source_reference="test:dispatch",
        received_at=datetime(2026, 9, 8, tzinfo=UTC),
        text=text,
        **values,
    )


def test_text_review_branches_are_interpreted_without_a_model() -> None:
    """Cover every Italian acceptance branch with the bounded text grammar."""

    expected = {
        "Approva": (CallbackAction.APPROVE_CATEGORY, None),
        "Usa una delle categorie esistenti": (
            CallbackAction.USE_BEST_EXISTING,
            None,
        ),
        "Usa la categoria Cibo e accessori": (
            CallbackAction.USE_NAMED_EXISTING,
            "Cibo e accessori",
        ),
        "Proponine un'altra": (CallbackAction.REPROPOSE_CATEGORY, None),
        "Annulla": (CallbackAction.CANCEL_REVIEW, None),
    }

    for text, (action, name) in expected.items():
        decision = review_decision_from_message(_message(text))
        assert decision is not None
        assert decision.action is action
        assert decision.category_name == name


def test_unrelated_review_text_is_not_guessed_into_a_write() -> None:
    """Require an explicit branch statement before review settlement."""

    assert (
        review_decision_from_message(_message("Forse quella per gli animali")) is None
    )


def test_named_category_callback_requires_owner_text() -> None:
    """Reject a typed named-category branch when its only free value is absent."""

    message = ChannelMessage(
        source_reference="test:dispatch",
        received_at=datetime(2026, 9, 8, tzinfo=UTC),
        callback_action=CallbackAction.USE_NAMED_EXISTING,
        pending_action_id=uuid4(),
    )

    assert review_decision_from_message(message) is None


def test_pending_action_resolution_is_exact() -> None:
    """Resolve only the trusted action-to-movement pair supplied by runtime."""

    action_id = uuid4()
    movement_id = uuid4()
    context = DispatchContext(
        movement_actions=(PendingMovementAction(action_id, movement_id),)
    )

    assert context.movement_for_action(action_id) == movement_id
    assert context.movement_for_action(uuid4()) is None
