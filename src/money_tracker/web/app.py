"""FastAPI composition for the loopback API and compiled React dashboard."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import Error as PsycopgError
from pydantic import ValidationError

from money_tracker.api.contracts import (
    API_V1_PREFIX,
    PROBLEM_MEDIA_TYPE,
    ExpenseCreateRequest,
    ProblemDetails,
)
from money_tracker.application import (
    AggregateNotFoundError,
    IdempotencyConflictError,
    MonthlyOverview,
    OptimisticConcurrencyError,
    TransactionRegister,
)
from money_tracker.domain import DomainError, Expense, YearMonth
from money_tracker.web.config import WebSettings
from money_tracker.web.contracts import (
    CsrfTokenResponse,
    ExpenseCancelRequest,
    ExpenseMutationResponse,
    ExpensePostRequest,
    ExpenseReplaceRequest,
    HealthResponse,
    MonthlyOverviewResponse,
    TransactionRegisterResponse,
)
from money_tracker.web.mutations import ExpenseMutationAdapter
from money_tracker.web.security import (
    CSRF_COOKIE_NAME,
    BrowserSecurityPolicy,
    LocalBrowserProtectionMiddleware,
    issue_csrf_token,
)

_PERIOD = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")


class DashboardReader(Protocol):
    """Read only the aggregates needed by implemented dashboard views."""

    def monthly_overview(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> MonthlyOverview:
        """Return exact posted and budget facts for one month."""

    def transaction_register(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> TransactionRegister:
        """Return complete movement lifecycle facts for one month."""


def create_app(
    *,
    settings: WebSettings,
    profile_id: UUID,
    reporting: DashboardReader,
    mutations: ExpenseMutationAdapter,
    readiness_check: Callable[[], bool],
) -> FastAPI:
    """Compose the local web adapter around narrow application reads.

    Args:
        settings: Validated listener, browser, and static-asset configuration.
        profile_id: Trusted single-owner scope, never accepted from a request.
        reporting: Read-only application service for dashboard facts.
        mutations: Browser adapter over audited, replay-safe finance use cases.
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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """Hide parser internals behind one stable validation problem."""

        del error
        return _problem(
            status=422,
            title="Richiesta non valida",
            detail="Controlla i campi e le intestazioni della richiesta.",
            code="request_invalid",
            instance=request.url.path,
        )

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

    @router.get("/transactions", response_model=TransactionRegisterResponse)
    def transactions(period: str) -> TransactionRegisterResponse | JSONResponse:
        """Return all movement states for one trusted-profile month."""

        parsed = _parse_period(period)
        if parsed is None:
            return _problem(
                status=422,
                title="Periodo non valido",
                detail="Il periodo deve usare il formato YYYY-MM.",
                code="period_invalid",
                instance=f"{API_V1_PREFIX}/transactions",
            )
        try:
            result = reporting.transaction_register(
                profile_id=profile_id,
                period=parsed,
            )
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/transactions")
        return TransactionRegisterResponse.from_application(result)

    @router.post(
        "/expenses",
        response_model=ExpenseMutationResponse,
        status_code=201,
    )
    async def create_expense(
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Create one manual draft through the existing finance service."""

        try:
            payload = ExpenseCreateRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(f"{API_V1_PREFIX}/expenses")
        return _execute_mutation(
            lambda: mutations.create(
                profile_id=profile_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/expenses",
        )

    @router.put("/expenses/{expense_id}", response_model=ExpenseMutationResponse)
    async def replace_expense(
        expense_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Correct one movement under optimistic concurrency."""

        try:
            payload = ExpenseReplaceRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(f"{API_V1_PREFIX}/expenses/{expense_id}")
        return _execute_mutation(
            lambda: mutations.replace(
                profile_id=profile_id,
                expense_id=expense_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/expenses/{expense_id}",
        )

    @router.post(
        "/expenses/{expense_id}/post",
        response_model=ExpenseMutationResponse,
    )
    def post_expense(
        expense_id: UUID,
        request: ExpensePostRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Register one reconciled draft under its observed version."""

        return _execute_mutation(
            lambda: mutations.post(
                profile_id=profile_id,
                expense_id=expense_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/expenses/{expense_id}/post",
        )

    @router.post(
        "/expenses/{expense_id}/cancel",
        response_model=ExpenseMutationResponse,
    )
    def cancel_expense(
        expense_id: UUID,
        request: ExpenseCancelRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Discard a draft or void a posted movement without deletion."""

        return _execute_mutation(
            lambda: mutations.cancel(
                profile_id=profile_id,
                expense_id=expense_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/expenses/{expense_id}/cancel",
        )

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


def _execute_mutation(
    operation: Callable[[], Expense],
    *,
    command_key: str,
    instance: str,
) -> ExpenseMutationResponse | JSONResponse:
    """Run one use case and map typed failures to bounded HTTP problems."""

    if not command_key.strip():
        return _problem(
            status=422,
            title="Chiave idempotente non valida",
            detail="Idempotency-Key non può essere vuota.",
            code="idempotency_key_invalid",
            instance=instance,
        )
    try:
        result = operation()
    except AggregateNotFoundError:
        return _problem(
            status=404,
            title="Movimento non trovato",
            detail="Il movimento richiesto non esiste.",
            code="expense_not_found",
            instance=instance,
        )
    except (IdempotencyConflictError, OptimisticConcurrencyError):
        return _problem(
            status=409,
            title="Conflitto sul movimento",
            detail="Ricarica i dati prima di riprovare.",
            code="expense_conflict",
            instance=instance,
        )
    except DomainError:
        return _problem(
            status=422,
            title="Movimento non valido",
            detail="Controlla importo, allocazioni e stato del movimento.",
            code="expense_invalid",
            instance=instance,
        )
    except PsycopgError:
        return _database_problem(instance)
    return ExpenseMutationResponse.from_domain(result)


def _database_problem(instance: str) -> JSONResponse:
    """Return the shared safe dependency failure."""

    return _problem(
        status=503,
        title="Dati non disponibili",
        detail="Il database dell'applicazione non è disponibile.",
        code="database_unavailable",
        instance=instance,
    )


def _invalid_body(instance: str) -> JSONResponse:
    """Return one bounded strict-JSON validation failure."""

    return _problem(
        status=422,
        title="Richiesta non valida",
        detail="Controlla i campi JSON della richiesta.",
        code="request_invalid",
        instance=instance,
    )


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
    @app.get("/movimenti", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Return the compiled single-page application shell."""

        return FileResponse(index)


__all__ = ["DashboardReader", "create_app"]
