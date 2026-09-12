"""Tests for expense lifecycle and dual-allocation invariants."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    DomainError,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    InvalidTransitionError,
    Money,
    PaymentAllocation,
    ReconciliationError,
    initial_taxonomy,
    posted_category_spend,
    posted_funding_class_split,
)

ROME = ZoneInfo("Europe/Rome")


def _source(profile_id: UUID, funding_class: FundingClass) -> FundingSource:
    """Build one active source for aggregate tests."""

    return FundingSource(
        id=uuid4(),
        profile_id=profile_id,
        name=funding_class.value,
        funding_class=funding_class,
        balance_mode=BalanceMode.UNTRACKED,
    )


def _draft(
    *,
    amount: int = 5000,
    category_amounts: tuple[int, ...] = (5000,),
    payment_amounts: tuple[int, ...] = (5000,),
    receipt_id: UUID | None = None,
) -> tuple[Expense, tuple[FundingSource, ...]]:
    """Build a draft and matching active references for one test."""

    taxonomy = initial_taxonomy()
    profile_id = uuid4()
    sources = tuple(
        _source(
            profile_id,
            FundingClass.BENEFIT if index == 0 else FundingClass.PERSONAL_FUNDS,
        )
        for index in range(len(payment_amounts))
    )
    expense = Expense(
        id=uuid4(),
        profile_id=profile_id,
        amount=Money(amount),
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=ROME),
        description="Spesa di prova",
        merchant="Negozio",
        status=ExpenseStatus.DRAFT,
        category_allocations=tuple(
            CategoryAllocation(category.id, Money(part), "owner")
            for category, part in zip(
                taxonomy.categories[: len(category_amounts)],
                category_amounts,
                strict=True,
            )
        ),
        payment_allocations=tuple(
            PaymentAllocation(source.id, Money(part))
            for source, part in zip(sources, payment_amounts, strict=True)
        ),
        source_reference="api:test",
        idempotency_key=f"test:{uuid4()}",
        receipt_id=receipt_id,
    )
    return expense, sources


def test_post_requires_both_allocation_sums() -> None:
    """Block posting when either purpose or payment does not equal the total."""

    taxonomy = initial_taxonomy()
    category_mismatch, sources = _draft(category_amounts=(4900,))
    with pytest.raises(ReconciliationError, match="category"):
        category_mismatch.post(taxonomy=taxonomy, funding_sources=sources)

    payment_mismatch, sources = _draft(payment_amounts=(4900,))
    with pytest.raises(ReconciliationError, match="payment"):
        payment_mismatch.post(taxonomy=taxonomy, funding_sources=sources)


def test_split_payment_and_mixed_category_post_once() -> None:
    """Post one purchase after two independent split dimensions reconcile."""

    draft, sources = _draft(
        category_amounts=(3000, 2000),
        payment_amounts=(3000, 2000),
    )
    posted = draft.post(taxonomy=initial_taxonomy(), funding_sources=sources)

    assert posted.status is ExpenseStatus.POSTED
    assert posted.version == 2
    assert posted.amount == Money(5000)
    assert posted_funding_class_split(
        (posted,), funding_sources=sources
    ).benefit == Money(3000)
    assert posted_funding_class_split(
        (posted,), funding_sources=sources
    ).personal_funds == Money(2000)


def test_refund_uses_negative_allocations_and_reverses_reports() -> None:
    """Represent a refund as one negative movement with matching dimensions."""

    original_id = uuid4()
    draft, sources = _draft(
        amount=-1200,
        category_amounts=(-1200,),
        payment_amounts=(-1200,),
    )
    refund = replace(draft, original_expense_id=original_id)
    posted = refund.post(taxonomy=initial_taxonomy(), funding_sources=sources)

    assert posted.is_refund
    assert posted_funding_class_split(
        (posted,), funding_sources=sources
    ).benefit == Money(-1200)


def test_receipt_requires_explicit_approval_before_posting() -> None:
    """Keep every image-backed V1 draft out of authoritative state until approval."""

    draft, sources = _draft(receipt_id=uuid4())
    with pytest.raises(DomainError, match="owner approval"):
        draft.post(taxonomy=initial_taxonomy(), funding_sources=sources)

    assert (
        draft.post(
            taxonomy=initial_taxonomy(),
            funding_sources=sources,
            receipt_approved=True,
        ).status
        is ExpenseStatus.POSTED
    )


def test_correction_and_undo_preserve_identity_and_versions() -> None:
    """Replace complete state and void without destructive history deletion."""

    draft, sources = _draft()
    posted = draft.post(taxonomy=initial_taxonomy(), funding_sources=sources)
    corrected = posted.corrected(
        amount=Money(6000),
        occurred_at=posted.occurred_at,
        description="Spesa corretta",
        merchant=posted.merchant,
        category_allocations=(
            CategoryAllocation(
                posted.category_allocations[0].category_id,
                Money(6000),
                "owner-correction",
                owner_override=True,
            ),
        ),
        payment_allocations=(PaymentAllocation(sources[0].id, Money(6000)),),
    )
    voided = corrected.void()

    assert corrected.id == posted.id
    assert corrected.version == posted.version + 1
    assert voided.status is ExpenseStatus.VOIDED
    assert voided.version == corrected.version + 1
    with pytest.raises(InvalidTransitionError, match="cannot be corrected"):
        voided.corrected(
            amount=voided.amount,
            occurred_at=voided.occurred_at,
            description=voided.description,
            merchant=voided.merchant,
            category_allocations=voided.category_allocations,
            payment_allocations=voided.payment_allocations,
        )


def test_parent_rollup_counts_child_allocations_only_once() -> None:
    """Include direct and descendant posted allocations without adding parent total."""

    taxonomy = initial_taxonomy()
    draft, sources = _draft(category_amounts=(3000, 2000))
    posted = draft.post(taxonomy=taxonomy, funding_sources=sources)
    root = taxonomy.categories[0]
    child = taxonomy.categories[1]

    assert posted_category_spend(
        (posted,), category_id=root.id, taxonomy=taxonomy
    ) == Money(5000)
    assert posted_category_spend(
        (posted,), category_id=child.id, taxonomy=taxonomy
    ) == Money(2000)
    assert (
        posted_category_spend((posted.void(),), category_id=root.id, taxonomy=taxonomy)
        == Money.zero()
    )


def test_authoritative_state_cannot_be_constructed_unreconciled() -> None:
    """Reject bypassing the posting method with an invalid posted aggregate."""

    draft, _ = _draft(category_amounts=(4900,))
    with pytest.raises(ReconciliationError, match="category"):
        replace(draft, status=ExpenseStatus.POSTED)


def test_allocations_must_be_unique_and_match_parent_polarity() -> None:
    """Reject ambiguous duplicate rows and positive allocations on a refund."""

    draft, _ = _draft()
    duplicate = draft.category_allocations[0]
    with pytest.raises(DomainError, match="identifiers must be unique"):
        replace(draft, category_allocations=(duplicate, duplicate))
    with pytest.raises(DomainError, match="polarity"):
        replace(draft, amount=Money(-5000), original_expense_id=uuid4())


def test_post_rejects_inactive_or_cross_profile_references() -> None:
    """Keep trusted profile scope and active-reference checks outside the model."""

    draft, sources = _draft()
    archived = replace(sources[0], status=FundingSourceStatus.ARCHIVED)
    with pytest.raises(DomainError, match="active funding source"):
        draft.post(taxonomy=initial_taxonomy(), funding_sources=(archived,))

    other_profile = replace(sources[0], profile_id=uuid4())
    with pytest.raises(DomainError, match="another profile"):
        draft.post(taxonomy=initial_taxonomy(), funding_sources=(other_profile,))


def test_needs_review_can_be_discarded_but_not_reentered() -> None:
    """Allow owner cancellation while rejecting a repeated review transition."""

    draft, _ = _draft(category_amounts=(), payment_amounts=())
    review = draft.mark_needs_review()
    assert review.status is ExpenseStatus.NEEDS_REVIEW
    with pytest.raises(InvalidTransitionError, match="only a draft"):
        review.mark_needs_review()
    assert review.discard().status is ExpenseStatus.DISCARDED


def test_occurrence_time_and_original_refund_link_are_validated() -> None:
    """Reject naive occurrence times and original links on positive purchases."""

    draft, _ = _draft()
    with pytest.raises(DomainError, match="timezone"):
        replace(draft, occurred_at=datetime(2026, 9, 2, 10))
    with pytest.raises(DomainError, match="only refunds"):
        replace(draft, original_expense_id=uuid4())


def test_funding_split_ignores_non_posted_records_and_requires_known_sources() -> None:
    """Report only authoritative payments and fail closed on broken references."""

    draft, sources = _draft()
    split = posted_funding_class_split((draft,), funding_sources=sources)
    assert split.personal_funds == Money.zero()
    assert split.benefit == Money.zero()
    posted = draft.post(taxonomy=initial_taxonomy(), funding_sources=sources)
    with pytest.raises(DomainError, match="unknown funding source"):
        posted_funding_class_split((posted,), funding_sources=())
