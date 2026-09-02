"""Tests for the strict versioned router model boundary.

Decisions are parsed from JSON because that is how a local model returns them.
Strict Python-object validation is deliberately stricter than JSON validation
in Pydantic, so parsing the real transport shape is the faithful test.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from money_tracker.workflows import (
    ROUTER_CONTRACT_VERSION,
    RouteClassification,
    WorkflowIntent,
)

FORBIDDEN_AUTHORITY_FIELDS = (
    "profile_id",
    "profile",
    "timezone",
    "funding_source_id",
    "idempotency_key",
    "actor",
    "category_id",
)


def _parse(payload: dict[str, object]) -> RouteClassification:
    """Parse one classification through the real JSON model boundary."""

    return RouteClassification.model_validate_json(json.dumps(payload))


def test_contract_version_is_pinned() -> None:
    """Keep resumed threads anchored to a declared boundary version."""

    assert ROUTER_CONTRACT_VERSION == "v1"


def test_classification_accepts_a_complete_decision() -> None:
    """Parse a well-formed decision with evidence and explicit unknowns."""

    classification = _parse(
        {
            "intent": "expense",
            "confidence": 0.91,
            "evidence": "ho speso 12 euro",
            "unknowns": ["metodo di pagamento"],
        }
    )

    assert classification.intent is WorkflowIntent.EXPENSE
    assert classification.confidence == 0.91
    assert classification.unknowns == ("metodo di pagamento",)


def test_classification_defaults_to_no_declared_unknowns() -> None:
    """Treat an omitted unknown list as an empty immutable tuple."""

    classification = _parse(
        {"intent": "query", "confidence": 0.8, "evidence": "quanto ho speso"}
    )

    assert classification.unknowns == ()


def test_classification_exposes_no_authority_field() -> None:
    """Deny the model any shape in which to choose scope or authorization."""

    fields = set(RouteClassification.model_fields)

    assert fields == {"intent", "confidence", "evidence", "unknowns"}
    for forbidden in FORBIDDEN_AUTHORITY_FIELDS:
        assert forbidden not in fields


@pytest.mark.parametrize("intent", ["receipt", "command"])
def test_classification_rejects_a_deterministic_only_route(intent: str) -> None:
    """Refuse a model claim on a route that must stay reproducible."""

    with pytest.raises(ValidationError, match="assigned deterministically"):
        _parse({"intent": intent, "confidence": 1.0, "evidence": "foto"})


def test_classification_rejects_an_unsupported_intent_value() -> None:
    """Keep the destination set closed to invented routes."""

    with pytest.raises(ValidationError):
        _parse({"intent": "transfer_money", "confidence": 1.0, "evidence": "bonifico"})


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_classification_rejects_confidence_outside_the_unit_interval(
    confidence: float,
) -> None:
    """Keep the diagnostic confidence signal comparable across runs."""

    with pytest.raises(ValidationError):
        _parse({"intent": "query", "confidence": confidence, "evidence": "quanto"})


def test_classification_rejects_unknown_fields() -> None:
    """Fail closed on an extended decision that could smuggle authority."""

    with pytest.raises(ValidationError):
        _parse(
            {
                "intent": "query",
                "confidence": 0.5,
                "evidence": "quanto",
                "profile_id": "00000000-0000-0000-0000-000000000001",
            }
        )


def test_classification_requires_auditable_evidence() -> None:
    """Refuse a decision no reviewer could check against the input."""

    with pytest.raises(ValidationError):
        _parse({"intent": "query", "confidence": 0.5, "evidence": "   "})
    with pytest.raises(ValidationError):
        _parse({"intent": "query", "confidence": 0.5})
    with pytest.raises(ValidationError):
        _parse({"intent": "query", "confidence": 0.5, "evidence": "x" * 281})


def test_classification_rejects_repeated_unknowns() -> None:
    """Keep unresolved aspects a set of distinct statements."""

    with pytest.raises(ValidationError, match="distinct"):
        _parse(
            {
                "intent": "expense",
                "confidence": 0.5,
                "evidence": "12 euro",
                "unknowns": ["data", "data"],
            }
        )


def test_classification_rejects_coerced_confidence() -> None:
    """Refuse a quoted number so scoring cannot depend on coercion."""

    with pytest.raises(ValidationError):
        _parse({"intent": "query", "confidence": "0.9", "evidence": "quanto"})
