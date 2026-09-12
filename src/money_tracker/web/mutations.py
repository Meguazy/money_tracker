"""Translate strict browser commands into existing finance use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from money_tracker.api.contracts import ExpenseCreateRequest
from money_tracker.application import IdGenerator
from money_tracker.domain import (
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    Money,
    PaymentAllocation,
)
from money_tracker.web.contracts import (
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


__all__ = ["ExpenseMutationAdapter", "ExpenseWriter", "WEB_ACTOR"]
