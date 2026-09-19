"""Operator command for age-based deletion of concluded runtime content.

`money-tracker-retention` implements the owner's decision of 2026-09-19:
concluded Telegram messages, their control correlations, and terminal workflow
checkpoints are deleted 30 days after they concluded. Runtime traces have their
own command, `money-tracker-traces`, because they live in another store.

`plan` and an unconfirmed `delete` run the deletion in a transaction that is
rolled back, so both report exactly what a confirmed run would remove. Standard
output is the command's JSON result: counts, the cutoff, and flags, never
message text, workflow state, or the database URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta

from money_tracker.persistence.database import DatabaseSettings
from money_tracker.persistence.retention import (
    MINIMUM_RETENTION,
    RETENTION_DAYS,
    PostgresRuntimeRetention,
    RuntimeRetentionError,
    RuntimeRetentionResult,
)
from money_tracker.structured_logging import configure_logging, get_logger

_LOGGER = get_logger(__name__)


def _days(value: str) -> int:
    """Parse a whole number of days no shorter than the minimum window.

    Args:
        value: Command-line value.

    Returns:
        The parsed number of days.

    Raises:
        argparse.ArgumentTypeError: If the value is not a whole number of days
            at least as long as the minimum window.
    """

    minimum = MINIMUM_RETENTION.days
    if not value.isdigit() or int(value) < minimum:
        raise argparse.ArgumentTypeError(
            f"--older-than-days must be a whole number of at least {minimum}"
        )
    return int(value)


def _parser() -> argparse.ArgumentParser:
    """Build the runtime retention command parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Delete concluded Telegram messages, control correlations, and "
            "terminal workflow checkpoints older than the retention window"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "report what a retention run would delete, changing nothing"),
        ("delete", "delete concluded runtime content; dry run by default"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument(
            "--older-than-days",
            type=_days,
            default=RETENTION_DAYS,
            help=f"retention window in days (default: {RETENTION_DAYS})",
        )
    subparsers.choices["delete"].add_argument(
        "--confirm",
        action="store_true",
        help="actually delete; without it the command only reports its plan",
    )
    return parser


def render(
    command: str, retention_days: int, result: RuntimeRetentionResult
) -> dict[str, object]:
    """Render a retention outcome as the command's content-free JSON facts.

    Args:
        command: Subcommand that produced the outcome.
        retention_days: Window the cutoff was computed from.
        result: Outcome returned by the retention adapter.

    Returns:
        A JSON-compatible mapping of counts, instants, and flags.
    """

    deleted = result.deleted
    retained = result.retained
    return {
        "command": command,
        "dry_run": result.dry_run,
        "retention_days": retention_days,
        "cutoff": result.cutoff.isoformat(),
        "deleted": {
            "pending_actions": deleted.pending_actions,
            "outbox_messages": deleted.outbox_messages,
            "inbox_items": deleted.inbox_items,
            "checkpoint_threads": deleted.checkpoint_threads,
            "checkpoints": deleted.checkpoints,
            "checkpoint_blobs": deleted.checkpoint_blobs,
            "checkpoint_writes": deleted.checkpoint_writes,
        },
        "detached_outbox_messages": deleted.detached_outbox_messages,
        "retained_past_cutoff": {
            "inbox_items": retained.inbox_items,
            "outbox_messages": retained.outbox_messages,
            "pending_actions": retained.pending_actions,
            "checkpoint_threads": retained.checkpoint_threads,
        },
        "checkpoint_store_present": result.checkpoint_store_present,
        "vacuumed": result.vacuumed,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Run one runtime retention operation and print its JSON result.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With 1 when configuration, the schema, or the database
            refuses the run, and 2 when the database is unreachable or busy.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    confirm = args.command == "delete" and args.confirm
    _LOGGER.info(
        "runtime_retention.command.started",
        command=args.command,
        confirm=confirm,
        retention_days=args.older_than_days,
    )
    try:
        settings = DatabaseSettings.from_env()
    except RuntimeError:
        # The settings loader has already named the missing or invalid variable.
        print(
            "Runtime retention refused: database_configuration_invalid", file=sys.stderr
        )
        raise SystemExit(1) from None
    try:
        result = PostgresRuntimeRetention(settings.database_url).run(
            retention=timedelta(days=args.older_than_days),
            confirm=confirm,
        )
    except RuntimeRetentionError as error:
        _LOGGER.error(
            "runtime_retention.refused",
            reason_code=error.reason_code,
            transient=error.transient,
        )
        print(f"Runtime retention refused: {error.reason_code}", file=sys.stderr)
        raise SystemExit(2 if error.transient else 1) from None
    print(
        json.dumps(
            render(args.command, args.older_than_days, result),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()


__all__ = ["main", "render"]
