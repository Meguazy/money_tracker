"""Real PostgreSQL integration tests for the local dashboard API."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from psycopg import connect

from money_tracker.application import (
    CategoryAdministrationService,
    CategoryProposalService,
    CategoryReviewService,
    FinanceAdministrationService,
    FinanceService,
    FundingSourceAdministrationService,
    ProfileSettingsService,
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
    MonthlyBudget,
    PaymentAllocation,
    Profile,
    ReceiptMetadata,
    ReceiptStatus,
    initial_taxonomy,
)
from money_tracker.messaging import (
    BotEnvironment,
    MessagingAdministration,
    MessagingScope,
    NormalizedTelegramUpdate,
    OutboxMessageDraft,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.web import WebSettings, create_app
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    CategoryManagementMutationAdapter,
    CategoryReviewMutationAdapter,
    ExpenseMutationAdapter,
    FundingSourceMutationAdapter,
    MessagingOperationsAdapter,
    ReceiptReviewMutationAdapter,
)
from money_tracker.workflows import posting_request_hash

ROME = ZoneInfo("Europe/Rome")


def _messaging(
    postgres_url: str,
    *,
    scope: MessagingScope | None = None,
) -> MessagingOperationsAdapter:
    """Bind the browser messaging surface to one isolated real bot scope."""

    return MessagingOperationsAdapter(
        MessagingAdministration(
            PostgresMessagingRepository(postgres_url),
            clock=SystemClock(),
            ids=UUID4Generator(),
        ),
        scope=scope
        or MessagingScope(
            environment=BotEnvironment.TEST,
            bot_key=f"test-{uuid4().hex}",
        ),
    )


def _categories(
    repository: PostgresFinanceRepository,
    ids: UUID4Generator,
) -> CategoryManagementMutationAdapter:
    """Bind category administration to the real finance repository."""

    return CategoryManagementMutationAdapter(
        CategoryAdministrationService(
            repository,
            clock=SystemClock(),
            ids=ids,
        )
    )


def _funding_sources(
    repository: PostgresFinanceRepository,
    ids: UUID4Generator,
) -> FundingSourceMutationAdapter:
    """Bind source administration to the real finance repository."""

    return FundingSourceMutationAdapter(
        FundingSourceAdministrationService(
            repository,
            clock=SystemClock(),
            ids=ids,
        ),
        ids=ids,
    )


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


def _store_split_posted_expense(
    repository: PostgresFinanceRepository,
    *,
    profile: Profile,
    occurred_at: datetime,
    category_allocations: tuple[tuple[UUID, int], ...],
    payment_allocations: tuple[tuple[UUID, int], ...],
) -> None:
    """Post one reconciled split movement through the real application service."""

    total = sum(amount for _, amount in category_allocations)
    assert total == sum(amount for _, amount in payment_allocations)
    reference = f"integration:web:analytics:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(total),
        occurred_at=occurred_at,
        description="movimento analitico",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=tuple(
            CategoryAllocation(
                category_id=category_id,
                amount=Money(amount),
                provenance="test",
            )
            for category_id, amount in category_allocations
        ),
        payment_allocations=tuple(
            PaymentAllocation(
                funding_source_id=funding_source_id,
                amount=Money(amount),
            )
            for funding_source_id, amount in payment_allocations
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    created = finance.create_expense(
        expense,
        request_hash=sha256(reference.encode()).hexdigest(),
        actor="test",
        reason="web range analytics fixture",
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
        reason="web range analytics fixture",
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
        profile_settings=ProfileSettingsService(repository),
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
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
def test_range_analytics_resolve_calendar_and_both_dimensions_in_postgresql(
    postgres_url: str,
) -> None:
    """Read an ISO week and whole month through HTTP over real stored splits."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta analytics web",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    vouchers = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Buoni analytics web",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.UNTRACKED,
    )
    repository.create_funding_source(card, occurred_at=now)
    repository.create_funding_source(vouchers, occurred_at=now)
    food = taxonomy.resolve_active("Spesa alimentare")
    home = taxonomy.resolve_active("Prodotti casa, arredo e manutenzione")
    food_root = taxonomy.resolve_active("Alimentazione")
    _store_split_posted_expense(
        repository,
        profile=profile,
        occurred_at=datetime(2025, 12, 28, 12, tzinfo=ROME),
        category_allocations=((food.id, 700),),
        payment_allocations=((card.id, 700),),
    )
    _store_split_posted_expense(
        repository,
        profile=profile,
        occurred_at=datetime(2025, 12, 31, 12, tzinfo=ROME),
        category_allocations=((food.id, 3000), (home.id, 2000)),
        payment_allocations=((card.id, 3500), (vouchers.id, 1500)),
    )
    _store_split_posted_expense(
        repository,
        profile=profile,
        occurred_at=datetime(2026, 1, 5, 12, tzinfo=ROME),
        category_allocations=((food.id, 900),),
        payment_allocations=((card.id, 900),),
    )
    FinanceAdministrationService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).create_budget(
        MonthlyBudget(
            id=uuid4(),
            profile_id=profile.id,
            category_id=food_root.id,
            recurring_target=Money(6000),
        ),
        idempotency_key=f"integration-web-analytics-budget:{uuid4()}",
        actor="test",
        reason="web range analytics fixture",
    )
    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")

    week = client.get(
        "/api/v1/overview/range?granularity=week&day=2025-12-31",
    )
    whole_month = client.get(
        "/api/v1/overview/range?granularity=custom&start=2025-12-01&end=2025-12-31",
    )

    assert week.status_code == 200
    assert week.json()["period"] == {
        "granularity": "week",
        "start": "2025-12-29",
        "end": "2026-01-04",
        "day_count": 7,
        "iso_year": 2026,
        "iso_week": 1,
    }
    assert week.json()["posted_total"]["minor_units"] == 5000
    assert week.json()["movement_count"] == 1
    assert week.json()["funding_split"]["personal_funds"]["minor_units"] == 3500
    assert week.json()["funding_split"]["benefit"]["minor_units"] == 1500
    assert {
        item["category_name"]: item["amount"]["minor_units"]
        for item in week.json()["categories"]
    } == {
        "Spesa alimentare": 3000,
        "Prodotti casa, arredo e manutenzione": 2000,
    }
    assert week.json()["budgets"] is None
    assert "profile_id" not in week.text
    assert whole_month.status_code == 200
    assert whole_month.json()["posted_total"]["minor_units"] == 5700
    assert whole_month.json()["budgets"]["period"] == {"year": 2025, "month": 12}
    assert whole_month.json()["budgets"]["positions"] == [
        {
            "category_name": "Alimentazione",
            "target": {"minor_units": 6000, "currency": "EUR"},
            "posted_spend": {"minor_units": 3700, "currency": "EUR"},
            "remaining": {"minor_units": 2300, "currency": "EUR"},
        }
    ]


@pytest.mark.postgres
def test_funding_source_and_wallet_control_plane_is_atomic_and_replay_safe(
    postgres_url: str,
) -> None:
    """Manage source lifecycle and expiry-aware wallet state through real PostgreSQL."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta integrazione fonti",
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    origin = {"origin": "http://localhost:8000"}
    csrf = client.get("/api/v1/csrf", headers=origin).json()["token"]
    initial_settings = client.get("/api/v1/settings", headers=origin)
    assert initial_settings.status_code == 200
    assert initial_settings.json()["locale"] == "it-IT"
    assert initial_settings.json()["timezone"] == "Europe/Rome"
    assert initial_settings.json()["currency"] == "EUR"
    assert initial_settings.json()["default_funding_source"]["id"] == str(card.id)
    assert "profile_id" not in initial_settings.text

    def headers(command_key: str) -> dict[str, str]:
        """Build same-origin CSRF and idempotency headers."""

        return {
            **origin,
            "x-csrf-token": csrf,
            "Idempotency-Key": command_key,
            "content-type": "application/json",
        }

    create_body = {
        "name": "Benefit integrazione fonti",
        "funding_class": "benefit",
        "balance_mode": "ledger",
        "is_default": False,
    }
    created = client.post(
        "/api/v1/funding-sources",
        headers=headers("integration-source-create"),
        json=create_body,
    )
    replayed = client.post(
        "/api/v1/funding-sources",
        headers=headers("integration-source-create"),
        json=create_body,
    )
    assert created.status_code == replayed.status_code == 201
    assert replayed.json() == created.json()
    wallet_id = created.json()["id"]

    expires_on = (now.astimezone(ROME).date() + timedelta(days=30)).isoformat()
    opening = client.post(
        f"/api/v1/funding-sources/{wallet_id}/wallet-entries",
        headers=headers("integration-wallet-opening"),
        json={
            "entry_type": "opening",
            "amount": {"minor_units": 10000, "currency": "EUR"},
            "occurred_at": (now - timedelta(minutes=5)).isoformat(),
            "expires_on": expires_on,
        },
    )
    rejected = client.post(
        f"/api/v1/funding-sources/{wallet_id}/wallet-entries",
        headers=headers("integration-wallet-invalid"),
        json={
            "entry_type": "adjustment",
            "amount": {"minor_units": -10001, "currency": "EUR"},
            "occurred_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_on": None,
        },
    )
    assert opening.status_code == 201
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "wallet_invalid"

    snapshot = client.get("/api/v1/funding-sources", headers=origin)
    assert snapshot.status_code == 200
    wallet = next(
        item for item in snapshot.json()["sources"] if item["id"] == wallet_id
    )
    assert wallet["availability"] == {
        "booked_balance": {"minor_units": 10000, "currency": "EUR"},
        "available_balance": {"minor_units": 10000, "currency": "EUR"},
        "expiring": [
            {
                "expires_on": expires_on,
                "amount": {"minor_units": 10000, "currency": "EUR"},
            }
        ],
    }
    assert len(wallet["entries"]) == 1
    assert "note" not in wallet["entries"][0]
    assert "profile_id" not in snapshot.text

    renamed = client.put(
        f"/api/v1/funding-sources/{wallet_id}",
        headers=headers("integration-source-rename"),
        json={"name": "Benefit rinominato", "expected_version": 1},
    )
    changed_default = client.post(
        f"/api/v1/funding-sources/{wallet_id}/default",
        headers=headers("integration-source-default"),
        json={
            "expected_version": 2,
            "expected_previous_default_id": str(card.id),
            "expected_previous_default_version": 1,
        },
    )
    archived_old = client.post(
        f"/api/v1/funding-sources/{card.id}/archive",
        headers=headers("integration-source-archive"),
        json={"expected_version": 2},
    )
    refused_default_archive = client.post(
        f"/api/v1/funding-sources/{wallet_id}/archive",
        headers=headers("integration-source-archive-default"),
        json={"expected_version": 3},
    )
    assert renamed.json()["version"] == 2
    assert changed_default.json()["version"] == 3
    changed_settings = client.get("/api/v1/settings", headers=origin)
    assert changed_settings.status_code == 200
    assert changed_settings.json()["default_funding_source"] == {
        "id": wallet_id,
        "name": "Benefit rinominato",
        "funding_class": "benefit",
        "version": 3,
    }
    fixed_fields = client.put(
        "/api/v1/settings",
        headers=headers("integration-settings-fixed"),
        json={"locale": "en-US", "timezone": "UTC", "currency": "USD"},
    )
    assert fixed_fields.status_code == 405
    assert archived_old.json()["status"] == "archived"
    assert refused_default_archive.status_code == 422

    with connect(postgres_url) as connection:
        counts = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM finance.wallet_entries WHERE profile_id = %s), "
            "(SELECT count(*) FROM finance.audit_events WHERE profile_id = %s), "
            "(SELECT count(*) FROM finance.idempotency_records WHERE profile_id = %s)",
            (profile.id, profile.id, profile.id),
        ).fetchone()
        default_pointer = connection.execute(
            "SELECT default_funding_source_id FROM finance.profiles WHERE id = %s",
            (profile.id,),
        ).fetchone()
    assert counts == (1, 6, 5)
    assert default_pointer == (UUID(wallet_id),)


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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(finance, ids=ids),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(review),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
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
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(metadata),
        messaging=_messaging(postgres_url),
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


@pytest.mark.postgres
def test_messaging_operations_are_inspected_and_requeued_over_http(
    postgres_url: str,
) -> None:
    """Read one bot's real queues and requeue both failure kinds safely."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    clock = SystemClock()
    now = clock.now()
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    scope = MessagingScope(
        environment=BotEnvironment.TEST,
        bot_key=f"test-{uuid4().hex}",
    )
    messaging_repository = PostgresMessagingRepository(postgres_url)
    messaging_repository.ensure_bot(
        scope.bot_key,
        environment=scope.environment,
        now=now,
    )
    update = NormalizedTelegramUpdate(
        id=uuid4(),
        bot_key=scope.bot_key,
        update_id=907,
        telegram_user_id=1001,
        telegram_chat_id=1001,
        telegram_message_id=908,
        received_at=now,
        accepted_at=now,
        text="tre euro di caffè al bar",
    )
    messaging_repository.commit_ingest_batch(
        bot_key=scope.bot_key,
        expected_offset=messaging_repository.polling_offset(scope.bot_key),
        observed_update_ids=(update.update_id,),
        accepted_updates=(update,),
        rejected_count=0,
        unsupported_count=0,
        now=now,
    )
    claim = messaging_repository.claim_next_inbox(
        scope.bot_key,
        now=now + timedelta(seconds=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert claim is not None
    notice_text = "Non sono riuscito a elaborare un messaggio."
    messaging_repository.fail_inbox(
        claim,
        error_code="model_unavailable",
        transient=False,
        retry_at=None,
        response=OutboxMessageDraft(
            id=uuid4(),
            bot_key=scope.bot_key,
            telegram_chat_id=update.telegram_chat_id,
            logical_key=f"inbox:{update.id}:response:terminal_failure",
            response_kind="terminal_failure",
            text=notice_text,
            content_hash=hashlib.sha256(notice_text.encode()).hexdigest(),
            inbox_id=update.id,
        ),
        now=now + timedelta(seconds=2),
    )
    delivery_now = now + timedelta(minutes=1)
    for _ in range(5):
        outbox = messaging_repository.claim_next_outbox(
            scope.bot_key,
            now=delivery_now,
            lease=timedelta(seconds=30),
            claim_token=uuid4(),
        )
        assert outbox is not None
        messaging_repository.fail_outbox(
            outbox,
            error_code="telegram_unavailable",
            transient=True,
            retry_at=delivery_now + timedelta(seconds=2),
            now=delivery_now,
        )
        delivery_now += timedelta(seconds=2)

    ids = UUID4Generator()
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=ReportingService(repository),
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=clock, ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(
            FinanceAdministrationService(repository, clock=clock, ids=ids),
            ids=ids,
        ),
        categories=_categories(repository, ids),
        funding_sources=_funding_sources(repository, ids),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=clock, ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=clock, ids=ids)
        ),
        messaging=_messaging(postgres_url, scope=scope),
        readiness_check=lambda: repository.profile_exists(profile.id),
    )
    client = TestClient(app, base_url="http://localhost:8000")
    csrf = client.get(
        "/api/v1/csrf",
        headers={"origin": "http://localhost:8000"},
    ).json()["token"]

    def headers(command_key: str) -> dict[str, str]:
        """Return the exact browser mutation boundary headers."""

        return {
            "origin": "http://localhost:8000",
            "x-csrf-token": csrf,
            "Idempotency-Key": command_key,
        }

    listed = client.get("/api/v1/messaging")
    state = listed.json()
    inbox_id = state["inbox_dead_letters"][0]["inbox_id"]
    outbox_id = state["outbox_dead_letters"][0]["outbox_id"]
    replayed = client.post(
        f"/api/v1/messaging/inbox/{inbox_id}/replay",
        json={"expected_replay_count": 0},
        headers=headers(f"messaging-replay:{inbox_id}:0"),
    )
    retried = client.post(
        f"/api/v1/messaging/inbox/{inbox_id}/replay",
        json={"expected_replay_count": 0},
        headers=headers(f"messaging-replay:{inbox_id}:0"),
    )
    stale = client.post(
        f"/api/v1/messaging/inbox/{inbox_id}/replay",
        json={"expected_replay_count": 3},
        headers=headers(f"messaging-replay:{inbox_id}:3"),
    )
    foreign = uuid4()
    unknown = client.post(
        f"/api/v1/messaging/outbox/{foreign}/resend",
        json={"expected_replay_count": 0},
        headers=headers(f"messaging-resend:{foreign}:0"),
    )
    wrong_key = client.post(
        f"/api/v1/messaging/outbox/{outbox_id}/resend",
        json={"expected_replay_count": 0},
        headers=headers(f"messaging-replay:{outbox_id}:0"),
    )
    resent = client.post(
        f"/api/v1/messaging/outbox/{outbox_id}/resend",
        json={"expected_replay_count": 0},
        headers=headers(f"messaging-resend:{outbox_id}:0"),
    )

    assert listed.status_code == 200
    assert state["environment"] == "test"
    assert state["owner_paired"] is False
    assert state["inbox"]["dead_letters"] == 1
    assert state["outbox"]["dead_letters"] == 1
    assert state["inbox_dead_letters"][0]["update_id"] == 907
    assert state["inbox_dead_letters"][0]["last_error_code"] == "model_unavailable"
    assert state["outbox_dead_letters"][0]["response_kind"] == "terminal_failure"
    assert update.text is not None
    assert update.text not in listed.text
    assert notice_text not in listed.text
    assert scope.bot_key not in listed.text
    assert "source_reference" not in listed.text
    assert replayed.status_code == 200
    assert replayed.json() == {
        "id": inbox_id,
        "queue": "inbox",
        "outcome": "applied",
    }
    assert retried.json()["outcome"] == "replayed"
    assert stale.status_code == 409
    assert stale.json()["code"] == "messaging_conflict"
    assert unknown.status_code == 409
    assert unknown.json() == stale.json() | {"instance": unknown.json()["instance"]}
    assert wrong_key.status_code == 409
    assert wrong_key.json()["code"] == "messaging_conflict"
    assert resent.status_code == 200
    assert resent.json() == {
        "id": outbox_id,
        "queue": "outbox",
        "outcome": "applied",
    }

    health = messaging_repository.health(scope.bot_key)
    assert health.inbox_dead_letters == 0
    assert health.outbox_dead_letters == 0
    assert health.pending_inbox == 1
    assert health.pending_outbox == 1
    recovered = messaging_repository.claim_next_inbox(
        scope.bot_key,
        now=delivery_now + timedelta(minutes=1),
        lease=timedelta(minutes=1),
        claim_token=uuid4(),
    )
    assert recovered is not None
    assert recovered.update.source_reference == update.source_reference
    assert recovered.replay_count == 1
    assert client.get("/api/v1/messaging").json()["inbox_dead_letters"] == []
