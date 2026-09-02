"""Tests for assembling a draft movement from resolved model output."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from money_tracker.domain import (
    BalanceMode,
    CategoryProposalStatus,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    initial_taxonomy,
)
from money_tracker.workflows import (
    EXPENSE_COMMAND_PREFIX,
    CategoryFit,
    ChannelMessage,
    ExpenseExtraction,
    PreparedExpense,
    ResolutionReason,
    UnresolvedExtractionError,
    canonical_request_hash,
    expense_command_key,
    prepare_expense_draft,
)

TAXONOMY = initial_taxonomy()
PROFILE_ID = uuid4()
RECEIVED_AT = datetime(2026, 9, 2, 21, 30, tzinfo=UTC)
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
SOURCES = (CARD, MEAL_VOUCHERS)


def _extraction(**overrides: Any) -> ExpenseExtraction:
    """Build one schema-valid extraction for preparation tests."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "description": "croccantini per i gatti",
        "evidence": "ho speso 12 euro per i croccantini",
    }
    payload.update(overrides)
    return ExpenseExtraction.model_validate_json(json.dumps(payload))


def _existing_fit(**overrides: Any) -> CategoryFit:
    """Build one existing-category judgment."""

    payload: dict[str, Any] = {
        "outcome": "existing",
        "category_name": "Cibo e accessori",
        "evidence": "croccantini per i gatti",
    }
    payload.update(overrides)
    return CategoryFit.model_validate_json(json.dumps(payload))


def _proposal_fit(**overrides: Any) -> CategoryFit:
    """Build one proposal judgment."""

    payload: dict[str, Any] = {
        "outcome": "proposal",
        "proposed_name": "Assicurazione animali",
        "description": "Polizze sanitarie per animali domestici.",
        "rationale": "Nessuna categoria attiva copre le polizze.",
        "parent_name": "Animali",
        "evidence": "polizza per il gatto",
    }
    payload.update(overrides)
    return CategoryFit.model_validate_json(json.dumps(payload))


def _message(**overrides: Any) -> ChannelMessage:
    """Build one normalized inbound message."""

    fields: dict[str, Any] = {
        "source_reference": "channel:inbox:42",
        "received_at": RECEIVED_AT,
        "text": "ho speso 12 euro per i croccantini",
    }
    fields.update(overrides)
    return ChannelMessage(**fields)


def _prepare(
    extraction: ExpenseExtraction | None = None,
    fit: CategoryFit | None = None,
    **overrides: Any,
) -> PreparedExpense:
    """Prepare one draft with explicit trusted context."""

    arguments: dict[str, Any] = {
        "message": _message(),
        "profile_id": PROFILE_ID,
        "expense_id": uuid4(),
        "proposal_id": uuid4(),
        "taxonomy": TAXONOMY,
        "funding_sources": SOURCES,
    }
    arguments.update(overrides)
    return prepare_expense_draft(
        extraction or _extraction(), fit or _existing_fit(), **arguments
    )


def test_clear_expense_becomes_a_complete_reconciled_draft() -> None:
    """Reproduce the documented clear-expense journey up to the draft."""

    prepared = _prepare()

    assert prepared.expense.status is ExpenseStatus.DRAFT
    assert prepared.expense.amount == Money(1200)
    assert prepared.expense.description == "croccantini per i gatti"
    assert prepared.expense.occurred_at == RECEIVED_AT
    assert not prepared.awaits_category_approval
    prepared.expense.require_reconciled()


def test_no_text_message_may_post_itself() -> None:
    """Hold the rule that auto-post stays disabled until it is calibrated."""

    assert _prepare().expense.status is ExpenseStatus.DRAFT


def test_omitted_payment_method_uses_the_configured_default() -> None:
    """Fill the funding dimension from trusted profile configuration."""

    prepared = _prepare()

    assert len(prepared.expense.payment_allocations) == 1
    assert prepared.expense.payment_allocations[0].funding_source_id == CARD.id


def test_split_funding_journey_reaches_a_reconciled_draft() -> None:
    """Reproduce the documented benefit and personal-funds split."""

    prepared = _prepare(
        _extraction(
            amount_minor_units=5000,
            description="spesa al supermercato",
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ],
        ),
        _existing_fit(category_name="Spesa alimentare"),
    )

    by_source = {
        item.funding_source_id: item.amount
        for item in prepared.expense.payment_allocations
    }

    assert by_source == {MEAL_VOUCHERS.id: Money(3000), CARD.id: Money(2000)}
    prepared.expense.require_reconciled()


def test_refund_reaches_a_negative_reconciled_draft() -> None:
    """Keep both dimensions negative for a reimbursement."""

    prepared = _prepare(_extraction(polarity="refund"))

    assert prepared.expense.is_refund
    assert prepared.expense.amount == Money(-1200)
    assert prepared.expense.category_allocations[0].amount == Money(-1200)
    assert prepared.expense.payment_allocations[0].amount == Money(-1200)
    prepared.expense.require_reconciled()


def test_relative_italian_date_resolves_against_the_message() -> None:
    """Anchor the occurrence to the Rome day the owner named."""

    prepared = _prepare(_extraction(occurred_at_text="ieri"))

    assert prepared.expense.occurred_at.astimezone(UTC).day == 1


def test_proposal_leaves_the_purpose_dimension_pending() -> None:
    """Prepare the draft while only owner approval may extend the taxonomy."""

    proposal_id = uuid4()

    prepared = _prepare(fit=_proposal_fit(), proposal_id=proposal_id)

    assert prepared.awaits_category_approval
    assert prepared.expense.category_allocations == ()
    assert prepared.expense.payment_allocations != ()
    assert prepared.proposal is not None
    assert prepared.proposal.id == proposal_id
    assert prepared.proposal.status is CategoryProposalStatus.PENDING
    assert prepared.proposal.source_reference == "channel:inbox:42"


def test_pending_draft_cannot_be_posted_as_it_stands() -> None:
    """Keep an unreconciled purpose dimension from reaching a posted state."""

    prepared = _prepare(fit=_proposal_fit())

    with pytest.raises(Exception, match="category allocations are required"):
        prepared.expense.require_reconciled()


def test_prepared_result_rejects_a_contradictory_combination() -> None:
    """Refuse a result that both resolved a purpose and proposed one."""

    resolved = _prepare()
    pending = _prepare(fit=_proposal_fit())

    with pytest.raises(ValueError, match="never both and never neither"):
        PreparedExpense(
            expense=resolved.expense,
            request_hash=resolved.request_hash,
            proposal=pending.proposal,
        )
    with pytest.raises(ValueError, match="never both and never neither"):
        PreparedExpense(expense=pending.expense, request_hash=pending.request_hash)


def test_excluded_candidate_is_refused_on_re_proposal() -> None:
    """Exclude a candidate the owner already rejected in this review."""

    with pytest.raises(UnresolvedExtractionError) as error:
        _prepare(
            fit=_proposal_fit(),
            excluded_category_names=("assicurazione animali",),
        )

    assert error.value.reason is ResolutionReason.CATEGORY_EXCLUDED


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"amount_minor_units": None, "unknowns": ["amount"]},
            ResolutionReason.AMOUNT_UNKNOWN,
        ),
        (
            {"unknowns": ["occurred_at"]},
            ResolutionReason.OCCURRENCE_UNKNOWN,
        ),
        (
            {"occurred_at_text": "l'altro giorno"},
            ResolutionReason.OCCURRENCE_UNREADABLE,
        ),
        (
            {"unknowns": ["payment"]},
            ResolutionReason.PAYMENT_UNKNOWN,
        ),
        (
            {"payment_parts": [{"funding_source_text": "bancomat"}]},
            ResolutionReason.FUNDING_SOURCE_UNRESOLVED,
        ),
    ],
)
def test_unresolvable_parts_report_the_question_to_ask(
    overrides: dict[str, Any],
    reason: ResolutionReason,
) -> None:
    """Select a targeted question instead of writing a guess."""

    with pytest.raises(UnresolvedExtractionError) as error:
        _prepare(_extraction(**overrides))

    assert error.value.reason is reason


def test_invented_category_is_refused_before_any_draft_exists() -> None:
    """Refuse a purpose the active taxonomy does not contain."""

    with pytest.raises(UnresolvedExtractionError) as error:
        _prepare(fit=_existing_fit(category_name="Criptovalute"))

    assert error.value.reason is ResolutionReason.CATEGORY_UNRESOLVED


def test_command_key_is_derived_from_the_inbound_record() -> None:
    """Let a redelivered message claim the same key rather than a new one."""

    first = _prepare()
    second = _prepare(expense_id=uuid4())

    assert first.expense.idempotency_key == f"{EXPENSE_COMMAND_PREFIX}:channel:inbox:42"
    assert second.expense.idempotency_key == first.expense.idempotency_key


def test_command_key_requires_a_source_reference() -> None:
    """Refuse a key that could not identify its inbound record."""

    with pytest.raises(ValueError, match="requires a source reference"):
        expense_command_key("   ")


def test_redelivery_of_the_same_message_hashes_identically() -> None:
    """Match a retry so the repository returns the first movement."""

    first = _prepare(expense_id=uuid4())
    second = _prepare(expense_id=uuid4())

    assert first.expense.id != second.expense.id
    assert first.request_hash == second.request_hash


def test_request_hash_ignores_the_stored_representation() -> None:
    """Compare what the owner asked for, not how the record is kept."""

    prepared = _prepare()
    marked = prepared.expense.mark_needs_review()

    assert canonical_request_hash(marked) == prepared.request_hash


def test_request_hash_is_offset_independent() -> None:
    """Hash the same instant identically whatever offset expressed it."""

    rome = timezone(timedelta(hours=2))
    utc_message = _prepare(_extraction(), _existing_fit(), message=_message())
    rome_message = _prepare(
        _extraction(),
        _existing_fit(),
        message=_message(received_at=RECEIVED_AT.astimezone(rome)),
    )

    assert rome_message.expense.occurred_at == utc_message.expense.occurred_at
    assert rome_message.request_hash == utc_message.request_hash


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_minor_units": 1300},
        {"description": "croccantini per il cane"},
        {"merchant": "Arcaplanet"},
        {"occurred_at_text": "ieri"},
    ],
)
def test_changed_request_content_changes_the_hash(
    overrides: dict[str, Any],
) -> None:
    """Let the repository detect a reused key with different content."""

    baseline = _prepare()

    assert _prepare(_extraction(**overrides)).request_hash != baseline.request_hash


def test_changed_funding_split_changes_the_hash() -> None:
    """Cover the payment dimension in the request semantics."""

    baseline = _prepare(_extraction(payment_parts=[{"funding_source_text": "carta"}]))
    moved = _prepare(
        _extraction(payment_parts=[{"funding_source_text": "buoni pasto"}])
    )

    assert moved.request_hash != baseline.request_hash


def test_changed_category_changes_the_hash() -> None:
    """Cover the purpose dimension in the request semantics."""

    baseline = _prepare()
    other = _prepare(fit=_existing_fit(category_name="Spesa alimentare"))

    assert other.request_hash != baseline.request_hash


def test_request_hash_is_a_lowercase_sha256() -> None:
    """Match the exact form the repository requires."""

    digest = _prepare().request_hash

    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(char in "0123456789abcdef" for char in digest)
