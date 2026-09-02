"""Tests for the strict versioned expense and refund extraction boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from money_tracker.workflows import (
    EXTRACTION_CONTRACT_VERSION,
    ExpenseExtraction,
    ExpensePolarity,
    ExtractionField,
    ResolutionReason,
    UnresolvedExtractionError,
    resolve_amount,
)

FORBIDDEN_AUTHORITY_FIELDS = (
    "profile_id",
    "timezone",
    "funding_source_id",
    "category_id",
    "idempotency_key",
    "actor",
    "occurred_at",
)


def _payload(**overrides: Any) -> dict[str, Any]:
    """Build one valid extraction payload with explicit overrides."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "description": "croccantini per i gatti",
        "evidence": "ho speso 12 euro per i croccantini",
    }
    payload.update(overrides)
    return payload


def _parse(**overrides: Any) -> ExpenseExtraction:
    """Parse one extraction through the real JSON model boundary."""

    return ExpenseExtraction.model_validate_json(json.dumps(_payload(**overrides)))


def test_contract_version_is_pinned_independently_of_the_router() -> None:
    """Let the extraction boundary evolve without invalidating router threads."""

    assert EXTRACTION_CONTRACT_VERSION == "v1"


def test_extraction_accepts_a_minimal_clear_expense() -> None:
    """Parse the canonical clear-expense journey without unknowns."""

    extraction = _parse()

    assert extraction.polarity is ExpensePolarity.EXPENSE
    assert extraction.amount_minor_units == 1200
    assert extraction.occurred_at_text is None
    assert extraction.payment_parts == ()
    assert extraction.unknowns == ()
    assert extraction.is_resolvable


def test_extraction_exposes_no_authority_or_resolved_field() -> None:
    """Keep scope, identifiers, and resolved time out of the model's reach."""

    fields = set(ExpenseExtraction.model_fields)

    for forbidden in FORBIDDEN_AUTHORITY_FIELDS:
        assert forbidden not in fields


def test_refund_polarity_is_stated_as_a_word_not_a_sign() -> None:
    """Require a positive magnitude so a stray sign cannot flip direction."""

    extraction = _parse(polarity="refund")

    assert extraction.polarity is ExpensePolarity.REFUND
    assert extraction.amount_minor_units == 1200
    with pytest.raises(ValidationError):
        _parse(amount_minor_units=-1200)
    with pytest.raises(ValidationError):
        _parse(amount_minor_units=0)


def test_an_absent_amount_is_unresolvable() -> None:
    """Refuse to store a movement whose total was never determined."""

    extraction = _parse(amount_minor_units=None)

    assert extraction.amount_minor_units is None
    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_amount(extraction)
    assert error.value.reason is ResolutionReason.AMOUNT_UNKNOWN


@pytest.mark.parametrize(
    ("field", "value", "declared"),
    [
        ("amount_minor_units", 1200, "amount"),
        ("occurred_at_text", "ieri", "occurred_at"),
        ("merchant", "Conad", "merchant"),
    ],
)
def test_a_declared_unknown_wins_over_a_value_supplied_beside_it(
    field: str,
    value: object,
    declared: str,
) -> None:
    """Read the abstention, not the guess, when a model supplies both.

    Strict constrained decoding requires every property to be present, so a
    model routinely fills a field and flags it in the same answer. The boundary
    accepts that shape and the deterministic layer prefers the flag, because a
    flag leads to a question while a guess would be stored.
    """

    extraction = _parse(**{field: value}, unknowns=[declared])

    assert extraction.declares_unknown(ExtractionField(declared))
    assert not extraction.is_resolvable


def test_unknown_payment_wins_over_itemized_parts() -> None:
    """Ask about funding rather than charging portions the model doubted."""

    extraction = _parse(
        payment_parts=[{"funding_source_text": "carta"}],
        unknowns=["payment"],
    )

    assert extraction.payment_parts != ()
    assert extraction.declares_unknown(ExtractionField.PAYMENT)


def test_absent_optional_values_are_defaults_not_unknowns() -> None:
    """Treat silence as the documented default rather than a question."""

    extraction = _parse(occurred_at_text=None, merchant=None, payment_parts=[])

    assert extraction.unknowns == ()
    assert extraction.is_resolvable


def test_single_named_source_may_omit_its_amount() -> None:
    """Let one named source carry the whole total without repeating it."""

    extraction = _parse(payment_parts=[{"funding_source_text": "buoni pasto"}])

    assert extraction.payment_parts[0].amount_minor_units is None


def test_split_payment_must_quantify_every_part() -> None:
    """Reject a split that could never reconcile against the total."""

    with pytest.raises(ValidationError, match="quantify every part"):
        _parse(
            amount_minor_units=5000,
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta"},
            ],
        )


def test_split_payment_accepts_fully_quantified_parts() -> None:
    """Parse the documented split-funding journey."""

    extraction = _parse(
        amount_minor_units=5000,
        description="spesa al supermercato",
        payment_parts=[
            {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
            {"funding_source_text": "carta", "amount_minor_units": 2000},
        ],
    )

    assert len(extraction.payment_parts) == 2
    assert (
        sum(part.amount_minor_units or 0 for part in extraction.payment_parts)
        == extraction.amount_minor_units
    )


def test_payment_parts_must_name_distinct_sources() -> None:
    """Prevent one source from being allocated twice at the boundary."""

    with pytest.raises(ValidationError, match="distinct funding sources"):
        _parse(
            amount_minor_units=5000,
            payment_parts=[
                {"funding_source_text": "Carta", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ],
        )


def test_payment_part_amount_must_be_positive() -> None:
    """Keep a funding portion a real magnitude."""

    with pytest.raises(ValidationError):
        _parse(
            payment_parts=[{"funding_source_text": "carta", "amount_minor_units": 0}]
        )


def test_extraction_requires_description_and_evidence() -> None:
    """Refuse an extraction no reviewer could audit or summarize."""

    with pytest.raises(ValidationError):
        _parse(description="   ")
    with pytest.raises(ValidationError):
        _parse(evidence="   ")


def test_extraction_rejects_unknown_fields_and_repeated_unknowns() -> None:
    """Fail closed on an extended or self-contradicting extraction."""

    with pytest.raises(ValidationError):
        ExpenseExtraction.model_validate_json(
            json.dumps(_payload(profile_id="00000000-0000-0000-0000-000000000001"))
        )
    with pytest.raises(ValidationError, match="distinct"):
        _parse(
            amount_minor_units=None,
            currency=None,
            unknowns=["amount", "amount"],
        )


def test_extraction_rejects_an_unsupported_unknown_field() -> None:
    """Keep the abstention vocabulary closed."""

    with pytest.raises(ValidationError):
        _parse(unknowns=["description"])
