"""Regression checks for corrected development labels and empty tool sets."""

import json
from pathlib import Path

from money_tracker.evaluations.tool_use import _facets
from money_tracker.workflows import WorkflowIntent, registry_for


def test_revised_tools_never_require_an_answer_without_an_available_tool() -> None:
    """Catch the contradictory forced-query survey labels before inference."""

    cases = [
        json.loads(line)
        for line in Path("evals/datasets/tools-selection-and-use-it-v1.1.jsonl")
        .read_text()
        .splitlines()
    ]
    for case in cases:
        if case["expected_outcome"] == "answered":
            assert case["expected_tool"] in registry_for(WorkflowIntent(case["route"]))
            assert case["expected_answer"] is not None
        if "forbidden-tool" in case["tags"]:
            assert case["expected_outcome"] == "refused"


def test_empty_permitted_set_scores_explicit_refusal_and_rejects_broadening() -> None:
    """An unanswerable request cannot pass selection by using a nearby tool."""

    case = {
        "expected_tool": None,
        "expected_outcome": "refused",
        "expected_period": None,
    }
    refused = {"out_of_registry": False, "outcome": "refused", "period": None}
    assert _facets(case, refused)["selection"]
    broadened = {**refused, "outcome": "answered", "tool": "funding_class_split"}
    assert not _facets(case, broadened)["selection"]


def test_changed_historical_labels_have_a_recorded_contract_reason() -> None:
    """Dataset corrections remain auditable rather than silently replacing labels."""

    for old, new in (
        ("routing-intent-it-v1.1", "routing-intent-it-v1.2"),
        ("categorization-purpose-it-v1", "categorization-purpose-it-v1.1"),
        ("tools-selection-and-use-it-v1", "tools-selection-and-use-it-v1.1"),
    ):
        previous = {
            c["case_id"]: c
            for c in (
                json.loads(line)
                for line in Path(f"evals/datasets/{old}.jsonl").read_text().splitlines()
            )
        }
        revised = [
            json.loads(line)
            for line in Path(f"evals/datasets/{new}.jsonl").read_text().splitlines()
        ]
        assert set(previous) <= {c["case_id"] for c in revised}
        for case in revised:
            before = previous.get(case["case_id"])
            if before and any(
                before.get(key) != case.get(key)
                for key in before
                if key.startswith("expected_")
            ):
                assert case["revision_reason"]
                assert case["reviewer_status"] == "contract-reviewed"
