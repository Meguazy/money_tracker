"""Behavioral tests for stable version-one finance wire contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from money_tracker.api import (
    API_V1_PREFIX,
    PROBLEM_MEDIA_TYPE,
    ExpenseCreateRequest,
    ExpenseResponse,
    MutationHeaders,
    ProblemDetails,
)
from money_tracker.domain import (
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    Money,
    PaymentAllocation,
)


def _expense() -> Expense:
    """Build one real reconciled domain aggregate for response conversion."""

    category_id = UUID("00000000-0000-0000-0000-000000000101")
    funding_source_id = UUID("00000000-0000-0000-0000-000000000201")
    return Expense(
        id=UUID("00000000-0000-0000-0000-000000000301"),
        profile_id=UUID("00000000-0000-0000-0000-000000000401"),
        amount=Money(599),
        occurred_at=datetime(2026, 9, 2, 10, 30, tzinfo=UTC),
        description="Abbonamento",
        merchant="Netflix",
        status=ExpenseStatus.POSTED,
        category_allocations=(
            CategoryAllocation(
                category_id=category_id,
                amount=Money(599),
                provenance="owner:v1",
            ),
        ),
        payment_allocations=(
            PaymentAllocation(
                funding_source_id=funding_source_id,
                amount=Money(599),
            ),
        ),
        source_reference="api:test:expense",
        idempotency_key="command-123",
        version=2,
    )


def _request_payload() -> dict[str, object]:
    """Return a JSON-compatible valid expense request."""

    return {
        "amount": {"minor_units": 599, "currency": "EUR"},
        "occurred_at": "2026-09-02T10:30:00+02:00",
        "description": "Abbonamento",
        "merchant": "Netflix",
        "category_allocations": [
            {
                "category_id": str(uuid4()),
                "amount": {"minor_units": 599, "currency": "EUR"},
                "provenance": "owner:v1",
            }
        ],
        "payment_allocations": [
            {
                "funding_source_id": str(uuid4()),
                "amount": {"minor_units": 599, "currency": "EUR"},
            }
        ],
    }


def test_domain_expense_serializes_stable_ids_time_money_and_version() -> None:
    """Map real production domain state to the promised JSON representation."""

    payload = ExpenseResponse.from_domain(_expense()).model_dump(mode="json")

    assert API_V1_PREFIX == "/api/v1"
    assert payload["id"] == "00000000-0000-0000-0000-000000000301"
    assert payload["profile_id"] == "00000000-0000-0000-0000-000000000401"
    assert payload["occurred_at"] == "2026-09-02T10:30:00Z"
    assert payload["amount"] == {"minor_units": 599, "currency": "EUR"}
    assert payload["category_allocations"][0]["amount"]["minor_units"] == 599
    assert payload["payment_allocations"][0]["amount"]["currency"] == "EUR"
    assert payload["status"] == "posted"
    assert payload["version"] == 2
    assert "idempotency_key" not in payload
    assert "path" not in payload


def test_json_request_accepts_uuid_and_aware_iso_timestamp() -> None:
    """Parse standard JSON strings while retaining strict semantic types."""

    request = ExpenseCreateRequest.model_validate_json(json.dumps(_request_payload()))

    assert request.amount.minor_units == 599
    assert request.occurred_at.utcoffset() is not None
    assert isinstance(request.category_allocations[0].category_id, UUID)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("amount", "minor_units"), 599.0),
        (("amount", "currency"), "USD"),
        (("occurred_at",), "2026-09-02T10:30:00"),
        (("amount", "minor_units"), 0),
    ],
)
def test_request_rejects_float_currency_naive_time_and_zero(
    path: tuple[str, ...],
    value: object,
) -> None:
    """Fail before domain dispatch when wire-level finance semantics are unsafe."""

    payload = _request_payload()
    target: dict[str, object] = payload
    for part in path[:-1]:
        nested = target[part]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        ExpenseCreateRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field",
    ["archive_path", "profile_id", "source_reference", "idempotency_key"],
)
def test_request_rejects_unknown_or_trusted_context_fields(field: str) -> None:
    """Prevent silent contract drift at the public boundary."""

    payload = _request_payload()
    payload[field] = "caller-controlled"

    with pytest.raises(ValidationError, match="Extra inputs"):
        ExpenseCreateRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Idempotency-Key": ""},
        {"Idempotency-Key": "   "},
        {"idempotency_key": "command-123"},
    ],
)
def test_mutation_contract_requires_canonical_nonempty_idempotency_header(
    headers: dict[str, str],
) -> None:
    """Reject missing, blank, or body-style idempotency identifiers."""

    with pytest.raises(ValidationError):
        MutationHeaders.model_validate(headers)


def test_mutation_header_preserves_stable_retry_key() -> None:
    """Accept the canonical external header without coercion."""

    headers = MutationHeaders.model_validate({"Idempotency-Key": "expense-command-123"})

    assert headers.idempotency_key == "expense-command-123"
    assert headers.model_dump(by_alias=True) == {
        "Idempotency-Key": "expense-command-123"
    }


def test_problem_details_have_stable_json_shape_and_status_bounds() -> None:
    """Expose a safe machine-readable error contract for future adapters."""

    problem = ProblemDetails(
        type="https://money-tracker.local/problems/version-conflict",
        title="Version conflict",
        status=409,
        detail="The expense changed after it was loaded.",
        instance="/api/v1/expenses/00000000-0000-0000-0000-000000000301",
        code="optimistic_concurrency_conflict",
    )

    assert PROBLEM_MEDIA_TYPE == "application/problem+json"
    assert problem.model_dump(mode="json") == {
        "type": "https://money-tracker.local/problems/version-conflict",
        "title": "Version conflict",
        "status": 409,
        "detail": "The expense changed after it was loaded.",
        "instance": "/api/v1/expenses/00000000-0000-0000-0000-000000000301",
        "code": "optimistic_concurrency_conflict",
    }
    with pytest.raises(ValidationError):
        ProblemDetails(
            title="Not an error",
            status=200,
            detail="Invalid status",
            code="invalid_status",
        )
