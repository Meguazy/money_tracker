"""Real PostgreSQL integration tests for the local dashboard API."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from money_tracker.application import FinanceService, ReportingService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.web import WebSettings, create_app
from money_tracker.workflows import posting_request_hash

ROME = ZoneInfo("Europe/Rome")


def _store_posted_expense(
    repository: PostgresFinanceRepository,
    *,
    profile: Profile,
    source: FundingSource,
) -> None:
    """Create and post one expense through the real application service."""

    category = repository.get_taxonomy(profile.id).resolve_active(
        "Spesa alimentare"
    )
    amount = Money(4250)
    reference = f"integration:web:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=amount,
        occurred_at=datetime(2026, 9, 11, 12, tzinfo=ROME),
        description="spesa dashboard",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id,
                amount=amount,
                provenance="test",
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=source.id, amount=amount),
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    created = finance.create_expense(
        expense,
        request_hash=sha256(reference.encode()).hexdigest(),
        actor="test",
        reason="web integration fixture",
    )
    finance.post_expense(
        profile_id=profile.id,
        expense_id=created.id,
        expected_version=created.version,
        idempotency_key=f"posting:{created.id}",
        request_hash=posting_request_hash(
            profile_id=profile.id,
            expense_id=created.id,
        ),
        actor="test",
        reason="web integration fixture",
    )


@pytest.mark.postgres
def test_monthly_endpoint_reads_real_postgresql_state(postgres_url: str) -> None:
    """Serve exact posted facts through HTTP and the production read adapter."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=now,
    )
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta test web",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    _store_posted_expense(repository, profile=profile, source=card)
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )

    response = TestClient(app, base_url="http://localhost:8000").get(
        "/api/v1/overview/monthly?period=2026-09",
        headers={"origin": "http://localhost:8000"},
    )

    assert response.status_code == 200
    assert response.json()["posted_total"] == {
        "minor_units": 4250,
        "currency": "EUR",
    }
    assert response.json()["funding_split"]["personal_funds"][
        "minor_units"
    ] == 4250
    assert response.json()["unbudgeted_categories"] == ["Spesa alimentare"]
