"""Real PostgreSQL integration tests for the local dashboard API."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
    FinanceAdministrationService,
    FinanceService,
    ReceiptMetadataService,
    ReportingService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    CategoryProposal,
    CategoryProposalStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    ReceiptMetadata,
    ReceiptStatus,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.web import WebSettings, create_app
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    CategoryReviewMutationAdapter,
    ExpenseMutationAdapter,
    ReceiptReviewMutationAdapter,
)
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
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
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
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
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


@pytest.mark.postgres
def test_multi_allocation_movement_survives_correction_in_real_postgresql(
    postgres_url: str,
) -> None:
    """Split both dimensions, refuse unreconciled posting, and persist a rewrite."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    food = taxonomy.resolve_active("Spesa alimentare")
    home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")
    dining = taxonomy.resolve_active("Bar, ristoranti e asporto")
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta split web",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    vouchers = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Buoni pasto split web",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.UNTRACKED,
    )
    repository.create_funding_source(card, occurred_at=now)
    repository.create_funding_source(vouchers, occurred_at=now)
    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
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

    created = client.post(
        "/api/v1/expenses",
        json={
            "amount": {"minor_units": 5000, "currency": "EUR"},
            "occurred_at": "2026-09-12T13:00:00+02:00",
            "description": "Spesa mista",
            "merchant": "Mercato",
            "category_allocations": [
                {
                    "category_id": str(food.id),
                    "amount": {"minor_units": 3000, "currency": "EUR"},
                    "provenance": "owner:web:v1",
                    "owner_override": True,
                },
                {
                    "category_id": str(home.id),
                    "amount": {"minor_units": 2000, "currency": "EUR"},
                    "provenance": "owner:web:v1",
                    "owner_override": True,
                },
            ],
            "payment_allocations": [
                {
                    "funding_source_id": str(vouchers.id),
                    "amount": {"minor_units": 3000, "currency": "EUR"},
                },
                {
                    "funding_source_id": str(card.id),
                    "amount": {"minor_units": 2000, "currency": "EUR"},
                },
            ],
        },
        headers=mutation_headers("integration-web-split-create"),
    )

    assert created.status_code == 201
    expense_id = created.json()["id"]

    register = client.get(
        "/api/v1/transactions?period=2026-09",
        headers={"origin": "http://localhost:8000"},
    )
    transaction = register.json()["transactions"][0]
    assert len(transaction["category_allocations"]) == 2
    assert {
        item["category_name"]: item["amount"]["minor_units"]
        for item in transaction["category_allocations"]
    } == {"Spesa alimentare": 3000, "Prodotti casa, arredo e manutenzione": 2000}
    assert len(transaction["payment_allocations"]) == 2
    assert {
        item["funding_source_name"]: item["amount"]["minor_units"]
        for item in transaction["payment_allocations"]
    } == {"Buoni pasto split web": 3000, "Carta split web": 2000}
    assert all(
        item["receipt_line_id"] is None for item in transaction["category_allocations"]
    )

    unreconciled = {
        "amount": {"minor_units": 6000, "currency": "EUR"},
        "occurred_at": "2026-09-12T13:00:00+02:00",
        "description": "Ripartizione non riconciliata",
        "merchant": "Mercato",
        "category_allocations": [
            {
                "category_id": str(food.id),
                "amount": {"minor_units": 2500, "currency": "EUR"},
                "provenance": "owner:web:v1",
                "owner_override": True,
            }
        ],
        "payment_allocations": [
            {
                "funding_source_id": str(card.id),
                "amount": {"minor_units": 6000, "currency": "EUR"},
            }
        ],
        "expected_version": 1,
    }
    drafted = client.put(
        f"/api/v1/expenses/{expense_id}",
        json=unreconciled,
        headers=mutation_headers("integration-web-split-draft-mismatch"),
    )
    refused_posting = client.post(
        f"/api/v1/expenses/{expense_id}/post",
        json={"expected_version": 2, "receipt_approved": False},
        headers=mutation_headers("integration-web-split-refused-post"),
    )

    assert drafted.status_code == 200
    assert drafted.json()["version"] == 2
    assert refused_posting.status_code == 422
    assert refused_posting.json()["code"] == "expense_invalid"
    assert repository.get_expense(profile.id, UUID(expense_id)).status.value == "draft"

    corrected = client.put(
        f"/api/v1/expenses/{expense_id}",
        json={
            "amount": {"minor_units": 6000, "currency": "EUR"},
            "occurred_at": "2026-09-12T13:00:00+02:00",
            "description": "Spesa mista corretta",
            "merchant": "Mercato",
            "category_allocations": [
                {
                    "category_id": str(food.id),
                    "amount": {"minor_units": 2500, "currency": "EUR"},
                    "provenance": "owner:web:v1",
                    "owner_override": True,
                },
                {
                    "category_id": str(home.id),
                    "amount": {"minor_units": 2500, "currency": "EUR"},
                    "provenance": "owner:web:v1",
                    "owner_override": True,
                },
                {
                    "category_id": str(dining.id),
                    "amount": {"minor_units": 1000, "currency": "EUR"},
                    "provenance": "owner:web:v1",
                    "owner_override": True,
                },
            ],
            "payment_allocations": [
                {
                    "funding_source_id": str(vouchers.id),
                    "amount": {"minor_units": 1000, "currency": "EUR"},
                },
                {
                    "funding_source_id": str(card.id),
                    "amount": {"minor_units": 5000, "currency": "EUR"},
                },
            ],
            "expected_version": 2,
        },
        headers=mutation_headers("integration-web-split-correct"),
    )

    assert corrected.status_code == 200
    assert corrected.json()["version"] == 3

    posted = client.post(
        f"/api/v1/expenses/{expense_id}/post",
        json={"expected_version": 3, "receipt_approved": False},
        headers=mutation_headers("integration-web-split-post"),
    )
    assert posted.status_code == 200
    assert posted.json()["status"] == "posted"

    refused_correction = client.put(
        f"/api/v1/expenses/{expense_id}",
        json={**unreconciled, "expected_version": posted.json()["version"]},
        headers=mutation_headers("integration-web-split-posted-mismatch"),
    )
    assert refused_correction.status_code == 422
    assert refused_correction.json()["code"] == "expense_invalid"

    stored = repository.get_expense(profile.id, UUID(expense_id))
    assert {
        item.category_id: item.amount.minor_units
        for item in stored.category_allocations
    } == {food.id: 2500, home.id: 2500, dining.id: 1000}
    assert {
        item.funding_source_id: item.amount.minor_units
        for item in stored.payment_allocations
    } == {vouchers.id: 1000, card.id: 5000}

    overview = client.get("/api/v1/overview/monthly?period=2026-09").json()
    assert overview["posted_total"]["minor_units"] == 6000
    assert overview["funding_split"]["personal_funds"]["minor_units"] == 5000
    assert overview["funding_split"]["benefit"]["minor_units"] == 1000


@pytest.mark.postgres
def test_budget_plan_is_configured_and_corrected_in_real_postgresql(
    postgres_url: str,
) -> None:
    """Configure, replay, correct, and measure one budget through HTTP."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    food = taxonomy.resolve_active("Spesa alimentare")
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta budget web",
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
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
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

    empty = client.get("/api/v1/budgets?period=2026-09")
    assert empty.status_code == 200
    assert empty.json()["entries"] == []
    assert "Spesa alimentare" in {
        item["name"] for item in empty.json()["budgetable_categories"]
    }

    create_payload = {
        "category_id": str(food.id),
        "recurring_target": {"minor_units": 40000, "currency": "EUR"},
        "overrides": [
            {
                "period": {"year": 2026, "month": 12},
                "target": {"minor_units": 60000, "currency": "EUR"},
            }
        ],
    }
    created = client.post(
        "/api/v1/budgets",
        json=create_payload,
        headers=mutation_headers("integration-web-budget-create"),
    )
    replayed = client.post(
        "/api/v1/budgets",
        json=create_payload,
        headers=mutation_headers("integration-web-budget-create"),
    )

    assert created.status_code == 201
    assert created.json() == {"category_id": str(food.id), "version": 1}
    assert replayed.status_code == 201
    assert replayed.json() == created.json()

    plan = client.get("/api/v1/budgets?period=2026-09").json()
    assert len(plan["entries"]) == 1
    entry = plan["entries"][0]
    assert entry["category_name"] == "Spesa alimentare"
    assert entry["target"] == {"minor_units": 40000, "currency": "EUR"}
    assert entry["posted_spend"] == {"minor_units": 4250, "currency": "EUR"}
    assert entry["remaining"] == {"minor_units": 35750, "currency": "EUR"}
    assert entry["version"] == 1
    assert "Spesa alimentare" not in {
        item["name"] for item in plan["budgetable_categories"]
    }

    december = client.get("/api/v1/budgets?period=2026-12").json()["entries"][0]
    assert december["target"] == {"minor_units": 60000, "currency": "EUR"}
    assert december["posted_spend"] == {"minor_units": 0, "currency": "EUR"}

    reused_key = client.put(
        f"/api/v1/budgets/{food.id}",
        json={
            "recurring_target": {"minor_units": 999, "currency": "EUR"},
            "overrides": [],
            "expected_version": 1,
        },
        headers=mutation_headers("integration-web-budget-create"),
    )
    assert reused_key.status_code == 409
    assert reused_key.json()["code"] == "budget_conflict"

    corrected = client.put(
        f"/api/v1/budgets/{food.id}",
        json={
            "recurring_target": {"minor_units": 30000, "currency": "EUR"},
            "overrides": [],
            "expected_version": 1,
        },
        headers=mutation_headers("integration-web-budget-correct"),
    )
    stale = client.put(
        f"/api/v1/budgets/{food.id}",
        json={
            "recurring_target": {"minor_units": 20000, "currency": "EUR"},
            "overrides": [],
            "expected_version": 1,
        },
        headers=mutation_headers("integration-web-budget-stale"),
    )

    assert corrected.status_code == 200
    assert corrected.json() == {"category_id": str(food.id), "version": 2}
    assert stale.status_code == 409
    assert stale.json()["code"] == "budget_conflict"

    stored = repository.get_budget(profile.id, food.id)
    assert stored is not None
    assert stored.recurring_target == Money(30000)
    assert stored.overrides == ()
    assert stored.version == 2

    overview = client.get("/api/v1/overview/monthly?period=2026-09").json()
    assert overview["budgets"] == [
        {
            "category_name": "Spesa alimentare",
            "target": {"minor_units": 30000, "currency": "EUR"},
            "posted_spend": {"minor_units": 4250, "currency": "EUR"},
            "remaining": {"minor_units": 25750, "currency": "EUR"},
        }
    ]
    assert overview["unbudgeted_categories"] == []


@pytest.mark.postgres
def test_budget_routes_refuse_unknown_and_inactive_categories(
    postgres_url: str,
) -> None:
    """Keep a budget attached to an active owner-scoped category only."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    csrf = client.get(
        "/api/v1/csrf",
        headers={"origin": "http://localhost:8000"},
    ).json()["token"]
    headers = {
        "origin": "http://localhost:8000",
        "x-csrf-token": csrf,
        "Idempotency-Key": "integration-web-budget-unknown",
    }

    unknown = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(uuid4()),
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
        },
        headers=headers,
    )
    missing = client.put(
        f"/api/v1/budgets/{taxonomy.resolve_active('Abbonamenti').id}",
        json={
            "recurring_target": {"minor_units": 40000, "currency": "EUR"},
            "overrides": [],
            "expected_version": 1,
        },
        headers={**headers, "Idempotency-Key": "integration-web-budget-missing"},
    )
    negative = client.post(
        "/api/v1/budgets",
        json={
            "category_id": str(taxonomy.resolve_active("Abbonamenti").id),
            "recurring_target": {"minor_units": -100, "currency": "EUR"},
        },
        headers={**headers, "Idempotency-Key": "integration-web-budget-negative"},
    )

    assert unknown.status_code == 404
    assert unknown.json()["code"] == "budget_not_found"
    assert missing.status_code == 404
    assert missing.json()["code"] == "budget_not_found"
    assert negative.status_code == 422
    assert negative.json()["code"] == "budget_invalid"
    assert repository.get_budgets(profile.id) == ()


@pytest.mark.postgres
def test_category_proposals_are_resolved_through_real_services(
    postgres_url: str,
) -> None:
    """List, approve, reuse, cancel, and replay reviews through HTTP."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta review web",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    ids = UUID4Generator()
    finance = FinanceService(repository, clock=SystemClock(), ids=ids)
    proposals = CategoryProposalService(repository, clock=SystemClock(), ids=ids)

    def pending(name: str, reference: str) -> tuple[Expense, CategoryProposal]:
        """Store one correlated draft and pending candidate."""

        draft = Expense(
            id=uuid4(),
            profile_id=profile.id,
            amount=Money(1250),
            occurred_at=datetime(2026, 9, 12, 10, 30, tzinfo=ROME),
            description="movimento da classificare",
            merchant=None,
            status=ExpenseStatus.DRAFT,
            category_allocations=(),
            payment_allocations=(
                PaymentAllocation(funding_source_id=card.id, amount=Money(1250)),
            ),
            source_reference=reference,
            idempotency_key=f"expense:{reference}",
        )
        created = finance.create_expense(
            draft,
            request_hash=sha256(reference.encode()).hexdigest(),
            actor="test",
            reason="web category review fixture",
        )
        proposal = proposals.submit(
            CategoryProposal(
                id=uuid4(),
                profile_id=profile.id,
                proposed_name=name,
                description=f"Ambito riutilizzabile {name}.",
                parent_id=None,
                rationale="Nessuna categoria attiva descrive la finalità.",
                alternative_category_ids=(taxonomy.resolve_active("Formazione").id,),
                excluded_names=(),
                source_reference=reference,
            ),
            actor="workflow",
            reason="candidate awaiting browser review",
        )
        return created, proposal

    approved_draft, approved_proposal = pending(
        "Giardinaggio web",
        f"integration:web:category-review:{uuid4()}",
    )
    reused_draft, reused_proposal = pending(
        "Materiale didattico web",
        f"integration:web:category-review:{uuid4()}",
    )
    cancelled_draft, cancelled_proposal = pending(
        "Collezionismo web",
        f"integration:web:category-review:{uuid4()}",
    )
    review = CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(finance, ids=ids),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(review),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    csrf = client.get(
        "/api/v1/csrf",
        headers={"origin": "http://localhost:8000"},
    ).json()["token"]

    def review_headers(proposal_id: UUID) -> dict[str, str]:
        """Return browser headers bound to one proposal-wide command."""

        return {
            "origin": "http://localhost:8000",
            "x-csrf-token": csrf,
            "Idempotency-Key": f"category-review:{proposal_id}",
        }

    listed = client.get("/api/v1/category-proposals")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["proposals"]] == [
        str(approved_proposal.id),
        str(reused_proposal.id),
        str(cancelled_proposal.id),
    ]
    assert all("source_reference" not in item for item in listed.json()["proposals"])

    approved = client.post(
        f"/api/v1/category-proposals/{approved_proposal.id}/approve",
        json={},
        headers=review_headers(approved_proposal.id),
    )
    replayed = client.post(
        f"/api/v1/category-proposals/{approved_proposal.id}/approve",
        json={},
        headers=review_headers(approved_proposal.id),
    )
    reused = client.post(
        f"/api/v1/category-proposals/{reused_proposal.id}/use-existing",
        json={"category_name": "Formazione"},
        headers=review_headers(reused_proposal.id),
    )
    cancelled = client.post(
        f"/api/v1/category-proposals/{cancelled_proposal.id}/cancel",
        json={},
        headers=review_headers(cancelled_proposal.id),
    )

    assert approved.status_code == 200
    assert approved.json() == replayed.json()
    assert approved.json()["status"] == "posted"
    assert reused.status_code == 200
    assert reused.json()["status"] == "posted"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "discarded"
    assert (
        repository.get_expense(profile.id, approved_draft.id).status
        is ExpenseStatus.POSTED
    )
    assert (
        repository.get_expense(profile.id, reused_draft.id).status
        is ExpenseStatus.POSTED
    )
    assert (
        repository.get_expense(profile.id, cancelled_draft.id).status
        is ExpenseStatus.DISCARDED
    )
    assert (
        repository.get_category_proposal(profile.id, approved_proposal.id).status
        is CategoryProposalStatus.APPROVED
    )
    assert (
        repository.get_category_proposal(profile.id, reused_proposal.id).status
        is CategoryProposalStatus.REJECTED
    )
    assert (
        repository.get_category_proposal(profile.id, cancelled_proposal.id).status
        is CategoryProposalStatus.CANCELLED
    )
    assert client.get("/api/v1/category-proposals").json()["proposals"] == []


@pytest.mark.postgres
def test_receipt_review_is_operated_through_http_and_real_postgresql(
    postgres_url: str,
) -> None:
    """List retained receipts and run mandatory review over real services."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    ids = UUID4Generator()
    metadata = ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)

    def store(digest: str, *, stored_at: datetime, filename: str) -> ReceiptMetadata:
        """Register one already archived original through the real service."""

        return metadata.register(
            ReceiptMetadata(
                id=uuid4(),
                profile_id=profile.id,
                content_sha256=digest,
                media_type="image/jpeg",
                byte_size=131072,
                stored_at=stored_at,
                source_reference=f"integration:web:receipt:{digest[:8]}",
                original_filename=filename,
            ),
            actor="local-admin",
            reason="web receipt review fixture",
        )

    newer = store(
        "a1" * 32,
        stored_at=datetime(2026, 9, 12, 19, tzinfo=ROME),
        filename="mercato.jpg",
    )
    older = store(
        "b2" * 32,
        stored_at=datetime(2026, 9, 10, 9, tzinfo=ROME),
        filename="farmacia.jpg",
    )
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(metadata),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    csrf = client.get(
        "/api/v1/csrf",
        headers={"origin": "http://localhost:8000"},
    ).json()["token"]

    def headers(receipt_id: UUID, expected_version: int) -> dict[str, str]:
        """Return browser headers bound to one receipt transition."""

        return {
            "origin": "http://localhost:8000",
            "x-csrf-token": csrf,
            "Idempotency-Key": f"receipt-review:{receipt_id}:{expected_version}",
        }

    listed = client.get("/api/v1/receipts")
    opened = client.post(
        f"/api/v1/receipts/{newer.id}/review",
        json={"expected_version": 1},
        headers=headers(newer.id, 1),
    )
    replayed = client.post(
        f"/api/v1/receipts/{newer.id}/review",
        json={"expected_version": 1},
        headers=headers(newer.id, 1),
    )
    early = client.post(
        f"/api/v1/receipts/{older.id}/approve",
        json={"expected_version": 1},
        headers=headers(older.id, 1),
    )
    approved = client.post(
        f"/api/v1/receipts/{newer.id}/approve",
        json={"expected_version": 2},
        headers=headers(newer.id, 2),
    )
    stale = client.post(
        f"/api/v1/receipts/{newer.id}/approve",
        json={"expected_version": 3},
        headers=headers(newer.id, 3),
    )
    absent_id = uuid4()
    unknown = client.post(
        f"/api/v1/receipts/{absent_id}/review",
        json={"expected_version": 1},
        headers=headers(absent_id, 1),
    )
    wrong_key = client.post(
        f"/api/v1/receipts/{older.id}/review",
        json={"expected_version": 1},
        headers={
            "origin": "http://localhost:8000",
            "x-csrf-token": csrf,
            "Idempotency-Key": f"receipt-review:{older.id}",
        },
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["receipts"]] == [
        str(newer.id),
        str(older.id),
    ]
    assert listed.json()["receipts"][0]["original_filename"] == "mercato.jpg"
    assert "a1" * 32 not in listed.text
    assert "source_reference" not in listed.text
    assert opened.status_code == 200
    assert opened.json() == {
        "id": str(newer.id),
        "status": "needs_review",
        "version": 2,
    }
    assert replayed.json() == opened.json()
    assert early.status_code == 422
    assert early.json()["code"] == "receipt_invalid"
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert stale.status_code == 422
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "receipt_not_found"
    assert wrong_key.status_code == 409
    assert wrong_key.json()["code"] == "receipt_conflict"
    assert (
        repository.get_receipt_metadata(profile.id, newer.id).status
        is ReceiptStatus.APPROVED
    )
    assert (
        repository.get_receipt_metadata(profile.id, older.id).status
        is ReceiptStatus.STORED
    )
    assert [
        item["status"] for item in client.get("/api/v1/receipts").json()["receipts"]
    ] == [
        "approved",
        "stored",
    ]
