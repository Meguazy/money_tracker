"""Tests for deterministic pre-routing and model classification guards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.workflows import (
    CATEGORY_REVIEW_ACTIONS,
    MUTATING_INTENTS,
    CallbackAction,
    ChannelMessage,
    RouteClassification,
    RoutingContext,
    RoutingReason,
    RoutingSource,
    SupportedCommand,
    WorkflowIntent,
    accept_classification,
    parse_command,
    pre_route,
)

RECEIVED_AT = datetime(2026, 9, 2, 18, 30, tzinfo=UTC)


def _message(**overrides: object) -> ChannelMessage:
    """Build one normalized inbound message with explicit overrides."""

    fields: dict[str, object] = {
        "source_reference": "channel:inbox:1",
        "received_at": RECEIVED_AT,
    }
    fields.update(overrides)
    return ChannelMessage(**fields)  # type: ignore[arg-type]


def _classification(
    intent: WorkflowIntent,
    confidence: float = 0.99,
) -> RouteClassification:
    """Build one schema-valid classification for guard tests."""

    return RouteClassification(
        intent=intent,
        confidence=confidence,
        evidence="testo del proprietario",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/help", SupportedCommand.HELP),
        ("/HELP", SupportedCommand.HELP),
        ("/status", SupportedCommand.STATUS),
        ("/pair ABC123", SupportedCommand.PAIR),
        ("/help@money_tracker_bot", SupportedCommand.HELP),
        ("/unknown", None),
        ("aiuto", None),
        ("ho speso 12 euro per /help", None),
    ],
)
def test_command_parsing_is_exact_and_never_inferred_from_prose(
    text: str,
    expected: SupportedCommand | None,
) -> None:
    """Recognize only a leading supported command token."""

    assert parse_command(text) == expected


def test_explicit_interface_decision_outranks_every_other_signal() -> None:
    """Honor an owner decision without consulting a model."""

    pending = uuid4()
    decision = pre_route(
        _message(
            text="ho speso 12 euro",
            has_image=True,
            callback_action=CallbackAction.APPROVE_CATEGORY,
            pending_action_id=pending,
        )
    )

    assert decision is not None
    assert decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert decision.source is RoutingSource.DETERMINISTIC
    assert decision.reason is RoutingReason.CALLBACK_ACTION
    assert decision.pending_action_id == pending


@pytest.mark.parametrize("action", sorted(CATEGORY_REVIEW_ACTIONS))
def test_every_category_review_branch_routes_to_the_review_workflow(
    action: CallbackAction,
) -> None:
    """Cover approve, best existing, named existing, re-propose, and cancel."""

    decision = pre_route(_message(callback_action=action, pending_action_id=uuid4()))

    assert decision is not None
    assert decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert decision.callback_action is action


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (CallbackAction.MODIFY_EXPENSE, WorkflowIntent.CORRECTION),
        (CallbackAction.POST_EXPENSE, WorkflowIntent.CORRECTION),
        (CallbackAction.UNDO_EXPENSE, WorkflowIntent.UNDO),
    ],
)
def test_movement_callbacks_route_to_their_own_workflow(
    action: CallbackAction,
    expected: WorkflowIntent,
) -> None:
    """Keep lifecycle callbacks distinct from category review."""

    decision = pre_route(_message(callback_action=action, pending_action_id=uuid4()))

    assert decision is not None
    assert decision.intent is expected


def test_uncorrelated_callback_is_clarified_instead_of_applied() -> None:
    """Never apply a decision to whichever action happens to be pending."""

    decision = pre_route(_message(callback_action=CallbackAction.APPROVE_CATEGORY))

    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.UNCORRELATED_CALLBACK
    assert decision.pending_action_id is None


def test_image_outranks_its_own_caption() -> None:
    """Prevent a caption from turning a review-only receipt into a write."""

    decision = pre_route(
        _message(text="ho speso 40 euro al supermercato", has_image=True)
    )

    assert decision is not None
    assert decision.intent is WorkflowIntent.RECEIPT
    assert decision.source is RoutingSource.DETERMINISTIC
    assert decision.reason is RoutingReason.IMAGE_ATTACHMENT
    assert decision.intent not in MUTATING_INTENTS


def test_image_with_a_command_caption_still_routes_to_receipt() -> None:
    """Keep image precedence total rather than wording-dependent."""

    decision = pre_route(_message(text="/status", has_image=True))

    assert decision is not None
    assert decision.intent is WorkflowIntent.RECEIPT


def test_supported_command_routes_deterministically() -> None:
    """Execute a stated command without model interpretation."""

    decision = pre_route(_message(text="  /pair ABC123  "))

    assert decision is not None
    assert decision.intent is WorkflowIntent.COMMAND
    assert decision.command is SupportedCommand.PAIR
    assert decision.reason is RoutingReason.SUPPORTED_COMMAND


def test_routing_decision_does_not_carry_command_arguments() -> None:
    """Keep a one-use pairing code out of routing telemetry."""

    decision = pre_route(_message(text="/pair SECRET-CODE"))

    assert decision is not None
    assert "SECRET-CODE" not in repr(decision)


@pytest.mark.parametrize("text", ["/unknown", "/", "/nope extra"])
def test_unknown_command_is_clarified(text: str) -> None:
    """Answer an unrecognized command without guessing an intent."""

    decision = pre_route(_message(text=text))

    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.UNKNOWN_COMMAND


def test_blank_text_is_clarified_without_reaching_a_model() -> None:
    """Avoid spending an inference call on empty input."""

    decision = pre_route(_message(text="   "))

    assert decision is not None
    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.EMPTY_INPUT


def test_free_text_defers_to_model_classification() -> None:
    """Hand only genuinely ambiguous prose to the nondeterministic half."""

    assert pre_route(_message(text="ho speso 12 euro per i croccantini")) is None


def test_accepted_classification_records_its_model_provenance() -> None:
    """Keep the deciding half and its confidence visible downstream."""

    decision = accept_classification(
        _classification(WorkflowIntent.EXPENSE, 0.97),
        message=_message(text="ho speso 12 euro"),
        context=RoutingContext(),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.EXPENSE
    assert decision.source is RoutingSource.MODEL
    assert decision.reason is RoutingReason.MODEL_CLASSIFICATION
    assert decision.confidence == 0.97


def test_low_confidence_classification_is_clarified() -> None:
    """Ask a targeted question instead of acting on a weak decision."""

    decision = accept_classification(
        _classification(WorkflowIntent.EXPENSE, 0.42),
        message=_message(text="forse ho speso qualcosa"),
        context=RoutingContext(),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.LOW_CONFIDENCE
    assert decision.confidence == 0.42


def test_confidence_bar_is_inclusive() -> None:
    """Treat the calibrated threshold itself as passing."""

    decision = accept_classification(
        _classification(WorkflowIntent.QUERY, 0.9),
        message=_message(text="quanto ho speso"),
        context=RoutingContext(),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.QUERY


def test_confidence_bar_must_be_stated_within_the_unit_interval() -> None:
    """Refuse a nonsensical threshold rather than silently clamping it."""

    with pytest.raises(ValueError, match="unit interval"):
        accept_classification(
            _classification(WorkflowIntent.QUERY),
            message=_message(text="quanto ho speso"),
            context=RoutingContext(),
            minimum_confidence=1.5,
        )


def test_category_follow_up_needs_a_pending_review() -> None:
    """Refuse to continue a review that does not exist."""

    decision = accept_classification(
        _classification(WorkflowIntent.CATEGORY_FOLLOW_UP),
        message=_message(text="usa la categoria Animali"),
        context=RoutingContext(),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.NO_PENDING_REVIEW


def test_single_pending_review_needs_no_reply_reference() -> None:
    """Correlate the only pending review without asking again."""

    pending = uuid4()

    decision = accept_classification(
        _classification(WorkflowIntent.CATEGORY_FOLLOW_UP),
        message=_message(text="usa la categoria Animali"),
        context=RoutingContext(pending_category_review_ids=(pending,)),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert decision.pending_action_id == pending


def test_several_pending_reviews_require_an_explicit_reply_reference() -> None:
    """Clarify rather than guess which paused review the owner answered."""

    first, second = uuid4(), uuid4()
    context = RoutingContext(pending_category_review_ids=(first, second))

    ambiguous = accept_classification(
        _classification(WorkflowIntent.CATEGORY_FOLLOW_UP),
        message=_message(text="approva"),
        context=context,
        minimum_confidence=0.9,
    )
    correlated = accept_classification(
        _classification(WorkflowIntent.CATEGORY_FOLLOW_UP),
        message=_message(text="approva", reply_to_action_id=second),
        context=context,
        minimum_confidence=0.9,
    )

    assert ambiguous.intent is WorkflowIntent.CLARIFICATION
    assert ambiguous.reason is RoutingReason.AMBIGUOUS_PENDING_REVIEW
    assert correlated.intent is WorkflowIntent.CATEGORY_FOLLOW_UP
    assert correlated.pending_action_id == second


def test_reply_to_an_unknown_action_does_not_hijack_a_pending_review() -> None:
    """Reject a stale or foreign reply reference instead of substituting one."""

    pending = uuid4()

    decision = accept_classification(
        _classification(WorkflowIntent.CATEGORY_FOLLOW_UP),
        message=_message(text="approva", reply_to_action_id=uuid4()),
        context=RoutingContext(pending_category_review_ids=(pending,)),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.CLARIFICATION
    assert decision.reason is RoutingReason.NO_PENDING_REVIEW


def test_unrelated_message_while_a_review_is_pending_is_routed_independently() -> None:
    """Process other input without consuming or losing the pending review."""

    pending = uuid4()

    decision = accept_classification(
        _classification(WorkflowIntent.QUERY),
        message=_message(text="quanto ho speso per gli abbonamenti"),
        context=RoutingContext(pending_category_review_ids=(pending,)),
        minimum_confidence=0.9,
    )

    assert decision.intent is WorkflowIntent.QUERY
    assert decision.pending_action_id is None


@pytest.mark.parametrize("intent", [WorkflowIntent.CORRECTION, WorkflowIntent.UNDO])
def test_movement_change_needs_a_resolved_target(intent: WorkflowIntent) -> None:
    """Refuse to correct or void a movement the runtime never resolved."""

    absent = accept_classification(
        _classification(intent),
        message=_message(text="annulla l'ultima spesa"),
        context=RoutingContext(),
        minimum_confidence=0.9,
    )

    assert absent.intent is WorkflowIntent.CLARIFICATION
    assert absent.reason is RoutingReason.NO_CORRECTABLE_MOVEMENT


@pytest.mark.parametrize("intent", [WorkflowIntent.CORRECTION, WorkflowIntent.UNDO])
def test_movement_change_targets_the_runtime_resolved_movement(
    intent: WorkflowIntent,
) -> None:
    """Take the target from trusted context, never from the classification."""

    movement = uuid4()

    decision = accept_classification(
        _classification(intent),
        message=_message(text="annulla l'ultima spesa"),
        context=RoutingContext(latest_correctable_movement_id=movement),
        minimum_confidence=0.9,
    )

    assert decision.intent is intent
    assert decision.target_movement_id == movement


def test_classification_cannot_claim_a_deterministic_route() -> None:
    """Defend the guard even when a contract instance bypasses validation."""

    classification = RouteClassification.model_construct(
        intent=WorkflowIntent.RECEIPT,
        confidence=1.0,
        evidence="foto",
        unknowns=(),
    )

    with pytest.raises(ValueError, match="assigned deterministically"):
        accept_classification(
            classification,
            message=_message(text="foto"),
            context=RoutingContext(),
            minimum_confidence=0.9,
        )


@pytest.mark.parametrize(
    "intent",
    [
        WorkflowIntent.QUERY,
        WorkflowIntent.ADVICE,
        WorkflowIntent.NON_FINANCE,
        WorkflowIntent.UNSUPPORTED,
        WorkflowIntent.CLARIFICATION,
    ],
)
def test_read_only_and_inert_classifications_never_reach_a_write(
    intent: WorkflowIntent,
) -> None:
    """Hold the safety gate that forbids a write for a non-write request."""

    decision = accept_classification(
        _classification(intent),
        message=_message(text="quanto ho speso"),
        context=RoutingContext(
            pending_category_review_ids=(uuid4(),),
            latest_correctable_movement_id=uuid4(),
        ),
        minimum_confidence=0.9,
    )

    assert decision.intent is intent
    assert decision.intent not in MUTATING_INTENTS


def test_routing_reasons_carry_no_owner_text() -> None:
    """Keep routing telemetry free of message content."""

    secret = "ho speso 12 euro dal veterinario"

    decisions = [
        pre_route(_message(text=secret, has_image=True)),
        pre_route(_message(text="/nope")),
        accept_classification(
            _classification(WorkflowIntent.EXPENSE, 0.1),
            message=_message(text=secret),
            context=RoutingContext(),
            minimum_confidence=0.9,
        ),
    ]

    for decision in decisions:
        assert decision is not None
        assert secret not in repr(decision)
        assert isinstance(decision.reason.value, str)


def test_uuid_targets_are_stable_references_not_embedded_records() -> None:
    """Keep graph-bound state to small serializable values and references."""

    movement = UUID("00000000-0000-0000-0000-0000000000aa")

    decision = accept_classification(
        _classification(WorkflowIntent.UNDO),
        message=_message(text="annulla"),
        context=RoutingContext(latest_correctable_movement_id=movement),
        minimum_confidence=0.9,
    )

    for value in (
        decision.intent,
        decision.source,
        decision.reason,
        decision.target_movement_id,
        decision.confidence,
    ):
        assert value is None or isinstance(value, str | float | UUID)
