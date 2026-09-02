"""Stable version-one finance wire contracts independent of FastAPI."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from money_tracker.domain import Expense, ExpenseStatus

API_V1_PREFIX = "/api/v1"
PROBLEM_MEDIA_TYPE = "application/problem+json"

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class _ApiContract(BaseModel):
    """Reject unknown or coercion-dependent fields at the public boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class MoneyContract(_ApiContract):
    """Represent exact V1 money without binary floating point.

    Attributes:
        minor_units: Signed integer cents.
        currency: Operational V1 ISO currency.
    """

    minor_units: int
    currency: Literal["EUR"] = "EUR"


class CategoryAllocationContract(_ApiContract):
    """Expose one purpose allocation using stable identifiers.

    Attributes:
        category_id: Stable category identifier.
        amount: Signed exact share of the parent movement.
        provenance: Versioned classification origin.
        evidence: Optional concise review evidence.
        owner_override: Whether the owner replaced an earlier classification.
        receipt_line_id: Optional stable receipt-line reference.
    """

    category_id: UUID
    amount: MoneyContract
    provenance: NonEmptyText
    evidence: str | None = None
    owner_override: bool = False
    receipt_line_id: UUID | None = None


class PaymentAllocationContract(_ApiContract):
    """Expose one funding allocation using a stable source identifier.

    Attributes:
        funding_source_id: Stable funding-source identifier.
        amount: Signed exact share of the parent movement.
    """

    funding_source_id: UUID
    amount: MoneyContract


class ExpenseCreateRequest(_ApiContract):
    """Validate the stable body of a version-one expense mutation.

    Idempotency is carried by the separate MutationHeaders contract so every
    mutating resource uses one transport convention.

    Attributes:
        amount: Non-zero exact expense or refund total.
        occurred_at: Timezone-aware economic occurrence time.
        description: Non-empty owner-facing description.
        merchant: Optional merchant metadata.
        category_allocations: Complete purpose dimension.
        payment_allocations: Complete funding dimension.
        original_expense_id: Optional original movement for a refund.
        receipt_id: Optional supporting receipt metadata identifier.
    """

    amount: MoneyContract
    occurred_at: AwareDatetime
    description: NonEmptyText
    merchant: str | None = None
    category_allocations: tuple[CategoryAllocationContract, ...]
    payment_allocations: tuple[PaymentAllocationContract, ...]
    original_expense_id: UUID | None = None
    receipt_id: UUID | None = None

    @field_validator("amount")
    @classmethod
    def _require_nonzero_amount(cls, amount: MoneyContract) -> MoneyContract:
        """Reject a zero movement before it reaches the domain service."""

        if amount.minor_units == 0:
            raise ValueError("expense amount cannot be zero")
        return amount


class MutationHeaders(_ApiContract):
    """Require one transport-level idempotency key for every mutation.

    Attributes:
        idempotency_key: Stable retry key supplied as the Idempotency-Key header.
    """

    idempotency_key: NonEmptyText = Field(alias="Idempotency-Key")


class ExpenseResponse(_ApiContract):
    """Serialize one authoritative expense through stable V1 wire fields.

    Attributes:
        id: Stable expense identifier.
        profile_id: Stable owner scope.
        amount: Exact signed total.
        occurred_at: Timezone-aware economic occurrence time.
        description: Owner-facing description.
        merchant: Optional merchant metadata.
        status: Lifecycle state.
        category_allocations: Complete purpose dimension.
        payment_allocations: Complete funding dimension.
        source_reference: Stable source reference.
        original_expense_id: Optional original movement for a refund.
        receipt_id: Optional supporting receipt.
        version: Positive optimistic concurrency version.
    """

    id: UUID
    profile_id: UUID
    amount: MoneyContract
    occurred_at: AwareDatetime
    description: NonEmptyText
    merchant: str | None
    status: ExpenseStatus
    category_allocations: tuple[CategoryAllocationContract, ...]
    payment_allocations: tuple[PaymentAllocationContract, ...]
    source_reference: NonEmptyText
    original_expense_id: UUID | None
    receipt_id: UUID | None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, expense: Expense) -> Self:
        """Build a public response from the real domain aggregate.

        Args:
            expense: Validated authoritative aggregate.

        Returns:
            A response containing no repository or private-media details.
        """

        return cls(
            id=expense.id,
            profile_id=expense.profile_id,
            amount=MoneyContract(
                minor_units=expense.amount.minor_units,
            ),
            occurred_at=expense.occurred_at,
            description=expense.description,
            merchant=expense.merchant,
            status=expense.status,
            category_allocations=tuple(
                CategoryAllocationContract(
                    category_id=item.category_id,
                    amount=MoneyContract(
                        minor_units=item.amount.minor_units,
                    ),
                    provenance=item.provenance,
                    evidence=item.evidence,
                    owner_override=item.owner_override,
                    receipt_line_id=item.receipt_line_id,
                )
                for item in expense.category_allocations
            ),
            payment_allocations=tuple(
                PaymentAllocationContract(
                    funding_source_id=item.funding_source_id,
                    amount=MoneyContract(
                        minor_units=item.amount.minor_units,
                    ),
                )
                for item in expense.payment_allocations
            ),
            source_reference=expense.source_reference,
            original_expense_id=expense.original_expense_id,
            receipt_id=expense.receipt_id,
            version=expense.version,
        )


class ProblemDetails(_ApiContract):
    """Represent a stable machine-readable API failure without private data.

    Attributes:
        type: Stable problem category URI or about:blank.
        title: Short human-readable summary.
        status: HTTP error status.
        detail: Safe occurrence-specific explanation.
        instance: Optional request-path reference without query secrets.
        code: Stable application error code.
    """

    type: str = "about:blank"
    title: NonEmptyText
    status: int = Field(ge=400, le=599)
    detail: NonEmptyText
    instance: str | None = None
    code: NonEmptyText
