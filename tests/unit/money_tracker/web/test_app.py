"""Tests for observable behavior of the local web adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from money_tracker.application import MonthlyOverview, PeriodSplit
from money_tracker.domain import (
    BalanceMode,
    BudgetStatus,
    FundingClass,
    FundingClassSplit,
    FundingSource,
    Money,
    YearMonth,
)
from money_tracker.web import WebSettings, create_app

PROFILE_ID = uuid4()
PERIOD = YearMonth(2026, 9)
ORIGIN = "http://localhost:8000"


@dataclass
class OverviewReader:
    """Small in-memory implementation of the read-only web port."""

    requested_profile: UUID | None = None
    requested_period: YearMonth | None = None

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Return fixed exact facts and record trusted scoping inputs."""

        self.requested_profile = profile_id
        self.requested_period = period
        source = FundingSource(
            id=uuid4(),
            profile_id=profile_id,
            name="Carta",
            funding_class=FundingClass.PERSONAL_FUNDS,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=True,
        )
        return MonthlyOverview(
            period=period,
            posted_total=Money(5000),
            split=PeriodSplit(
                period=period,
                split=FundingClassSplit(Money(2000), Money(3000), Money(0)),
                sources=((source, Money(2000)),),
            ),
            budgets=(
                (
                    "Spesa alimentare",
                    BudgetStatus(period, Money(8000), Money(5000), Money(3000)),
                ),
            ),
            unbudgeted_category_names=("Abbonamenti",),
        )


def _app(
    reader: OverviewReader | None = None,
    *,
    static_dir: Path | None = None,
    ready: bool = True,
) -> FastAPI:
    """Compose an application with real middleware and an in-memory read port."""

    return create_app(
        settings=WebSettings(static_dir=static_dir),
        profile_id=PROFILE_ID,
        reporting=reader or OverviewReader(),
        readiness_check=lambda: ready,
    )


def _client(app: FastAPI) -> TestClient:
    """Use an allowed authority so only intentional policy failures vary."""

    return TestClient(app, base_url=ORIGIN)


def test_monthly_overview_returns_exact_minor_units_in_the_trusted_profile() -> None:
    """Expose deterministic facts without accepting owner identity from input."""

    reader = OverviewReader()

    response = _client(_app(reader)).get("/api/v1/overview/monthly?period=2026-09")

    assert response.status_code == 200
    assert response.json() == {
        "period": {"year": 2026, "month": 9},
        "posted_total": {"minor_units": 5000, "currency": "EUR"},
        "funding_split": {
            "personal_funds": {"minor_units": 2000, "currency": "EUR"},
            "benefit": {"minor_units": 3000, "currency": "EUR"},
            "other": {"minor_units": 0, "currency": "EUR"},
        },
        "funding_sources": [
            {
                "name": "Carta",
                "funding_class": "personal_funds",
                "amount": {"minor_units": 2000, "currency": "EUR"},
            }
        ],
        "budgets": [
            {
                "category_name": "Spesa alimentare",
                "target": {"minor_units": 8000, "currency": "EUR"},
                "posted_spend": {"minor_units": 5000, "currency": "EUR"},
                "remaining": {"minor_units": 3000, "currency": "EUR"},
            }
        ],
        "unbudgeted_categories": ["Abbonamenti"],
    }
    assert reader.requested_profile == PROFILE_ID
    assert reader.requested_period == PERIOD
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["content-security-policy"].startswith("default-src")
    assert response.headers["cache-control"] == "no-store"


def test_invalid_period_returns_a_bounded_problem() -> None:
    """Reject ambiguous dates without invoking the application reader."""

    reader = OverviewReader()
    response = _client(_app(reader)).get(
        "/api/v1/overview/monthly?period=09-2026"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "period_invalid"
    assert reader.requested_period is None


def test_health_distinguishes_liveness_from_dependency_readiness() -> None:
    """Keep process health observable while PostgreSQL is unavailable."""

    client = _client(_app(ready=False))

    assert client.get("/api/v1/health/live").status_code == 200
    readiness = client.get("/api/v1/health/ready")
    assert readiness.status_code == 503
    assert readiness.json()["code"] == "database_unavailable"


def test_host_and_origin_allowlists_block_browser_boundary_bypass() -> None:
    """Reject DNS rebinding and cross-origin reads before routing."""

    client = _client(_app())

    bad_host = client.get("/api/v1/health/live", headers={"host": "evil.test"})
    bad_origin = client.get(
        "/api/v1/health/live",
        headers={"origin": "http://evil.test"},
    )

    assert bad_host.status_code == 403
    assert bad_host.json()["code"] == "host_not_allowed"
    assert bad_origin.status_code == 403
    assert bad_origin.json()["code"] == "origin_not_allowed"


def test_unsafe_methods_require_matching_origin_cookie_and_csrf_header() -> None:
    """Enforce the double-submit contract even on later mutation routes."""

    app = _app()

    @app.post("/api/v1/test-mutation")
    def mutation() -> dict[str, bool]:
        """Provide a test-only unsafe route behind production middleware."""

        return {"accepted": True}

    client = _client(app)
    rejected = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN},
    )
    issued = client.get("/api/v1/csrf", headers={"origin": ORIGIN})
    accepted = client.post(
        "/api/v1/test-mutation",
        headers={"origin": ORIGIN, "x-csrf-token": issued.json()["token"]},
    )

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_validation_failed"
    assert issued.headers["cache-control"] == "no-store"
    assert accepted.json() == {"accepted": True}


def test_compiled_dashboard_is_served_from_the_api_origin(tmp_path: Path) -> None:
    """Serve one build while keeping source maps and arbitrary files private."""

    static_dir = tmp_path / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<main>Money Tracker</main>")

    response = _client(_app(static_dir=static_dir)).get("/")

    assert response.status_code == 200
    assert "Money Tracker" in response.text
