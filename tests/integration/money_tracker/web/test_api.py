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
from money_tracker.web.mutations import ExpenseMutationAdapter
from money_tracker.workflows import posting_request_hash

ROME = ZoneInfo("Europe/Rome")


def _store_posted_expense(
    repository: PostgresFinanceRepository,
    *,
    profile: Profile,
    source: FundingSource,
) -> None:
    """Create and post one expense through the real application service."""

    category = repository.get_taxonomy(profile.id).resolve_active("Spesa alimentare")
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
    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(
                repository,
                clock=SystemClock(),
                ids=ids,
            ),
            ids=ids,
        ),
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
    assert response.json()["funding_split"]["personal_funds"]["minor_units"] == 4250
    assert response.json()["unbudgeted_categories"] == ["Spesa alimentare"]


@pytest.mark.postgres
def test_manual_expense_workflow_is_replay_safe_and_visible(
    postgres_url: str,
) -> None:
    """Create, correct, post, replay, and void through HTTP and real PostgreSQL."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    category = taxonomy.resolve_active("Spesa alimentare")
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta workflow web",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    csrf = client.get(
        "/api/v1/csrf",
        headers={"origin": "http://localhost:8000"},
    ).json()["token"]

    def mutation_headers(command_key: str) -> dict[str, str]:
        """Return the exact browser mutation boundary headers."""

        return {
            "origin": "http://localhost:8000",
            "x-csrf-token": csrf,
            "Idempotency-Key": command_key,
        }

    create_payload = {
        "amount": {"minor_units": 1800, "currency": "EUR"},
        "occurred_at": "2026-09-12T10:30:00+02:00",
        "description": "Pranzo manuale",
        "merchant": "Forno",
        "category_allocations": [
            {
                "category_id": str(category.id),
                "amount": {"minor_units": 1800, "currency": "EUR"},
                "provenance": "owner:web:v1",
                "owner_override": True,
            }
        ],
        "payment_allocations": [
            {
                "funding_source_id": str(card.id),
                "amount": {"minor_units": 1800, "currency": "EUR"},
            }
        ],
    }
    created = client.post(
        "/api/v1/expenses",
        json=create_payload,
        headers=mutation_headers("integration-web-create"),
    )
    replayed_create = client.post(
        "/api/v1/expenses",
        json=create_payload,
        headers=mutation_headers("integration-web-create"),
    )

    assert created.status_code == 201
    assert replayed_create.json() == created.json()
    expense_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["version"] == 1

    register = client.get(
        "/api/v1/transactions?period=2026-09",
        headers={"origin": "http://localhost:8000"},
    )
    assert register.status_code == 200
    assert register.json()["transactions"][0]["id"] == expense_id
    assert (
        register.json()["transactions"][0]["category_allocations"][0]["category_name"]
        == "Spesa alimentare"
    )
    assert "profile_id" not in register.text
    assert "source_reference" not in register.text

    replacement = {
        **create_payload,
        "amount": {"minor_units": 2100, "currency": "EUR"},
        "description": "Pranzo corretto",
        "expected_version": 1,
    }
    replacement["category_allocations"][0]["amount"]["minor_units"] = 2100
    replacement["payment_allocations"][0]["amount"]["minor_units"] = 2100
    corrected = client.put(
        f"/api/v1/expenses/{expense_id}",
        json=replacement,
        headers=mutation_headers("integration-web-correct"),
    )
    assert corrected.status_code == 200
    assert corrected.json()["version"] == 2

    posted = client.post(
        f"/api/v1/expenses/{expense_id}/post",
        json={"expected_version": 2, "receipt_approved": False},
        headers=mutation_headers("integration-web-post"),
    )
    replayed_post = client.post(
        f"/api/v1/expenses/{expense_id}/post",
        json={"expected_version": 2, "receipt_approved": False},
        headers=mutation_headers("integration-web-post"),
    )
    assert posted.status_code == 200
    assert posted.json()["status"] == "posted"
    assert replayed_post.json() == posted.json()

    overview = client.get("/api/v1/overview/monthly?period=2026-09")
    assert overview.json()["posted_total"]["minor_units"] == 2100

    cancelled = client.post(
        f"/api/v1/expenses/{expense_id}/cancel",
        json={"expected_version": 3},
        headers=mutation_headers("integration-web-cancel"),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "voided"
    assert (
        client.get("/api/v1/overview/monthly?period=2026-09").json()["posted_total"][
            "minor_units"
        ]
        == 0
    )
