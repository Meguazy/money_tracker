"""Tests for content-addressed receipt metadata registration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    APPROVE_ACTION,
    MARK_NEEDS_REVIEW_ACTION,
    AggregateNotFoundError,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    ReceiptMetadataService,
    receipt_review_identity,
)
from money_tracker.domain import (
    AuditEvent,
    InvalidTransitionError,
    ReceiptMetadata,
    ReceiptStatus,
)


@dataclass
class _ReceiptRepository:
    """In-memory implementation preserving content-addressed idempotency."""

    receipts: dict[tuple[UUID, str], ReceiptMetadata] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    commands: dict[tuple[UUID, str], tuple[str, str, UUID]] = field(
        default_factory=dict
    )

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

    def get_receipt_metadata(
        self,
        profile_id: UUID,
        receipt_id: UUID,
    ) -> ReceiptMetadata:
        """Load one receipt by stable identifier inside its profile."""

        for (owner_id, _), metadata in self.receipts.items():
            if owner_id == profile_id and metadata.id == receipt_id:
                return metadata
        raise AggregateNotFoundError("receipt metadata does not exist in profile")

    def list_receipt_metadata(self, profile_id: UUID) -> tuple[ReceiptMetadata, ...]:
        """Return the profile's receipts newest storage first, ties by ID."""

        return tuple(
            sorted(
                (
                    metadata
                    for (owner_id, _), metadata in self.receipts.items()
                    if owner_id == profile_id
                ),
                key=lambda item: (item.stored_at, item.id),
                reverse=True,
            )
        )

    def get_idempotent_receipt_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> ReceiptMetadata | None:
        """Return the receipt stored by an identical earlier transition."""

        prior = self.commands.get((profile_id, idempotency_key))
        if prior is None:
            return None
        if prior[:2] != (command_type, request_hash):
            raise IdempotencyConflictError(
                "idempotency key was used for different receipt content"
            )
        return self.get_receipt_metadata(profile_id, prior[2])

    def replace_receipt_metadata(
        self,
        metadata: ReceiptMetadata,
        *,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> ReceiptMetadata:
        """Claim the command and replace state at the observed version."""

        claim = (metadata.profile_id, idempotency_key)
        prior = self.commands.get(claim)
        if prior is not None:
            if prior[:2] != (command_type, request_hash):
                raise IdempotencyConflictError(
                    "idempotency key was used for different receipt content"
                )
            return self.get_receipt_metadata(metadata.profile_id, prior[2])
        key = (metadata.profile_id, metadata.content_sha256)
        current = self.receipts.get(key)
        if current is None or current.version != expected_version:
            raise OptimisticConcurrencyError(
                "receipt changed since the requested version"
            )
        self.commands[claim] = (command_type, request_hash, metadata.id)
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

    values: list[UUID] = field(default_factory=lambda: [uuid4() for _ in range(8)])

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


def test_register_rejects_noninitial_version_without_writes() -> None:
    """Require every newly registered content record to start at version one."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())

    with pytest.raises(ValueError, match="version one"):
        service.register(
            replace(_metadata(), version=2),
            actor="invalid-caller",
            reason="attempted pre-versioning",
        )

    assert repository.receipts == {}
    assert repository.audits == []
    assert repository.audits == []


def test_receipt_enters_review_then_records_explicit_owner_approval() -> None:
    """Advance the mandatory review lifecycle with matching versioned audits."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    stored = service.register(
        _metadata(),
        actor="receipt-store",
        reason="validated content stored",
    )

    review = service.mark_needs_review(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="structured draft ready",
    )
    approved = service.approve(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=2,
        actor="owner",
        reason="owner approved preview",
    )

    assert review.status is ReceiptStatus.NEEDS_REVIEW
    assert review.version == 2
    assert approved.status is ReceiptStatus.APPROVED
    assert approved.version == 3
    assert [
        (event.action, event.prior_version, event.current_version)
        for event in repository.audits
    ] == [
        ("create", None, 1),
        ("mark_needs_review", 1, 2),
        ("approve", 2, 3),
    ]


def test_receipt_cannot_be_approved_before_mandatory_review() -> None:
    """Reject a stored-to-approved shortcut without appending an audit."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    stored = service.register(
        _metadata(),
        actor="receipt-store",
        reason="validated content stored",
    )

    with pytest.raises(InvalidTransitionError, match="in review"):
        service.approve(
            profile_id=stored.profile_id,
            receipt_id=stored.id,
            expected_version=1,
            actor="owner",
            reason="attempted early approval",
        )

    assert repository.get_receipt_metadata(stored.profile_id, stored.id) == stored
    assert len(repository.audits) == 1


def test_repeating_one_review_transition_replays_its_first_result() -> None:
    """Return the committed transition instead of applying a second one."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    stored = service.register(
        _metadata(),
        actor="receipt-store",
        reason="validated content stored",
    )
    winner = service.mark_needs_review(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="first writer",
    )

    replayed = service.mark_needs_review(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="repeated transition",
    )

    assert replayed == winner
    assert repository.get_receipt_metadata(stored.profile_id, stored.id) == winner
    assert len(repository.audits) == 2


def test_another_decision_on_the_observed_version_conflicts() -> None:
    """Refuse a second decision taken from one displayed receipt state."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    stored = service.register(
        _metadata(),
        actor="receipt-store",
        reason="validated content stored",
    )
    service.mark_needs_review(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="first writer",
    )

    with pytest.raises(IdempotencyConflictError, match="different receipt"):
        service.approve(
            profile_id=stored.profile_id,
            receipt_id=stored.id,
            expected_version=1,
            actor="owner",
            reason="decision taken from a stale page",
        )

    assert (
        repository.get_receipt_metadata(stored.profile_id, stored.id).status
        is ReceiptStatus.NEEDS_REVIEW
    )
    assert len(repository.audits) == 2


def test_stale_version_refuses_a_transition_the_owner_never_saw() -> None:
    """Refuse a decision quoting a version another transition already left."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    stored = service.register(
        _metadata(),
        actor="receipt-store",
        reason="validated content stored",
    )
    service.mark_needs_review(
        profile_id=stored.profile_id,
        receipt_id=stored.id,
        expected_version=1,
        actor="receipt-workflow",
        reason="first writer",
    )

    with pytest.raises(InvalidTransitionError, match="stored"):
        service.mark_needs_review(
            profile_id=stored.profile_id,
            receipt_id=stored.id,
            expected_version=2,
            actor="receipt-workflow",
            reason="stale repetition",
        )

    assert len(repository.audits) == 2


def test_review_command_identity_names_one_transition_of_one_receipt() -> None:
    """Key the command by the receipt and the version the caller observed."""

    receipt_id = UUID("00000000-0000-0000-0000-0000000000a1")

    review = receipt_review_identity(
        receipt_id,
        1,
        action=MARK_NEEDS_REVIEW_ACTION,
    )
    approve = receipt_review_identity(receipt_id, 1, action=APPROVE_ACTION)
    later = receipt_review_identity(receipt_id, 2, action=APPROVE_ACTION)

    assert review[0] == f"receipt-review:{receipt_id}:1"
    assert review[0] == approve[0]
    assert review[1] == "receipt_mark_needs_review"
    assert review[2] != approve[2]
    assert later[0] == f"receipt-review:{receipt_id}:2"


def test_stored_receipts_are_listed_newest_first() -> None:
    """Order the archive deterministically for the control plane."""

    repository = _ReceiptRepository()
    service = ReceiptMetadataService(repository, clock=_Clock(), ids=_Ids())
    first = _metadata()
    second = replace(
        first,
        id=uuid4(),
        content_sha256="b" * 64,
        stored_at=datetime(2026, 9, 3, 8, tzinfo=UTC),
        source_reference="api:receipt:command-2",
    )
    service.register(first, actor="receipt-store", reason="first")
    service.register(second, actor="receipt-store", reason="second")

    listed = service.stored_receipts(profile_id=first.profile_id)

    assert [item.id for item in listed] == [second.id, first.id]


def test_receipt_repository_rejects_stale_version_without_audit() -> None:
    """Exercise the port's optimistic compare-and-swap contract directly."""

    repository = _ReceiptRepository()
    stored = _metadata()
    repository.receipts[(stored.profile_id, stored.content_sha256)] = stored
    updated = stored.mark_needs_review()
    audit = AuditEvent(
        id=uuid4(),
        profile_id=stored.profile_id,
        aggregate_type="receipt",
        aggregate_id=stored.id,
        action="mark_needs_review",
        actor="receipt-workflow",
        source_reference=stored.source_reference,
        occurred_at=_Clock().now(),
        prior_version=1,
        current_version=2,
        reason="stale test",
    )

    identity = receipt_review_identity(
        stored.id,
        99,
        action=MARK_NEEDS_REVIEW_ACTION,
    )

    with pytest.raises(OptimisticConcurrencyError, match="changed"):
        repository.replace_receipt_metadata(
            updated,
            expected_version=99,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            audit_event=audit,
        )

    assert repository.get_receipt_metadata(stored.profile_id, stored.id) == stored
    assert repository.audits == []


@pytest.mark.parametrize(
    "status",
    [ReceiptStatus.APPROVED, ReceiptStatus.DELETED],
)
def test_terminal_receipt_states_expose_no_further_transition(
    status: ReceiptStatus,
) -> None:
    """Keep approved content immutable and deletion behind its future service."""

    metadata = replace(_metadata(), status=status, version=3)

    with pytest.raises(InvalidTransitionError):
        metadata.mark_needs_review()
    with pytest.raises(InvalidTransitionError):
        metadata.approve()
