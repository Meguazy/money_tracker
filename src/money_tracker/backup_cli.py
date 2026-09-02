"""Command-line entry point for encrypted authoritative backup operations."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from money_tracker.backup import AuthoritativeBackupService, BackupSettings


def _parser() -> argparse.ArgumentParser:
    """Build the backup administration parser."""

    parser = argparse.ArgumentParser(
        description="Create and verify encrypted Money Tracker backups"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="initialize one empty Restic repository")
    subparsers.add_parser("create", help="create and verify a backup snapshot")
    subparsers.add_parser("status", help="report the newest snapshot and its age")
    restore = subparsers.add_parser(
        "restore-drill",
        help="restore a snapshot into explicitly isolated empty targets",
    )
    restore.add_argument("--snapshot-id", required=True)
    return parser


def _required_environment(name: str) -> str:
    """Read one required restore-only environment setting."""

    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    """Run one backup operation and print only safe result metadata.

    Args:
        argv: Optional arguments used instead of process arguments.
    """

    args = _parser().parse_args(argv)
    service = AuthoritativeBackupService(BackupSettings.from_env())
    if args.command == "init":
        service.initialize_repository()
        print(json.dumps({"status": "initialized"}, sort_keys=True))
        return
    if args.command == "create":
        backup_result = service.create_backup()
        print(
            json.dumps(
                {
                    "database_name": backup_result.database_name,
                    "receipt_count": backup_result.receipt_count,
                    "snapshot_id": backup_result.snapshot_id,
                    "status": "verified",
                },
                sort_keys=True,
            )
        )
        return
    if args.command == "status":
        health = service.latest_snapshot()
        if health is None:
            print(json.dumps({"status": "no-snapshot"}, sort_keys=True))
            return
        print(
            json.dumps(
                {
                    "age_hours": round(health.age_hours, 2),
                    "created_at": health.created_at.isoformat(),
                    "snapshot_id": health.snapshot_id,
                    "status": "present",
                },
                sort_keys=True,
            )
        )
        return
    if args.command == "restore-drill":
        target_root = Path(_required_environment("MONEY_TRACKER_RESTORE_RECEIPT_ROOT"))
        restore_result = service.restore_snapshot(
            args.snapshot_id,
            target_database_url=_required_environment(
                "MONEY_TRACKER_RESTORE_DATABASE_URL"
            ),
            target_database_name=_required_environment(
                "MONEY_TRACKER_RESTORE_EXPECTED_DATABASE"
            ),
            target_receipt_root=target_root,
        )
        print(
            json.dumps(
                {
                    "database_name": restore_result.database_name,
                    "receipt_count": restore_result.receipt_count,
                    "snapshot_id": restore_result.snapshot_id,
                    "status": "restored-and-verified",
                },
                sort_keys=True,
            )
        )
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
