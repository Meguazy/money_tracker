"""Expense aggregate, allocation, lifecycle, and reporting policies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from money_tracker.domain.categories import CategoryStatus, Taxonomy
from money_tracker.domain.errors import (
    DomainError,
    InvalidTransitionError,
    ReconciliationError,
)
from money_tracker.domain.funding import (
    FundingClass,
    FundingSource,
    FundingSourceStatus,
)
from money_tracker.domain.money import Money, sum_money


class ExpenseStatus(StrEnum):
    """Lifecycle states controlling authority and reporting inclusion."""

    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    POSTED = "posted"
    VOIDED = "voided"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class CategoryAllocation:
    """Exact part of an expense assigned to a spending purpose.

    Attributes:
        category_id: Stable category identifier.
        amount: Signed allocated amount matching the parent movement polarity.
        provenance: Versioned origin such as owner, rule, prompt, or receipt line.
        evidence: Concise source evidence suitable for audit and review.
        owner_override: Whether the owner replaced an earlier classification.
        receipt_line_id: Optional receipt-line reference.
    """

    category_id: UUID
    amount: Money
    provenance: str
    evidence: str | None = None
    owner_override: bool = False
    receipt_line_id: UUID | None = None

    def __post_init__(self) -> None:
        """Reject empty provenance and zero-value allocation rows."""

        if self.amount.minor_units == 0:
            raise DomainError("category allocation amount cannot be zero")
        if not self.provenance.strip():
            raise DomainError("category allocation requires provenance")


@dataclass(frozen=True, slots=True)
class PaymentAllocation:
    """Exact part of an expense assigned to a funding source.

    Attributes:
        funding_source_id: Stable funding source identifier.
        amount: Signed allocated amount matching the parent movement polarity.
    """

    funding_source_id: UUID
    amount: Money

    def __post_init__(self) -> None:
        """Reject zero-value allocation rows."""

        if self.amount.minor_units == 0:
            raise DomainError("payment allocation amount cannot be zero")


@dataclass(frozen=True, slots=True)
class Expense:
    """One purchase or refund with two independent allocation dimensions.

    Draft and needs-review aggregates may be incomplete. Posted and voided
    aggregates must remain fully reconciled so history never loses its meaning.

    Attributes:
        id: Stable aggregate identifier.
        profile_id: Trusted owner scope.
        amount: Positive purchase total or negative refund total.
        occurred_at: Time the economic event happened, including timezone.
        description: Owner-facing movement description.
        merchant: Optional seller or provider metadata.
        status: Lifecycle state controlling analytics inclusion.
        category_allocations: Purpose dimension.
        payment_allocations: Funding dimension.
        source_reference: Stable source message, CLI, or API reference.
        idempotency_key: Stable command key preventing duplicate mutation.
        original_expense_id: Optional original movement for a refund.
        receipt_id: Optional receipt metadata reference.
        version: Optimistic concurrency version, starting at one.
    """

    id: UUID
    profile_id: UUID
    amount: Money
    occurred_at: datetime
    description: str
    merchant: str | None
    status: ExpenseStatus
    category_allocations: tuple[CategoryAllocation, ...]
    payment_allocations: tuple[PaymentAllocation, ...]
    source_reference: str
    idempotency_key: str
    original_expense_id: UUID | None = None
    receipt_id: UUID | None = None
    version: int = 1

    def __post_init__(self) -> None:
        """Validate aggregate-local fields and authoritative-state invariants."""

        if self.amount.minor_units == 0:
            raise DomainError("expense amount cannot be zero")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise DomainError("expense occurrence time must include a timezone")
        if not self.description.strip():
            raise DomainError("expense description cannot be empty")
        if not self.source_reference.strip():
            raise DomainError("expense requires a source reference")
        if not self.idempotency_key.strip():
            raise DomainError("expense requires an idempotency key")
        if self.version < 1:
            raise DomainError("expense version must be positive")
        if self.original_expense_id is not None and self.amount.minor_units > 0:
            raise DomainError("only refunds may reference an original expense")
        if self.original_expense_id == self.id:
            raise DomainError("refund cannot reference itself")
        self._validate_allocation_shape()
        if self.status in {ExpenseStatus.POSTED, ExpenseStatus.VOIDED}:
            self.require_reconciled()

    @property
    def is_refund(self) -> bool:
        """Return whether this aggregate reverses spending."""

        return self.amount.minor_units < 0

    def require_reconciled(self) -> None:
        """Require both independent allocation dimensions to equal the total.

        Raises:
            ReconciliationError: If either dimension is absent or mismatched.
        """

        if not self.category_allocations:
            raise ReconciliationError("category allocations are required")
        if not self.payment_allocations:
            raise ReconciliationError("payment allocations are required")
        category_total = sum_money(
            tuple(item.amount for item in self.category_allocations),
            currency=self.amount.currency,
        )
        if category_total != self.amount:
            raise ReconciliationError("category allocations do not equal expense total")
        payment_total = sum_money(
            tuple(item.amount for item in self.payment_allocations),
            currency=self.amount.currency,
        )
        if payment_total != self.amount:
            raise ReconciliationError("payment allocations do not equal expense total")

    def post(
        self,
        *,
        taxonomy: Taxonomy,
        funding_sources: tuple[FundingSource, ...],
        receipt_approved: bool = False,
    ) -> Expense:
        """Validate external references and transition a draft to posted.

        Args:
            taxonomy: Complete validated category view for the profile.
            funding_sources: Authorized funding sources for the profile.
            receipt_approved: Explicit owner approval for image-backed drafts.

        Returns:
            A new posted aggregate with its version incremented.

        Raises:
            InvalidTransitionError: If current state cannot be posted.
            DomainError: If a referenced category/source is unavailable or an
                image-backed draft lacks explicit approval.
            ReconciliationError: If either allocation dimension mismatches.
        """

        if self.status not in {ExpenseStatus.DRAFT, ExpenseStatus.NEEDS_REVIEW}:
            raise InvalidTransitionError("only draft or needs-review expense can post")
        self.require_reconciled()
        for category_allocation in self.category_allocations:
            category = taxonomy.category(category_allocation.category_id)
            if category.status is not CategoryStatus.ACTIVE:
                raise DomainError("posted allocation requires an active category")
        sources = {source.id: source for source in funding_sources}
        for payment_allocation in self.payment_allocations:
            source = sources.get(payment_allocation.funding_source_id)
            if source is None or source.status is not FundingSourceStatus.ACTIVE:
                raise DomainError("posted allocation requires an active funding source")
            if source.profile_id != self.profile_id:
                raise DomainError("funding source belongs to another profile")
        if self.receipt_id is not None and not receipt_approved:
            raise DomainError("receipt-backed expense requires owner approval")
        return replace(self, status=ExpenseStatus.POSTED, version=self.version + 1)

    def mark_needs_review(self) -> Expense:
        """Move an incomplete draft into visible owner review.

        Returns:
            A new needs-review aggregate.

        Raises:
            InvalidTransitionError: If the expense is no longer a draft.
        """

        if self.status is not ExpenseStatus.DRAFT:
            raise InvalidTransitionError("only a draft can enter needs review")
        return replace(
            self, status=ExpenseStatus.NEEDS_REVIEW, version=self.version + 1
        )

    def void(self) -> Expense:
        """Undo a posted aggregate without deleting its financial history.

        Returns:
            A new voided aggregate.

        Raises:
            InvalidTransitionError: If the expense is not posted.
        """

        if self.status is not ExpenseStatus.POSTED:
            raise InvalidTransitionError("only a posted expense can be voided")
        return replace(self, status=ExpenseStatus.VOIDED, version=self.version + 1)

    def discard(self) -> Expense:
        """Abandon a draft that will never be posted.

        Distinct from `void`, which reverses a movement that already counted
        and therefore stays reconciled. A discarded draft never counted and may
        have no purpose dimension at all, so it reaches its own terminal state
        rather than weakening what `voided` guarantees. History keeps the
        abandoned attempt visible instead of deleting it.

        Returns:
            A new discarded aggregate.

        Raises:
            InvalidTransitionError: If the expense is not a draft.
        """

        if self.status is not ExpenseStatus.DRAFT:
            raise InvalidTransitionError("only a draft expense can be discarded")
        return replace(self, status=ExpenseStatus.DISCARDED, version=self.version + 1)

    def corrected(
        self,
        *,
        amount: Money,
        occurred_at: datetime,
        description: str,
        merchant: str | None,
        category_allocations: tuple[CategoryAllocation, ...],
        payment_allocations: tuple[PaymentAllocation, ...],
    ) -> Expense:
        """Build a full validated replacement while preserving stable identity.

        Args:
            amount: Replacement total.
            occurred_at: Replacement occurrence time.
            description: Replacement description.
            merchant: Replacement merchant metadata.
            category_allocations: Complete replacement purpose dimension.
            payment_allocations: Complete replacement funding dimension.

        Returns:
            A new aggregate with incremented optimistic version.

        Raises:
            InvalidTransitionError: If a voided record is changed.
            ReconciliationError: If an authoritative replacement does not
                reconcile.
        """

        if self.status is ExpenseStatus.VOIDED:
            raise InvalidTransitionError("voided expense cannot be corrected")
        return replace(
            self,
            amount=amount,
            occurred_at=occurred_at,
            description=description,
            merchant=merchant,
            category_allocations=category_allocations,
            payment_allocations=payment_allocations,
            version=self.version + 1,
        )

    def _validate_allocation_shape(self) -> None:
        """Reject duplicate rows and allocation polarity inversions."""

        category_ids = tuple(item.category_id for item in self.category_allocations)
        if len(category_ids) != len(set(category_ids)):
            raise DomainError("category allocation identifiers must be unique")
        source_ids = tuple(item.funding_source_id for item in self.payment_allocations)
        if len(source_ids) != len(set(source_ids)):
            raise DomainError("payment allocation identifiers must be unique")
        expected_positive = self.amount.minor_units > 0
        amounts = tuple(item.amount for item in self.category_allocations) + tuple(
            item.amount for item in self.payment_allocations
        )
        if any((amount.minor_units > 0) != expected_positive for amount in amounts):
            raise DomainError("allocation polarity must match expense total")


@dataclass(frozen=True, slots=True)
class FundingClassSplit:
    """Exact posted totals by economic source class.

    Attributes:
        personal_funds: Card, cash, and other owner-funded payments.
        benefit: Meal-voucher, welfare, and other benefit payments.
        other: Payments using another explicitly classified resource.
    """

    personal_funds: Money
    benefit: Money
    other: Money


def posted_funding_class_split(
    expenses: tuple[Expense, ...],
    *,
    funding_sources: tuple[FundingSource, ...],
) -> FundingClassSplit:
    """Sum posted payment allocations by funding class without double-counting.

    Args:
        expenses: Candidate expense and refund aggregates.
        funding_sources: Sources needed to resolve every posted allocation.

    Returns:
        Exact totals for personal funds, benefits, and other resources.

    Raises:
        DomainError: If a posted allocation references an unknown source.
    """

    sources = {source.id: source for source in funding_sources}
    amounts: dict[FundingClass, list[Money]] = {
        FundingClass.PERSONAL_FUNDS: [],
        FundingClass.BENEFIT: [],
        FundingClass.OTHER: [],
    }
    for expense in expenses:
        if expense.status is not ExpenseStatus.POSTED:
            continue
        for allocation in expense.payment_allocations:
            source = sources.get(allocation.funding_source_id)
            if source is None:
                raise DomainError(
                    "payment allocation references unknown funding source"
                )
            amounts[source.funding_class].append(allocation.amount)
    return FundingClassSplit(
        personal_funds=sum_money(tuple(amounts[FundingClass.PERSONAL_FUNDS])),
        benefit=sum_money(tuple(amounts[FundingClass.BENEFIT])),
        other=sum_money(tuple(amounts[FundingClass.OTHER])),
    )


def posted_funding_source_spend(
    expenses: tuple[Expense, ...], *, funding_source_id: UUID
) -> Money:
    """Sum one source's posted payment portions, including signed refunds.

    Args:
        expenses: Candidate expenses already scoped to a profile and period.
        funding_source_id: Stable source whose payment portions are counted.

    Returns:
        Net consumption paid by this source, never its wallet balance.
    """

    return sum_money(
        tuple(
            allocation.amount
            for expense in expenses
            if expense.status is ExpenseStatus.POSTED
            for allocation in expense.payment_allocations
            if allocation.funding_source_id == funding_source_id
        )
    )


def posted_category_spend(
    expenses: tuple[Expense, ...],
    *,
    category_id: UUID,
    taxonomy: Taxonomy,
) -> Money:
    """Compute direct and descendant posted spend without double-counting.

    Args:
        expenses: Candidate expense aggregates.
        category_id: Category whose roll-up is requested.
        taxonomy: Category relationships for ancestor resolution.

    Returns:
        Sum of matching category allocations from posted records only.
    """

    taxonomy.category(category_id)
    amounts: list[Money] = []
    for expense in expenses:
        if expense.status is not ExpenseStatus.POSTED:
            continue
        for allocation in expense.category_allocations:
            if category_id in taxonomy.ancestor_ids(allocation.category_id):
                amounts.append(allocation.amount)
    return sum_money(tuple(amounts))
