"""Production entry point for the durable Telegram gateway."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from money_tracker.application.runtime import UUID4Generator
from money_tracker.messaging import MessagingAdministration
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository
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
        choices=("run", "poll-once", "pair", "validate"),
        help=(
            "validate configuration, create an owner pairing code, process one "
            "batch, or run continuously"
        ),
    )
    return parser


def _pair(
    settings: TelegramSettings,
    repository: PostgresMessagingRepository,
    database_url: str,
) -> dict[str, object]:
    """Create one owner pairing code and the instructions for using it.

    The clear code is printed once and never stored, so the operator has to
    use it now or generate another. It is deliberately not written to the log,
    which is why this returns it instead of logging it.

    Args:
        settings: Selected bot environment.
        repository: Durable messaging state owner.
        database_url: Application database holding the sole active profile.

    Returns:
        The code, its expiry, and what the owner must send.

    Raises:
        SystemExit: If no single active profile exists to own the binding.
    """

    profiles = PostgresFinanceRepository(database_url).list_profiles()
    if len(profiles) != 1:
        raise SystemExit(
            "pairing needs exactly one active profile; create it with "
            "money-tracker-admin before pairing"
        )
    pairing = MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    ).create_pairing_code(bot_key=settings.bot_key, profile_id=profiles[0])
    return {
        "environment": settings.environment.value,
        "expires_at": pairing.expires_at.isoformat(),
        "send_in_private_chat": f"/pair {pairing.code}",
        "status": "created",
    }


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
        database_url = database_url_from_env()
        repository = PostgresMessagingRepository(database_url)
        if args.command == "pair":
            print(json.dumps(_pair(settings, repository, database_url), indent=2))
            return
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
