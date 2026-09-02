"""Tests for audited wallet and budget application use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    FinanceAdministrationService,
    OptimisticConcurrencyError,
)
from money_tracker.domain import (
    AuditEvent,
    Money,
    MonthlyBudget,
    WalletEntry,
    WalletEntryType,
    YearMonth,
)


@dataclass
class _FrozenClock:
    """Return one aware instant for deterministic audit assertions."""

    value: datetime = datetime(2026, 9, 2, 18, 30, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the configured instant."""

        return self.value


@dataclass
class _PreparedIds:
    """Yield stable identifiers for application-created audit events."""

    values: list[UUID] = field(default_factory=lambda: [uuid4() for _ in range(8)])

    def new(self) -> UUID:
        """Return and consume the next identifier."""

        return self.values.pop(0)


@dataclass
class _AdministrationRepository:
    """In-memory wallet and budget repository with atomic audit behavior."""

    budgets: dict[tuple[UUID, UUID], MonthlyBudget] = field(default_factory=dict)
    wallet_entries: list[WalletEntry] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    def append_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Append an immutable entry and its audit together."""

        self.wallet_entries.append(entry)
        self.audits.append(audit_event)

    def get_budget(
        self,
        profile_id: UUID,
        category_id: UUID,
    ) -> MonthlyBudget | None:
        """Return a budget in the requested owner scope."""

        return self.budgets.get((profile_id, category_id))

    def save_budget(
        self,
        budget: MonthlyBudget,
        *,
        expected_version: int | None,
        audit_event: AuditEvent,
    ) -> None:
        """Create or replace a budget using optimistic concurrency."""

        key = (budget.profile_id, budget.category_id)
        current = self.budgets.get(key)
        if expected_version is None:
            if current is not None:
                raise OptimisticConcurrencyError("budget already exists")
        elif current is None or current.version != expected_version:
            raise OptimisticConcurrencyError("stale budget version")
        self.budgets[key] = budget
        self.audits.append(audit_event)


def _service(
    repository: _AdministrationRepository,
) -> FinanceAdministrationService:
    """Build the service with deterministic clock and identifiers."""

    return FinanceAdministrationService(
        repository,
        clock=_FrozenClock(),
        ids=_PreparedIds(),
    )


def _budget(*, version: int = 1) -> MonthlyBudget:
    """Build one valid category budget."""

    return MonthlyBudget(
        id=uuid4(),
        profile_id=uuid4(),
        category_id=uuid4(),
        recurring_target=Money(20_000),
        overrides=((YearMonth(2026, 12), Money(25_000)),),
        version=version,
    )


def test_add_wallet_entry_persists_matching_audit() -> None:
    """Persist the exact entry with non-sensitive immutable audit metadata."""

    repository = _AdministrationRepository()
    service = _service(repository)
    entry = WalletEntry(
        id=uuid4(),
        profile_id=uuid4(),
        funding_source_id=uuid4(),
        entry_type=WalletEntryType.CREDIT,
        amount=Money(5_000),
        occurred_at=datetime(2026, 9, 2, 17, tzinfo=UTC),
        note="benefit top-up",
    )

    result = service.add_wallet_entry(
        entry,
        actor="owner",
        reason="manual top-up",
    )

    assert result is entry
    assert repository.wallet_entries == [entry]
    audit = repository.audits[0]
    assert audit.profile_id == entry.profile_id
    assert audit.aggregate_id == entry.id
    assert audit.aggregate_type == "wallet_entry"
    assert audit.source_reference == f"wallet:{entry.funding_source_id}"
    assert audit.prior_version is None
    assert audit.current_version == 1


def test_create_budget_persists_complete_aggregate_and_audit() -> None:
    """Create recurring target and overrides as one audited aggregate."""

    repository = _AdministrationRepository()
    service = _service(repository)
    budget = _budget()

    result = service.create_budget(
        budget,
        actor="owner",
        reason="set monthly target",
    )

    assert result is budget
    assert repository.budgets[(budget.profile_id, budget.category_id)] == budget
    audit = repository.audits[0]
    assert audit.action == "create"
    assert audit.aggregate_id == budget.id
    assert audit.prior_version is None
    assert audit.current_version == 1


def test_create_budget_rejects_non_initial_version_without_writes() -> None:
    """Reject imported stale state before repository or audit mutation."""

    repository = _AdministrationRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="version 1"):
        service.create_budget(
            _budget(version=2),
            actor="owner",
            reason="invalid import",
        )

    assert repository.budgets == {}
    assert repository.audits == []


def test_replace_budget_replaces_overrides_and_increments_version() -> None:
    """Replace the whole budget snapshot from the caller-observed version."""

    repository = _AdministrationRepository()
    current = _budget()
    repository.budgets[(current.profile_id, current.category_id)] = current
    service = _service(repository)

    updated = service.replace_budget(
        profile_id=current.profile_id,
        category_id=current.category_id,
        expected_version=1,
        recurring_target=Money(30_000),
        overrides=((YearMonth(2027, 1), Money(35_000)),),
        actor="owner",
        reason="annual revision",
    )

    assert updated.version == 2
    assert updated.recurring_target == Money(30_000)
    assert updated.overrides == ((YearMonth(2027, 1), Money(35_000)),)
    assert repository.budgets[(current.profile_id, current.category_id)] == updated
    audit = repository.audits[0]
    assert audit.action == "replace"
    assert audit.prior_version == 1
    assert audit.current_version == 2


def test_replace_budget_conflict_keeps_state_and_audit_unchanged() -> None:
    """Preserve atomicity when a stale caller loses optimistic concurrency."""

    repository = _AdministrationRepository()
    current = _budget(version=2)
    repository.budgets[(current.profile_id, current.category_id)] = current
    service = _service(repository)

    with pytest.raises(OptimisticConcurrencyError, match="stale"):
        service.replace_budget(
            profile_id=current.profile_id,
            category_id=current.category_id,
            expected_version=1,
            recurring_target=Money(40_000),
            overrides=(),
            actor="owner",
            reason="stale screen",
        )

    assert repository.budgets[(current.profile_id, current.category_id)] == current
    assert repository.audits == []


def test_replace_missing_budget_fails_before_audit_creation() -> None:
    """Report a missing aggregate without writing repository state."""

    repository = _AdministrationRepository()
    service = _service(repository)

    with pytest.raises(LookupError, match="does not exist"):
        service.replace_budget(
            profile_id=uuid4(),
            category_id=uuid4(),
            expected_version=1,
            recurring_target=Money(10_000),
            overrides=(),
            actor="owner",
            reason="missing category budget",
        )

    assert repository.budgets == {}
    assert repository.audits == []
