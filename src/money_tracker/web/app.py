"""FastAPI composition for the loopback API and compiled React dashboard."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Error as PsycopgError

from money_tracker.api.contracts import (
    API_V1_PREFIX,
    PROBLEM_MEDIA_TYPE,
    ProblemDetails,
)
from money_tracker.application import MonthlyOverview
from money_tracker.domain import YearMonth
from money_tracker.web.config import WebSettings
from money_tracker.web.contracts import (
    CsrfTokenResponse,
    HealthResponse,
    MonthlyOverviewResponse,
)
from money_tracker.web.security import (
    CSRF_COOKIE_NAME,
    BrowserSecurityPolicy,
    LocalBrowserProtectionMiddleware,
    issue_csrf_token,
)

_PERIOD = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")


class MonthlyOverviewReader(Protocol):
    """Read only the aggregate needed by the first dashboard view."""

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Return exact posted and budget facts for one month."""


def create_app(
    *,
    settings: WebSettings,
    profile_id: UUID,
    reporting: MonthlyOverviewReader,
    readiness_check: Callable[[], bool],
) -> FastAPI:
    """Compose the local web adapter around narrow application reads.

    Args:
        settings: Validated listener, browser, and static-asset configuration.
        profile_id: Trusted single-owner scope, never accepted from a request.
        reporting: Read-only application service for monthly overview facts.
        readiness_check: Content-free database/schema readiness probe.

    Returns:
        Configured FastAPI application with a closed browser boundary.

    Raises:
        ValueError: If a configured static directory has no compiled UI.
    """

    app = FastAPI(
        title="Money Tracker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        LocalBrowserProtectionMiddleware,
        policy=BrowserSecurityPolicy(
            allowed_hosts=frozenset(settings.allowed_hosts),
            allowed_origins=frozenset(settings.allowed_origins),
        ),
    )
    router = APIRouter(prefix=API_V1_PREFIX)

    @router.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        """Report process liveness without touching a dependency."""

        return HealthResponse(status="healthy")

    @router.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse | JSONResponse:
        """Report whether the authoritative profile can be read."""

        try:
            available = readiness_check()
        except PsycopgError:
            available = False
        if not available:
            return _problem(
                status=503,
                title="Servizio non pronto",
                detail="Il database dell'applicazione non è disponibile.",
                code="database_unavailable",
                instance=f"{API_V1_PREFIX}/health/ready",
            )
        return HealthResponse(status="healthy")

    @router.get("/csrf", response_model=CsrfTokenResponse)
    def csrf(response: Response) -> CsrfTokenResponse:
        """Issue the browser value required by every unsafe HTTP method."""

        token = issue_csrf_token()
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=True,
            max_age=3600,
            path=API_V1_PREFIX,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        return CsrfTokenResponse(token=token)

    @router.get("/overview/monthly", response_model=MonthlyOverviewResponse)
    def monthly_overview(period: str) -> MonthlyOverviewResponse | JSONResponse:
        """Return one exact month of posted consumption and budget state."""

        parsed = _parse_period(period)
        if parsed is None:
            return _problem(
                status=422,
                title="Periodo non valido",
                detail="Il periodo deve usare il formato YYYY-MM.",
                code="period_invalid",
                instance=f"{API_V1_PREFIX}/overview/monthly",
            )
        try:
            result = reporting.monthly_overview(
                profile_id=profile_id,
                period=parsed,
            )
        except PsycopgError:
            return _problem(
                status=503,
                title="Dati non disponibili",
                detail="Il database dell'applicazione non è disponibile.",
                code="database_unavailable",
                instance=f"{API_V1_PREFIX}/overview/monthly",
            )
        return MonthlyOverviewResponse.from_application(result)

    app.include_router(router)
    if settings.static_dir is not None:
        _mount_dashboard(app, settings.static_dir)
    return app


def _parse_period(value: str) -> YearMonth | None:
    """Parse the single accepted month representation without coercion."""

    match = _PERIOD.fullmatch(value)
    if match is None:
        return None
    return YearMonth(int(match.group("year")), int(match.group("month")))


def _problem(
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    instance: str,
) -> JSONResponse:
    """Render a bounded problem-details response."""

    problem = ProblemDetails(
        title=title,
        status=status,
        detail=detail,
        code=code,
        instance=instance,
    )
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _mount_dashboard(app: FastAPI, static_dir: Path) -> None:
    """Serve a verified Vite build from the API origin."""

    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if not index.is_file() or not assets.is_dir():
        raise ValueError(
            "compiled dashboard is missing; run npm ci and npm run build in frontend"
        )
    app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Return the compiled single-page application shell."""

        return FileResponse(index)


__all__ = ["MonthlyOverviewReader", "create_app"]
