"""Real PostgreSQL and filesystem checks for restored-data verification."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from psycopg import connect

from money_tracker.application import ReceiptMetadataService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.backup_cli import main as backup_main
from money_tracker.domain import Profile, ReceiptMetadata, initial_taxonomy
from money_tracker.media import LocalReceiptMediaStore
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.recovery import (
    RestoredDataVerifier,
    RestoreIntegrityError,
    RestoreIntegritySettings,
)


def _png() -> bytes:
    """Create a small decodable retained-media fixture."""

    output = io.BytesIO()
    Image.new("RGB", (6, 4), color=(235, 235, 235)).save(output, format="PNG")
    return output.getvalue()


def _restored_fixture(database_url: str, receipt_root: Path) -> ReceiptMetadata:
    """Create restorable state through production repositories and media adapters."""

    upgrade_database(database_url)
    repository = PostgresFinanceRepository(database_url)
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=datetime.now(UTC),
    )
    stored = LocalReceiptMediaStore(receipt_root).store(_png())
    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=profile.id,
        content_sha256=stored.content_sha256,
        media_type=stored.media_type,
        byte_size=stored.byte_size,
        stored_at=datetime.now(UTC),
        source_reference="integration:restore-integrity",
        original_filename="restore.png",
    )
    ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    ).register(
        metadata,
        actor="integration",
        reason="restore integrity fixture",
    )
    return metadata


@pytest.mark.postgres
def test_restore_integrity_verifies_real_database_media_and_cli(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify safe counts through the service and production operator command."""

    receipt_root = tmp_path / "restored-receipts"
    metadata = _restored_fixture(postgres_url, receipt_root)
    settings = RestoreIntegritySettings(
        database_url=postgres_url,
        expected_database="money_tracker_test",
        receipt_root=receipt_root,
    )

    result = RestoredDataVerifier(settings).verify()

    assert result.database_name == "money_tracker_test"
    assert result.alembic_revision == "20260923_0008"
    assert result.active_profile_count == 1
    assert result.default_source_count == 0
    assert result.expense_count == 0
    assert result.receipt_count == 1
    assert result.idempotency_record_count == 0
    assert result.inbox_count == 0
    assert result.outbox_count == 0
    assert result.pending_action_count == 0

    monkeypatch.setenv("MONEY_TRACKER_RESTORE_DATABASE_URL", postgres_url)
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_EXPECTED_DATABASE", "money_tracker_test")
    monkeypatch.setenv("MONEY_TRACKER_RESTORE_RECEIPT_ROOT", str(receipt_root))
    backup_main(["verify-restore"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert payload["status"] == "restore-integrity-verified"
    assert payload["receipt_count"] == 1
    assert str(receipt_root) not in output
    assert metadata.content_sha256 not in output


@pytest.mark.postgres
def test_restore_integrity_rejects_missing_media_and_profile_default_drift(
    postgres_url: str,
    tmp_path: Path,
) -> None:
    """Fail closed for storage loss and cross-record configuration drift."""

    receipt_root = tmp_path / "restored-receipts"
    metadata = _restored_fixture(postgres_url, receipt_root)
    verifier = RestoredDataVerifier(
        RestoreIntegritySettings(
            database_url=postgres_url,
            expected_database="money_tracker_test",
            receipt_root=receipt_root,
        )
    )
    receipt_path = receipt_root / metadata.content_sha256[:2] / metadata.content_sha256
    receipt_path.unlink()

    with pytest.raises(RestoreIntegrityError) as missing_media:
        verifier.verify()
    assert missing_media.value.reason_code == "receipt_file_unavailable"

    LocalReceiptMediaStore(receipt_root).store(_png())
    source_id = uuid4()
    with connect(postgres_url) as connection:
        profile_id = connection.execute(
            "SELECT id FROM finance.profiles WHERE active"
        ).fetchone()
        assert profile_id is not None
        now = datetime.now(UTC)
        connection.execute(
            """
            INSERT INTO finance.funding_sources (
                id, profile_id, name, normalized_name, funding_class,
                balance_mode, status, is_default, version, created_at, updated_at
            ) VALUES (%s, %s, 'Carta', 'carta', 'personal_funds',
                      'untracked', 'active', true, 1, %s, %s)
            """,
            (source_id, profile_id[0], now, now),
        )

    with pytest.raises(RestoreIntegrityError) as default_drift:
        verifier.verify()
    assert default_drift.value.reason_code == "profile_default_source_invalid"
