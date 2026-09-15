"""Production entry point for the loopback FastAPI/dashboard process."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from psycopg import Error as PsycopgError

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
from money_tracker.messaging import MessagingAdministration, MessagingScope
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.process_exit import exit_code_for, failure_reason
from money_tracker.structured_logging import configure_logging, get_logger
from money_tracker.web.app import create_app
from money_tracker.web.config import WebSettings
from money_tracker.web.mutations import (
    BudgetMutationAdapter,
    CategoryManagementMutationAdapter,
    CategoryReviewMutationAdapter,
    ExpenseMutationAdapter,
    FundingSourceMutationAdapter,
    MessagingOperationsAdapter,
    ReceiptReviewMutationAdapter,
)

_LOGGER = get_logger("money_tracker.web.cli")


def _parser() -> argparse.ArgumentParser:
    """Build the local API command parser."""

    parser = argparse.ArgumentParser(
        description="Run the local Money Tracker API and dashboard"
    )
    parser.add_argument("command", choices=("run",), help="serve until stopped")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Validate composition and run the same-origin web service.

    Args:
        argv: Optional command arguments in place of process arguments.

    Raises:
        SystemExit: If configuration, static assets, profile, or database setup
            is invalid or unavailable.
    """

    _parser().parse_args(argv)
    configure_logging()
    try:
        settings = WebSettings.from_env()
        database_url = database_url_from_env()
        repository = PostgresFinanceRepository(database_url)
        profiles = repository.list_profiles()
        if len(profiles) != 1:
            _LOGGER.error("web.composition.rejected", profiles=len(profiles))
            raise RuntimeError(
                "the web service requires exactly one active owner profile"
            )
        profile_id = profiles[0]
        ids = UUID4Generator()
        clock = SystemClock()
        # Messaging is scoped by bot and environment rather than by profile.
        # The scope is read here, from configuration, and bound into the
        # adapter, so no HTTP request can name another bot. The web process
        # deliberately reads no Bot API credential: it inspects and requeues
        # durable rows and never speaks to Telegram.
        scope = MessagingScope.from_env()
        app = create_app(
            settings=settings,
            profile_id=profile_id,
            reporting=ReportingService(repository),
            profile_settings=ProfileSettingsService(repository),
            mutations=ExpenseMutationAdapter(
                FinanceService(
                    repository,
                    clock=clock,
                    ids=ids,
                ),
                ids=ids,
            ),
            budgets=BudgetMutationAdapter(
                FinanceAdministrationService(
                    repository,
                    clock=clock,
                    ids=ids,
                ),
                ids=ids,
            ),
            categories=CategoryManagementMutationAdapter(
                CategoryAdministrationService(
                    repository,
                    clock=clock,
                    ids=ids,
                )
            ),
            funding_sources=FundingSourceMutationAdapter(
                FundingSourceAdministrationService(
                    repository,
                    clock=clock,
                    ids=ids,
                ),
                ids=ids,
            ),
            category_reviews=CategoryReviewMutationAdapter(
                CategoryReviewService(repository, repository, clock=clock, ids=ids)
            ),
            receipts=ReceiptReviewMutationAdapter(
                ReceiptMetadataService(repository, clock=clock, ids=ids)
            ),
            messaging=MessagingOperationsAdapter(
                MessagingAdministration(
                    PostgresMessagingRepository(database_url),
                    clock=clock,
                    ids=ids,
                ),
                scope=scope,
            ),
            readiness_check=lambda: repository.profile_exists(profile_id),
        )
        _LOGGER.info(
            "web.service.starting",
            host=settings.host,
            port=settings.port,
            profile_id=profile_id,
            environment=scope.environment.value,
        )
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            access_log=False,
            proxy_headers=False,
        )
    except (PsycopgError, RuntimeError, ValueError) as error:
        _LOGGER.exception("web.service.failed", reason=failure_reason(error))
        print(f"Web service failed: {failure_reason(error)}", file=sys.stderr)
        raise SystemExit(int(exit_code_for(error))) from None


__all__ = ["main"]
