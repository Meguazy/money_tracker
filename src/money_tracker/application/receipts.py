"""Application service for receipt metadata registration without media I/O."""

from __future__ import annotations

from uuid import UUID

from money_tracker.application.ports import (
    Clock,
    IdGenerator,
    ReceiptMetadataRepository,
)
from money_tracker.domain import AuditEvent, ReceiptMetadata, ReceiptStatus


class ReceiptMetadataService:
    """Register stored content and coordinate mandatory owner review."""

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

        if metadata.status is not ReceiptStatus.STORED or metadata.version != 1:
            raise ValueError("new receipt metadata must be stored at version one")
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

    def mark_needs_review(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Move stored metadata into visible mandatory review.

        Args:
            profile_id: Trusted owner scope.
            receipt_id: Stable receipt identifier.
            expected_version: Version observed by the caller.
            actor: Trusted workflow or application actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Needs-review metadata with its version incremented.
        """

        current = self._repository.get_receipt_metadata(profile_id, receipt_id)
        updated = current.mark_needs_review()
        self._replace(
            updated,
            expected_version=expected_version,
            prior_version=current.version,
            action="mark_needs_review",
            actor=actor,
            reason=reason,
        )
        return updated

    def approve(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Record explicit owner approval for a receipt already in review.

        Args:
            profile_id: Trusted owner scope.
            receipt_id: Stable receipt identifier.
            expected_version: Version observed by the caller.
            actor: Trusted owner identity supplied by the channel boundary.
            reason: Non-sensitive audit explanation.

        Returns:
            Approved metadata with its version incremented.
        """

        current = self._repository.get_receipt_metadata(profile_id, receipt_id)
        updated = current.approve()
        self._replace(
            updated,
            expected_version=expected_version,
            prior_version=current.version,
            action="approve",
            actor=actor,
            reason=reason,
        )
        return updated

    def _replace(
        self,
        metadata: ReceiptMetadata,
        *,
        expected_version: int,
        prior_version: int,
        action: str,
        actor: str,
        reason: str,
    ) -> None:
        """Persist one receipt transition with matching immutable audit."""

        audit = AuditEvent(
            id=self._ids.new(),
            profile_id=metadata.profile_id,
            aggregate_type="receipt",
            aggregate_id=metadata.id,
            action=action,
            actor=actor,
            source_reference=metadata.source_reference,
            occurred_at=self._clock.now(),
            prior_version=prior_version,
            current_version=metadata.version,
            reason=reason,
        )
        self._repository.replace_receipt_metadata(
            metadata,
            expected_version=expected_version,
            audit_event=audit,
        )
