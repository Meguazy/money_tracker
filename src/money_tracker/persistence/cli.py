"""Administrative CLI for application migrations and checkpoint bootstrap."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from alembic import command

from money_tracker.persistence.database import (
    _alembic_config,
    bootstrap_checkpointer,
    database_url_from_env,
    downgrade_database,
    upgrade_database,
)
from money_tracker.structured_logging import configure_logging, get_logger

_LOGGER = get_logger("money_tracker.persistence.cli")


def _parser() -> argparse.ArgumentParser:
    """Build the database administration argument parser.

    Returns:
        Parser containing only Step 1 foundation operations.
    """

    parser = argparse.ArgumentParser(description="Manage application PostgreSQL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("upgrade", help="apply migrations to head")
    subparsers.add_parser(
        "bootstrap", help="apply migrations and initialize LangGraph checkpoints"
    )
    subparsers.add_parser("current", help="show the current migration revision")
    downgrade = subparsers.add_parser(
        "downgrade-foundation",
        help="remove empty Step 1 schemas; refuses non-empty schemas",
    )
    downgrade.add_argument(
        "--confirm-empty-foundation",
        action="store_true",
        help="confirm that no persistent application/checkpoint data exists",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one database administration operation.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: If destructive-foundation confirmation is absent.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    database_url = database_url_from_env()
    _LOGGER.info("database.command.started", command=args.command)

    if args.command == "upgrade":
        upgrade_database(database_url)
        return
    if args.command == "bootstrap":
        upgrade_database(database_url)
        bootstrap_checkpointer(database_url)
        return
    if args.command == "current":
        command.current(_alembic_config(database_url), verbose=True)
        return
    if args.command == "downgrade-foundation":
        if not args.confirm_empty_foundation:
            _LOGGER.error("database.downgrade.unconfirmed")
            raise SystemExit(
                "--confirm-empty-foundation is required; non-empty schemas are not "
                "removed by this migration"
            )
        # A downgrade removes schemas, so it is worth a record of its own
        # even though the command already refuses a non-empty foundation.
        _LOGGER.warning("database.downgrade.confirmed", revision="base")
        downgrade_database(database_url, "base")
        return
    _LOGGER.error("database.command.unsupported", command=args.command)
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
