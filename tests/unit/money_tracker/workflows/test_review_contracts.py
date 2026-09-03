"""Tests for the framework-independent owner category review contracts."""

from __future__ import annotations

import pytest

from money_tracker.domain import DomainError
from money_tracker.workflows import (
    CallbackAction,
    ReviewDecision,
    ReviewQuestion,
)


def _question(**overrides: object) -> ReviewQuestion:
    """Build one paused question with explicit overrides."""

    fields: dict[str, object] = {
        "proposal_id": "00000000-0000-0000-0000-000000000001",
        "candidate_name": "Giardinaggio",
        "parent_name": None,
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "alternative_names": (),
        "can_repropose": True,
    }
    fields.update(overrides)
    return ReviewQuestion(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "action",
    [
        CallbackAction.APPROVE_CATEGORY,
        CallbackAction.USE_BEST_EXISTING,
        CallbackAction.REPROPOSE_CATEGORY,
        CallbackAction.CANCEL_REVIEW,
    ],
)
def test_every_review_branch_is_accepted_without_a_name(
    action: CallbackAction,
) -> None:
    """Carry no free value on a branch that needs none."""

    decision = ReviewDecision(action)

    assert decision.action is action
    assert decision.category_name is None


@pytest.mark.parametrize(
    "action", [CallbackAction.MODIFY_EXPENSE, CallbackAction.UNDO_EXPENSE]
)
def test_a_movement_branch_is_not_a_review_decision(action: CallbackAction) -> None:
    """Keep correction and undo out of the review's authority."""

    with pytest.raises(DomainError, match="category review"):
        ReviewDecision(action)


def test_the_named_branch_requires_a_usable_name() -> None:
    """Refuse a named reuse that names nothing."""

    with pytest.raises(DomainError, match="requires a name"):
        ReviewDecision(CallbackAction.USE_NAMED_EXISTING)
    with pytest.raises(DomainError, match="requires a name"):
        ReviewDecision(CallbackAction.USE_NAMED_EXISTING, "   ")


def test_only_the_named_branch_may_carry_a_name() -> None:
    """Prevent a name from riding along on a branch that ignores it."""

    with pytest.raises(DomainError, match="only the named branch"):
        ReviewDecision(CallbackAction.APPROVE_CATEGORY, "Cibo e accessori")


def test_the_owner_typed_name_is_normalized_for_resolution() -> None:
    """Resolve owner text the same way the taxonomy compares it."""

    decision = ReviewDecision(
        CallbackAction.USE_NAMED_EXISTING, "  Cibo   e  accessori "
    )

    assert decision.normalized_category_name == "Cibo e accessori"


def test_a_branch_without_a_name_exposes_none_to_read() -> None:
    """Refuse to invent a name for a branch that carries none."""

    decision = ReviewDecision(CallbackAction.APPROVE_CATEGORY)

    with pytest.raises(DomainError, match="carries no category name"):
        _ = decision.normalized_category_name


def test_a_question_requires_a_candidate_and_a_rationale() -> None:
    """Refuse a question the owner could not act on."""

    with pytest.raises(DomainError, match="candidate name"):
        _question(candidate_name="   ")
    with pytest.raises(DomainError, match="rationale"):
        _question(rationale="   ")


def test_offered_actions_follow_the_review_state() -> None:
    """Offer exactly the branches this paused review can still apply."""

    bare = _question().offered_actions()
    listed = _question(alternative_names=("Cibo e accessori",)).offered_actions()
    capped = _question(can_repropose=False).offered_actions()

    assert bare == (
        CallbackAction.APPROVE_CATEGORY,
        CallbackAction.USE_NAMED_EXISTING,
        CallbackAction.REPROPOSE_CATEGORY,
        CallbackAction.CANCEL_REVIEW,
    )
    assert CallbackAction.USE_BEST_EXISTING in listed
    assert CallbackAction.REPROPOSE_CATEGORY not in capped
    assert CallbackAction.USE_NAMED_EXISTING in capped
    assert CallbackAction.CANCEL_REVIEW in capped
