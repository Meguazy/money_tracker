"""Regression checks for advice interpretation and missing human evidence."""

from __future__ import annotations

import pytest

from money_tracker.evaluations.advice import _facets, _metrics
from money_tracker.evaluations.suites import CaseResult, CaseStatus


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


def test_automatic_grounding_cannot_replace_human_usefulness_review() -> None:
    """The required human gate remains blocking until an owner supplies ratings."""

    metrics = _metrics(
        [{}],
        [CaseResult(case_id="a", status=CaseStatus.PASSED, expected="answered")],
        {"a": {}},
    )
    usefulness = next(m for m in metrics if m.name == "human_usefulness_median")
    assert usefulness.blocking
    assert usefulness.denominator == 0
    assert not usefulness.passed
