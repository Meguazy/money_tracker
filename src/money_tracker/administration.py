"""Minimal local administration entry points for active Step 2 components."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from money_tracker.application import (
    ReceiptMediaError,
    ReceiptMetadataService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import ReceiptMetadata
from money_tracker.media import LocalReceiptMediaStore, ReceiptArchiveSettings
from money_tracker.media.local import MAX_RECEIPT_BYTES
from money_tracker.persistence.database import database_url_from_env
from money_tracker.persistence.finance import PostgresFinanceRepository


def _parser() -> argparse.ArgumentParser:
    """Build the local administration parser."""

    parser = argparse.ArgumentParser(description="Manage local Money Tracker data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    receipt = subparsers.add_parser(
        "receipt-store",
        help="validate, retain, and register one original receipt",
    )
    receipt.add_argument("--profile-id", required=True, type=UUID)
    receipt.add_argument("--file", required=True, type=Path)
    receipt.add_argument("--source-reference", required=True)
    receipt.add_argument("--original-filename")
    return parser


def _read_private_input(path: Path) -> bytes:
    """Read a bounded regular input file without following a final symlink.

    Args:
        path: Owner-selected local receipt file.

    Returns:
        At most the configured receipt byte limit.

    Raises:
        ReceiptMediaError: If the path is unsafe, missing, or oversized.
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            content = handle.read(MAX_RECEIPT_BYTES + 1)
    except OSError as error:
        raise ReceiptMediaError("receipt input must be a safe regular file") from error
    if len(content) > MAX_RECEIPT_BYTES:
        raise ReceiptMediaError("receipt input exceeds the byte limit")
    return content


def _receipt_store(args: argparse.Namespace) -> dict[str, object]:
    """Store and register one receipt after validating all cheap preconditions."""

    database_url = database_url_from_env()
    repository = PostgresFinanceRepository(database_url)
    if not repository.profile_exists(args.profile_id):
        raise SystemExit("active profile does not exist")
    source_reference = args.source_reference.strip()
    if not source_reference:
        raise SystemExit("--source-reference cannot be empty")
    original_filename = args.original_filename or args.file.name
    if (
        not original_filename.strip()
        or Path(original_filename).name != original_filename
    ):
        raise SystemExit("--original-filename must be a display name, not a path")

    content = _read_private_input(args.file)
    media = LocalReceiptMediaStore(ReceiptArchiveSettings.from_env().root).store(
        content
    )
    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=args.profile_id,
        content_sha256=media.content_sha256,
        media_type=media.media_type,
        byte_size=media.byte_size,
        stored_at=datetime.now(UTC),
        source_reference=source_reference,
        original_filename=original_filename,
    )
    persisted = ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).register(
        metadata,
        actor="local-admin",
        reason="validated original stored through administration CLI",
    )
    return {
        "id": str(persisted.id),
        "content_sha256": persisted.content_sha256,
        "media_type": persisted.media_type,
        "byte_size": persisted.byte_size,
        "status": persisted.status.value,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Run a local administration command and emit non-sensitive JSON.

    Args:
        argv: Optional arguments used instead of process arguments.
    """

    args = _parser().parse_args(argv)
    if args.command == "receipt-store":
        print(json.dumps(_receipt_store(args), sort_keys=True))
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
