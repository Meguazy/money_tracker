"""Behavior tests for the Italian owner-facing response renderer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from money_tracker.domain import ExpenseStatus, Money
from money_tracker.messaging import OwnerBinding, PendingActionTarget
from money_tracker.presentation import (
    MovementView,
    ResponseKind,
    TelegramResponseRenderer,
    action_code,
    action_for_code,
    downtime_notice,
    pairing_confirmation,
)
from money_tracker.telegram import (
    TelegramInboundUpdate,
    TelegramUpdateIngestor,
)
from money_tracker.workflows import (
    CallbackAction,
    DispatchOutcome,
    DispatchStatus,
    MovementChangeReason,
    ReadReason,
    ReadTool,
    ResolutionReason,
    ReviewQuestion,
    ReviewReason,
    RoutingDecision,
    RoutingReason,
    RoutingSource,
    SupportedCommand,
    WorkflowIntent,
)
from money_tracker.workflows.dispatch import DispatchReason

ACTION_ID = UUID("11111111-2222-3333-4444-555555555555")
EXPENSE_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
PROPOSAL_ID = UUID("99999999-8888-7777-6666-555555555555")
OCCURRED_AT = datetime(2026, 9, 9, 21, 30, tzinfo=UTC)

# Reason codes never used as a clarification: they accompany a route that was
# accepted, so the renderer answers them through the route instead.
ACCEPTED_ROUTING_REASONS = {
    RoutingReason.CALLBACK_ACTION.value,
    RoutingReason.SUPPORTED_COMMAND.value,
    RoutingReason.MODEL_CLASSIFICATION.value,
    RoutingReason.IMAGE_ATTACHMENT.value,
}


def _routing(
    intent: WorkflowIntent = WorkflowIntent.EXPENSE,
    *,
    command: SupportedCommand | None = None,
) -> RoutingDecision:
    """Build one accepted routing decision for a rendered outcome."""

    return RoutingDecision(
        intent=intent,
        source=RoutingSource.DETERMINISTIC,
        reason=RoutingReason.SUPPORTED_COMMAND,
        command=command,
    )


def _movement(**overrides: Any) -> MovementView:
    """Build the committed movement facts a summary is rendered from."""

    fields: dict[str, Any] = {
        "amount": Money(1200),
        "occurred_at": OCCURRED_AT,
        "description": "croccantini per i gatti",
        "status": ExpenseStatus.POSTED,
        "category_names": ("Cibo e accessori",),
        "funding_source_names": ("Carta principale",),
    }
    fields.update(overrides)
    return MovementView(**fields)


def _question(**overrides: Any) -> ReviewQuestion:
    """Build the paused review question shown to the owner."""

    fields: dict[str, Any] = {
        "proposal_id": str(PROPOSAL_ID),
        "candidate_name": "Giardinaggio",
        "parent_name": "Casa e utenze",
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "alternative_names": ("Casa",),
        "can_repropose": True,
    }
    fields.update(overrides)
    return ReviewQuestion(**fields)


def _render(outcome: DispatchOutcome, **kwargs: Any) -> Any:
    """Render one outcome through the production renderer."""

    return TelegramResponseRenderer().render(outcome, action_id=ACTION_ID, **kwargs)


def test_every_clarification_reason_has_an_owner_question() -> None:
    """Never answer a stable reason code with the generic fallback."""

    codes = {
        reason.value
        for enum in (
            RoutingReason,
            DispatchReason,
            MovementChangeReason,
            ReviewReason,
            ReadReason,
            ResolutionReason,
        )
        for reason in enum
    } - ACCEPTED_ROUTING_REASONS

    rendered = {
        code: _render(
            DispatchOutcome(
                routing=_routing(),
                status=DispatchStatus.CLARIFICATION,
                clarification_reason=code,
            )
        ).text
        for code in codes
    }

    generic = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.CLARIFICATION,
            clarification_reason="a_code_no_enum_defines",
        )
    ).text
    assert [code for code, text in rendered.items() if text == generic] == []


def test_a_stored_movement_shows_amount_date_purpose_and_source() -> None:
    """Answer J2 with the four facts the owner needs to check the write."""

    response = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.DRAFTED,
            expense_id=EXPENSE_ID,
        ),
        movement=_movement(status=ExpenseStatus.DRAFT),
    )

    assert response.kind is ResponseKind.MOVEMENT
    assert response.text == (
        "Bozza registrata.\n"
        "12,00 € · croccantini per i gatti\n"
        "Data: 09/09/2026\n"
        "Categoria: Cibo e accessori\n"
        "Fonte: Carta principale\n"
        "Resta in bozza finché non viene registrata."
    )
    assert [button.label for button in response.buttons] == ["Modifica", "Annulla"]
    assert response.target is not None
    assert response.target.target_type is PendingActionTarget.MOVEMENT
    assert response.target.target_id == EXPENSE_ID
    assert response.target.allowed_actions == ("modify", "undo")


def test_a_local_evening_keeps_the_owner_calendar_day() -> None:
    """Render the owner's date rather than the stored UTC day."""

    response = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.DRAFTED,
            expense_id=EXPENSE_ID,
        ),
        movement=_movement(
            occurred_at=datetime(2026, 9, 9, 23, 30, tzinfo=UTC),
        ),
    )

    assert "Data: 10/09/2026" in response.text


def test_a_voided_movement_offers_no_further_control() -> None:
    """Never invite a second undo of a movement that is already voided."""

    response = _render(
        DispatchOutcome(
            routing=_routing(WorkflowIntent.UNDO),
            status=DispatchStatus.VOIDED,
            expense_id=EXPENSE_ID,
        ),
        movement=_movement(status=ExpenseStatus.VOIDED),
    )

    assert response.kind is ResponseKind.VOIDED
    assert response.buttons == ()
    assert response.target is None
    assert response.text.startswith("Movimento annullato.")


def test_a_movement_summary_refuses_to_render_without_its_facts() -> None:
    """Refuse a summary that would omit what the owner must verify."""

    with pytest.raises(ValueError, match="movement summary"):
        _render(
            DispatchOutcome(
                routing=_routing(),
                status=DispatchStatus.DRAFTED,
                expense_id=EXPENSE_ID,
            )
        )


def test_a_paused_review_offers_only_the_branches_it_still_has() -> None:
    """Show the candidate, its rationale, and the branches AD-008 allows."""

    response = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
            expense_id=EXPENSE_ID,
            proposal_id=PROPOSAL_ID,
        ),
        question=_question(),
    )

    assert response.kind is ResponseKind.CATEGORY_REVIEW
    assert "Proposta: Giardinaggio (in Casa e utenze)" in response.text
    assert "Alternative esistenti: Casa" in response.text
    assert "«Usa la categoria <nome>»" in response.text
    assert [button.label for button in response.buttons] == [
        "Approva",
        "Usa una categoria esistente",
        "Proponine un'altra",
        "Annulla",
    ]
    assert response.target is not None
    assert response.target.target_type is PendingActionTarget.CATEGORY_REVIEW
    assert response.target.target_id == PROPOSAL_ID
    assert response.target.allowed_actions == ("approve", "best", "again", "cancel")


def test_an_exhausted_review_stops_offering_another_candidate() -> None:
    """Respect the capped proposal loop in the controls, not only in prose."""

    response = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
            expense_id=EXPENSE_ID,
            proposal_id=PROPOSAL_ID,
        ),
        question=_question(can_repropose=False, alternative_names=()),
    )

    assert response.target is not None
    assert response.target.allowed_actions == ("approve", "cancel")


def test_a_cancelled_review_states_that_nothing_was_written() -> None:
    """Say plainly that a cancellation created neither category nor movement."""

    response = _render(
        DispatchOutcome(
            routing=_routing(WorkflowIntent.CATEGORY_FOLLOW_UP),
            status=DispatchStatus.CANCELLED,
            proposal_id=PROPOSAL_ID,
        )
    )

    assert response.kind is ResponseKind.REVIEW_CANCELLED
    assert response.buttons == ()
    assert "nessun movimento registrato" in response.text


@pytest.mark.parametrize(
    ("tool", "facts", "expected"),
    [
        (
            ReadTool.CATEGORY_SPENDING,
            {
                "period": "2026-09",
                "category_name": "Spesa alimentare",
                "posted_spend": {"minor_units": 123456, "currency": "EUR"},
                "split": {
                    "personal_funds": {"minor_units": 100000, "currency": "EUR"},
                    "benefit": {"minor_units": 23456, "currency": "EUR"},
                    "other": {"minor_units": 0, "currency": "EUR"},
                },
            },
            "Spesa alimentare a settembre 2026: 1.234,56 € "
            "(fondi personali 1.000,00 €, benefit 234,56 €).",
        ),
        (
            ReadTool.BUDGET_STATUS,
            {
                "period": "2026-09",
                "category_name": "Trasporti",
                "status": {
                    "target": {"minor_units": 10000, "currency": "EUR"},
                    "posted_spend": {"minor_units": 12500, "currency": "EUR"},
                    "remaining": {"minor_units": -2500, "currency": "EUR"},
                },
            },
            "Budget Trasporti a settembre 2026: 125,00 € su 100,00 €, "
            "25,00 € oltre il budget.",
        ),
        (
            ReadTool.BUDGET_STATUS,
            {"period": "2026-09", "category_name": "Animali", "status": None},
            "Nessun budget configurato per Animali a settembre 2026.",
        ),
        (
            ReadTool.BUDGETS_AT_RISK,
            {
                "period": "2026-09",
                "statuses": [
                    {
                        "category_name": "Trasporti",
                        "target": {"minor_units": 10000, "currency": "EUR"},
                        "posted_spend": {"minor_units": 9000, "currency": "EUR"},
                        "remaining": {"minor_units": 1000, "currency": "EUR"},
                    }
                ],
                "unbudgeted_category_names": ["Animali"],
            },
            "Budget di settembre 2026:\n"
            "- Trasporti: 90,00 € su 100,00 €, 10,00 € disponibili\n"
            "Senza budget: Animali.",
        ),
    ],
)
def test_an_answer_restates_only_the_tool_result(
    tool: ReadTool,
    facts: dict[str, Any],
    expected: str,
) -> None:
    """Narrate the deterministic tool output without inventing a figure."""

    response = _render(
        DispatchOutcome(
            routing=_routing(WorkflowIntent.QUERY),
            status=DispatchStatus.ANSWERED,
            executed_tool=tool,
            facts=facts,
        )
    )

    assert response.kind is ResponseKind.ANSWER
    assert response.text == expected


def test_a_month_without_spending_is_stated_rather_than_shown_as_zero() -> None:
    """Say that nothing was recorded instead of listing three zero amounts."""

    response = _render(
        DispatchOutcome(
            routing=_routing(WorkflowIntent.QUERY),
            status=DispatchStatus.ANSWERED,
            executed_tool=ReadTool.FUNDING_CLASS_SPLIT,
            facts={
                "period": "2026-01",
                "split": {
                    "personal_funds": {"minor_units": 0, "currency": "EUR"},
                    "benefit": {"minor_units": 0, "currency": "EUR"},
                    "other": {"minor_units": 0, "currency": "EUR"},
                },
                "sources": [],
            },
        )
    )

    assert response.text == "Spese di gennaio 2026: nessuna spesa registrata."


def test_an_answered_outcome_without_facts_is_refused() -> None:
    """Never present an answer the read boundary did not produce."""

    with pytest.raises(ValueError, match="tool facts"):
        _render(
            DispatchOutcome(
                routing=_routing(WorkflowIntent.QUERY),
                status=DispatchStatus.ANSWERED,
            )
        )


@pytest.mark.parametrize(
    ("command", "intent", "kind", "fragment"),
    [
        (SupportedCommand.HELP, WorkflowIntent.COMMAND, ResponseKind.HELP, "/help"),
        (
            SupportedCommand.STATUS,
            WorkflowIntent.COMMAND,
            ResponseKind.STATUS,
            "un messaggio alla volta",
        ),
        (None, WorkflowIntent.RECEIPT, ResponseKind.UNSUPPORTED, "ricevute"),
        (
            None,
            WorkflowIntent.NON_FINANCE,
            ResponseKind.UNSUPPORTED,
            "solo delle tue spese",
        ),
    ],
)
def test_a_route_without_a_specialist_still_answers_the_owner(
    command: SupportedCommand | None,
    intent: WorkflowIntent,
    kind: ResponseKind,
    fragment: str,
) -> None:
    """Leave no accepted update silent, including commands and receipts."""

    response = _render(
        DispatchOutcome(
            routing=_routing(intent, command=command),
            status=DispatchStatus.ROUTED,
        )
    )

    assert response.kind is kind
    assert fragment in response.text
    assert response.buttons == ()


def test_the_same_outcome_renders_one_identical_response() -> None:
    """Keep a replayed render byte-identical so the outbox accepts it."""

    outcome = DispatchOutcome(
        routing=_routing(),
        status=DispatchStatus.DRAFTED,
        expense_id=EXPENSE_ID,
    )

    first = _render(outcome, movement=_movement())
    second = _render(outcome, movement=_movement())

    assert first == second
    assert first.content_hash == second.content_hash


def test_changed_content_changes_the_digest() -> None:
    """Detect a different response under the same logical identity."""

    outcome = DispatchOutcome(
        routing=_routing(),
        status=DispatchStatus.DRAFTED,
        expense_id=EXPENSE_ID,
    )

    first = _render(outcome, movement=_movement())
    other = _render(outcome, movement=_movement(amount=Money(1300)))

    assert first.content_hash != other.content_hash


def test_a_negative_amount_keeps_its_sign_and_grouping() -> None:
    """Render a refund without floating point and with Italian separators."""

    response = _render(
        DispatchOutcome(
            routing=_routing(WorkflowIntent.REFUND),
            status=DispatchStatus.DRAFTED,
            expense_id=EXPENSE_ID,
        ),
        movement=_movement(amount=Money(-1234567)),
    )

    assert "-12.345,67 € · croccantini per i gatti" in response.text


def test_the_callback_vocabulary_round_trips() -> None:
    """Decode exactly the codes the controls encode, and nothing else."""

    for action in CallbackAction:
        assert action_for_code(action_code(action)) is action
    assert action_for_code("approve_category") is None


def test_the_gateway_accepts_every_rendered_control() -> None:
    """Prove the controls satisfy the real ingestion callback grammar."""

    response = _render(
        DispatchOutcome(
            routing=_routing(),
            status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
            expense_id=EXPENSE_ID,
            proposal_id=PROPOSAL_ID,
        ),
        question=_question(),
    )
    repository = _IngestRepository()
    ingestor = TelegramUpdateIngestor(repository)

    report = ingestor.ingest(
        bot_key="telegram-test",
        expected_offset=0,
        updates=tuple(
            TelegramInboundUpdate(
                update_id=index,
                kind="callback_query",
                telegram_user_id=1001,
                telegram_chat_id=1001,
                chat_type="private",
                received_at=OCCURRED_AT,
                telegram_message_id=7,
                callback_data=button.callback_data,
                callback_query_id=f"q{index}",
            )
            for index, button in enumerate(response.buttons)
        ),
        now=OCCURRED_AT,
    )

    assert len(report.accepted) == len(response.buttons)
    assert report.unsupported_count == 0
    for button in response.buttons:
        assert len(button.callback_data.encode("utf-8")) <= 64


class _IngestRepository:
    """Minimum durable boundary the ingestion contract test needs."""

    def __init__(self) -> None:
        self.binding = OwnerBinding("telegram-test", uuid4(), 1001, 1001, OCCURRED_AT)

    def owner_binding(self, _: str) -> OwnerBinding:
        """Return the trusted owner scope."""

        return self.binding

    def commit_ingest_batch(self, **values: Any) -> int:
        """Return the advanced offset for the observed batch."""

        return int(max(values["observed_update_ids"])) + 1


def test_a_downtime_warning_names_when_the_gap_started() -> None:
    """Say what the owner can act on: when I stopped listening, and to resend."""

    notice = downtime_notice(datetime(2026, 9, 8, 5, 30, tzinfo=UTC))

    assert notice.kind is ResponseKind.DOWNTIME
    assert "08/09/2026" in notice.text
    assert "07:30" in notice.text
    assert "riscrivimi" in notice.text
    assert notice.buttons == ()


def test_two_renders_of_one_outage_are_identical() -> None:
    """Let a restart inside the same outage reuse its logical response."""

    started = datetime(2026, 9, 8, 5, 30, tzinfo=UTC)

    assert downtime_notice(started) == downtime_notice(started)
    assert downtime_notice(started).content_hash != (
        downtime_notice(started + timedelta(hours=1)).content_hash
    )


def test_a_pairing_confirmation_offers_no_controls() -> None:
    """Confirm the binding without inviting a callback nothing can answer."""

    response = pairing_confirmation()

    assert response.kind is ResponseKind.PAIRED
    assert response.buttons == ()
    assert response.target is None
    assert "/help" in response.text
