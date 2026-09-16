"""Production entry point for the durable Telegram gateway.

`run` and `poll-once` ingest and deliver, so they open the tracer the switch
selects and flush it within the export bound once the loops have stopped. The
operator commands touch no owner message and stay untraced.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Error as PsycopgError

from money_tracker.application.runtime import UUID4Generator
from money_tracker.messaging import (
    TELEGRAM_UPDATE_RETENTION,
    MessagingAdministration,
    downtime_gap,
)
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.messaging import PostgresMessagingRepository
from money_tracker.process_exit import (
    exit_code_for,
    failure_reason,
    shutdown_event,
)
from money_tracker.structured_logging import (
    configure_logging,
    get_logger,
    log_context,
)
from money_tracker.telegram.config import TelegramSettings
from money_tracker.telegram.gateway import TelegramGateway
from money_tracker.telegram.ingestion import TelegramUpdateIngestor
from money_tracker.telegram.transport import BotApiClient
from money_tracker.tracing import DISABLED_TRACER, Tracer, open_tracer

_LOGGER = get_logger("money_tracker.telegram.cli")


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
        choices=(
            "run",
            "poll-once",
            "pair",
            "health",
            "latency",
            "dead-letters",
            "replay",
            "resend",
            "validate",
        ),
        help=(
            "validate configuration, create an owner pairing code, report "
            "health, list or replay dead letters, process one batch, or run "
            "continuously"
        ),
    )
    parser.add_argument(
        "--inbox-id",
        type=UUID,
        help="dead letter to return to the queue, required by replay",
    )
    parser.add_argument(
        "--outbox-id",
        type=UUID,
        help="delivery dead letter to return to the queue, required by resend",
    )
    parser.add_argument(
        "--since",
        type=_aware_datetime,
        help="inclusive aware ISO-8601 start time, required by latency",
    )
    return parser


def _aware_datetime(value: str) -> datetime:
    """Parse one timezone-aware ISO-8601 command argument.

    Args:
        value: User-supplied timestamp, optionally ending in Z.

    Returns:
        Parsed aware datetime.

    Raises:
        argparse.ArgumentTypeError: If the value is invalid or timezone-naive.
    """

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--since must be an aware ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--since must be an aware ISO-8601 timestamp")
    return parsed


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
    ).create_pairing_code(
        bot_key=settings.bot_key,
        profile_id=profiles[0],
        environment=settings.environment,
    )
    return {
        "environment": settings.environment.value,
        "expires_at": pairing.expires_at.isoformat(),
        "send_in_private_chat": f"/pair {pairing.code}",
        "status": "created",
    }


def _health(
    settings: TelegramSettings,
    repository: PostgresMessagingRepository,
) -> dict[str, object]:
    """Report queue, offset, and downtime state without message content."""

    now = _SystemClock().now()
    health = MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    ).health(settings.bot_key)
    gap = downtime_gap(health, now=now)
    return {
        "active_actions": health.active_actions,
        "dead_letters": {
            "inbox": health.inbox_dead_letters,
            "outbox": health.outbox_dead_letters,
        },
        "downtime_exceeds_update_retention": (
            gap is not None and gap > TELEGRAM_UPDATE_RETENTION
        ),
        "environment": settings.environment.value,
        "inbox": {
            "pending": health.pending_inbox,
            "retrying": health.retrying_inbox,
            "oldest_at": (
                None
                if health.oldest_inbox_at is None
                else health.oldest_inbox_at.isoformat()
            ),
        },
        "last_poll_at": (
            None if health.last_poll_at is None else health.last_poll_at.isoformat()
        ),
        "next_offset": health.next_offset,
        "outbox": {
            "pending": health.pending_outbox,
            "retrying": health.retrying_outbox,
            "oldest_at": (
                None
                if health.oldest_outbox_at is None
                else health.oldest_outbox_at.isoformat()
            ),
        },
        "owner_paired": repository.owner_binding(settings.bot_key) is not None,
    }


def _latency(
    settings: TelegramSettings,
    repository: PostgresMessagingRepository,
    since: datetime | None,
) -> dict[str, object]:
    """Report content-free warm text delivery latency.

    Raises:
        SystemExit: If the sample window has no explicit start.
    """

    if since is None:
        raise SystemExit("latency requires --since with an aware ISO-8601 timestamp")
    report = MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    ).text_latency_report(settings.bot_key, since=since)
    return {
        "environment": settings.environment.value,
        "maximum_ms": report.maximum_ms,
        "measurement": "accepted_at_to_bot_api_send_commit",
        "sample_count": report.sample_count,
        "warm_p95_ms": report.warm_p95_ms,
        "warm_sample_count": report.warm_sample_count,
        "warmup_excluded": report.warmup_excluded,
    }


def _dead_letters(
    settings: TelegramSettings,
    repository: PostgresMessagingRepository,
) -> dict[str, object]:
    """List failed work items with identity and reason but no content."""

    administration = MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    )
    items = administration.dead_letters(settings.bot_key)
    outbox_items = administration.outbox_dead_letters(settings.bot_key)
    return {
        "dead_letters": [
            {
                "accepted_at": item.accepted_at.isoformat(),
                "attempt_count": item.attempt_count,
                "inbox_id": str(item.id),
                "last_error_code": item.last_error_code,
                "replay_count": item.replay_count,
                "source_reference": item.source_reference,
                "update_id": item.update_id,
            }
            for item in items
        ],
        "outbox_dead_letters": [
            {
                "attempt_count": item.attempt_count,
                "created_at": item.created_at.isoformat(),
                "last_error_code": item.last_error_code,
                "outbox_id": str(item.id),
                "replay_count": item.replay_count,
                "response_kind": item.response_kind,
            }
            for item in outbox_items
        ],
        "environment": settings.environment.value,
    }


def _replay(
    bot_key: str,
    repository: PostgresMessagingRepository,
    inbox_id: UUID | None,
) -> dict[str, object]:
    """Return one operator-selected dead letter to the queue.

    Raises:
        SystemExit: If no dead letter was named.
    """

    if inbox_id is None:
        raise SystemExit("replay requires --inbox-id from the dead-letters command")
    MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    ).replay(bot_key, inbox_id)
    return {"inbox_id": str(inbox_id), "status": "queued"}


def _resend(
    bot_key: str,
    repository: PostgresMessagingRepository,
    outbox_id: UUID | None,
) -> dict[str, object]:
    """Return one operator-selected delivery dead letter to the queue.

    Raises:
        SystemExit: If no delivery dead letter was named.
    """

    if outbox_id is None:
        raise SystemExit("resend requires --outbox-id from the dead-letters command")
    MessagingAdministration(
        repository,
        clock=_SystemClock(),
        ids=UUID4Generator(),
    ).resend(bot_key, outbox_id)
    return {"outbox_id": str(outbox_id), "status": "queued"}


def main(argv: Sequence[str] | None = None) -> None:
    """Run one safely configured Telegram gateway operation.

    Args:
        argv: Optional command arguments in place of process arguments.

    Raises:
        SystemExit: If configuration, database, or transport setup fails.
    """

    args = _parser().parse_args(argv)
    configure_logging()
    tracer: Tracer = DISABLED_TRACER
    try:
        settings = TelegramSettings.from_env()
        database_url = database_url_from_env()
        repository = PostgresMessagingRepository(database_url)
        with log_context(command=args.command, environment=settings.environment.value):
            _LOGGER.info("telegram.command.started")
        if args.command == "pair":
            print(json.dumps(_pair(settings, repository, database_url), indent=2))
            return
        if args.command == "health":
            print(json.dumps(_health(settings, repository), indent=2))
            return
        if args.command == "latency":
            print(json.dumps(_latency(settings, repository, args.since), indent=2))
            return
        if args.command == "dead-letters":
            print(json.dumps(_dead_letters(settings, repository), indent=2))
            return
        if args.command == "replay":
            print(
                json.dumps(
                    _replay(settings.bot_key, repository, args.inbox_id), indent=2
                )
            )
            return
        if args.command == "resend":
            print(
                json.dumps(
                    _resend(settings.bot_key, repository, args.outbox_id), indent=2
                )
            )
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
            tracer = open_tracer()
            gateway = TelegramGateway(
                settings=settings,
                transport=transport,
                repository=repository,
                ingestor=TelegramUpdateIngestor(repository),
                clock=_SystemClock(),
                tracer=tracer,
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
            gateway.run_forever(shutdown_event())
    except (
        LookupError,
        PsycopgError,
        RuntimeError,
        ValueError,
    ) as error:
        _LOGGER.exception(
            "telegram.command.failed",
            command=args.command,
            reason=failure_reason(error),
        )
        print(f"Telegram gateway failed: {failure_reason(error)}", file=sys.stderr)
        raise SystemExit(int(exit_code_for(error))) from None
    finally:
        tracer.shutdown()
