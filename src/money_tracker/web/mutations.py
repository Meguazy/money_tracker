"""Translate strict browser commands into existing finance use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from money_tracker.api.contracts import ExpenseCreateRequest
from money_tracker.application import (
    CategoryReviewQueue,
    IdempotencyConflictError,
    IdGenerator,
)
from money_tracker.domain import (
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    YearMonth,
)
from money_tracker.web.contracts import (
    BudgetCreateRequest,
    BudgetReplaceRequest,
    BudgetWriteRequest,
    CategoryReviewReuseRequest,
    ExpenseCancelRequest,
    ExpensePostRequest,
    ExpenseReplaceRequest,
)
from money_tracker.workflows import (
    canonical_request_hash,
    posting_request_hash,
    undo_request_hash,
)

WEB_ACTOR = "owner:web"


class ExpenseWriter(Protocol):
    """Expose only finance mutation operations required by the browser."""

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Create or replay one draft."""

    def post_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
        receipt_approved: bool = False,
    ) -> Expense:
        """Post or replay one reconciled draft."""

    def correct_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        amount: Money,
        occurred_at: datetime,
        description: str,
        merchant: str | None,
        category_allocations: tuple[CategoryAllocation, ...],
        payment_allocations: tuple[PaymentAllocation, ...],
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Replace or replay every editable field."""

    def cancel_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Discard or void one movement."""


class ExpenseMutationAdapter:
    """Own HTTP-to-domain construction without owning business state."""

    def __init__(self, writer: ExpenseWriter, *, ids: IdGenerator) -> None:
        """Bind browser command translation to replaceable application ports.

        Args:
            writer: Existing audited, idempotent finance use cases.
            ids: Identifier source for newly created movement aggregates.
        """

        self._writer = writer
        self._ids = ids

    def create(
        self,
        *,
        profile_id: UUID,
        command_key: str,
        request: ExpenseCreateRequest,
    ) -> Expense:
        """Create a draft from a strict browser request.

        Args:
            profile_id: Trusted owner scope from process composition.
            command_key: Transport idempotency key.
            request: Validated complete movement fields.

        Returns:
            Newly created draft or the prior identical result.
        """

        expense = _expense_from_request(
            request,
            expense_id=self._ids.new(),
            profile_id=profile_id,
            command_key=command_key,
        )
        return self._writer.create_expense(
            expense,
            request_hash=canonical_request_hash(expense),
            actor=WEB_ACTOR,
            reason="manual browser creation",
        )

    def replace(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        command_key: str,
        request: ExpenseReplaceRequest,
    ) -> Expense:
        """Correct every editable field under optimistic concurrency."""

        candidate = _expense_from_request(
            request,
            expense_id=expense_id,
            profile_id=profile_id,
            command_key=command_key,
        )
        return self._writer.correct_expense(
            profile_id=profile_id,
            expense_id=expense_id,
            expected_version=request.expected_version,
            amount=candidate.amount,
            occurred_at=candidate.occurred_at,
            description=candidate.description,
            merchant=candidate.merchant,
            category_allocations=candidate.category_allocations,
            payment_allocations=candidate.payment_allocations,
            idempotency_key=command_key,
            request_hash=canonical_request_hash(candidate),
            actor=WEB_ACTOR,
            reason="manual browser correction",
        )

    def post(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        command_key: str,
        request: ExpensePostRequest,
    ) -> Expense:
        """Register a reconciled draft under its observed version."""

        return self._writer.post_expense(
            profile_id=profile_id,
            expense_id=expense_id,
            expected_version=request.expected_version,
            idempotency_key=command_key,
            request_hash=posting_request_hash(
                profile_id=profile_id,
                expense_id=expense_id,
            ),
            actor=WEB_ACTOR,
            reason="manual browser registration",
            receipt_approved=request.receipt_approved,
        )

    def cancel(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        command_key: str,
        request: ExpenseCancelRequest,
    ) -> Expense:
        """Discard a draft or void a posted movement under one owner intent."""

        return self._writer.cancel_expense(
            profile_id=profile_id,
            expense_id=expense_id,
            expected_version=request.expected_version,
            idempotency_key=command_key,
            request_hash=undo_request_hash(
                profile_id=profile_id,
                expense_id=expense_id,
            ),
            actor=WEB_ACTOR,
            reason="manual browser cancellation",
        )


class BudgetWriter(Protocol):
    """Expose only the budget operations the browser is allowed to reach."""

    def create_budget(
        self,
        budget: MonthlyBudget,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Create or replay one category budget."""

    def replace_budget(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        recurring_target: Money,
        overrides: tuple[tuple[YearMonth, Money], ...],
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Replace or replay one complete budget plan."""


class BudgetMutationAdapter:
    """Translate strict browser budget commands into existing use cases.

    Wallet entries stay out of this adapter deliberately. They are a separate
    authority with their own balance consequences, and Step 5 exposes no
    browser route for them.
    """

    def __init__(self, writer: BudgetWriter, *, ids: IdGenerator) -> None:
        """Bind budget translation to replaceable application ports.

        Args:
            writer: Existing audited, replay-safe budget use cases.
            ids: Identifier source for newly created budget aggregates.
        """

        self._writer = writer
        self._ids = ids

    def create(
        self,
        *,
        profile_id: UUID,
        command_key: str,
        request: BudgetCreateRequest,
    ) -> MonthlyBudget:
        """Attach a first budget to one active category.

        Args:
            profile_id: Trusted owner scope from process composition.
            command_key: Transport idempotency key.
            request: Validated complete target and override plan.

        Returns:
            Newly created budget or the prior identical result.
        """

        return self._writer.create_budget(
            MonthlyBudget(
                id=self._ids.new(),
                profile_id=profile_id,
                category_id=request.category_id,
                recurring_target=Money(request.recurring_target.minor_units),
                overrides=_overrides_from_request(request),
            ),
            idempotency_key=command_key,
            actor=WEB_ACTOR,
            reason="manual browser budget creation",
        )

    def replace(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        command_key: str,
        request: BudgetReplaceRequest,
    ) -> MonthlyBudget:
        """Replace the complete plan under optimistic concurrency."""

        return self._writer.replace_budget(
            profile_id=profile_id,
            category_id=category_id,
            expected_version=request.expected_version,
            recurring_target=Money(request.recurring_target.minor_units),
            overrides=_overrides_from_request(request),
            idempotency_key=command_key,
            actor=WEB_ACTOR,
            reason="manual browser budget correction",
        )


class CategoryReviewWriter(Protocol):
    """Expose only owner category-review operations required by the browser."""

    def pending_reviews(self, *, profile_id: UUID) -> CategoryReviewQueue:
        """Return open proposals paired to their trusted movements."""

    def review_expense(self, *, profile_id: UUID, proposal_id: UUID) -> Expense:
        """Resolve one proposal's movement from persistent workflow identity."""

    def approve(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Approve a candidate and post its movement."""

    def use_existing(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        category_name: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Reuse an active category and post the movement."""

    def cancel(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Cancel the proposal and discard its movement."""


class CategoryReviewMutationAdapter:
    """Bind strict browser decisions to the existing review service.

    The proposal identifier is enough: the correlated movement is resolved by
    the service from persistent workflow identity and never accepted from the
    browser. The required HTTP idempotency key is the proposal-wide key already
    claimed atomically by every category-review branch.
    """

    def __init__(self, writer: CategoryReviewWriter) -> None:
        """Bind the browser boundary to replay-safe owner decisions.

        Args:
            writer: Existing category-review use cases and trusted reads.
        """

        self._writer = writer

    def pending(self, *, profile_id: UUID) -> CategoryReviewQueue:
        """Return the pending owner review queue."""

        return self._writer.pending_reviews(profile_id=profile_id)

    def approve(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        command_key: str,
    ) -> Expense:
        """Approve one candidate using its proposal-wide command key."""

        self._require_command_key(proposal_id, command_key)
        expense = self._writer.review_expense(
            profile_id=profile_id, proposal_id=proposal_id
        )
        return self._writer.approve(
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense.id,
            actor=WEB_ACTOR,
            reason="owner approved category in browser",
        )

    def use_existing(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        command_key: str,
        request: CategoryReviewReuseRequest,
    ) -> Expense:
        """Resolve one review with an active category name."""

        self._require_command_key(proposal_id, command_key)
        expense = self._writer.review_expense(
            profile_id=profile_id, proposal_id=proposal_id
        )
        return self._writer.use_existing(
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense.id,
            category_name=request.category_name,
            actor=WEB_ACTOR,
            reason="owner reused category in browser",
        )

    def cancel(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        command_key: str,
    ) -> Expense:
        """Cancel one review and discard its correlated movement."""

        self._require_command_key(proposal_id, command_key)
        expense = self._writer.review_expense(
            profile_id=profile_id, proposal_id=proposal_id
        )
        return self._writer.cancel(
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense.id,
            actor=WEB_ACTOR,
            reason="owner cancelled category review in browser",
        )

    @staticmethod
    def _require_command_key(proposal_id: UUID, command_key: str) -> None:
        """Require the exact key the application transaction claims."""

        if command_key != f"category-review:{proposal_id}":
            raise IdempotencyConflictError(
                "category review requires its proposal-wide command key"
            )


def _overrides_from_request(
    request: BudgetWriteRequest,
) -> tuple[tuple[YearMonth, Money], ...]:
    """Build validated domain override pairs from one strict browser plan."""

    return tuple(
        (
            YearMonth(item.period.year, item.period.month),
            Money(item.target.minor_units),
        )
        for item in request.overrides
    )


def _expense_from_request(
    request: ExpenseCreateRequest,
    *,
    expense_id: UUID,
    profile_id: UUID,
    command_key: str,
) -> Expense:
    """Build the real aggregate while injecting all trusted fields."""

    return Expense(
        id=expense_id,
        profile_id=profile_id,
        amount=Money(request.amount.minor_units),
        occurred_at=request.occurred_at,
        description=request.description,
        merchant=request.merchant,
        status=ExpenseStatus.DRAFT,
        category_allocations=tuple(
            CategoryAllocation(
                category_id=item.category_id,
                amount=Money(item.amount.minor_units),
                provenance=item.provenance,
                evidence=item.evidence,
                owner_override=item.owner_override,
                receipt_line_id=item.receipt_line_id,
            )
            for item in request.category_allocations
        ),
        payment_allocations=tuple(
            PaymentAllocation(
                funding_source_id=item.funding_source_id,
                amount=Money(item.amount.minor_units),
            )
            for item in request.payment_allocations
        ),
        source_reference=f"web:{command_key}",
        idempotency_key=command_key,
        original_expense_id=request.original_expense_id,
        receipt_id=request.receipt_id,
    )


__all__ = [
    "WEB_ACTOR",
    "BudgetMutationAdapter",
    "BudgetWriter",
    "CategoryReviewMutationAdapter",
    "CategoryReviewWriter",
    "ExpenseMutationAdapter",
    "ExpenseWriter",
]
