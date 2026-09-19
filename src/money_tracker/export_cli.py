"""Operator CLI for portable authoritative finance export."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from money_tracker.persistence.database import DatabaseSettings
from money_tracker.persistence.export import (
    FORMAT_VERSION,
    FinanceExportError,
    PostgresFinanceExporter,
)
from money_tracker.structured_logging import configure_logging, get_logger, log_context

_LOGGER = get_logger(__name__)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _profile_id(value: str) -> UUID:
    """Parse a stable profile identifier.

    Args:
        value: Command-line value.

    Returns:
        Parsed UUID.

    Raises:
        argparse.ArgumentTypeError: If the value is not a UUID.
    """

    try:
        return UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--profile-id must be a UUID") from None


def _parser() -> argparse.ArgumentParser:
    """Build the finance export parser."""

    parser = argparse.ArgumentParser(
        description="Export one profile's authoritative finance records"
    )
    parser.add_argument("--profile-id", type=_profile_id, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="absolute new JSONL file in a private directory outside the checkout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Write one private finance export and print only safe result metadata.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With 1 for a refused export or unsupported database state,
            and 2 for a transient database failure.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    with log_context(profile_id=args.profile_id):
        _LOGGER.info("finance_export.command.started")
        try:
            settings = DatabaseSettings.from_env()
            exporter = PostgresFinanceExporter(settings.database_url)
            result = exporter.export(
                args.profile_id,
                args.output,
                forbidden_root=_REPOSITORY_ROOT,
            )
        except FinanceExportError as error:
            _LOGGER.error(
                "finance_export.refused",
                reason_code=error.reason_code,
                transient=error.transient,
            )
            print(f"Finance export refused: {error.reason_code}", file=sys.stderr)
            raise SystemExit(2 if error.transient else 1) from None
    print(
        json.dumps(
            {
                "byte_count": result.byte_count,
                "format_version": FORMAT_VERSION,
                "profile_id": str(result.profile_id),
                "record_count": result.record_count,
                "record_counts": result.record_counts,
                "sha256": result.sha256,
                "status": "exported",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()


__all__ = ["main"]
