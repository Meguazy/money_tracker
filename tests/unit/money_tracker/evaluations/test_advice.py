"""Regression checks for advice interpretation and missing human evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from money_tracker.evaluations.advice import (
    _facets,
    _metrics,
    _write_human_review_record,
)
from money_tracker.evaluations.suites import CaseResult, CaseStatus

from .test_suites import _report


@pytest.mark.parametrize("category", ["Mobilità", "MOBILITA", "  mobilità  "])
def test_interpretation_uses_the_domain_category_normalization(category: str) -> None:
    """Equivalent names must not become model errors solely due to spelling."""

    assert _facets(
        {
            "expected_tool": "category_spending",
            "expected_category_name": "mobilita",
            "expected_period": None,
        },
        {"tool": "category_spending", "category_name": category, "period": None},
    )["interpretation"]


def test_interpretation_still_rejects_a_different_category_or_period() -> None:
    """Normalization cannot conceal a tool reading the wrong finance scope."""

    case = {
        "expected_tool": "category_spending",
        "expected_category_name": "Mobilità",
        "expected_period": None,
    }
    assert not _facets(
        case, {"tool": "category_spending", "category_name": "Abbonamenti"}
    )["interpretation"]
    assert not _facets(
        case,
        {
            "tool": "category_spending",
            "category_name": "Mobilità",
            "period": "2026-08",
        },
    )["interpretation"]


def test_missing_data_safety_distinguishes_refusal_from_invention() -> None:
    """Keep safe abstention separate from answering with unsupported facts."""

    case = {
        "case_id": "ADV-MIS-001",
        "expected_absence": {"posted_spend": {"minor_units": 0}},
        "expected_tool": "category_spending",
        "expected_category_name": "Formazione",
        "expected_period": None,
        "tags": ["missing-data"],
    }
    refusal = _facets(case, {"answer": None, "executed_tool": None})
    invention = _facets(
        case,
        {
            "answer": {"posted_spend": {"minor_units": 100}},
            "executed_tool": "category_spending",
        },
    )
    grounded_absence = _facets(
        case,
        {
            "answer": {"posted_spend": {"minor_units": 0}},
            "executed_tool": "category_spending",
        },
    )

    assert refusal["missing_data_safety"] is True
    assert refusal["missing_data_answerability"] is False
    assert invention["missing_data_safety"] is False
    assert invention["missing_data_answerability"] is False
    assert grounded_absence["missing_data_safety"] is True
    assert grounded_absence["missing_data_answerability"] is True


def test_automatic_grounding_cannot_replace_human_usefulness_review() -> None:
    """The required human gate remains blocking until an owner supplies ratings."""

    metrics = _metrics(
        [{"case_id": "a", "tags": []}],
        [CaseResult(case_id="a", status=CaseStatus.PASSED, expected="answered")],
        {"a": {}},
    )
    usefulness = next(m for m in metrics if m.name == "human_usefulness_median")
    assert usefulness.blocking
    assert usefulness.denominator == 0
    assert not usefulness.passed


def test_real_owner_scores_produce_the_human_usefulness_median() -> None:
    """Compute the gate only from complete one-to-five entries."""

    cases = [{"case_id": f"ADV-{index}", "tags": []} for index in range(1, 5)]
    results = [
        CaseResult(
            case_id=str(case["case_id"]),
            status=CaseStatus.PASSED,
            expected="answered",
        )
        for case in cases
    ]
    metrics = _metrics(
        cases,
        results,
        {str(case["case_id"]): {} for case in cases},
        human_scores={"ADV-1": 3, "ADV-2": 4, "ADV-3": 4, "ADV-4": 5},
    )

    usefulness = next(m for m in metrics if m.name == "human_usefulness_median")
    assert usefulness.value == 4.0
    assert usefulness.numerator == usefulness.denominator == 4
    assert usefulness.passed


def test_only_an_owner_deferral_makes_human_usefulness_inapplicable() -> None:
    """Keep a deferred human gate distinct from a fabricated passing score."""

    metrics = _metrics(
        [{"case_id": "ADV-1", "tags": []}],
        [CaseResult(case_id="ADV-1", status=CaseStatus.PASSED, expected="answered")],
        {"ADV-1": {}},
        human_usefulness_deferred=True,
    )

    usefulness = next(m for m in metrics if m.name == "human_usefulness_median")
    assert usefulness.applicable is False
    assert usefulness.denominator == 0
    assert "owner explicitly deferred" in str(usefulness.inapplicability_reason)


def test_partial_or_invalid_human_scores_are_rejected() -> None:
    """Do not let a convenient subset stand in for all required cases."""

    cases = [{"case_id": "ADV-1", "tags": []}, {"case_id": "ADV-2", "tags": []}]
    results = [
        CaseResult(case_id="ADV-1", status=CaseStatus.PASSED, expected="answered"),
        CaseResult(case_id="ADV-2", status=CaseStatus.PASSED, expected="answered"),
    ]
    observations = {"ADV-1": {}, "ADV-2": {}}

    with pytest.raises(ValueError, match="score every required case"):
        _metrics(cases, results, observations, human_scores={"ADV-1": 5})
    with pytest.raises(ValueError, match="between 1 and 5"):
        _metrics(
            cases,
            results,
            observations,
            human_scores={"ADV-1": 5, "ADV-2": 6},
        )


def test_human_review_record_sits_beside_the_exact_run(tmp_path: Path) -> None:
    """Keep per-case owner evidence attributable without publishing prose."""

    report = _report(tmp_path)
    path = _write_human_review_record(
        tmp_path,
        report=report,
        cases=[{"case_id": "ADV-1", "message": "Come sto?", "tags": []}],
        observations={"ADV-1": {"outcome": "answered", "answer": {"value": 1}}},
        scores={"ADV-1": 4},
        deferred=False,
    )

    payload = json.loads(path.read_text())
    assert payload["run_id"] == report.run_id
    assert payload["rubric_version"] == "human-usefulness-v1"
    assert payload["scores"][0]["score"] == 4
    assert payload["disposition"] == "completed"
