"""Production entry point for the durable Telegram gateway."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.telegram.config import TelegramSettings
from money_tracker.telegram.gateway import TelegramGateway
from money_tracker.telegram.ingestion import TelegramUpdateIngestor
from money_tracker.telegram.transport import BotApiClient, TelegramTransportError


class _SystemClock:
    """Aware UTC system clock for the production gateway."""

    def now(self) -> datetime:
        """Return the current UTC instant.

        Returns:
            A timezone-aware UTC timestamp.
        """

        return datetime.now(UTC)


def _parser() -> argparse.ArgumentParser:
    """Build the production gateway command parser."""

    parser = argparse.ArgumentParser(description="Run the durable Telegram gateway")
    parser.add_argument(
        "command",
        choices=("run", "poll-once", "validate"),
        help="validate configuration, process one batch, or run continuously",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one safely configured Telegram gateway operation.

    Args:
        argv: Optional command arguments in place of process arguments.

    Raises:
        SystemExit: If configuration, database, or transport setup fails.
    """

    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = TelegramSettings.from_env()
        repository = PostgresMessagingRepository(database_url_from_env())
        token = settings.read_token()
        with BotApiClient(token, settings) as transport:
            if args.command == "validate":
                transport.validate_bot()
                print(
                    json.dumps(
                        {
                            "environment": settings.environment.value,
                            "status": "valid",
                        },
                        sort_keys=True,
                    )
                )
                return
            gateway = TelegramGateway(
                settings=settings,
                transport=transport,
                repository=repository,
                ingestor=TelegramUpdateIngestor(repository),
                clock=_SystemClock(),
            )
            if args.command == "poll-once":
                gateway.prepare()
                report = gateway.run_once()
                print(
                    json.dumps(
                        {
                            "accepted": len(report.accepted),
                            "environment": settings.environment.value,
                            "rejected": report.rejected_count,
                            "status": "committed",
                            "unsupported": report.unsupported_count,
                        },
                        sort_keys=True,
                    )
                )
                return
            gateway.run_forever()
    except (RuntimeError, ValueError, LookupError, TelegramTransportError) as error:
        if isinstance(error, TelegramTransportError):
            reason = error.code
        else:
            reason = "configuration_or_database"
        print(f"Telegram gateway failed: {reason}", file=sys.stderr)
        raise SystemExit(1) from None
