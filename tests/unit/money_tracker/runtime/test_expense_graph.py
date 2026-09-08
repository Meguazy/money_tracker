"""Tests for the compiled expense and refund workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from money_tracker.application import CategoryProposalService, FinanceService
from money_tracker.application.runtime import SystemClock
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryProposal,
    Expense,
    FundingClass,
    FundingSource,
    Taxonomy,
    initial_taxonomy,
)
from money_tracker.runtime import build_expense_graph
from money_tracker.workflows import (
    CATEGORY_FIT,
    EXTRACTION,
    CategoryFit,
    ChannelMessage,
    ExpenseExtraction,
    ExpenseOutcome,
    ExpenseWorkflowState,
    ResolutionReason,
    WorkflowIntent,
    default_prompt,
    initial_expense_state,
)

TAXONOMY = initial_taxonomy()
PROFILE_ID = uuid4()
RECEIVED_AT = datetime(2026, 9, 2, 21, 30, tzinfo=UTC)
CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Carta",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    is_default=True,
)


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
        repair: Any = None,
    ) -> str:
        """Return the next scripted completion and count the request."""

        self.calls += 1
        return self.completions[self.calls - 1]


@dataclass
class InMemoryFinanceStore:
    """A lightweight real implementation of the repository ports used here."""

    expenses: dict[UUID, Expense] = field(default_factory=dict)
    proposals: list[CategoryProposal] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    request_hashes: dict[str, str] = field(default_factory=dict)

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the seeded taxonomy for any profile in these tests."""

        return TAXONOMY

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return one default source for any profile in these tests."""

        return (CARD,)

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Claim the command key once and store the aggregate."""

        prior = self.request_hashes.get(expense.idempotency_key)
        if prior is not None:
            return next(
                item
                for item in self.expenses.values()
                if item.idempotency_key == expense.idempotency_key
            )
        self.request_hashes[expense.idempotency_key] = request_hash
        self.expenses[expense.id] = expense
        self.audits.append(audit_event)
        return expense

    def create_category_proposal(
        self,
        proposal: CategoryProposal,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Store one pending candidate with its audit."""

        self.proposals.append(proposal)
        self.audits.append(audit_event)


@dataclass
class SequentialIds:
    """Deterministic identifier provider for assertable test output."""

    issued: list[UUID] = field(default_factory=list)

    def new(self) -> UUID:
        """Return a fresh identifier and remember it."""

        value = uuid4()
        self.issued.append(value)
        return value


def _extraction(**overrides: Any) -> str:
    """Render one schema-valid extraction completion."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "description": "croccantini per i gatti",
        "evidence": "ho speso 12 euro",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _fit(**overrides: Any) -> str:
    """Render one schema-valid existing-category completion."""

    payload: dict[str, Any] = {
        "outcome": "existing",
        "category_name": "Cibo e accessori",
        "evidence": "croccantini",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _proposal_fit() -> str:
    """Render one schema-valid proposal completion."""

    return json.dumps(
        {
            "outcome": "proposal",
            "proposed_name": "Assicurazione animali",
            "description": "Polizze sanitarie per animali domestici.",
            "rationale": "Nessuna categoria attiva copre le polizze.",
            "parent_name": "Animali",
            "evidence": "polizza",
        }
    )


def _message(text: str = "ho speso 12 euro per i croccantini") -> ChannelMessage:
    """Build one normalized inbound record."""

    return ChannelMessage(
        source_reference="channel:inbox:7",
        received_at=RECEIVED_AT,
        text=text,
    )


def _run(
    completions: list[str],
    *,
    intent: WorkflowIntent = WorkflowIntent.EXPENSE,
    text: str = "ho speso 12 euro per i croccantini",
) -> tuple[ExpenseWorkflowState, InMemoryFinanceStore, ScriptedModel]:
    """Invoke the compiled workflow once and return its state and stores."""

    model = ScriptedModel(completions)
    store = InMemoryFinanceStore()
    ids = SequentialIds()
    graph = build_expense_graph(
        model=model,
        configuration=store,
        finance=FinanceService(store, clock=SystemClock(), ids=ids),  # type: ignore[arg-type]
        proposals=CategoryProposalService(store, clock=SystemClock(), ids=ids),  # type: ignore[arg-type]
        ids=ids,
        extraction_instructions=default_prompt(EXTRACTION).text,
        category_instructions=default_prompt(CATEGORY_FIT).text,
    )
    final = graph.invoke(
        initial_expense_state(
            _message(text), profile_id=PROFILE_ID, routed_intent=intent
        )
    )
    return final, store, model  # type: ignore[return-value]


def test_clear_expense_reaches_a_stored_draft() -> None:
    """Complete the documented clear-expense journey through the workflow."""

    state, store, model = _run([_extraction(), _fit()])

    assert state["outcome"] == ExpenseOutcome.DRAFTED.value
    assert state["expense_id"] is not None
    assert state["proposal_id"] is None
    assert model.calls == 2
    stored = store.expenses[UUID(state["expense_id"])]
    stored.require_reconciled()
    assert stored.payment_allocations[0].funding_source_id == CARD.id


def test_refund_route_reaches_a_negative_draft() -> None:
    """Store a reimbursement when routing and extraction agree on direction."""

    state, store, _ = _run(
        [_extraction(polarity="refund"), _fit()],
        intent=WorkflowIntent.REFUND,
        text="mi hanno rimborsato 12 euro",
    )

    assert state["outcome"] == ExpenseOutcome.DRAFTED.value
    assert store.expenses[UUID(state["expense_id"])].is_refund


@pytest.mark.parametrize(
    ("intent", "polarity"),
    [
        (WorkflowIntent.EXPENSE, "refund"),
        (WorkflowIntent.REFUND, "expense"),
    ],
)
def test_polarity_disagreement_writes_nothing(
    intent: WorkflowIntent,
    polarity: str,
) -> None:
    """Refuse to pick a direction when the two judgments disagree."""

    state, store, model = _run([_extraction(polarity=polarity)], intent=intent)

    assert state["outcome"] == ExpenseOutcome.CLARIFICATION.value
    assert state["clarification_reason"] == (
        ResolutionReason.POLARITY_DISAGREEMENT.value
    )
    assert store.expenses == {}
    assert model.calls == 1


def test_unresolvable_amount_writes_nothing() -> None:
    """Ask about the total instead of storing a movement without one."""

    state, store, _ = _run(
        [
            _extraction(amount_minor_units=None),
            _fit(),
        ]
    )

    assert state["clarification_reason"] == ResolutionReason.AMOUNT_UNKNOWN.value
    assert store.expenses == {}


def test_unnamed_merchant_does_not_block_a_draft() -> None:
    """Treat an absent merchant as absent rather than as a question."""

    state, store, _ = _run([_extraction(merchant=None), _fit()])

    assert state["outcome"] == ExpenseOutcome.DRAFTED.value
    assert store.expenses[UUID(state["expense_id"])].merchant is None


def test_unresolved_funding_words_write_nothing() -> None:
    """Ask which resource rather than charging an arbitrary one."""

    state, store, _ = _run(
        [
            _extraction(payment_parts=[{"funding_source_text": "bancomat"}]),
            _fit(),
        ]
    )

    assert state["clarification_reason"] == (
        ResolutionReason.FUNDING_SOURCE_UNRESOLVED.value
    )
    assert store.expenses == {}


def test_invented_category_writes_nothing() -> None:
    """Refuse a purpose the owner's taxonomy does not contain."""

    state, store, _ = _run([_extraction(), _fit(category_name="Criptovalute")])

    assert state["clarification_reason"] == (ResolutionReason.CATEGORY_UNRESOLVED.value)
    assert store.expenses == {}


@pytest.mark.parametrize("failing_boundary", [0, 1])
def test_exhausted_retry_budget_writes_nothing(failing_boundary: int) -> None:
    """Ask a question when a boundary cannot satisfy its schema."""

    completions = [_extraction(), _fit()]
    completions[failing_boundary] = "not json"
    completions.insert(failing_boundary + 1, "still not json")

    state, store, _ = _run(completions)

    assert state["clarification_reason"] == (
        ResolutionReason.MODEL_OUTPUT_INVALID.value
    )
    assert store.expenses == {}


def test_repaired_boundary_still_reaches_a_draft() -> None:
    """Recover one malformed answer without asking the owner."""

    state, store, model = _run(["not json", _extraction(), _fit()])

    assert state["outcome"] == ExpenseOutcome.DRAFTED.value
    assert model.calls == 3


def test_proposal_stores_a_draft_and_its_pending_candidate() -> None:
    """Keep the draft while only owner approval may extend the taxonomy."""

    state, store, _ = _run(
        [_extraction(), _proposal_fit()], text="polizza per il gatto"
    )

    assert state["outcome"] == ExpenseOutcome.AWAITING_CATEGORY_APPROVAL.value
    assert state["proposal_id"] is not None
    stored = store.expenses[UUID(state["expense_id"])]
    assert stored.category_allocations == ()
    assert stored.payment_allocations != ()
    assert len(store.proposals) == 1
    assert store.proposals[0].proposed_name == "Assicurazione animali"
    assert {item.id for item in TAXONOMY.categories} == {
        item.id for item in initial_taxonomy().categories
    }


def test_every_stored_movement_is_a_draft() -> None:
    """Hold the rule that no text message may post itself."""

    for completions in ([_extraction(), _fit()], [_extraction(), _proposal_fit()]):
        state, store, _ = _run(completions)
        stored = store.expenses[UUID(state["expense_id"])]
        assert stored.status.value == "draft"


def test_clarification_state_stays_json_primitive() -> None:
    """Keep the workflow state checkpointable on the failure path too."""

    state, _, _ = _run([_extraction(polarity="refund")])

    assert json.loads(json.dumps(state)) == state


def test_drafted_state_stays_json_primitive() -> None:
    """Keep the workflow state checkpointable on the success path."""

    state, _, _ = _run([_extraction(), _fit()])

    assert json.loads(json.dumps(state)) == state


def test_workflow_accepts_only_a_movement_route() -> None:
    """Refuse to run the movement workflow for another intent."""

    with pytest.raises(ValueError, match="only a movement route"):
        initial_expense_state(
            _message(), profile_id=PROFILE_ID, routed_intent=WorkflowIntent.QUERY
        )


def test_workflow_requires_owner_text() -> None:
    """Refuse to run the boundaries with nothing to interpret."""

    with pytest.raises(ValueError, match="requires owner text"):
        initial_expense_state(
            ChannelMessage(
                source_reference="channel:inbox:8",
                received_at=RECEIVED_AT,
                has_image=True,
            ),
            profile_id=PROFILE_ID,
            routed_intent=WorkflowIntent.EXPENSE,
        )


def test_category_boundary_sees_only_the_owner_active_categories() -> None:
    """Let a model name a category only from the owner's own list."""

    from money_tracker.workflows import render_active_categories

    rendered = render_active_categories(("Animali", "Cibo e accessori"))

    assert "- Animali" in rendered
    assert "- Cibo e accessori" in rendered
    with pytest.raises(ValueError, match="at least one active category"):
        render_active_categories(())


def test_boundary_answers_are_carried_in_state_not_module_globals() -> None:
    """Let a resumed thread reuse both answers without re-asking the model."""

    state, _, _ = _run([_extraction(), _fit()])

    assert ExpenseExtraction.model_validate_json(state["extraction_json"])
    assert CategoryFit.model_validate_json(state["category_fit_json"])


def test_persistence_refuses_state_missing_an_earlier_answer() -> None:
    """Fail loudly if the topology stops guaranteeing the node order.

    The graph's edges make this unreachable today. The guard exists so a future
    change that reorders or skips a boundary is caught instead of silently
    assembling a movement from an absent answer.
    """

    from money_tracker.runtime.expense_graph import _required

    with pytest.raises(KeyError, match="without extraction_json"):
        _required(ExpenseWorkflowState(), "extraction_json")
