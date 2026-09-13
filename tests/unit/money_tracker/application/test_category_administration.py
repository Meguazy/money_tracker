"""Tests for audited category display and lifecycle administration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    CategoryAdministrationService,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
)
from money_tracker.domain import AuditEvent, Category, CategoryStatus, Taxonomy


@dataclass
class _Clock:
    """Return one deterministic audit instant."""

    def now(self) -> datetime:
        """Return an aware time."""

        return datetime(2026, 9, 13, 21, tzinfo=UTC)


@dataclass
class _Ids:
    """Return deterministic but valid audit identifiers."""

    def new(self) -> UUID:
        """Return a fresh identifier."""

        return uuid4()


@dataclass
class _Repository:
    """In-memory category repository preserving atomic command semantics."""

    profile_id: UUID
    taxonomy: Taxonomy
    audits: list[AuditEvent] = field(default_factory=list)
    claims: dict[tuple[UUID, str], tuple[str, str, UUID]] = field(default_factory=dict)

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the complete taxonomy in the trusted profile."""

        assert profile_id == self.profile_id
        return self.taxonomy

    def get_idempotent_category_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> Category | None:
        """Return an identical prior result and reject a reused key."""

        prior = self.claims.get((profile_id, idempotency_key))
        if prior is None:
            return None
        if prior[:2] != (command_type, request_hash):
            raise IdempotencyConflictError("command key reused")
        return self.taxonomy.category(prior[2])

    def save_category(
        self,
        profile_id: UUID,
        category: Category,
        *,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> None:
        """Replace one version and append its audit under one command claim."""

        claim = (profile_id, idempotency_key)
        prior = self.claims.get(claim)
        if prior is not None:
            if prior[:2] != (command_type, request_hash):
                raise IdempotencyConflictError("command key reused")
            return
        current = self.taxonomy.category(category.id)
        if current.version != expected_version:
            raise OptimisticConcurrencyError("stale category version")
        self.taxonomy = Taxonomy(
            tuple(
                category if item.id == category.id else item
                for item in self.taxonomy.categories
            )
        )
        self.claims[claim] = (command_type, request_hash, category.id)
        self.audits.append(audit_event)


def _service(repository: _Repository) -> CategoryAdministrationService:
    """Build the real use case with deterministic external boundaries."""

    return CategoryAdministrationService(repository, clock=_Clock(), ids=_Ids())


def _repository() -> tuple[_Repository, Category, Category]:
    """Build a two-level taxonomy in one owner scope."""

    profile_id = uuid4()
    root = Category(uuid4(), "Casa", "Spese per la casa.", seeded=True)
    child = Category(
        uuid4(),
        "Arredo",
        "Mobili e accessori.",
        parent_id=root.id,
        aliases=("Mobili",),
        seeded=True,
    )
    return _Repository(profile_id, Taxonomy((root, child))), root, child


def test_rename_is_audited_once_and_identical_retry_replays() -> None:
    """Preserve identity and commit one audit across an HTTP-style retry."""

    repository, _, child = _repository()
    service = _service(repository)
    command = {
        "profile_id": repository.profile_id,
        "category_id": child.id,
        "expected_version": 1,
        "name": "  Mobili   e arredo ",
        "idempotency_key": "rename-1",
        "actor": "owner:web",
        "reason": "owner renamed category in browser",
    }

    renamed = service.rename(**command)
    replayed = service.rename(**command)

    assert replayed == renamed
    assert renamed.id == child.id
    assert renamed.name == "Mobili e arredo"
    assert renamed.version == 2
    assert len(repository.audits) == 1
    audit = repository.audits[0]
    assert audit.action == "rename"
    assert audit.aggregate_id == child.id
    assert (audit.prior_version, audit.current_version) == (1, 2)


def test_archive_leaf_is_audited_and_keeps_hierarchy() -> None:
    """Archive a leaf without deleting or reparenting its stable node."""

    repository, root, child = _repository()

    archived = _service(repository).archive(
        profile_id=repository.profile_id,
        category_id=child.id,
        expected_version=1,
        idempotency_key="archive-1",
        actor="owner:web",
        reason="owner archived category in browser",
    )

    assert archived.status is CategoryStatus.ARCHIVED
    assert archived.parent_id == root.id
    assert archived.version == 2
    assert repository.audits[0].action == "archive"


def test_invalid_archive_and_stale_rename_leave_no_audit() -> None:
    """Keep state atomic when hierarchy or optimistic version rejects work."""

    repository, root, child = _repository()
    service = _service(repository)

    with pytest.raises(ValueError, match="active parent"):
        service.archive(
            profile_id=repository.profile_id,
            category_id=root.id,
            expected_version=1,
            idempotency_key="archive-root",
            actor="owner:web",
            reason="owner archived category in browser",
        )
    with pytest.raises(OptimisticConcurrencyError, match="stale"):
        service.rename(
            profile_id=repository.profile_id,
            category_id=child.id,
            expected_version=9,
            name="Interni",
            idempotency_key="rename-stale",
            actor="owner:web",
            reason="owner renamed category in browser",
        )

    assert repository.taxonomy.category(root.id) == root
    assert repository.taxonomy.category(child.id) == child
    assert repository.audits == []


def test_reused_command_key_with_other_content_is_rejected() -> None:
    """Reject another rename hidden behind an already claimed key."""

    repository, _, child = _repository()
    service = _service(repository)
    common = {
        "profile_id": repository.profile_id,
        "category_id": child.id,
        "expected_version": 1,
        "idempotency_key": "rename-reused",
        "actor": "owner:web",
        "reason": "owner renamed category in browser",
    }
    service.rename(name="Interni", **common)

    with pytest.raises(IdempotencyConflictError, match="reused"):
        service.rename(name="Arredamento", **common)

    assert len(repository.audits) == 1
