"""Application service for receipt metadata registration without media I/O."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from money_tracker.application.ports import (
    Clock,
    IdGenerator,
    ReceiptMetadataRepository,
)
from money_tracker.domain import AuditEvent, ReceiptMetadata, ReceiptStatus
from money_tracker.structured_logging import get_logger

MARK_NEEDS_REVIEW_ACTION = "mark_needs_review"
"""Audit and command action that exposes stored media to mandatory review."""

APPROVE_ACTION = "approve"
"""Audit and command action recording explicit owner approval."""

_LOGGER = get_logger("money_tracker.application.receipts")


def receipt_review_identity(
    receipt_id: UUID,
    expected_version: int,
    *,
    action: str,
) -> tuple[str, str, str]:
    """Build the stable identity shared by every replay of one transition.

    A receipt is reviewed in more than one step, so the command is the
    transition rather than the receipt: the observed version is part of the
    key. Two clicks on the same button therefore replay one decision, while a
    different decision taken from the same displayed state conflicts instead of
    applying a second transition the owner never saw.

    Args:
        receipt_id: Stable receipt identifier.
        expected_version: Version the caller observed before deciding.
        action: Transition the caller intends.

    Returns:
        Canonical idempotency key, command type, and request digest.
    """

    canonical = json.dumps(
        {
            "action": action,
            "expected_version": expected_version,
            "receipt_id": str(receipt_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"receipt-review:{receipt_id}:{expected_version}",
        f"receipt_{action}",
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


class ReceiptMetadataService:
    """Register stored content and coordinate mandatory owner review.

    The service owns no media. It reads and advances the metadata AD-014 keeps
    beside the private archive, and every transition it applies is audited,
    versioned, and replay-safe.
    """

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
            _LOGGER.error(
                "receipt.registration_invalid",
                profile_id=metadata.profile_id,
                receipt_id=metadata.id,
                status=metadata.status.value,
                version=metadata.version,
            )
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

    def stored_receipts(self, *, profile_id: UUID) -> tuple[ReceiptMetadata, ...]:
        """Return every retained receipt so the owner can review its state.

        The result carries safe derived facts only. Media bytes, archive
        paths, and extracted content are not reachable from this service and
        remain outside the read model by construction.

        Args:
            profile_id: Trusted owner scope.

        Returns:
            Retained receipts ordered by storage time, newest first.
        """

        return self._repository.list_receipt_metadata(profile_id)

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
            Needs-review metadata with its version incremented, or the metadata
            an identical replayed transition already stored.

        Raises:
            IdempotencyConflictError: If the observed state already decided
                something else.
            OptimisticConcurrencyError: If another transition already won.
        """

        return self._transition(
            profile_id=profile_id,
            receipt_id=receipt_id,
            expected_version=expected_version,
            action=MARK_NEEDS_REVIEW_ACTION,
            actor=actor,
            reason=reason,
        )

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
            Approved metadata with its version incremented, or the metadata an
            identical replayed transition already stored.

        Raises:
            IdempotencyConflictError: If the observed state already decided
                something else.
            OptimisticConcurrencyError: If another transition already won.
        """

        return self._transition(
            profile_id=profile_id,
            receipt_id=receipt_id,
            expected_version=expected_version,
            action=APPROVE_ACTION,
            actor=actor,
            reason=reason,
        )

    def _transition(
        self,
        *,
        profile_id: UUID,
        receipt_id: UUID,
        expected_version: int,
        action: str,
        actor: str,
        reason: str,
    ) -> ReceiptMetadata:
        """Apply or replay one audited review transition.

        The replay is resolved before the current row is read, so a retry never
        depends on state a first attempt already advanced. The domain decides
        which transition is legal; this method only decides whether it has
        already been applied.
        """

        identity = receipt_review_identity(
            receipt_id,
            expected_version,
            action=action,
        )
        replayed = self._repository.get_idempotent_receipt_command(
            profile_id=profile_id,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
        )
        if replayed is not None:
            _LOGGER.info(
                "receipt.command.replayed",
                profile_id=profile_id,
                receipt_id=receipt_id,
                command_type=identity[1],
                version=replayed.version,
            )
            return replayed
        current = self._repository.get_receipt_metadata(profile_id, receipt_id)
        updated = (
            current.mark_needs_review()
            if action == MARK_NEEDS_REVIEW_ACTION
            else current.approve()
        )
        stored = self._repository.replace_receipt_metadata(
            updated,
            expected_version=expected_version,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            audit_event=self._transition_audit(
                updated,
                prior_version=current.version,
                action=action,
                actor=actor,
                reason=reason,
            ),
        )
        _LOGGER.info(
            "receipt.transitioned",
            profile_id=stored.profile_id,
            receipt_id=stored.id,
            action=action,
            status=stored.status.value,
            prior_version=current.version,
            version=stored.version,
        )
        return stored

    def _transition_audit(
        self,
        metadata: ReceiptMetadata,
        *,
        prior_version: int,
        action: str,
        actor: str,
        reason: str,
    ) -> AuditEvent:
        """Build the immutable record of one receipt review transition."""

        return AuditEvent(
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


__all__ = [
    "APPROVE_ACTION",
    "MARK_NEEDS_REVIEW_ACTION",
    "ReceiptMetadataService",
    "receipt_review_identity",
]
