"""Tests for the committed capabilities/local-model dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from money_tracker.evaluations.capabilities import (
    ALWAYS_STATED_DRAFT_FIELDS,
    OPTIONAL_DRAFT_SLOTS,
    UNION_VARIANT_KEYS,
    _case_by_capability,
    _cases_by_capability,
    _dataset_version,
    _load_cases,
)

DATASET = Path("evals/datasets/capabilities-local-model-v1.2.jsonl")
PREVIOUS = Path("evals/datasets/capabilities-local-model-v1.1.jsonl")

SINGLE_CASE_CAPABILITIES = (
    "strict_structured_output",
    "tool_calling",
    "tool_result_sequencing",
    "image_input",
)


@pytest.fixture(scope="module")
def cases() -> list[dict[str, object]]:
    """Load the committed dataset once for every assertion about it."""

    return _load_cases(DATASET)


def test_every_case_declares_the_same_version(cases: list[dict[str, object]]) -> None:
    """Keep a run's recorded dataset version unambiguous."""

    assert _dataset_version(cases) == "1.2.0"
    assert len({str(case["case_id"]) for case in cases}) == len(cases)


def test_the_protocol_capabilities_keep_exactly_one_case(
    cases: list[dict[str, object]],
) -> None:
    """Carry the 1.1.0 protocol probes forward without duplicating them."""

    for capability in SINGLE_CASE_CAPABILITIES:
        assert _case_by_capability(cases, capability)["dataset_version"] == "1.2.0"


def test_the_earlier_dataset_is_left_as_historical_input() -> None:
    """Never rewrite the input a concluded run was scored against."""

    assert _dataset_version(_load_cases(PREVIOUS)) == "1.1.0"


def test_the_omission_battery_labels_every_optional_slot(
    cases: list[dict[str, object]],
) -> None:
    """Score every slot of every case, so silence is never assumed."""

    battery = _cases_by_capability(cases, "optional_field_omission")

    assert len(battery) >= 20
    for case in battery:
        expected = case["expected"]
        assert isinstance(expected, dict)
        assert set(expected) == set(OPTIONAL_DRAFT_SLOTS)
        assert "description" not in expected
        assert set(ALWAYS_STATED_DRAFT_FIELDS) == {"description"}


def test_the_omission_battery_holds_both_silent_and_stated_slots(
    cases: list[dict[str, object]],
) -> None:
    """Keep the control half, without which emptying every slot would score 1.0."""

    battery = _cases_by_capability(cases, "optional_field_omission")
    silent = stated = 0
    for case in battery:
        expected = case["expected"]
        assert isinstance(expected, dict)
        for slot in OPTIONAL_DRAFT_SLOTS:
            wanted = expected[slot]
            if wanted is None or wanted == []:
                silent += 1
            else:
                stated += 1

    assert silent >= 60
    assert stated >= 10


def test_the_union_battery_covers_both_variants(
    cases: list[dict[str, object]],
) -> None:
    """Reach each branch, so a model answering one shape cannot screen as able."""

    battery = _cases_by_capability(cases, "union_output")
    outcomes = [dict(case["expected"]).get("outcome") for case in battery]  # type: ignore[arg-type]

    assert len(battery) >= 16
    assert set(outcomes) == set(UNION_VARIANT_KEYS)
    for outcome in UNION_VARIANT_KEYS:
        assert outcomes.count(outcome) >= 8
    for case in battery:
        supplied = case["active_categories"]
        assert isinstance(supplied, list)
        assert len(supplied) >= 4
        expected = case["expected"]
        assert isinstance(expected, dict)
        if expected["outcome"] == "existing":
            assert expected["category_name"] in supplied
        else:
            assert expected["category_name"] is None


def test_no_case_carries_a_personal_identifier(
    cases: list[dict[str, object]],
) -> None:
    """Keep the committed dataset synthetic, as the dataset policy requires."""

    text = json.dumps(cases, ensure_ascii=False).casefold()

    assert "@" not in text
    for case in cases:
        tags = case["tags"]
        assert isinstance(tags, list)
        assert "synthetic" in tags
