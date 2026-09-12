"""Production entry point for the loopback FastAPI/dashboard process."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from psycopg import Error as PsycopgError

from money_tracker.application import FinanceService, ReportingService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.logging_safety import configure_safe_logging
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.process_exit import exit_code_for, failure_reason
from money_tracker.web.app import create_app
from money_tracker.web.config import WebSettings
from money_tracker.web.mutations import ExpenseMutationAdapter


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
    configure_safe_logging()
    try:
        settings = WebSettings.from_env()
        database_url = database_url_from_env()
        repository = PostgresFinanceRepository(database_url)
        profiles = repository.list_profiles()
        if len(profiles) != 1:
            raise RuntimeError(
                "the web service requires exactly one active owner profile"
            )
        profile_id = profiles[0]
        ids = UUID4Generator()
        app = create_app(
            settings=settings,
            profile_id=profile_id,
            reporting=ReportingService(repository),
            mutations=ExpenseMutationAdapter(
                FinanceService(
                    repository,
                    clock=SystemClock(),
                    ids=ids,
                ),
                ids=ids,
            ),
            readiness_check=lambda: repository.profile_exists(profile_id),
        )
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            access_log=False,
            proxy_headers=False,
        )
    except (PsycopgError, RuntimeError, ValueError) as error:
        print(f"Web service failed: {failure_reason(error)}", file=sys.stderr)
        raise SystemExit(int(exit_code_for(error))) from None


__all__ = ["main"]
