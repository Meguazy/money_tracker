"""Tests for the routing confidence-bar calibration and its leakage guards."""

from __future__ import annotations

from pathlib import Path

from money_tracker.evaluations.calibration import (
    CANDIDATE_BARS,
    HOLDOUT_FRACTION,
    SAFETY_GATE,
    assign_split,
    select_bar,
)
from money_tracker.evaluations.suites import load_cases
from money_tracker.workflows import WorkflowIntent

DATASET = Path("evals/datasets/routing-intent-it-v1.1.jsonl")


def _sweep(**bars: dict[str, float]) -> dict[float, dict[str, float]]:
    """Build one sweep keyed by bar from readable keyword arguments."""

    return {float(bar.replace("_", ".")): scores for bar, scores in bars.items()}


def test_the_split_is_stable_and_independent_of_dataset_order() -> None:
    """Keep a split that cannot be nudged by rerunning until it is convenient."""

    first = {
        c["case_id"]: assign_split(str(c["case_id"]))
        for (c,) in [(c,) for c in load_cases(DATASET)[0]]
    }
    second = {case_id: assign_split(case_id) for case_id in reversed(list(first))}

    assert first == second
    assert set(first.values()) == {"calibration", "holdout"}


def test_the_holdout_is_close_to_the_declared_fraction() -> None:
    """Reserve roughly the share the module declares, on the real dataset."""

    cases, _ = load_cases(DATASET)
    splits = [assign_split(str(case["case_id"])) for case in cases]
    holdout = splits.count("holdout") / len(splits)

    assert abs(holdout - HOLDOUT_FRACTION) < 0.12


def test_a_case_belongs_to_exactly_one_split() -> None:
    """Keep calibration and holdout disjoint, which is what prevents leakage."""

    cases, _ = load_cases(DATASET)
    identifiers = [str(case["case_id"]) for case in cases]
    calibration = {i for i in identifiers if assign_split(i) == "calibration"}
    holdout = {i for i in identifiers if assign_split(i) == "holdout"}

    assert not calibration & holdout
    assert calibration | holdout == set(identifiers)


def test_safety_decides_admissibility_before_any_aggregate() -> None:
    """Refuse a bar that escalates a read-only message, however accurate.

    The matrix forbids a high aggregate from concealing a query-to-write
    failure, so the selection rule cannot trade one for the other.
    """

    sweep = _sweep(
        **{
            "0_50": {SAFETY_GATE: 3.0, "macro_f1": 0.99},
            "0_80": {SAFETY_GATE: 0.0, "macro_f1": 0.70},
        }
    )

    assert select_bar(sweep) == 0.8


def test_the_smallest_admissible_bar_is_preferred() -> None:
    """Buy the safety guarantee with the fewest questions to the owner."""

    sweep = _sweep(
        **{
            "0_40": {SAFETY_GATE: 0.0, "macro_f1": 0.80},
            "0_60": {SAFETY_GATE: 0.0, "macro_f1": 0.95},
            "0_90": {SAFETY_GATE: 0.0, "macro_f1": 0.99},
        }
    )

    assert select_bar(sweep) == 0.4


def test_no_bar_is_selected_when_none_is_admissible() -> None:
    """Say so rather than promoting the least bad option.

    A calibration that cannot reach the safety gate has found that the router
    needs a change, not a threshold.
    """

    sweep = _sweep(
        **{
            "0_00": {SAFETY_GATE: 9.0, "macro_f1": 0.90},
            "1_00": {SAFETY_GATE: 2.0, "macro_f1": 0.40},
        }
    )

    assert select_bar(sweep) is None


def test_the_candidate_grid_spans_the_whole_unit_interval() -> None:
    """Consider accepting anything and demanding certainty, and every step."""

    assert CANDIDATE_BARS[0] == 0.0
    assert CANDIDATE_BARS[-1] == 1.0
    assert len(CANDIDATE_BARS) == 21
    assert all(0.0 <= bar <= 1.0 for bar in CANDIDATE_BARS)


def test_every_routable_intent_the_dataset_labels_is_a_real_intent() -> None:
    """Keep the calibration scoring labels the taxonomy actually contains."""

    cases, _ = load_cases(DATASET)
    labels = {str(case["expected_intent"]) for case in cases}

    assert labels <= {intent.value for intent in WorkflowIntent}
