"""Real encrypted backup and isolated restore integration tests."""

from __future__ import annotations

import io
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from PIL import Image
from psycopg import connect, sql

from money_tracker.application import ReceiptMetadataService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.backup import (
    AUTHORITATIVE_TAG,
    RETENTION_ARGUMENTS,
    AuthoritativeBackupService,
    BackupError,
    BackupSettings,
)
from money_tracker.backup_cli import main as backup_main
from money_tracker.domain import Profile, ReceiptMetadata, initial_taxonomy
from money_tracker.media import LocalReceiptMediaStore
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository


@dataclass(frozen=True, slots=True)
class _Fixture:
    """Authoritative test data and private inputs owned by one drill."""

    profile: Profile
    metadata: ReceiptMetadata
    content: bytes
    archive_root: Path
    config_file: Path
    password_file: Path
    restic_binary: Path
    pg_dump_binary: Path
    pg_restore_binary: Path


def _real_tool(name: str) -> Path:
    """Return an explicit real integration executable or skip transparently."""

    if os.getenv("RUN_BACKUP_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_BACKUP_INTEGRATION_TESTS=1 for real Restic tests")
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    path = Path(value)
    if not path.is_absolute() or not os.access(path, os.X_OK):
        raise RuntimeError(f"{name} must name an executable absolute path")
    return path


def _png() -> bytes:
    """Create a real decodable receipt fixture in memory."""

    output = io.BytesIO()
    Image.new("RGB", (8, 6), color=(240, 240, 240)).save(output, format="PNG")
    return output.getvalue()


def _reset_owned_test_database(database_url: str) -> None:
    """Empty only the fixture-owned local money_tracker_test database."""

    with connect(database_url, autocommit=True) as connection:
        assert connection.execute("SELECT current_database()").fetchone() == (
            "money_tracker_test",
        )
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


def _authoritative_fixture(postgres_url: str, tmp_path: Path) -> _Fixture:
    """Migrate, seed one profile and one retained receipt, and stage secrets."""

    restic_binary = _real_tool("MONEY_TRACKER_TEST_RESTIC_BINARY")
    pg_dump_binary = _real_tool("MONEY_TRACKER_TEST_PG_DUMP_BINARY")
    pg_restore_binary = _real_tool("MONEY_TRACKER_TEST_PG_RESTORE_BINARY")
    upgrade_database(postgres_url)

    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=datetime.now(UTC),
    )
    archive_root = tmp_path / "authoritative-receipts"
    content = _png()
    stored_media = LocalReceiptMediaStore(archive_root).store(content)
    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=profile.id,
        content_sha256=stored_media.content_sha256,
        media_type=stored_media.media_type,
        byte_size=stored_media.byte_size,
        stored_at=datetime.now(UTC),
        source_reference="integration:backup-receipt",
        original_filename="receipt.png",
    )
    ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).register(
        metadata,
        actor="integration",
        reason="authoritative backup fixture",
    )

    config_file = tmp_path / "runtime.env"
    config_file.write_text("PRIVATE_TEST_VALUE=encrypted-only\n", encoding="utf-8")
    config_file.chmod(0o600)
    password_file = tmp_path / "restic-password"
    password_file.write_text("integration-recovery-secret\n", encoding="utf-8")
    password_file.chmod(0o600)
    return _Fixture(
        profile=profile,
        metadata=metadata,
        content=content,
        archive_root=archive_root,
        config_file=config_file,
        password_file=password_file,
        restic_binary=restic_binary,
        pg_dump_binary=pg_dump_binary,
        pg_restore_binary=pg_restore_binary,
    )


@pytest.mark.postgres
def test_encrypted_snapshot_restores_database_and_receipt_media(
    postgres_url: str,
    tmp_path: Path,
) -> None:
    """Reconstruct a finance fixture and media reference from real Restic."""

    fixture = _authoritative_fixture(postgres_url, tmp_path)
    settings = BackupSettings(
        database_url=postgres_url,
        expected_database="money_tracker_test",
        receipt_archive_root=fixture.archive_root,
        config_file=fixture.config_file,
        restic_repository=tmp_path / "restic-repository",
        restic_password_file=fixture.password_file,
        lock_file=tmp_path / "backup.lock",
        staging_parent=tmp_path / "staging",
        restic_binary=fixture.restic_binary,
        pg_dump_binary=fixture.pg_dump_binary,
        pg_restore_binary=fixture.pg_restore_binary,
    )
    service = AuthoritativeBackupService(settings)
    service.initialize_repository()

    assert service.latest_snapshot() is None

    backup = service.create_backup()

    assert backup.receipt_count == 1
    assert backup.database_name == "money_tracker_test"
    assert len(backup.snapshot_id) >= 8

    repeated = service.create_backup()
    grouped = json.loads(
        service._run_restic(
            "snapshots",
            "--json",
            "--tag",
            AUTHORITATIVE_TAG,
            *RETENTION_ARGUMENTS[:2],
        )
    )

    assert repeated.snapshot_id != backup.snapshot_id
    assert len(grouped) == 1
    assert len(grouped[0]["snapshots"]) == 2
    health = service.latest_snapshot()
    assert health is not None
    assert health.snapshot_id == repeated.snapshot_id
    assert 0 <= health.age_hours < 1

    _reset_owned_test_database(postgres_url)
    restored_archive = tmp_path / "isolated-restored-receipts"
    restore = service.restore_snapshot(
        backup.snapshot_id,
        target_database_url=postgres_url,
        target_database_name="money_tracker_test",
        target_receipt_root=restored_archive,
    )

    assert restore.receipt_count == 1
    restarted = PostgresFinanceRepository(postgres_url)
    assert restarted.get_receipt_metadata(fixture.profile.id, fixture.metadata.id) == (
        fixture.metadata
    )
    assert (
        LocalReceiptMediaStore(restored_archive).read(fixture.metadata.content_sha256)
        == fixture.content
    )
    restored_path = (
        restored_archive
        / fixture.metadata.content_sha256[:2]
        / fixture.metadata.content_sha256
    )
    assert stat.S_IMODE(restored_archive.stat().st_mode) == 0o700
    assert stat.S_IMODE(restored_path.stat().st_mode) == 0o600

    with pytest.raises(BackupError, match="restore receipt target must be empty"):
        service.restore_snapshot(
            backup.snapshot_id,
            target_database_url=postgres_url,
            target_database_name="money_tracker_test",
            target_receipt_root=restored_archive,
        )
    with pytest.raises(BackupError, match="database must be an empty isolated target"):
        service.restore_snapshot(
            backup.snapshot_id,
            target_database_url=postgres_url,
            target_database_name="money_tracker_test",
            target_receipt_root=tmp_path / "second-empty-target",
        )


@pytest.mark.postgres
def test_backup_cli_drill_reports_only_safe_metadata(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the documented operator commands without printing private inputs."""

    fixture = _authoritative_fixture(postgres_url, tmp_path)
    restic_repository = tmp_path / "cli-restic-repository"
    restored_archive = tmp_path / "cli-restored-receipts"
    for name, value in {
        "MONEY_TRACKER_DATABASE_URL": postgres_url,
        "MONEY_TRACKER_BACKUP_EXPECTED_DATABASE": "money_tracker_test",
        "MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT": str(fixture.archive_root),
        "MONEY_TRACKER_BACKUP_CONFIG_FILE": str(fixture.config_file),
        "MONEY_TRACKER_RESTIC_REPOSITORY": str(restic_repository),
        "MONEY_TRACKER_RESTIC_PASSWORD_FILE": str(fixture.password_file),
        "MONEY_TRACKER_BACKUP_LOCK_FILE": str(tmp_path / "cli-backup.lock"),
        "MONEY_TRACKER_BACKUP_STAGING_PARENT": str(tmp_path / "cli-staging"),
        "MONEY_TRACKER_RESTIC_BINARY": str(fixture.restic_binary),
        "MONEY_TRACKER_PG_DUMP_BINARY": str(fixture.pg_dump_binary),
        "MONEY_TRACKER_PG_RESTORE_BINARY": str(fixture.pg_restore_binary),
        "MONEY_TRACKER_RESTORE_DATABASE_URL": postgres_url,
        "MONEY_TRACKER_RESTORE_EXPECTED_DATABASE": "money_tracker_test",
        "MONEY_TRACKER_RESTORE_RECEIPT_ROOT": str(restored_archive),
    }.items():
        monkeypatch.setenv(name, value)

    backup_main(["init"])
    assert json.loads(capsys.readouterr().out) == {"status": "initialized"}
    backup_main(["status"])
    assert json.loads(capsys.readouterr().out) == {"status": "no-snapshot"}

    backup_main(["create"])
    created_output = capsys.readouterr().out
    backup_main(["status"])
    status_output = capsys.readouterr().out
    created = json.loads(created_output)
    status = json.loads(status_output)

    assert created["status"] == "verified"
    assert created["receipt_count"] == 1
    assert created["database_name"] == "money_tracker_test"
    assert status["status"] == "present"
    assert status["snapshot_id"] == created["snapshot_id"]
    assert 0 <= status["age_hours"] < 1

    _reset_owned_test_database(postgres_url)
    backup_main(["restore-drill", "--snapshot-id", created["snapshot_id"]])
    restored_output = capsys.readouterr().out
    restored = json.loads(restored_output)

    assert restored == {
        "database_name": "money_tracker_test",
        "receipt_count": 1,
        "snapshot_id": created["snapshot_id"],
        "status": "restored-and-verified",
    }
    assert PostgresFinanceRepository(postgres_url).get_receipt_metadata(
        fixture.profile.id, fixture.metadata.id
    ) == (fixture.metadata)
    assert (
        LocalReceiptMediaStore(restored_archive).read(fixture.metadata.content_sha256)
        == fixture.content
    )

    combined = created_output + status_output + restored_output
    secret = urlparse(postgres_url).password
    assert secret
    for private in (
        secret,
        "integration-recovery-secret",
        str(fixture.archive_root),
        str(fixture.config_file),
        str(fixture.password_file),
        str(restic_repository),
        str(restored_archive),
    ):
        assert private not in combined
