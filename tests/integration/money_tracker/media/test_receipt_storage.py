"""Integration tests spanning receipt filesystem storage and PostgreSQL metadata."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from psycopg import connect

from money_tracker.administration import main as administration_main
from money_tracker.application import ReceiptMediaError, ReceiptMetadataService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import Profile, ReceiptMetadata, initial_taxonomy
from money_tracker.media import LocalReceiptMediaStore
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository


def _png_receipt() -> bytes:
    """Encode a small deterministic image through the installed production decoder."""

    output = BytesIO()
    Image.new("RGB", (64, 96), color=(245, 242, 230)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.postgres
def test_store_register_retry_and_restart_round_trip(
    postgres_url: str,
    tmp_path: Path,
) -> None:
    """Join real image decoding, atomic files, metadata idempotency, and restart."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=SystemClock().now(),
    )
    archive_root = tmp_path / "private-receipts"
    media_store = LocalReceiptMediaStore(archive_root)
    content = _png_receipt()
    stored = media_store.store(content)
    service = ReceiptMetadataService(
        repository,
        clock=SystemClock(),
        ids=UUID4Generator(),
    )
    metadata = ReceiptMetadata(
        id=uuid4(),
        profile_id=profile.id,
        content_sha256=stored.content_sha256,
        media_type=stored.media_type,
        byte_size=stored.byte_size,
        stored_at=datetime(2026, 9, 2, 22, tzinfo=UTC),
        source_reference="integration:media-upload",
        original_filename="telegram-name.png",
    )

    first = service.register(
        metadata,
        actor="local-media-store",
        reason="validated original retained",
    )
    retry_stored = media_store.store(content)
    retry = service.register(
        ReceiptMetadata(
            id=uuid4(),
            profile_id=profile.id,
            content_sha256=retry_stored.content_sha256,
            media_type=retry_stored.media_type,
            byte_size=retry_stored.byte_size,
            stored_at=datetime(2026, 9, 2, 22, 1, tzinfo=UTC),
            source_reference="integration:media-retry",
            original_filename="retry.png",
        ),
        actor="local-media-store",
        reason="duplicate upload retry",
    )

    restarted_store = LocalReceiptMediaStore(archive_root)
    restarted_repository = PostgresFinanceRepository(postgres_url)
    assert retry == first
    assert restarted_store.read(first.content_sha256) == content
    assert restarted_repository.get_receipt_metadata(profile.id, first.id) == first
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.receipts"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE aggregate_type = 'receipt'"
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_invalid_media_never_creates_metadata(
    postgres_url: str,
    tmp_path: Path,
) -> None:
    """Fail at the untrusted image boundary before PostgreSQL sees a receipt."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=SystemClock().now(),
    )
    media_store = LocalReceiptMediaStore(tmp_path / "private-receipts")

    with pytest.raises(ReceiptMediaError, match="decoded"):
        media_store.store(b"not an image")

    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.receipts"
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_administration_cli_stores_registers_and_redacts_paths(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise the real CLI entry point without exposing private local paths."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    repository.bootstrap_profile(
        profile,
        taxonomy=initial_taxonomy(),
        occurred_at=SystemClock().now(),
    )
    receipt_path = tmp_path / "private-input-name.png"
    receipt_path.write_bytes(_png_receipt())
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    monkeypatch.setenv("MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT", str(archive_root))
    arguments = [
        "receipt-store",
        "--profile-id",
        str(profile.id),
        "--file",
        str(receipt_path),
        "--source-reference",
        "integration:admin-cli",
    ]

    administration_main(arguments)
    first_output = capsys.readouterr().out
    administration_main(arguments)
    second_output = capsys.readouterr().out

    first = json.loads(first_output)
    second = json.loads(second_output)
    assert first == second
    assert first["status"] == "stored"
    assert first["media_type"] == "image/png"
    assert str(receipt_path) not in first_output
    assert str(archive_root) not in first_output
    assert LocalReceiptMediaStore(archive_root).read(first["content_sha256"]) == (
        receipt_path.read_bytes()
    )
    with connect(postgres_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM finance.receipts"
        ).fetchone() == (1,)
