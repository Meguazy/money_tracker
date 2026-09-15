"""FastAPI composition for the loopback API and compiled React dashboard."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
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
    BudgetPlan,
    IdempotencyConflictError,
    MonthlyOverview,
    OptimisticConcurrencyError,
    ProfileSettings,
    RangeOverview,
    TransactionRegister,
)
from money_tracker.domain import (
    DomainError,
    PeriodGranularity,
    ResolvedPeriod,
    YearMonth,
    resolve_custom,
    resolve_day,
    resolve_iso_week,
    resolve_month,
    resolve_year,
)
from money_tracker.messaging import MessagingConflictError
from money_tracker.structured_logging import get_logger
from money_tracker.web.config import WebSettings
from money_tracker.web.contracts import (
    BudgetCreateRequest,
    BudgetMutationResponse,
    BudgetPlanResponse,
    BudgetReplaceRequest,
    CategoryArchiveRequest,
    CategoryManagementResponse,
    CategoryMutationResponse,
    CategoryRenameRequest,
    CategoryReviewDecisionRequest,
    CategoryReviewQueueResponse,
    CategoryReviewReuseRequest,
    CsrfTokenResponse,
    ExpenseCancelRequest,
    ExpenseMutationResponse,
    ExpensePostRequest,
    ExpenseReplaceRequest,
    FundingSourceArchiveRequest,
    FundingSourceCreateRequest,
    FundingSourceDefaultRequest,
    FundingSourceManagementResponse,
    FundingSourceMutationResponse,
    FundingSourceRenameRequest,
    HealthResponse,
    MessagingOperationsResponse,
    MessagingRequeueRequest,
    MessagingRequeueResponse,
    MonthlyOverviewResponse,
    ProfileSettingsResponse,
    RangeOverviewResponse,
    ReceiptMutationResponse,
    ReceiptRegisterResponse,
    ReceiptReviewRequest,
    TransactionRegisterResponse,
    WalletEntryCreateRequest,
    WalletEntryMutationResponse,
)
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    CategoryManagementMutationAdapter,
    CategoryReviewMutationAdapter,
    ExpenseMutationAdapter,
    FundingSourceMutationAdapter,
    MessagingOperationsAdapter,
    ReceiptReviewMutationAdapter,
)
from money_tracker.web.security import (
    CSRF_COOKIE_NAME,
    BrowserSecurityPolicy,
    LocalBrowserProtectionMiddleware,
    issue_csrf_token,
)

_PERIOD = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")
_DAY = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12][0-9]|3[01])$"
)
_YEAR = re.compile(r"^[0-9]{4}$")
_PERIOD_PARAMETERS: dict[PeriodGranularity, frozenset[str]] = {
    PeriodGranularity.DAY: frozenset({"day"}),
    PeriodGranularity.WEEK: frozenset({"day"}),
    PeriodGranularity.MONTH: frozenset({"month"}),
    PeriodGranularity.YEAR: frozenset({"year"}),
    PeriodGranularity.CUSTOM: frozenset({"start", "end"}),
}


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

    def budget_plan(
        self,
        *,
        profile_id: UUID,
        period: YearMonth,
    ) -> BudgetPlan:
        """Return the stored budget plan and its position for one month."""

    def range_overview(
        self,
        *,
        profile_id: UUID,
        period: ResolvedPeriod,
    ) -> RangeOverview:
        """Return exact posted facts for any resolved calendar period."""


class SettingsReader(Protocol):
    """Read only safe owner-facing profile configuration."""

    def settings(self, *, profile_id: UUID) -> ProfileSettings:
        """Return fixed V1 preferences and eligible default sources."""


_LOGGER = get_logger("money_tracker.web.app")


def create_app(
    *,
    settings: WebSettings,
    profile_id: UUID,
    reporting: DashboardReader,
    profile_settings: SettingsReader,
    mutations: ExpenseMutationAdapter,
    budgets: BudgetMutationAdapter,
    categories: CategoryManagementMutationAdapter,
    funding_sources: FundingSourceMutationAdapter,
    category_reviews: CategoryReviewMutationAdapter,
    receipts: ReceiptReviewMutationAdapter,
    messaging: MessagingOperationsAdapter,
    readiness_check: Callable[[], bool],
) -> FastAPI:
    """Compose the local web adapter around narrow application reads.

    Args:
        settings: Validated listener, browser, and static-asset configuration.
        profile_id: Trusted single-owner scope, never accepted from a request.
        reporting: Read-only application service for dashboard facts.
        profile_settings: Safe profile-preference reader bound to trusted scope.
        mutations: Browser adapter over audited, replay-safe finance use cases.
        budgets: Browser adapter over audited, replay-safe budget use cases.
        categories: Browser adapter over audited stable taxonomy management.
        funding_sources: Browser adapter over versioned source lifecycle and
            immutable wallet movements.
        category_reviews: Browser adapter over owner category decisions.
        receipts: Browser adapter over retained receipt metadata and its
            audited review transitions. It reaches no media store.
        messaging: Browser adapter over one bot's durable messaging state and
            its requeue commands. Its bot scope is bound at composition time.
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
            # The traceback is the only place the cause of an unreachable
            # database appears; the response deliberately says nothing.
            _LOGGER.exception("web.readiness.database_unavailable")
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
            return _database_problem(f"{API_V1_PREFIX}/overview/monthly")
        return MonthlyOverviewResponse.from_application(result)

    @router.get("/overview/range", response_model=RangeOverviewResponse)
    def range_overview(
        granularity: str,
        day: str | None = None,
        month: str | None = None,
        year: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> RangeOverviewResponse | JSONResponse:
        """Return exact posted facts for a day, week, month, year, or range."""

        instance = f"{API_V1_PREFIX}/overview/range"
        selection = _resolve_selection(
            granularity=granularity,
            day=day,
            month=month,
            year=year,
            start=start,
            end=end,
        )
        if isinstance(selection, _PeriodRejection):
            return _problem(
                status=422,
                title="Periodo non valido",
                detail=selection.detail,
                code=selection.code,
                instance=instance,
            )
        try:
            result = reporting.range_overview(
                profile_id=profile_id,
                period=selection,
            )
        except PsycopgError:
            return _database_problem(instance)
        return RangeOverviewResponse.from_application(result)

    @router.get("/settings", response_model=ProfileSettingsResponse)
    def profile_configuration() -> ProfileSettingsResponse | JSONResponse:
        """Return only safe owner-facing V1 profile preferences."""

        try:
            result = profile_settings.settings(profile_id=profile_id)
        except (AggregateNotFoundError, DomainError):
            return _problem(
                status=503,
                title="Impostazioni non disponibili",
                detail="La configurazione del profilo richiede assistenza.",
                code="profile_settings_invalid",
                instance=f"{API_V1_PREFIX}/settings",
            )
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/settings")
        return ProfileSettingsResponse.from_application(result)

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

    @router.get("/categories", response_model=CategoryManagementResponse)
    def category_management() -> CategoryManagementResponse | JSONResponse:
        """Return stable category nodes, versions, and complexity warnings."""

        try:
            result = categories.management(profile_id=profile_id)
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/categories")
        return CategoryManagementResponse.from_application(result)

    @router.put(
        "/categories/{category_id}",
        response_model=CategoryMutationResponse,
    )
    async def rename_category(
        category_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> CategoryMutationResponse | JSONResponse:
        """Rename one stable category without moving historical allocations."""

        instance = f"{API_V1_PREFIX}/categories/{category_id}"
        try:
            payload = CategoryRenameRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: categories.rename(
                profile_id=profile_id,
                category_id=category_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=CategoryMutationResponse.from_domain,
            problems=_CATEGORY_MANAGEMENT_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.post(
        "/categories/{category_id}/archive",
        response_model=CategoryMutationResponse,
    )
    async def archive_category(
        category_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> CategoryMutationResponse | JSONResponse:
        """Archive one node while preserving every historical reference."""

        instance = f"{API_V1_PREFIX}/categories/{category_id}/archive"
        try:
            payload = CategoryArchiveRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: categories.archive(
                profile_id=profile_id,
                category_id=category_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=CategoryMutationResponse.from_domain,
            problems=_CATEGORY_MANAGEMENT_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.get(
        "/funding-sources",
        response_model=FundingSourceManagementResponse,
    )
    def funding_source_management() -> FundingSourceManagementResponse | JSONResponse:
        """Return labels, lifecycle state, and tracked-wallet history."""

        try:
            result = funding_sources.management(profile_id=profile_id)
        except DomainError:
            return _problem(
                status=503,
                title="Fonti non disponibili",
                detail="Lo stato di un wallet richiede assistenza.",
                code="funding_source_state_invalid",
                instance=f"{API_V1_PREFIX}/funding-sources",
            )
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/funding-sources")
        return FundingSourceManagementResponse.from_application(result)

    @router.post(
        "/funding-sources",
        response_model=FundingSourceMutationResponse,
        status_code=201,
    )
    async def create_funding_source(
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> FundingSourceMutationResponse | JSONResponse:
        """Create one active payment label or tracked wallet."""

        instance = f"{API_V1_PREFIX}/funding-sources"
        try:
            payload = FundingSourceCreateRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: funding_sources.create(
                profile_id=profile_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=FundingSourceMutationResponse.from_domain,
            problems=_FUNDING_SOURCE_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.put(
        "/funding-sources/{source_id}",
        response_model=FundingSourceMutationResponse,
    )
    async def rename_funding_source(
        source_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> FundingSourceMutationResponse | JSONResponse:
        """Rename one active source without changing its financial class."""

        instance = f"{API_V1_PREFIX}/funding-sources/{source_id}"
        try:
            payload = FundingSourceRenameRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: funding_sources.rename(
                profile_id=profile_id,
                source_id=source_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=FundingSourceMutationResponse.from_domain,
            problems=_FUNDING_SOURCE_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.post(
        "/funding-sources/{source_id}/default",
        response_model=FundingSourceMutationResponse,
    )
    async def set_default_funding_source(
        source_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> FundingSourceMutationResponse | JSONResponse:
        """Move the profile default under the complete displayed state."""

        instance = f"{API_V1_PREFIX}/funding-sources/{source_id}/default"
        try:
            payload = FundingSourceDefaultRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: funding_sources.set_default(
                profile_id=profile_id,
                source_id=source_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=FundingSourceMutationResponse.from_domain,
            problems=_FUNDING_SOURCE_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.post(
        "/funding-sources/{source_id}/archive",
        response_model=FundingSourceMutationResponse,
    )
    async def archive_funding_source(
        source_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> FundingSourceMutationResponse | JSONResponse:
        """Archive one non-default source without deleting history."""

        instance = f"{API_V1_PREFIX}/funding-sources/{source_id}/archive"
        try:
            payload = FundingSourceArchiveRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: funding_sources.archive(
                profile_id=profile_id,
                source_id=source_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=FundingSourceMutationResponse.from_domain,
            problems=_FUNDING_SOURCE_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.post(
        "/funding-sources/{source_id}/wallet-entries",
        response_model=WalletEntryMutationResponse,
        status_code=201,
    )
    async def add_wallet_entry(
        source_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> WalletEntryMutationResponse | JSONResponse:
        """Append one immutable manual wallet movement."""

        instance = f"{API_V1_PREFIX}/funding-sources/{source_id}/wallet-entries"
        try:
            payload = WalletEntryCreateRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(instance)
        return _execute_mutation(
            lambda: funding_sources.add_wallet_entry(
                profile_id=profile_id,
                source_id=source_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=WalletEntryMutationResponse.from_domain,
            problems=_WALLET_PROBLEMS,
            command_key=idempotency_key,
            instance=instance,
        )

    @router.get(
        "/category-proposals",
        response_model=CategoryReviewQueueResponse,
    )
    def category_proposals() -> CategoryReviewQueueResponse | JSONResponse:
        """Return pending proposals and active deterministic reuse choices."""

        try:
            queue = category_reviews.pending(profile_id=profile_id)
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/category-proposals")
        except (AggregateNotFoundError, IdempotencyConflictError):
            return _problem(
                status=503,
                title="Revisioni non disponibili",
                detail="La correlazione di una proposta richiede assistenza.",
                code="category_review_unavailable",
                instance=f"{API_V1_PREFIX}/category-proposals",
            )
        return CategoryReviewQueueResponse.from_application(queue)

    @router.post(
        "/category-proposals/{proposal_id}/approve",
        response_model=ExpenseMutationResponse,
    )
    async def approve_category_proposal(
        proposal_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Approve a candidate and post its correlated movement atomically."""

        try:
            CategoryReviewDecisionRequest.model_validate_json(await http_request.body())
        except ValidationError:
            return _invalid_body(
                f"{API_V1_PREFIX}/category-proposals/{proposal_id}/approve"
            )
        return _execute_mutation(
            lambda: category_reviews.approve(
                profile_id=profile_id,
                proposal_id=proposal_id,
                command_key=idempotency_key.strip(),
            ),
            render=ExpenseMutationResponse.from_domain,
            problems=_CATEGORY_REVIEW_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/category-proposals/{proposal_id}/approve",
        )

    @router.post(
        "/category-proposals/{proposal_id}/use-existing",
        response_model=ExpenseMutationResponse,
    )
    async def use_existing_category(
        proposal_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Refuse a candidate and post with one named active category."""

        try:
            payload = CategoryReviewReuseRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(
                f"{API_V1_PREFIX}/category-proposals/{proposal_id}/use-existing"
            )
        return _execute_mutation(
            lambda: category_reviews.use_existing(
                profile_id=profile_id,
                proposal_id=proposal_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=ExpenseMutationResponse.from_domain,
            problems=_CATEGORY_REVIEW_PROBLEMS,
            command_key=idempotency_key,
            instance=(f"{API_V1_PREFIX}/category-proposals/{proposal_id}/use-existing"),
        )

    @router.post(
        "/category-proposals/{proposal_id}/cancel",
        response_model=ExpenseMutationResponse,
    )
    async def cancel_category_proposal(
        proposal_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ExpenseMutationResponse | JSONResponse:
        """Cancel a proposal and discard its correlated movement."""

        try:
            CategoryReviewDecisionRequest.model_validate_json(await http_request.body())
        except ValidationError:
            return _invalid_body(
                f"{API_V1_PREFIX}/category-proposals/{proposal_id}/cancel"
            )
        return _execute_mutation(
            lambda: category_reviews.cancel(
                profile_id=profile_id,
                proposal_id=proposal_id,
                command_key=idempotency_key.strip(),
            ),
            render=ExpenseMutationResponse.from_domain,
            problems=_CATEGORY_REVIEW_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/category-proposals/{proposal_id}/cancel",
        )

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
            render=ExpenseMutationResponse.from_domain,
            problems=_EXPENSE_PROBLEMS,
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
            render=ExpenseMutationResponse.from_domain,
            problems=_EXPENSE_PROBLEMS,
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
            render=ExpenseMutationResponse.from_domain,
            problems=_EXPENSE_PROBLEMS,
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
            render=ExpenseMutationResponse.from_domain,
            problems=_EXPENSE_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/expenses/{expense_id}/cancel",
        )

    @router.get("/receipts", response_model=ReceiptRegisterResponse)
    def receipt_register() -> ReceiptRegisterResponse | JSONResponse:
        """Return retained receipt metadata and its current review state."""

        try:
            stored = receipts.register(profile_id=profile_id)
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/receipts")
        return ReceiptRegisterResponse.from_application(stored)

    @router.post(
        "/receipts/{receipt_id}/review",
        response_model=ReceiptMutationResponse,
    )
    def review_receipt(
        receipt_id: UUID,
        request: ReceiptReviewRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ReceiptMutationResponse | JSONResponse:
        """Open mandatory owner review for one stored receipt."""

        return _execute_mutation(
            lambda: receipts.mark_needs_review(
                profile_id=profile_id,
                receipt_id=receipt_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            render=ReceiptMutationResponse.from_domain,
            problems=_RECEIPT_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/receipts/{receipt_id}/review",
        )

    @router.post(
        "/receipts/{receipt_id}/approve",
        response_model=ReceiptMutationResponse,
    )
    def approve_receipt(
        receipt_id: UUID,
        request: ReceiptReviewRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> ReceiptMutationResponse | JSONResponse:
        """Record explicit owner approval of one reviewed receipt."""

        return _execute_mutation(
            lambda: receipts.approve(
                profile_id=profile_id,
                receipt_id=receipt_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            render=ReceiptMutationResponse.from_domain,
            problems=_RECEIPT_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/receipts/{receipt_id}/approve",
        )

    @router.get("/messaging", response_model=MessagingOperationsResponse)
    def messaging_operations() -> MessagingOperationsResponse | JSONResponse:
        """Return the configured bot's queues, offset, and failure queues."""

        try:
            snapshot = messaging.snapshot()
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/messaging")
        return MessagingOperationsResponse.from_application(snapshot)

    @router.post(
        "/messaging/inbox/{inbox_id}/replay",
        response_model=MessagingRequeueResponse,
    )
    def replay_inbox_item(
        inbox_id: UUID,
        request: MessagingRequeueRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> MessagingRequeueResponse | JSONResponse:
        """Return one failed work item to the sequential worker's queue."""

        return _execute_mutation(
            lambda: messaging.replay_inbox(
                inbox_id=inbox_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            render=MessagingRequeueResponse.from_domain,
            problems=_MESSAGING_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/messaging/inbox/{inbox_id}/replay",
        )

    @router.post(
        "/messaging/outbox/{outbox_id}/resend",
        response_model=MessagingRequeueResponse,
    )
    def resend_outbox_item(
        outbox_id: UUID,
        request: MessagingRequeueRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> MessagingRequeueResponse | JSONResponse:
        """Start a fresh bounded delivery cycle for one undelivered response."""

        return _execute_mutation(
            lambda: messaging.resend_outbox(
                outbox_id=outbox_id,
                command_key=idempotency_key.strip(),
                request=request,
            ),
            render=MessagingRequeueResponse.from_domain,
            problems=_MESSAGING_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/messaging/outbox/{outbox_id}/resend",
        )

    @router.get("/budgets", response_model=BudgetPlanResponse)
    def budget_plan(period: str) -> BudgetPlanResponse | JSONResponse:
        """Return the stored budget plan and its position in one month."""

        parsed = _parse_period(period)
        if parsed is None:
            return _problem(
                status=422,
                title="Periodo non valido",
                detail="Il periodo deve usare il formato YYYY-MM.",
                code="period_invalid",
                instance=f"{API_V1_PREFIX}/budgets",
            )
        try:
            result = reporting.budget_plan(profile_id=profile_id, period=parsed)
        except PsycopgError:
            return _database_problem(f"{API_V1_PREFIX}/budgets")
        return BudgetPlanResponse.from_application(result)

    @router.post("/budgets", response_model=BudgetMutationResponse, status_code=201)
    async def create_budget(
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> BudgetMutationResponse | JSONResponse:
        """Attach a first budget to one active category."""

        try:
            payload = BudgetCreateRequest.model_validate_json(await http_request.body())
        except ValidationError:
            return _invalid_body(f"{API_V1_PREFIX}/budgets")
        return _execute_mutation(
            lambda: budgets.create(
                profile_id=profile_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=BudgetMutationResponse.from_domain,
            problems=_BUDGET_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/budgets",
        )

    @router.put("/budgets/{category_id}", response_model=BudgetMutationResponse)
    async def replace_budget(
        category_id: UUID,
        http_request: Request,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
    ) -> BudgetMutationResponse | JSONResponse:
        """Replace one complete budget plan under optimistic concurrency."""

        try:
            payload = BudgetReplaceRequest.model_validate_json(
                await http_request.body()
            )
        except ValidationError:
            return _invalid_body(f"{API_V1_PREFIX}/budgets/{category_id}")
        return _execute_mutation(
            lambda: budgets.replace(
                profile_id=profile_id,
                category_id=category_id,
                command_key=idempotency_key.strip(),
                request=payload,
            ),
            render=BudgetMutationResponse.from_domain,
            problems=_BUDGET_PROBLEMS,
            command_key=idempotency_key,
            instance=f"{API_V1_PREFIX}/budgets/{category_id}",
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


@dataclass(frozen=True, slots=True)
class _PeriodRejection:
    """One bounded reason a period selection was refused.

    Attributes:
        code: Stable machine-readable problem code, which is also what the
            shared problem funnel records for the refusal.
        detail: Owner-facing explanation carrying no request content.
    """

    code: str
    detail: str


_UNKNOWN_GRANULARITY = _PeriodRejection(
    code="period_granularity_invalid",
    detail="La granularità deve essere day, week, month, year o custom.",
)
_WRONG_PARAMETERS = _PeriodRejection(
    code="period_selection_invalid",
    detail="I parametri non corrispondono alla granularità richiesta.",
)
_MALFORMED_VALUE = _PeriodRejection(
    code="period_value_invalid",
    detail="Il periodo deve usare i formati YYYY-MM-DD, YYYY-MM o YYYY.",
)
_UNSUPPORTED_RANGE = _PeriodRejection(
    code="period_range_invalid",
    detail="L'intervallo deve essere ordinato e non può superare un anno.",
)


def _resolve_selection(
    *,
    granularity: str,
    day: str | None,
    month: str | None,
    year: str | None,
    start: str | None,
    end: str | None,
) -> ResolvedPeriod | _PeriodRejection:
    """Turn untrusted query parameters into one resolved calendar period.

    This adapter only parses and dispatches. Which Rome days a granularity
    covers is decided by the domain calendar policy, so daylight saving and
    ISO week numbering are not reasoned about here, and an interval that is
    reversed or wider than the supported span is refused by the same domain
    value however it was selected.

    Args:
        granularity: Requested granularity token.
        day: Calendar day selecting a day or the ISO week containing it.
        month: Calendar month token.
        year: Calendar year token.
        start: First day of a custom interval.
        end: Last day of a custom interval.

    Returns:
        The resolved period, or the bounded reason it was refused.
    """

    try:
        requested = PeriodGranularity(granularity)
    except ValueError:
        return _UNKNOWN_GRANULARITY
    supplied = {
        name
        for name, value in (
            ("day", day),
            ("month", month),
            ("year", year),
            ("start", start),
            ("end", end),
        )
        if value is not None
    }
    if supplied != _PERIOD_PARAMETERS[requested]:
        return _WRONG_PARAMETERS
    try:
        return _resolve_parsed(
            requested, day=day, month=month, year=year, start=start, end=end
        )
    except DomainError:
        # DomainError derives from ValueError, so the domain refusal is
        # matched before the parsing one or it would be reported as a
        # malformed token.
        return _UNSUPPORTED_RANGE
    except ValueError:
        return _MALFORMED_VALUE


def _resolve_parsed(
    granularity: PeriodGranularity,
    *,
    day: str | None,
    month: str | None,
    year: str | None,
    start: str | None,
    end: str | None,
) -> ResolvedPeriod:
    """Parse the tokens one granularity requires and resolve its period.

    Args:
        granularity: Already validated granularity.
        day: Calendar day token when the granularity uses one.
        month: Calendar month token when the granularity uses one.
        year: Calendar year token when the granularity uses one.
        start: First custom day token.
        end: Last custom day token.

    Returns:
        The resolved calendar period.

    Raises:
        ValueError: If a token does not use its exact accepted format or does
            not name a real calendar date.
        DomainError: If the resulting interval is not supported.
    """

    if granularity is PeriodGranularity.DAY:
        return resolve_day(_parse_day(day))
    if granularity is PeriodGranularity.WEEK:
        return resolve_iso_week(_parse_day(day))
    if granularity is PeriodGranularity.MONTH:
        parsed = _parse_period(month or "")
        if parsed is None:
            raise ValueError("month token is not a calendar month")
        return resolve_month(parsed)
    if granularity is PeriodGranularity.YEAR:
        if year is None or _YEAR.fullmatch(year) is None:
            raise ValueError("year token is not a calendar year")
        return resolve_year(int(year))
    return resolve_custom(start=_parse_day(start), end=_parse_day(end))


def _parse_day(value: str | None) -> date:
    """Parse one Rome calendar day without accepting an instant.

    Args:
        value: Query token to parse.

    Returns:
        The named calendar date.

    Raises:
        ValueError: If the token is absent, not exactly ``YYYY-MM-DD``, or
            does not name a real day such as 30 February.
    """

    if value is None or _DAY.fullmatch(value) is None:
        raise ValueError("day token is not a calendar date")
    return date.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class _MutationProblems:
    """Owner-facing wording for one mutable resource's bounded failures.

    Only the words differ between resources; the failure classes and their
    status codes are the same contract, so they are mapped in one place.

    Attributes:
        not_found: Title and detail for an identifier outside owner scope.
        conflict: Title and detail for a stale version or reused command key.
        invalid: Title and detail for a refused domain rule.
        code_prefix: Stable machine-readable problem-code subject.
    """

    not_found: tuple[str, str]
    conflict: tuple[str, str]
    invalid: tuple[str, str]
    code_prefix: str


_EXPENSE_PROBLEMS = _MutationProblems(
    not_found=("Movimento non trovato", "Il movimento richiesto non esiste."),
    conflict=("Conflitto sul movimento", "Ricarica i dati prima di riprovare."),
    invalid=(
        "Movimento non valido",
        "Controlla importo, allocazioni e stato del movimento.",
    ),
    code_prefix="expense",
)

_BUDGET_PROBLEMS = _MutationProblems(
    not_found=(
        "Budget non trovato",
        "La categoria richiesta non esiste o non ha un budget.",
    ),
    conflict=("Conflitto sul budget", "Ricarica i dati prima di riprovare."),
    invalid=(
        "Budget non valido",
        "Il budget richiede una categoria attiva e importi positivi.",
    ),
    code_prefix="budget",
)

_RECEIPT_PROBLEMS = _MutationProblems(
    not_found=(
        "Scontrino non trovato",
        "Lo scontrino richiesto non esiste o non appartiene a questo profilo.",
    ),
    conflict=(
        "Scontrino già aggiornato",
        "Ricarica gli scontrini prima di riprovare.",
    ),
    invalid=(
        "Revisione non valida",
        "Lo scontrino non si trova nello stato richiesto da questa decisione.",
    ),
    code_prefix="receipt",
)

_MESSAGING_PROBLEMS = _MutationProblems(
    not_found=(
        "Elemento non trovato",
        "L'elemento richiesto non è in coda per questo bot.",
    ),
    conflict=(
        "Coda già aggiornata",
        "Ricarica lo stato della messaggistica prima di riprovare.",
    ),
    invalid=(
        "Richiesta non valida",
        "L'elemento non si trova nello stato richiesto da questa azione.",
    ),
    code_prefix="messaging",
)

_CATEGORY_MANAGEMENT_PROBLEMS = _MutationProblems(
    not_found=(
        "Categoria non trovata",
        "La categoria richiesta non esiste o non appartiene a questo profilo.",
    ),
    conflict=(
        "Categoria già aggiornata",
        "Ricarica le categorie prima di riprovare.",
    ),
    invalid=(
        "Modifica non valida",
        "La categoria non può essere modificata o archiviata in questo stato.",
    ),
    code_prefix="category",
)


_FUNDING_SOURCE_PROBLEMS = _MutationProblems(
    not_found=(
        "Fonte non trovata",
        "La fonte richiesta non esiste o non appartiene a questo profilo.",
    ),
    conflict=(
        "Fonte già aggiornata",
        "Ricarica le fonti prima di riprovare.",
    ),
    invalid=(
        "Modifica non valida",
        "La fonte non può essere modificata in questo stato.",
    ),
    code_prefix="funding_source",
)

_WALLET_PROBLEMS = _MutationProblems(
    not_found=(
        "Wallet non trovato",
        "Il wallet richiesto non esiste o non appartiene a questo profilo.",
    ),
    conflict=(
        "Movimento wallet già registrato",
        "Ricarica il wallet prima di riprovare.",
    ),
    invalid=(
        "Movimento wallet non valido",
        "Controlla tipo, importo, data, scadenza e saldi del wallet.",
    ),
    code_prefix="wallet",
)

_CATEGORY_REVIEW_PROBLEMS = _MutationProblems(
    not_found=(
        "Proposta non trovata",
        "La proposta richiesta non esiste o non appartiene a questo profilo.",
    ),
    conflict=(
        "Proposta già decisa",
        "Ricarica le proposte prima di scegliere un'altra azione.",
    ),
    invalid=(
        "Decisione non valida",
        "La proposta o la categoria scelta non può essere applicata.",
    ),
    code_prefix="category_review",
)


def _execute_mutation[AggregateT, ResponseT](
    operation: Callable[[], AggregateT],
    *,
    render: Callable[[AggregateT], ResponseT],
    problems: _MutationProblems,
    command_key: str,
    instance: str,
) -> ResponseT | JSONResponse:
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
            title=problems.not_found[0],
            detail=problems.not_found[1],
            code=f"{problems.code_prefix}_not_found",
            instance=instance,
        )
    except (
        IdempotencyConflictError,
        MessagingConflictError,
        OptimisticConcurrencyError,
    ):
        return _problem(
            status=409,
            title=problems.conflict[0],
            detail=problems.conflict[1],
            code=f"{problems.code_prefix}_conflict",
            instance=instance,
        )
    except DomainError:
        return _problem(
            status=422,
            title=problems.invalid[0],
            detail=problems.invalid[1],
            code=f"{problems.code_prefix}_invalid",
            instance=instance,
        )
    except PsycopgError:
        return _database_problem(instance)
    _LOGGER.info("web.mutation.applied", path=instance)
    return render(result)


def _database_problem(instance: str) -> JSONResponse:
    """Return the shared safe dependency failure and record its cause.

    Every caller is inside an ``except PsycopgError`` block, so this is the one
    place a database failure of this boundary is explained. The response says
    nothing about the cause deliberately, which makes the traceback the only
    record of it.

    Args:
        instance: Route the failure concerns.

    Returns:
        The bounded dependency problem-details response.
    """

    _LOGGER.exception("web.database_unavailable", path=instance)
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
    """Render a bounded problem-details response.

    Every error this API returns is built here, so this is where each one is
    recorded. A dependency failure is an error the owner has to act on; a
    rejected request is the API working as designed and warns instead. The
    stable code and the route are recorded, never the request body, because
    the body is owner finance data.

    Args:
        status: HTTP status code.
        title: Owner-facing title.
        detail: Owner-facing explanation.
        code: Stable machine-readable problem code.
        instance: Route the problem concerns.

    Returns:
        The problem-details response to return to the browser.
    """

    if status >= 500:
        _LOGGER.error("web.request.failed", status=status, code=code, path=instance)
    else:
        _LOGGER.warning("web.request.rejected", status=status, code=code, path=instance)
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
        _LOGGER.error("web.dashboard.build_missing", static_dir=static_dir)
        raise ValueError(
            "compiled dashboard is missing; run npm ci and npm run build in frontend"
        )
    app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/analisi", include_in_schema=False)
    @app.get("/movimenti", include_in_schema=False)
    @app.get("/categorie", include_in_schema=False)
    @app.get("/fonti", include_in_schema=False)
    @app.get("/budget", include_in_schema=False)
    @app.get("/scontrini", include_in_schema=False)
    @app.get("/messaggistica", include_in_schema=False)
    @app.get("/impostazioni", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Return the compiled single-page application shell.

        Every client route is listed here, because a route the browser can
        reach by navigation but not by reload is a broken deep link rather
        than a smaller surface.
        """

        return FileResponse(index)


__all__ = ["DashboardReader", "SettingsReader", "create_app"]
