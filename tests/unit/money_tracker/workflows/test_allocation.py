"""Tests for reconciled payment allocation from trusted profile context."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    Money,
    sum_money,
)
from money_tracker.workflows import (
    ExpenseExtraction,
    ResolutionReason,
    UnresolvedExtractionError,
    resolve_amount,
    resolve_funding_source,
    resolve_payment_allocations,
)

PROFILE_ID = uuid4()
CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Carta",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    is_default=True,
)
MEAL_VOUCHERS = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Buoni pasto",
    funding_class=FundingClass.BENEFIT,
    balance_mode=BalanceMode.LEDGER,
)
OLD_CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Carta vecchia",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    status=FundingSourceStatus.ARCHIVED,
)
SOURCES = (CARD, MEAL_VOUCHERS, OLD_CARD)


def _extraction(**overrides: Any) -> ExpenseExtraction:
    """Build one schema-valid extraction for allocation tests."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 5000,
        "description": "spesa al supermercato",
        "evidence": "ho speso 50 euro al supermercato",
    }
    payload.update(overrides)
    return ExpenseExtraction.model_validate_json(json.dumps(payload))


def _reason(
    extraction: ExpenseExtraction,
    sources: tuple[FundingSource, ...] = SOURCES,
) -> ResolutionReason:
    """Return the stable reason one unresolvable allocation reports."""

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_payment_allocations(extraction, funding_sources=sources)
    return error.value.reason


def test_unstated_funding_uses_the_configured_default() -> None:
    """Fill an omitted payment method from trusted profile configuration."""

    allocations = resolve_payment_allocations(_extraction(), funding_sources=SOURCES)

    assert allocations == (type(allocations[0])(CARD.id, Money(5000)),)


def test_default_resolution_ignores_an_archived_source() -> None:
    """Never allocate to a source that no longer accepts allocations."""

    archived_default = FundingSource(
        id=uuid4(),
        profile_id=PROFILE_ID,
        name="Carta chiusa",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        status=FundingSourceStatus.ARCHIVED,
        is_default=True,
    )

    assert _reason(_extraction(), (archived_default, MEAL_VOUCHERS)) is (
        ResolutionReason.NO_DEFAULT_FUNDING_SOURCE
    )


def test_missing_default_is_a_question_not_an_arbitrary_choice() -> None:
    """Refuse to pick a source when the profile configures no default."""

    assert _reason(_extraction(), (MEAL_VOUCHERS,)) is (
        ResolutionReason.NO_DEFAULT_FUNDING_SOURCE
    )


@pytest.mark.parametrize("stated", ["Buoni pasto", "buoni pasto", "  BUONI  PASTO "])
def test_named_source_matching_is_case_and_spacing_insensitive(
    stated: str,
) -> None:
    """Match the owner's own configured name however they typed it."""

    resolved = resolve_funding_source(stated, funding_sources=SOURCES)

    assert resolved.id == MEAL_VOUCHERS.id
    assert resolved.funding_class is FundingClass.BENEFIT


def test_single_named_source_carries_the_whole_total() -> None:
    """Allocate everything to one source without repeating the amount."""

    allocations = resolve_payment_allocations(
        _extraction(payment_parts=[{"funding_source_text": "buoni pasto"}]),
        funding_sources=SOURCES,
    )

    assert len(allocations) == 1
    assert allocations[0].funding_source_id == MEAL_VOUCHERS.id
    assert allocations[0].amount == Money(5000)


def test_single_named_source_may_restate_the_full_amount() -> None:
    """Accept a redundant but consistent restatement of the total."""

    allocations = resolve_payment_allocations(
        _extraction(
            payment_parts=[{"funding_source_text": "carta", "amount_minor_units": 5000}]
        ),
        funding_sources=SOURCES,
    )

    assert allocations[0].amount == Money(5000)


def test_single_named_source_with_a_partial_amount_does_not_reconcile() -> None:
    """Refuse a payment dimension that silently loses part of the total."""

    extraction = _extraction(
        payment_parts=[{"funding_source_text": "carta", "amount_minor_units": 3000}]
    )

    assert _reason(extraction) is ResolutionReason.SPLIT_DOES_NOT_RECONCILE


def test_split_funding_journey_reconciles_across_two_classes() -> None:
    """Reproduce the documented benefit and personal-funds split."""

    allocations = resolve_payment_allocations(
        _extraction(
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ]
        ),
        funding_sources=SOURCES,
    )

    by_source = {item.funding_source_id: item.amount for item in allocations}

    assert by_source == {
        MEAL_VOUCHERS.id: Money(3000),
        CARD.id: Money(2000),
    }
    assert sum_money(
        tuple(item.amount for item in allocations), currency="EUR"
    ) == Money(5000)


def test_split_that_does_not_sum_to_the_total_is_refused() -> None:
    """Never post partially when stated portions disagree with the total."""

    extraction = _extraction(
        payment_parts=[
            {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
            {"funding_source_text": "carta", "amount_minor_units": 2500},
        ]
    )

    assert _reason(extraction) is ResolutionReason.SPLIT_DOES_NOT_RECONCILE


def test_refund_allocations_carry_the_negative_total_sign() -> None:
    """Return value to its sources instead of inverting one dimension."""

    allocations = resolve_payment_allocations(
        _extraction(
            polarity="refund",
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ],
        ),
        funding_sources=SOURCES,
    )

    assert all(item.amount.minor_units < 0 for item in allocations)
    assert sum_money(
        tuple(item.amount for item in allocations), currency="EUR"
    ) == Money(-5000)


def test_refund_to_one_unstated_default_source_is_negative() -> None:
    """Keep the sign convention for the single-allocation shape too."""

    allocations = resolve_payment_allocations(
        _extraction(polarity="refund"), funding_sources=SOURCES
    )

    assert allocations[0].amount == Money(-5000)


def test_unresolved_funding_words_are_a_question() -> None:
    """Ask which resource rather than guessing a synonym."""

    extraction = _extraction(payment_parts=[{"funding_source_text": "bancomat"}])

    assert _reason(extraction) is ResolutionReason.FUNDING_SOURCE_UNRESOLVED


def test_archived_source_cannot_be_named() -> None:
    """Refuse an explicitly named source that no longer accepts allocations."""

    extraction = _extraction(payment_parts=[{"funding_source_text": "carta vecchia"}])

    assert _reason(extraction) is ResolutionReason.FUNDING_SOURCE_UNRESOLVED


def test_two_portions_resolving_to_one_source_are_refused() -> None:
    """Keep the payment dimension free of a duplicated source row.

    The contract rejects portions whose text is already identical, but only
    resolution knows the configured names. Accented typo variants differ as
    text and still name one source, so the authoritative duplicate check
    happens after resolution.
    """

    extraction = _extraction(
        payment_parts=[
            {"funding_source_text": "carta", "amount_minor_units": 3000},
            {"funding_source_text": "cartà", "amount_minor_units": 2000},
        ]
    )

    assert _reason(extraction) is ResolutionReason.FUNDING_SOURCE_REPEATED


def test_declared_unknown_payment_is_a_question() -> None:
    """Honor an explicit abstention instead of applying the default."""

    extraction = _extraction(unknowns=["payment"])

    assert _reason(extraction) is ResolutionReason.PAYMENT_UNKNOWN


def test_unknown_amount_blocks_the_payment_dimension() -> None:
    """Refuse to allocate against a total that was never determined."""

    extraction = _extraction(amount_minor_units=None, unknowns=["amount"])

    assert _reason(extraction) is ResolutionReason.AMOUNT_UNKNOWN


def test_allocations_are_never_zero_valued() -> None:
    """Keep every allocation a real movement the domain accepts."""

    allocations = resolve_payment_allocations(
        _extraction(
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ]
        ),
        funding_sources=SOURCES,
    )

    assert all(item.amount.minor_units != 0 for item in allocations)


@pytest.mark.parametrize("polarity", ["expense", "refund"])
def test_resolved_allocations_satisfy_the_domain_aggregate(polarity: str) -> None:
    """Prove the payment dimension the resolver builds is domain-acceptable.

    The domain independently requires non-zero rows, unique sources, a
    polarity matching the total, and exact reconciliation. Building a real
    aggregate from the resolver output checks all of that against production
    domain code rather than against a restatement of it here.
    """

    extraction = _extraction(
        polarity=polarity,
        payment_parts=[
            {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
            {"funding_source_text": "carta", "amount_minor_units": 2000},
        ],
    )
    allocations = resolve_payment_allocations(extraction, funding_sources=SOURCES)
    total = resolve_amount(extraction)

    expense = Expense(
        id=uuid4(),
        profile_id=PROFILE_ID,
        amount=total,
        occurred_at=datetime(2026, 9, 2, 21, 30, tzinfo=UTC),
        description=extraction.description,
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(
                category_id=uuid4(),
                amount=total,
                provenance="test:fixture",
            ),
        ),
        payment_allocations=allocations,
        source_reference="test:allocation",
        idempotency_key="test-allocation-1",
    )

    expense.require_reconciled()

    assert expense.is_refund == (polarity == "refund")
