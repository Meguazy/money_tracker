"""Tests for the authorization structure of the workflow intent taxonomy."""

from __future__ import annotations

from money_tracker.workflows import (
    DETERMINISTIC_ONLY_INTENTS,
    INERT_INTENTS,
    MODEL_ROUTABLE_INTENTS,
    MUTATING_INTENTS,
    READ_ONLY_INTENTS,
    WorkflowIntent,
)


def test_every_intent_has_exactly_one_authority_class() -> None:
    """Leave no intent whose mutation authority is undeclared or doubled."""

    classes = (MUTATING_INTENTS, READ_ONLY_INTENTS, INERT_INTENTS)

    assert MUTATING_INTENTS | READ_ONLY_INTENTS | INERT_INTENTS == frozenset(
        WorkflowIntent
    )
    for index, first in enumerate(classes):
        for second in classes[index + 1 :]:
            assert not first & second


def test_read_only_and_inert_intents_cannot_mutate_finance_data() -> None:
    """Keep questions, advice, receipts, and non-finance input out of writes."""

    assert WorkflowIntent.QUERY not in MUTATING_INTENTS
    assert WorkflowIntent.ADVICE not in MUTATING_INTENTS
    assert WorkflowIntent.NON_FINANCE not in MUTATING_INTENTS
    assert WorkflowIntent.UNSUPPORTED not in MUTATING_INTENTS
    assert WorkflowIntent.CLARIFICATION not in MUTATING_INTENTS
    assert WorkflowIntent.RECEIPT not in MUTATING_INTENTS


def test_model_may_not_select_a_deterministic_route() -> None:
    """Reserve image and command routing for reproducible pre-routing."""

    assert DETERMINISTIC_ONLY_INTENTS == frozenset(
        {WorkflowIntent.RECEIPT, WorkflowIntent.COMMAND}
    )
    assert MODEL_ROUTABLE_INTENTS.isdisjoint(DETERMINISTIC_ONLY_INTENTS)
    assert MODEL_ROUTABLE_INTENTS | DETERMINISTIC_ONLY_INTENTS == frozenset(
        WorkflowIntent
    )
