"""Versioned transport contracts for future local API adapters."""

from money_tracker.api.contracts import (
    API_V1_PREFIX,
    PROBLEM_MEDIA_TYPE,
    CategoryAllocationContract,
    ExpenseCreateRequest,
    ExpenseResponse,
    MoneyContract,
    MutationHeaders,
    PaymentAllocationContract,
    ProblemDetails,
)

__all__ = [
    "API_V1_PREFIX",
    "PROBLEM_MEDIA_TYPE",
    "CategoryAllocationContract",
    "ExpenseCreateRequest",
    "ExpenseResponse",
    "MoneyContract",
    "MutationHeaders",
    "PaymentAllocationContract",
    "ProblemDetails",
]
