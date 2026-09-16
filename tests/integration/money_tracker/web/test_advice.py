"""Real PostgreSQL and local-model integration for browser advice."""

from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from money_tracker.application import (
    CategoryAdministrationService,
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
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import LocalStructuredOutputModel
from money_tracker.web import WebSettings, create_app
from money_tracker.web.advice import AdviceWorkflow
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    CategoryManagementMutationAdapter,
    CategoryReviewMutationAdapter,
    ExpenseMutationAdapter,
    FundingSourceMutationAdapter,
    ReceiptReviewMutationAdapter,
)
from tests.integration.money_tracker.web.test_api import (
    _messaging,
    _store_posted_expense,
)

ROME = ZoneInfo("Europe/Rome")
ORIGIN = "http://localhost:8000"


@pytest.mark.inference
@pytest.mark.postgres
def test_advice_http_uses_real_model_and_postgresql_without_mutation(
    postgres_url: str,
) -> None:
    """Cross HTTP, promoted selection, reporting, and PostgreSQL as one read."""

    if os.getenv("RUN_ADVICE_HTTP_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_ADVICE_HTTP_INTEGRATION_TESTS=1 for real browser advice")
    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    now = datetime(2026, 9, 15, 12, tzinfo=ROME)
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta advice integrazione",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    _store_posted_expense(repository, profile=profile, source=card)
    food = taxonomy.resolve_active("Spesa alimentare")
    ids = UUID4Generator()
    administration = FinanceAdministrationService(
        repository,
        clock=SystemClock(),
        ids=ids,
    )
    administration.create_budget(
        MonthlyBudget(
            id=uuid4(),
            profile_id=profile.id,
            category_id=food.id,
            recurring_target=Money(4000),
        ),
        idempotency_key=f"integration-web-advice-budget:{uuid4()}",
        actor="test",
        reason="web advice integration fixture",
    )
    budget_before = repository.get_budget(profile.id, food.id)
    assert budget_before is not None

    reporting = ReportingService(repository)
    app = create_app(
        settings=WebSettings(static_dir=None),
        profile_id=profile.id,
        reporting=reporting,
        profile_settings=ProfileSettingsService(repository),
        mutations=ExpenseMutationAdapter(
            FinanceService(repository, clock=SystemClock(), ids=ids),
            ids=ids,
        ),
        budgets=BudgetMutationAdapter(administration, ids=ids),
        categories=CategoryManagementMutationAdapter(
            CategoryAdministrationService(repository, clock=SystemClock(), ids=ids)
        ),
        funding_sources=FundingSourceMutationAdapter(
            FundingSourceAdministrationService(
                repository,
                clock=SystemClock(),
                ids=ids,
            ),
            ids=ids,
        ),
        category_reviews=CategoryReviewMutationAdapter(
            CategoryReviewService(repository, repository, clock=SystemClock(), ids=ids)
        ),
        receipts=ReceiptReviewMutationAdapter(
            ReceiptMetadataService(repository, clock=SystemClock(), ids=ids)
        ),
        messaging=_messaging(postgres_url),
        readiness_check=lambda: repository.profile_exists(profile.id),
        advice=AdviceWorkflow(
            repository,
            reporting,
            clock=SystemClock(),
            model=LocalStructuredOutputModel(),
        ),
    )
    client = TestClient(app, base_url=ORIGIN)
    csrf = client.get("/api/v1/csrf", headers={"origin": ORIGIN}).json()["token"]

    response = client.post(
        "/api/v1/advice",
        headers={"origin": ORIGIN, "x-csrf-token": csrf},
        json={"message": "Come vanno tutti i miei budget questo mese?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["evidence"]["kind"] == "budgets_at_risk"
    assert payload["evidence"]["period"] == {"year": 2026, "month": 9}
    assert payload["evidence"]["positions"] == [
        {
            "category_name": "Spesa alimentare",
            "target": {"minor_units": 4000, "currency": "EUR"},
            "posted_spend": {"minor_units": 4250, "currency": "EUR"},
            "remaining": {"minor_units": -250, "currency": "EUR"},
        }
    ]
    assert payload["reason"] is None
    assert repository.get_budget(profile.id, food.id) == budget_before
