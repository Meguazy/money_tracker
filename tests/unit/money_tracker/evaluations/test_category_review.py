"""Tests for the category review suite's dataset and its safety guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from money_tracker.domain import CategoryStatus, initial_taxonomy
from money_tracker.evaluations.category_review import (
    ALIAS_CATEGORY,
    ARCHIVED_CATEGORY,
    DATASET_MINIMUM,
    SUITE,
    ScriptedModel,
    fixture_taxonomy,
)
from money_tracker.evaluations.fixtures import require_isolated_database
from money_tracker.evaluations.suites import dataset_metadata, load_cases
from money_tracker.workflows import CATEGORY_REVIEW_ACTIONS, CallbackAction

DATASET = Path("evals/datasets/category-review-it-v1.jsonl")
BRANCHES = {
    "approve",
    "best_existing",
    "named_existing",
    "repropose",
    "exhausted",
    "cancel",
    "unrelated",
}


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    """Load the committed scenario dataset once."""

    parsed, _ = load_cases(DATASET)
    return parsed


def test_the_committed_dataset_parses_and_reaches_its_minimum(
    cases: list[dict[str, Any]],
) -> None:
    """Keep the shipped scenarios loadable and numerous enough to gate on."""

    parsed, content_hash = load_cases(DATASET)
    metadata = dataset_metadata(
        suite=SUITE, path=DATASET, cases=parsed, content_hash=content_hash
    )

    assert metadata.item_count == len(cases)
    assert metadata.item_count >= DATASET_MINIMUM
    assert metadata.version == "1.0.0"


def test_every_branch_the_matrix_gates_is_exercised(
    cases: list[dict[str, Any]],
) -> None:
    """Refuse a dataset that leaves one gated branch unmeasured."""

    covered = {case["branch"] for case in cases}

    assert covered == BRANCHES
    for branch in BRANCHES:
        assert sum(1 for c in cases if c["branch"] == branch) >= 4, branch


def test_every_turn_is_an_action_the_review_owns(
    cases: list[dict[str, Any]],
) -> None:
    """Keep a scenario from driving a callback belonging to another workflow."""

    owned = {action.value for action in CATEGORY_REVIEW_ACTIONS}

    for case in cases:
        assert case["turns"], case["case_id"]
        for turn in case["turns"]:
            assert turn["action"] in owned, case["case_id"]
            if turn["action"] != CallbackAction.USE_NAMED_EXISTING.value:
                assert turn.get("category_name") is None, case["case_id"]


def test_no_two_scenarios_propose_the_same_candidate(
    cases: list[dict[str, Any]],
) -> None:
    """Keep one scenario's approval from making a later proposal a duplicate.

    V1 permits exactly one active profile, so every scenario shares an owner
    and its taxonomy. A repeated candidate name would be refused at submission
    for a reason belonging to the dataset rather than to the workflow.
    """

    proposed = [case["proposal"]["proposed_name"] for case in cases]

    assert len(set(proposed)) == len(proposed)


def test_no_scenario_proposes_a_name_the_seed_taxonomy_already_has(
    cases: list[dict[str, Any]],
) -> None:
    """Keep every candidate genuinely new, except where a case tests otherwise."""

    active = {
        category.name.casefold()
        for category in fixture_taxonomy().categories
        if category.status is CategoryStatus.ACTIVE
    }

    for case in cases:
        assert case["proposal"]["proposed_name"].casefold() not in active, case[
            "case_id"
        ]


def test_every_declared_expectation_is_an_observable_postcondition(
    cases: list[dict[str, Any]],
) -> None:
    """Refuse an expectation the runner would silently never compare."""

    observable = {
        "outcome",
        "clarification_reason",
        "categories_created",
        "expense_status",
        "settled_category_name",
        "purpose_allocations",
        "proposal_status",
        "excluded_names",
        "can_repropose",
        "restart_payload",
        "duplicate_writes",
    }

    for case in cases:
        assert case["expected"], case["case_id"]
        assert set(case["expected"]) <= observable, case["case_id"]


def test_a_replay_scenario_always_gates_on_zero_duplicate_writes(
    cases: list[dict[str, Any]],
) -> None:
    """Keep the retry gate stated by every scenario that exercises it."""

    replays = [case for case in cases if case["replay_last_turn"]]

    assert len(replays) >= 4
    for case in replays:
        assert case["expected"]["duplicate_writes"] == 0, case["case_id"]


def test_the_fixture_adds_only_the_nodes_the_gated_branches_need() -> None:
    """Keep the fixture honest about extending the accepted taxonomy.

    The seed taxonomy declares no alias and no archived node, so two branches
    the matrix gates could not be exercised against it. Both additions belong
    to the fixture and neither changes the product taxonomy.
    """

    seed = initial_taxonomy()
    fixture = fixture_taxonomy()
    added = {c.name for c in fixture.categories} - {c.name for c in seed.categories}

    assert added == {ALIAS_CATEGORY, ARCHIVED_CATEGORY}
    assert fixture.resolve_active("toeletta").name == ALIAS_CATEGORY
    assert not any(c.aliases for c in seed.categories)


def test_an_archived_fixture_node_cannot_receive_an_allocation() -> None:
    """Keep the archived branch measuring a refusal rather than a typo."""

    fixture = fixture_taxonomy()
    archived = next(c for c in fixture.categories if c.name == ARCHIVED_CATEGORY)

    assert archived.status is CategoryStatus.ARCHIVED
    with pytest.raises(Exception, match="active category does not exist"):
        fixture.resolve_active(ARCHIVED_CATEGORY)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:pw@127.0.0.1:5432/money_tracker",
        "postgresql://user:pw@db.example.com:5433/money_tracker_test",
        "postgresql://user:pw@127.0.0.1:5433/money_tracker",
        "sqlite:///money_tracker_test",
    ],
)
def test_the_suite_refuses_any_database_but_the_isolated_test_one(
    database_url: str,
) -> None:
    """Keep a suite that bootstraps profiles away from the owner's real data."""

    with pytest.raises(ValueError, match="money_tracker_test"):
        require_isolated_database(database_url)


def test_the_isolated_test_database_is_accepted() -> None:
    """Accept exactly the database the integration fixtures already use."""

    url = "postgresql://user:pw@127.0.0.1:5433/money_tracker_test"

    assert require_isolated_database(url) == url


def test_an_unscripted_candidate_request_is_refused_rather_than_invented() -> None:
    """Fail a scenario that reaches the boundary more often than it declared.

    A silently repeated completion would let a re-proposal loop appear to work
    on candidates the dataset never stated.
    """

    model = ScriptedModel(['{"outcome": "proposal"}'])

    assert model.complete_json(instructions="", message="", schema=type("S", (), {}))
    with pytest.raises(AssertionError, match="unscripted candidate"):
        model.complete_json(instructions="", message="", schema=type("S", (), {}))
