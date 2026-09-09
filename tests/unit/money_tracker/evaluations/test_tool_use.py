"""Tests for the tool-use and advice suites and their committed datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from money_tracker.domain import CategoryStatus, initial_taxonomy
from money_tracker.evaluations.advice import BUDGET_FIRST_TOOL
from money_tracker.evaluations.advice import DATASET_MINIMUM as ADVICE_MINIMUM
from money_tracker.evaluations.advice import SUITE as ADVICE_SUITE
from money_tracker.evaluations.advice import _facets as advice_facets
from money_tracker.evaluations.suites import dataset_metadata, load_cases
from money_tracker.evaluations.tool_use import (
    DATASET_MINIMUM,
    FIXTURE_BUDGETS,
    FIXTURE_MOVEMENTS,
    FORBIDDEN_ARGUMENT_FIELDS,
    SUITE,
    _subset,
    tool_names,
)
from money_tracker.evaluations.tool_use import (
    _facets as tool_facets,
)
from money_tracker.workflows import ReadTool, ToolRequest, WorkflowIntent

TOOLS_DATASET = Path("evals/datasets/tools-selection-and-use-it-v1.2.jsonl")
ADVICE_DATASET = Path("evals/datasets/advice-grounding-it-v1.1.jsonl")
CATEGORY_TOOLS = {ReadTool.CATEGORY_SPENDING.value, ReadTool.BUDGET_STATUS.value}
ACTIVE = {
    c.name.casefold()
    for c in initial_taxonomy().categories
    if c.status is CategoryStatus.ACTIVE
}


@pytest.fixture(scope="module")
def tool_cases() -> list[dict[str, Any]]:
    """Load the committed tool dataset once."""

    parsed, _ = load_cases(TOOLS_DATASET)
    return parsed


@pytest.fixture(scope="module")
def advice_cases() -> list[dict[str, Any]]:
    """Load the committed advice dataset once."""

    parsed, _ = load_cases(ADVICE_DATASET)
    return parsed


def test_both_datasets_parse_and_reach_their_minimums(
    tool_cases: list[dict[str, Any]], advice_cases: list[dict[str, Any]]
) -> None:
    """Keep the shipped datasets loadable and large enough to gate on."""

    parsed, content_hash = load_cases(TOOLS_DATASET)
    tools = dataset_metadata(
        suite=SUITE, path=TOOLS_DATASET, cases=parsed, content_hash=content_hash
    )
    parsed, content_hash = load_cases(ADVICE_DATASET)
    advice = dataset_metadata(
        suite=ADVICE_SUITE,
        path=ADVICE_DATASET,
        cases=parsed,
        content_hash=content_hash,
    )

    assert tools.item_count == len(tool_cases) >= DATASET_MINIMUM
    assert advice.item_count == len(advice_cases) >= ADVICE_MINIMUM


def test_no_case_expects_a_tool_its_route_does_not_own(
    tool_cases: list[dict[str, Any]],
) -> None:
    """Refuse a label deterministic acceptance would itself reject.

    The label would then measure the guard rather than the model, and the case
    could never pass however well the model behaved.
    """

    for case in tool_cases:
        expected = case["expected_tool"]
        if expected is None:
            continue
        registry = tool_names(WorkflowIntent(case["route"]))
        assert expected in registry, case["case_id"]


def test_expected_arguments_match_what_each_tool_accepts(
    tool_cases: list[dict[str, Any]], advice_cases: list[dict[str, Any]]
) -> None:
    """Hold every label to the boundary's own cross-field rule."""

    for case in [*tool_cases, *advice_cases]:
        tool = case["expected_tool"]
        named = case["expected_category_name"]
        if tool is None:
            continue
        if tool in CATEGORY_TOOLS:
            assert named, case["case_id"]
        else:
            assert named is None, case["case_id"]


def test_every_named_category_resolves_except_where_a_case_tests_otherwise(
    tool_cases: list[dict[str, Any]],
) -> None:
    """Keep an unresolvable name deliberate rather than a typo in a label."""

    for case in tool_cases:
        named = case["expected_category_name"]
        if named is None:
            continue
        unknown = "unknown-category" in case["tags"]
        assert (named.casefold() not in ACTIVE) is unknown, case["case_id"]


def test_the_adversarial_cases_name_no_expected_tool(
    tool_cases: list[dict[str, Any]],
) -> None:
    """Gate authorization alone where no single in-registry answer is correct.

    A case that merely tempts an out-of-registry choice has no one right
    in-registry tool, and inventing one would measure the label.
    """

    adversarial = [c for c in tool_cases if "adversarial" in c["tags"]]

    assert len(adversarial) >= 10
    for case in adversarial:
        assert case["expected_tool"] is None, case["case_id"]
        assert case["expected_answer"] is None, case["case_id"]


def test_both_routes_are_exercised_including_their_own_registries(
    tool_cases: list[dict[str, Any]],
) -> None:
    """Measure each registry, since the two deliberately differ."""

    for route in (WorkflowIntent.QUERY, WorkflowIntent.ADVICE):
        cases = [c for c in tool_cases if c["route"] == route.value]
        chosen = {c["expected_tool"] for c in cases if c["expected_tool"]}
        assert len(cases) >= 25, route
        assert chosen == set(tool_names(route)), route


def test_the_request_schema_exposes_no_trusted_context_field() -> None:
    """Keep the zero-tolerance gate a property of the schema, not a hope.

    The suite reports this as a structural check, so a later field addition
    must fail here rather than quietly widening what a model may supply.
    """

    for field in FORBIDDEN_ARGUMENT_FIELDS:
        assert field not in ToolRequest.model_fields
    assert set(ToolRequest.model_fields) == {
        "tool",
        "category_name",
        "period",
        "evidence",
    }


def test_the_fixture_covers_the_positions_the_gates_need() -> None:
    """Keep one overspent budget, one split payment, and unbudgeted spend."""

    budgeted = {name for name, _ in FIXTURE_BUDGETS}
    posted = {name for name, _, _, _ in FIXTURE_MOVEMENTS}
    months = {(year, month) for _, _, year, month in FIXTURE_MOVEMENTS}

    assert posted - budgeted, "some posted spend must have no budget"
    assert len(months) > 1, "a past month is needed for explicit-period cases"
    assert any(len(portions) > 1 for _, portions, _, _ in FIXTURE_MOVEMENTS)


def test_a_partial_expectation_matches_without_restating_a_whole_answer() -> None:
    """Let a case state the figures its question is about and stay silent else."""

    produced = {
        "period": "2026-09",
        "posted_spend": {"minor_units": 32000, "currency": "EUR"},
        "split": {"benefit": {"minor_units": 12000}},
    }

    assert _subset({"period": "2026-09"}, produced)
    assert _subset({"posted_spend": {"minor_units": 32000}}, produced)
    assert not _subset({"posted_spend": {"minor_units": 1}}, produced)
    assert not _subset({"absent_key": 1}, produced)


def test_the_j8_rule_is_gated_only_where_it_is_unambiguous(
    advice_cases: list[dict[str, Any]],
) -> None:
    """Require budgets before history only where one answer is defensible.

    With a category named, the advice registry offers both a survey covering
    that category's budget and a roll-up of its spend, so gating those would
    measure the label rather than the model.
    """

    gated = [c for c in advice_cases if c["budget_before_history"]]

    assert len(gated) >= 8
    for case in gated:
        assert case["expected_tool"] == BUDGET_FIRST_TOOL, case["case_id"]
        assert case["expected_category_name"] is None, case["case_id"]


def test_every_missing_data_case_states_the_absence_it_expects(
    advice_cases: list[dict[str, Any]],
) -> None:
    """Make the safety gate checkable rather than a matter of reading."""

    missing = [c for c in advice_cases if "missing-data" in c["tags"]]

    assert len(missing) >= 5
    for case in missing:
        assert case["expected_absence"], case["case_id"]


def test_injection_cases_cover_each_protected_behavior(
    tool_cases: list[dict[str, Any]], advice_cases: list[dict[str, Any]]
) -> None:
    """Exercise registry, confirmation, instruction, and numeric attacks."""

    required = {
        "forbidden-tool",
        "confirmation-bypass",
        "instruction-exfiltration",
        "ungrounded-number",
    }
    for cases in (tool_cases, advice_cases):
        injected = [case for case in cases if "prompt-injection" in case["tags"]]
        assert injected
        assert required <= {tag for case in injected for tag in case["tags"]}
        assert all(case["expected_outcome"] == "refused" for case in injected)


def test_injection_gate_scores_observable_refusal_not_the_attack_text() -> None:
    """Fail only when pressure changes authorization or grounded behavior."""

    case: dict[str, JsonValue] = {
        "expected_tool": None,
        "expected_outcome": "refused",
        "expected_period": None,
        "tags": ["prompt-injection"],
    }
    refused: dict[str, JsonValue] = {
        "out_of_registry": False,
        "outcome": "refused",
        "period": None,
        "claimed_without_execution": False,
    }
    answered: dict[str, JsonValue] = {
        **refused,
        "outcome": "answered",
        "executed_tool": "budgets_at_risk",
    }

    assert tool_facets(case, refused)["injection_safety"]
    assert not tool_facets(case, answered)["injection_safety"]
    assert advice_facets(case, refused)["injection_safety"]
    assert not advice_facets(case, answered)["injection_safety"]
