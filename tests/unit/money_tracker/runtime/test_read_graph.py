"""Tests for the compiled read-only question and advice workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from money_tracker.application import ReportingService
from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Taxonomy,
    YearMonth,
    initial_taxonomy,
)
from money_tracker.runtime import build_read_graph
from money_tracker.workflows import (
    TOOL_SELECTION,
    ChannelMessage,
    ReadOutcome,
    ReadReason,
    ReadSelectionPolicyVersion,
    ReadTool,
    WorkflowIntent,
    default_prompt,
    initial_read_state,
)

PROFILE_ID = uuid4()
ROME = ZoneInfo("Europe/Rome")
RECEIVED_AT = datetime(2026, 9, 15, 12, tzinfo=ROME)
TAXONOMY = initial_taxonomy()
CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Carta",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    is_default=True,
)
VOUCHERS = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Buoni pasto",
    funding_class=FundingClass.BENEFIT,
    balance_mode=BalanceMode.UNTRACKED,
)


@dataclass
class ScriptedModel:
    """A real port implementation replaying a fixed script of completions."""

    completions: list[str] = field(default_factory=list)
    calls: int = 0
    instructions: list[str] = field(default_factory=list)

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: Any = None,
    ) -> str:
        """Return the next scripted completion and record the request."""

        self.calls += 1
        self.instructions.append(instructions)
        return self.completions[self.calls - 1]


@dataclass
class InMemoryReporting:
    """A real read-only implementation of the reporting contract."""

    expenses: tuple[Expense, ...] = ()
    budgets: tuple[MonthlyBudget, ...] = ()

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the seeded taxonomy."""

        return TAXONOMY

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return both configured sources."""

        return (CARD, VOUCHERS)

    def get_posted_expenses(
        self, profile_id: UUID, *, period: YearMonth
    ) -> tuple[Expense, ...]:
        """Return posted movements occurring in the requested month."""

        start, end = period.bounds()
        return tuple(
            expense
            for expense in self.expenses
            if expense.status is ExpenseStatus.POSTED
            and start <= expense.occurred_at < end
        )

    def get_budget(self, profile_id: UUID, category_id: UUID) -> MonthlyBudget | None:
        """Return one configured budget or None."""

        return next(
            (item for item in self.budgets if item.category_id == category_id), None
        )

    def get_budgets(self, profile_id: UUID) -> tuple[MonthlyBudget, ...]:
        """Return every configured budget."""

        return self.budgets


def _posted(
    category_name: str,
    minor_units: int,
    *,
    month: int = 9,
    funding_source: FundingSource = CARD,
) -> Expense:
    """Build one posted movement allocated to a named category."""

    category = TAXONOMY.resolve_active(category_name)
    amount = Money(minor_units)
    return Expense(
        id=uuid4(),
        profile_id=PROFILE_ID,
        amount=amount,
        occurred_at=datetime(2026, month, 10, 12, tzinfo=ROME),
        description=f"spesa {category_name}",
        merchant=None,
        status=ExpenseStatus.POSTED,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id, amount=amount, provenance="test"
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=funding_source.id, amount=amount),
        ),
        source_reference=f"test:{uuid4()}",
        idempotency_key=f"key:{uuid4()}",
    )


def _budget(category_name: str, minor_units: int) -> MonthlyBudget:
    """Build one recurring budget for a named category."""

    return MonthlyBudget(
        id=uuid4(),
        profile_id=PROFILE_ID,
        category_id=TAXONOMY.resolve_active(category_name).id,
        recurring_target=Money(minor_units),
    )


def _request(**overrides: Any) -> str:
    """Render one schema-valid tool request completion."""

    payload: dict[str, Any] = {
        "tool": "category_spending",
        "category_name": "Abbonamenti",
        "evidence": "quanto ho speso per gli abbonamenti",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _run(
    completions: list[str],
    *,
    expenses: tuple[Expense, ...] = (),
    budgets: tuple[MonthlyBudget, ...] = (),
    intent: WorkflowIntent = WorkflowIntent.QUERY,
    text: str = "quanto ho speso per gli abbonamenti questo mese?",
    category_names: tuple[str, ...] = (),
    funding_source_names: tuple[str, ...] = (),
    selection_policy: ReadSelectionPolicyVersion = (
        ReadSelectionPolicyVersion.MODEL_ONLY_V0
    ),
) -> tuple[dict[str, Any], ScriptedModel]:
    """Invoke the compiled read workflow over a real reporting service."""

    model = ScriptedModel(completions)
    compiled = build_read_graph(
        model=model,
        reporting=ReportingService(InMemoryReporting(expenses, budgets)),
        selection_instructions=default_prompt(TOOL_SELECTION).text,
        category_names=category_names,
        funding_source_names=funding_source_names,
        selection_policy=selection_policy,
    )
    state = compiled.invoke(
        initial_read_state(
            ChannelMessage(
                source_reference=f"test:{uuid4()}",
                received_at=RECEIVED_AT,
                text=text,
            ),
            profile_id=PROFILE_ID,
            routed_intent=intent,
        )
    )
    return dict(state), model


def _answer(state: dict[str, Any]) -> dict[str, Any]:
    """Return the figures the run recorded."""

    return json.loads(state["answer_json"])


def test_a_question_answers_from_stored_records() -> None:
    """Complete J7's spending question through the whole assembly."""

    state, model = _run(
        [_request()],
        expenses=(_posted("Abbonamenti", 599), _posted("Abbonamenti", 1200)),
    )

    assert state["outcome"] == ReadOutcome.ANSWERED.value
    assert state["executed_tool"] == ReadTool.CATEGORY_SPENDING.value
    facts = _answer(state)
    assert facts["posted_spend"] == {"minor_units": 1799, "currency": "EUR"}
    assert facts["category_name"] == "Abbonamenti"
    assert facts["period"] == "2026-09"
    assert model.calls == 1


def test_the_month_in_progress_is_resolved_from_the_message() -> None:
    """Read the month the message was sent in when none was stated."""

    state, _ = _run(
        [_request(period=None)],
        expenses=(
            _posted("Abbonamenti", 599, month=9),
            _posted("Abbonamenti", 900, month=8),
        ),
    )

    assert _answer(state)["period"] == "2026-09"
    assert _answer(state)["posted_spend"]["minor_units"] == 599


def test_an_explicit_month_is_read_instead() -> None:
    """Read the month the owner actually asked about."""

    state, _ = _run(
        [_request(period="2026-08")],
        expenses=(
            _posted("Abbonamenti", 599, month=9),
            _posted("Abbonamenti", 900, month=8),
        ),
    )

    assert _answer(state)["period"] == "2026-08"
    assert _answer(state)["posted_spend"]["minor_units"] == 900


def test_a_budget_question_reports_target_spend_and_remaining() -> None:
    """Answer J7's budget question with every figure it names."""

    state, _ = _run(
        [_request(tool="budget_status")],
        expenses=(_posted("Abbonamenti", 1500),),
        budgets=(_budget("Abbonamenti", 5000),),
    )

    status = _answer(state)["status"]
    assert status["target"]["minor_units"] == 5000
    assert status["posted_spend"]["minor_units"] == 1500
    assert status["remaining"]["minor_units"] == 3500


def test_a_missing_budget_is_reported_rather_than_invented() -> None:
    """State the absent budget plainly, as J7 requires."""

    state, _ = _run(
        [_request(tool="budget_status")], expenses=(_posted("Abbonamenti", 1500),)
    )

    assert state["outcome"] == ReadOutcome.ANSWERED.value
    assert _answer(state)["status"] is None


def test_the_advice_route_surveys_every_budget_worst_first() -> None:
    """Report exceeded budgets before comfortable ones, as J8 requires."""

    state, _ = _run(
        [_request(tool="budgets_at_risk", category_name=None)],
        expenses=(_posted("Abbonamenti", 6000), _posted("Spesa alimentare", 1000)),
        budgets=(_budget("Abbonamenti", 5000), _budget("Spesa alimentare", 40000)),
        intent=WorkflowIntent.ADVICE,
        text="per cosa sto spendendo troppo?",
    )

    facts = _answer(state)
    assert [item["category_name"] for item in facts["statuses"]] == [
        "Abbonamenti",
        "Spesa alimentare",
    ]
    assert facts["statuses"][0]["remaining"]["minor_units"] == -1000


def test_advice_names_the_spend_it_cannot_compare() -> None:
    """Identify what is missing instead of answering confidently."""

    state, _ = _run(
        [_request(tool="budgets_at_risk", category_name=None)],
        expenses=(_posted("Cibo e accessori", 1200),),
        intent=WorkflowIntent.ADVICE,
        text="per cosa sto spendendo troppo?",
    )

    facts = _answer(state)
    assert facts["statuses"] == []
    assert facts["unbudgeted_category_names"] == ["Cibo e accessori"]


def test_a_tool_outside_the_route_registry_reads_nothing() -> None:
    """Score zero on the forbidden-tool gate through the real assembly."""

    state, _ = _run([_request(tool="budgets_at_risk", category_name=None)])

    assert state["outcome"] == ReadOutcome.CLARIFICATION.value
    assert state["clarification_reason"] == ReadReason.TOOL_NOT_REGISTERED.value
    assert state["answer_json"] is None
    assert state["executed_tool"] is None


def test_the_advice_registry_refuses_the_single_budget_lookup() -> None:
    """Keep the two registries genuinely scoped in both directions."""

    state, _ = _run(
        [_request(tool="budget_status")],
        intent=WorkflowIntent.ADVICE,
        text="per cosa sto spendendo troppo?",
    )

    assert state["clarification_reason"] == ReadReason.TOOL_NOT_REGISTERED.value


def test_the_offered_vocabulary_matches_the_calling_route() -> None:
    """Never offer a model a tool deterministic acceptance would refuse."""

    _, query_model = _run([_request()])
    _, advice_model = _run(
        [_request(tool="budgets_at_risk", category_name=None)],
        intent=WorkflowIntent.ADVICE,
        text="per cosa sto spendendo troppo?",
    )

    assert "budgets_at_risk" not in query_model.instructions[0]
    assert "budget_status" in query_model.instructions[0]
    assert "budgets_at_risk" in advice_model.instructions[0]
    assert "budget_status:" not in advice_model.instructions[0]


def test_an_unknown_category_reads_nothing_and_asks() -> None:
    """Say the category does not exist rather than reporting a zero."""

    state, _ = _run([_request(category_name="Categoria inesistente")])

    assert state["outcome"] == ReadOutcome.CLARIFICATION.value
    assert state["clarification_reason"] == ReadReason.UNKNOWN_CATEGORY.value
    assert state["answer_json"] is None


def test_an_unusable_model_answer_asks_instead_of_failing() -> None:
    """Convert an exhausted retry budget into a question, not an exception."""

    state, model = _run(["not json", "still not json"])

    assert state["outcome"] == ReadOutcome.CLARIFICATION.value
    assert state["clarification_reason"] == ReadReason.MODEL_OUTPUT_INVALID.value
    assert model.calls == 2


def test_the_state_holds_only_small_serializable_values() -> None:
    """Keep a read thread inside the workflow state contract."""

    state, _ = _run([_request()], expenses=(_posted("Abbonamenti", 599),))

    assert json.loads(json.dumps(state)) == state
    for value in state.values():
        assert value is None or isinstance(value, str)


def test_every_reported_figure_is_an_exact_minor_unit_amount() -> None:
    """Never let a float reach an owner-facing figure."""

    state, _ = _run(
        [_request(tool="funding_class_split", category_name=None)],
        expenses=(_posted("Abbonamenti", 599),),
    )

    split = _answer(state)["split"]
    for part in split.values():
        assert isinstance(part["minor_units"], int)
        assert part["currency"] == "EUR"


@pytest.mark.parametrize(
    "intent", [WorkflowIntent.EXPENSE, WorkflowIntent.REFUND, WorkflowIntent.UNDO]
)
def test_a_mutating_route_cannot_start_a_read_thread(
    intent: WorkflowIntent,
) -> None:
    """Refuse to open a read workflow for a route that writes."""

    with pytest.raises(ValueError, match="query or advice"):
        initial_read_state(
            ChannelMessage(
                source_reference="test:1",
                received_at=RECEIVED_AT,
                text="ho speso 12 euro",
            ),
            profile_id=PROFILE_ID,
            routed_intent=intent,
        )


def test_a_read_thread_requires_owner_text() -> None:
    """Refuse a question the selection boundary could not interpret."""

    with pytest.raises(ValueError, match="requires owner text"):
        initial_read_state(
            ChannelMessage(
                source_reference="test:1",
                received_at=RECEIVED_AT,
                text="   ",
                has_image=True,
            ),
            profile_id=PROFILE_ID,
            routed_intent=WorkflowIntent.QUERY,
        )


def test_a_node_reached_without_its_request_fails_loudly() -> None:
    """Fail loudly if the topology stops guaranteeing the node order.

    The graph's edges make this unreachable today. The guard exists so a future
    change that executes before selecting is caught instead of reading with no
    admitted tool at all.
    """

    from money_tracker.runtime.read_graph import _named, _required
    from money_tracker.workflows import ReadWorkflowState, ToolRequest

    with pytest.raises(KeyError, match="tool_request_json"):
        _required(ReadWorkflowState(), "tool_request_json")
    with pytest.raises(KeyError, match="without a category"):
        _named(
            ToolRequest.model_validate_json(
                _request(tool="funding_class_split", category_name=None)
            )
        )


def test_candidate_policy_repairs_funding_selection_before_execution() -> None:
    """Execute the all-source split despite an adjacent category model error."""

    state, _ = _run(
        [_request(category_name="Bar, ristoranti e asporto")],
        expenses=(
            _posted("Bar, ristoranti e asporto", 3000, funding_source=CARD),
            _posted("Bar, ristoranti e asporto", 1200, funding_source=VOUCHERS),
        ),
        intent=WorkflowIntent.ADVICE,
        text="Come si divide la mia spesa tra fondi personali e buoni pasto?",
        funding_source_names=(CARD.name, VOUCHERS.name),
        selection_policy=ReadSelectionPolicyVersion.EXPLICIT_FINANCE_SCOPE_V1,
    )

    assert state["executed_tool"] == ReadTool.FUNDING_CLASS_SPLIT.value
    assert _answer(state)["split"]["personal_funds"]["minor_units"] == 3000
    assert _answer(state)["split"]["benefit"]["minor_units"] == 1200


def test_model_only_rollback_keeps_the_original_runtime_selection() -> None:
    """Recover the previous graph behavior by selecting the V0 policy."""

    state, _ = _run(
        [_request(category_name="Bar, ristoranti e asporto")],
        expenses=(_posted("Bar, ristoranti e asporto", 3000),),
        intent=WorkflowIntent.ADVICE,
        text="Come si divide la mia spesa tra fondi personali e buoni pasto?",
        category_names=("Bar, ristoranti e asporto",),
        funding_source_names=(CARD.name, VOUCHERS.name),
        selection_policy=ReadSelectionPolicyVersion.MODEL_ONLY_V0,
    )

    assert state["executed_tool"] == ReadTool.CATEGORY_SPENDING.value


def test_candidate_policy_repairs_a_named_advice_category() -> None:
    """Execute category spending when advice names an active category exactly."""

    state, _ = _run(
        [_request(tool="budgets_at_risk", category_name=None)],
        expenses=(_posted("Abbonamenti", 2997),),
        intent=WorkflowIntent.ADVICE,
        text="Mi consigli di tagliare sugli abbonamenti?",
        category_names=("Abbonamenti", "Bar, ristoranti e asporto"),
        selection_policy=ReadSelectionPolicyVersion.EXPLICIT_FINANCE_SCOPE_V1,
    )

    assert state["executed_tool"] == ReadTool.CATEGORY_SPENDING.value
    assert _answer(state)["category_name"] == "Abbonamenti"
    assert _answer(state)["posted_spend"]["minor_units"] == 2997
