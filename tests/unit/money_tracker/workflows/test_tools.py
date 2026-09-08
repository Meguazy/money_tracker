"""Tests for the scoped read-only tool registry and its selection boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from money_tracker.domain import DomainError, YearMonth
from money_tracker.workflows import (
    ADVICE_TOOLS,
    MUTATING_INTENTS,
    QUERY_TOOLS,
    TOOL_CONTRACT_VERSION,
    ReadTool,
    ToolRequest,
    WorkflowIntent,
    accept_tool_request,
    registry_for,
    tool_vocabulary,
)
from money_tracker.workflows.tools import TOOL_REGISTRIES

FORBIDDEN_AUTHORITY_FIELDS = (
    "profile_id",
    "sql",
    "query",
    "table",
    "amount_minor_units",
    "funding_source_id",
    "category_id",
)


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build one valid request payload with explicit overrides."""

    payload: dict[str, Any] = {
        "tool": "category_spending",
        "category_name": "Abbonamenti",
        "evidence": "quanto ho speso per gli abbonamenti",
    }
    payload.update(overrides)
    return payload


def _parse(**overrides: Any) -> ToolRequest:
    """Parse one request through the real JSON model boundary."""

    return ToolRequest.model_validate_json(json.dumps(_payload(**overrides)))


def test_contract_version_is_pinned_independently() -> None:
    """Let the tool vocabulary grow without invalidating other threads."""

    assert TOOL_CONTRACT_VERSION == "v2"


def test_every_registry_belongs_to_a_read_only_route() -> None:
    """Give no mutating route a tool registry to draw from at all."""

    assert set(TOOL_REGISTRIES) == {WorkflowIntent.QUERY, WorkflowIntent.ADVICE}
    for intent in TOOL_REGISTRIES:
        assert intent not in MUTATING_INTENTS


@pytest.mark.parametrize(
    "intent",
    [
        WorkflowIntent.EXPENSE,
        WorkflowIntent.REFUND,
        WorkflowIntent.CATEGORY_FOLLOW_UP,
        WorkflowIntent.CORRECTION,
        WorkflowIntent.UNDO,
        WorkflowIntent.NON_FINANCE,
        WorkflowIntent.CLARIFICATION,
    ],
)
def test_a_route_without_a_registry_cannot_reach_a_tool(
    intent: WorkflowIntent,
) -> None:
    """Refuse a tool to every route AD-006 does not give a bounded loop."""

    with pytest.raises(DomainError, match="no read-tool registry"):
        registry_for(intent)


def test_the_offered_vocabulary_is_derived_from_the_registry() -> None:
    """Keep the names a model is offered identical to the admitted ones."""

    for intent, registry in TOOL_REGISTRIES.items():
        assert tool_vocabulary(intent) == tuple(sorted(t.value for t in registry))


def test_the_two_registries_are_scoped_rather_than_identical() -> None:
    """Scope per workflow instead of handing both routes one shared set."""

    assert QUERY_TOOLS != ADVICE_TOOLS
    assert ReadTool.BUDGETS_AT_RISK in ADVICE_TOOLS
    assert ReadTool.BUDGETS_AT_RISK not in QUERY_TOOLS
    assert ReadTool.BUDGET_STATUS in QUERY_TOOLS
    assert ReadTool.BUDGET_STATUS not in ADVICE_TOOLS


def test_a_tool_outside_the_calling_registry_is_refused() -> None:
    """Score zero on the forbidden-tool gate by construction."""

    survey = _parse(tool="budgets_at_risk", category_name=None)

    assert accept_tool_request(survey, intent=WorkflowIntent.ADVICE) is (
        ReadTool.BUDGETS_AT_RISK
    )
    with pytest.raises(DomainError, match="not registered for query"):
        accept_tool_request(survey, intent=WorkflowIntent.QUERY)


def test_the_route_comes_from_trusted_context_not_the_request() -> None:
    """Prevent a model from widening its own scope by naming a route."""

    fields = set(ToolRequest.model_fields)

    assert "intent" not in fields
    assert "route" not in fields
    for forbidden in FORBIDDEN_AUTHORITY_FIELDS:
        assert forbidden not in fields


def test_the_boundary_rejects_an_unregistered_tool_name() -> None:
    """Keep the vocabulary closed rather than approximating a near miss."""

    with pytest.raises(ValidationError):
        _parse(tool="run_sql")
    with pytest.raises(ValidationError):
        _parse(tool="category_spendings")


def test_the_boundary_rejects_an_extended_document() -> None:
    """Fail closed on a field the contract never declared."""

    with pytest.raises(ValidationError):
        ToolRequest.model_validate_json(json.dumps(_payload(sql="SELECT 1")))


@pytest.mark.parametrize("tool", [ReadTool.CATEGORY_SPENDING, ReadTool.BUDGET_STATUS])
def test_a_category_tool_requires_the_category_the_owner_named(
    tool: ReadTool,
) -> None:
    """Refuse a roll-up that names nothing to roll up."""

    with pytest.raises(ValidationError, match="requires a category name"):
        _parse(tool=tool.value, category_name=None)


@pytest.mark.parametrize(
    "tool", [ReadTool.FUNDING_CLASS_SPLIT, ReadTool.BUDGETS_AT_RISK]
)
def test_a_period_tool_takes_no_category(tool: ReadTool) -> None:
    """Keep an argument out of a tool that would silently ignore it."""

    with pytest.raises(ValidationError, match="takes no category name"):
        _parse(tool=tool.value, category_name="Abbonamenti")


def test_a_named_category_is_words_not_an_identifier() -> None:
    """Resolve through the owner's taxonomy rather than a model-chosen id."""

    request = _parse(category_name="  abbonamenti  ")

    assert request.category_name == "abbonamenti"
    with pytest.raises(ValidationError):
        _parse(category_name="   ")


@pytest.mark.parametrize(
    "period", ["2026-13", "2026-00", "26-09", "2026-9", "2026/09", "settembre"]
)
def test_only_a_real_calendar_month_is_accepted(period: str) -> None:
    """Refuse a period the domain calendar could not represent."""

    with pytest.raises(ValidationError):
        _parse(period=period)


def test_an_explicit_period_is_read_as_that_month() -> None:
    """Read the month the owner asked about."""

    request = _parse(period="2026-03")

    assert request.resolved_period(current=YearMonth(2026, 9)) == YearMonth(2026, 3)


def test_an_omitted_period_defaults_to_the_month_in_progress() -> None:
    """Treat an unqualified question as being about the current month."""

    request = _parse(period=None)

    assert request.resolved_period(current=YearMonth(2026, 9)) == YearMonth(2026, 9)


def test_the_request_carries_auditable_evidence() -> None:
    """Let a reviewer audit tool selection without the prompt."""

    assert _parse().evidence == "quanto ho speso per gli abbonamenti"
    with pytest.raises(ValidationError):
        _parse(evidence="   ")
    with pytest.raises(ValidationError):
        _parse(evidence="x" * 281)


def test_a_category_budget_question_is_refused_where_no_budget_tool_exists() -> None:
    """Refuse a substitution the route cannot answer, rather than reporting spend.

    The advice registry excludes `budget_status`, so a question about one
    category's budget has no tool there. The measured behaviour is that the
    boundary selects `category_spending`, which answers a different question
    with a correct figure.
    """

    from money_tracker.workflows.tools import asks_for_an_unavailable_budget

    spending = ToolRequest(
        tool=ReadTool.CATEGORY_SPENDING,
        category_name="Abbonamenti",
        evidence="budget degli abbonamenti",
    )

    assert asks_for_an_unavailable_budget(
        spending,
        intent=WorkflowIntent.ADVICE,
        message="Come sto con il budget degli abbonamenti?",
    )
    assert asks_for_an_unavailable_budget(
        spending,
        intent=WorkflowIntent.ADVICE,
        message="Dimmi il residuo del budget di bar",
    )
    assert asks_for_an_unavailable_budget(
        spending,
        intent=WorkflowIntent.ADVICE,
        message="Quanto manca al limite del budget",
    )


def test_the_budget_refusal_leaves_every_other_shape_alone() -> None:
    """Keep the rule to the one substitution it exists for.

    The query route owns `budget_status`, so the same question is answerable
    there. A survey request is not category-scoped, which is why
    `budgets_at_risk` questions still reach their tool on the advice route
    although their messages name budgets.
    """

    from money_tracker.workflows.tools import asks_for_an_unavailable_budget

    spending = ToolRequest(
        tool=ReadTool.CATEGORY_SPENDING,
        category_name="Abbonamenti",
        evidence="budget",
    )
    survey = ToolRequest(tool=ReadTool.BUDGETS_AT_RISK, evidence="i miei budget")

    assert not asks_for_an_unavailable_budget(
        spending,
        intent=WorkflowIntent.QUERY,
        message="Come sto con il budget degli abbonamenti?",
    )
    assert not asks_for_an_unavailable_budget(
        survey, intent=WorkflowIntent.ADVICE, message="Come vanno tutti i miei budget?"
    )
    assert not asks_for_an_unavailable_budget(
        spending,
        intent=WorkflowIntent.ADVICE,
        message="Quanto ho speso in abbonamenti?",
    )
