"""Audited owner management of stable taxonomy nodes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from money_tracker.application.errors import AggregateNotFoundError
from money_tracker.application.ports import (
    CategoryAdministrationRepository,
    Clock,
    IdGenerator,
)
from money_tracker.domain import (
    AuditEvent,
    Category,
    Taxonomy,
    TaxonomyError,
    TaxonomyWarning,
)
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger(__name__)

RENAME_CATEGORY_COMMAND = "rename_category"
ARCHIVE_CATEGORY_COMMAND = "archive_category"


@dataclass(frozen=True, slots=True)
class CategoryManagement:
    """Complete category state exposed to the local control plane.

    Attributes:
        categories: Stable nodes including archived history and optimistic
            versions, in repository order.
        warnings: Non-blocking taxonomy complexity warnings.
    """

    categories: tuple[Category, ...]
    warnings: tuple[TaxonomyWarning, ...]


def category_request_hash(
    *,
    profile_id: UUID,
    category_id: UUID,
    name: str | None,
) -> str:
    """Hash the complete semantics of one category-management command.

    Whitespace is normalized because the domain stores the same normalized
    display form. Letter case and accents remain significant here because a
    rename can intentionally change either while retaining the same lookup key.

    Args:
        profile_id: Trusted owner scope.
        category_id: Stable node being changed.
        name: Replacement display name for rename, or None for archive.

    Returns:
        Lowercase SHA-256 of the canonical command representation.
    """

    canonical = json.dumps(
        {
            "category_id": str(category_id),
            "name": None if name is None else " ".join(name.strip().split()),
            "profile_id": str(profile_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CategoryAdministrationService:
    """Read and mutate category display/lifecycle state without reparenting."""

    def __init__(
        self,
        repository: CategoryAdministrationRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind taxonomy management to its transactional repository.

        Args:
            repository: Versioned category storage and audit boundary.
            clock: Aware audit timestamp provider.
            ids: Stable audit identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def management(self, *, profile_id: UUID) -> CategoryManagement:
        """Return all nodes and current soft-complexity warnings.

        Args:
            profile_id: Trusted owner scope.

        Returns:
            Complete category-management state.
        """

        taxonomy = self._repository.get_taxonomy(profile_id)
        return CategoryManagement(
            categories=taxonomy.categories,
            warnings=taxonomy.warnings(),
        )

    def rename(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        name: str,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> Category:
        """Rename one active category under optimistic concurrency.

        Args:
            profile_id: Trusted owner scope.
            category_id: Stable node to rename.
            expected_version: Version observed by the owner.
            name: New owner-facing display name.
            idempotency_key: Stable external command key.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Renamed category or the result of an identical replay.

        Raises:
            AggregateNotFoundError: If the category is outside the profile.
            IdempotencyConflictError: If the command key was reused.
            OptimisticConcurrencyError: If the observed version is stale.
            DomainError: If taxonomy governance refuses the rename.
        """

        request_hash = category_request_hash(
            profile_id=profile_id,
            category_id=category_id,
            name=name,
        )
        replayed = self._repository.get_idempotent_category_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type=RENAME_CATEGORY_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        taxonomy, current = self._current(
            profile_id=profile_id,
            category_id=category_id,
        )
        updated = taxonomy.rename_category(category_id, name).category(category_id)
        self._save(
            profile_id=profile_id,
            current=current,
            updated=updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=RENAME_CATEGORY_COMMAND,
            request_hash=request_hash,
            action="rename",
            actor=actor,
            reason=reason,
        )
        _LOGGER.info(
            "category.renamed",
            profile_id=profile_id,
            category_id=category_id,
            prior_version=current.version,
            version=updated.version,
        )
        return updated

    def archive(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> Category:
        """Archive one active category under optimistic concurrency.

        Args:
            profile_id: Trusted owner scope.
            category_id: Stable node to archive.
            expected_version: Version observed by the owner.
            idempotency_key: Stable external command key.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Archived category or the result of an identical replay.

        Raises:
            AggregateNotFoundError: If the category is outside the profile.
            IdempotencyConflictError: If the command key was reused.
            OptimisticConcurrencyError: If the observed version is stale.
            DomainError: If taxonomy governance refuses the archive.
        """

        request_hash = category_request_hash(
            profile_id=profile_id,
            category_id=category_id,
            name=None,
        )
        replayed = self._repository.get_idempotent_category_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type=ARCHIVE_CATEGORY_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        taxonomy, current = self._current(
            profile_id=profile_id,
            category_id=category_id,
        )
        updated = taxonomy.archive_category(category_id).category(category_id)
        self._save(
            profile_id=profile_id,
            current=current,
            updated=updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=ARCHIVE_CATEGORY_COMMAND,
            request_hash=request_hash,
            action="archive",
            actor=actor,
            reason=reason,
        )
        _LOGGER.info(
            "category.archived",
            profile_id=profile_id,
            category_id=category_id,
            prior_version=current.version,
            version=updated.version,
        )
        return updated

    def _current(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
    ) -> tuple[Taxonomy, Category]:
        """Load one node while mapping only an unknown ID to not-found."""

        taxonomy = self._repository.get_taxonomy(profile_id)
        try:
            return taxonomy, taxonomy.category(category_id)
        except TaxonomyError as error:
            _LOGGER.warning(
                "category.not_found",
                profile_id=profile_id,
                category_id=category_id,
            )
            raise AggregateNotFoundError(
                "category does not exist in profile"
            ) from error

    def _save(
        self,
        *,
        profile_id: UUID,
        current: Category,
        updated: Category,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        action: str,
        actor: str,
        reason: str,
    ) -> None:
        """Persist one version transition and its immutable audit event."""

        audit = AuditEvent(
            id=self._ids.new(),
            profile_id=profile_id,
            aggregate_type="category",
            aggregate_id=updated.id,
            action=action,
            actor=actor,
            source_reference=f"category:{updated.id}",
            occurred_at=self._clock.now(),
            prior_version=current.version,
            current_version=updated.version,
            reason=reason,
        )
        self._repository.save_category(
            profile_id,
            updated,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_hash=request_hash,
            audit_event=audit,
        )


__all__ = [
    "ARCHIVE_CATEGORY_COMMAND",
    "RENAME_CATEGORY_COMMAND",
    "CategoryAdministrationService",
    "CategoryManagement",
    "category_request_hash",
]
