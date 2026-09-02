"""Tests for content-addressed receipt metadata registration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import ReceiptMetadataService
from money_tracker.domain import AuditEvent, ReceiptMetadata, ReceiptStatus


@dataclass
class _ReceiptRepository:
    """In-memory implementation preserving content-addressed idempotency."""

    receipts: dict[tuple[UUID, str], ReceiptMetadata] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)

    def create_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        audit_event: AuditEvent,
    ) -> ReceiptMetadata:
        """Create once per profile and digest, auditing only the first write."""

        key = (metadata.profile_id, metadata.content_sha256)
        existing = self.receipts.get(key)
        if existing is not None:
            return existing
        self.receipts[key] = metadata
        self.audits.append(audit_event)
        return metadata


@dataclass
class _Clock:
    """Return one deterministic audit instant."""

    value: datetime = datetime(2026, 9, 2, 21, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the configured aware instant."""

        return self.value


@dataclass
class _Ids:
    """Yield prepared audit identifiers."""

    values: list[UUID] = field(default_factory=lambda: [uuid4(), uuid4()])

    def new(self) -> UUID:
        """Return and consume the next identifier."""

        return self.values.pop(0)


def _metadata() -> ReceiptMetadata:
    """Build safe metadata for a separately stored PNG receipt."""

    return ReceiptMetadata(
        id=uuid4(),
        profile_id=uuid4(),
        content_sha256="a" * 64,
        media_type="image/png",
        byte_size=2048,
        stored_at=datetime(2026, 9, 2, 20, 45, tzinfo=UTC),
        source_reference="api:receipt:command-1",
        original_filename="scontrino.png",
    )


def test_register_persists_initial_metadata_with_matching_audit() -> None:
    """Audit only safe metadata and never media bytes or filesystem paths."""

    repository = _ReceiptRepository()
    ids = _Ids()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=ids)
    metadata = _metadata()

    result = service.register(
        metadata,
        actor="receipt-store",
        reason="validated content stored",
    )

    assert result is metadata
    assert list(repository.receipts.values()) == [metadata]
    audit = repository.audits[0]
    assert audit.aggregate_type == "receipt"
    assert audit.aggregate_id == metadata.id
    assert audit.source_reference == metadata.source_reference
    assert audit.action == "create"
    assert audit.current_version == 1


def test_register_identical_digest_returns_first_record_and_audits_once() -> None:
    """Treat a repeated content digest as a natural idempotency key."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    first = _metadata()
    service.register(first, actor="receipt-store", reason="first upload")
    retry = replace(
        first,
        id=uuid4(),
        source_reference="api:receipt:command-2",
        original_filename="copia.png",
    )

    result = service.register(retry, actor="receipt-store", reason="retry upload")

    assert result == first
    assert len(repository.receipts) == 1
    assert len(repository.audits) == 1


@pytest.mark.parametrize(
    "status",
    [ReceiptStatus.NEEDS_REVIEW, ReceiptStatus.APPROVED, ReceiptStatus.DELETED],
)
def test_register_rejects_later_lifecycle_states_without_writes(
    status: ReceiptStatus,
) -> None:
    """Reserve review and deletion transitions for later evaluated workflows."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())

    with pytest.raises(ValueError, match="must be stored"):
        service.register(
            replace(_metadata(), status=status),
            actor="invalid-caller",
            reason="attempted pre-transition",
        )

    assert repository.receipts == {}
    assert repository.audits == []
