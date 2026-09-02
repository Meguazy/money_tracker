"""Immutable audit-event contract for authoritative domain mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from money_tracker.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Trace one domain mutation without storing prompts or sensitive payloads.

    Attributes:
        id: Stable audit identifier.
        profile_id: Owner scope.
        aggregate_type: Stable domain aggregate kind.
        aggregate_id: Mutated aggregate identifier.
        action: Stable mutation action.
        actor: Owner, application policy, or administrative command identity.
        source_reference: Stable command source, not its private raw content.
        occurred_at: Time the mutation committed.
        prior_version: Aggregate version before mutation, if one existed.
        current_version: Aggregate version after mutation.
        reason: Concise non-sensitive explanation.
    """

    id: UUID
    profile_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    action: str
    actor: str
    source_reference: str
    occurred_at: datetime
    prior_version: int | None
    current_version: int
    reason: str

    def __post_init__(self) -> None:
        """Require useful, chronological, versioned audit metadata."""

        text_fields = (
            self.aggregate_type,
            self.action,
            self.actor,
            self.source_reference,
            self.reason,
        )
        if any(not value.strip() for value in text_fields):
            raise DomainError("audit event text fields cannot be empty")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise DomainError("audit event time must include a timezone")
        if self.current_version < 1:
            raise DomainError("audit current version must be positive")
        if self.prior_version is not None:
            if self.prior_version < 1:
                raise DomainError("audit prior version must be positive")
            if self.current_version <= self.prior_version:
                raise DomainError("audit current version must follow prior version")
