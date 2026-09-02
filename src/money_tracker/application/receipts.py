"""Application service for receipt metadata registration without media I/O."""

from __future__ import annotations

from money_tracker.application.ports import (
    Clock,
    IdGenerator,
    ReceiptMetadataRepository,
)
from money_tracker.domain import AuditEvent, ReceiptMetadata, ReceiptStatus


class ReceiptMetadataService:
    """Register stored-content metadata behind an idempotent repository boundary."""

    def __init__(
        self,
        repository: ReceiptMetadataRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind metadata registration to replaceable runtime ports.

        Args:
            repository: Content-addressed metadata transaction boundary.
            clock: Aware authoritative audit timestamp provider.
            ids: Stable audit identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def register(
        self,
        metadata: ReceiptMetadata,
        *,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Register metadata only after a separate store accepted its content.

        This service never receives media bytes or filesystem paths. The caller
        must first use the future receipt-media port and supply its validated
        content digest and decoded media type.

        Args:
            metadata: Validated metadata in initial ``stored`` state.
            actor: Trusted storage adapter or administrative caller.
            reason: Non-sensitive audit explanation.

        Returns:
            Created metadata, or the prior row for an identical content digest.

        Raises:
            ValueError: If the caller attempts to register a later review state.
        """

        if metadata.status is not ReceiptStatus.STORED:
            raise ValueError("new receipt metadata must be stored")
        audit = AuditEvent(
            id=self._ids.new(),
            profile_id=metadata.profile_id,
            aggregate_type="receipt",
            aggregate_id=metadata.id,
            action="create",
            actor=actor,
            source_reference=metadata.source_reference,
            occurred_at=self._clock.now(),
            prior_version=None,
            current_version=1,
            reason=reason,
        )
        return self._repository.create_receipt_metadata(
            metadata,
            audit_event=audit,
        )
