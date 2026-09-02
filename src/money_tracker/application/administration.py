"""Administrative wallet and budget use cases for Step 2 entry points."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from money_tracker.application.ports import (
    Clock,
    FinanceAdministrationRepository,
    IdGenerator,
)
from money_tracker.domain import (
    AuditEvent,
    Money,
    MonthlyBudget,
    WalletEntry,
    YearMonth,
)


class FinanceAdministrationService:
    """Coordinate audited wallet entries and monthly budget mutations."""

    def __init__(
        self,
        repository: FinanceAdministrationRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind administrative use cases to replaceable ports.

        Args:
            repository: Authoritative finance repository.
            clock: Aware audit timestamp provider.
            ids: Stable audit identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def add_wallet_entry(
        self,
        entry: WalletEntry,
        *,
        actor: str,
        reason: str,
    ) -> WalletEntry:
        """Append a wallet movement and its audit in one transaction.

        Args:
            entry: Validated immutable wallet movement.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            The persisted immutable entry.
        """

        audit = self._audit(
            profile_id=entry.profile_id,
            aggregate_type="wallet_entry",
            aggregate_id=entry.id,
            action="create",
            source_reference=f"wallet:{entry.funding_source_id}",
            actor=actor,
            reason=reason,
            prior_version=None,
            current_version=1,
        )
        self._repository.append_wallet_entry(entry, audit_event=audit)
        return entry

    def create_budget(
        self,
        budget: MonthlyBudget,
        *,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Create one category budget and its overrides atomically.

        Args:
            budget: Validated initial budget at version one.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Persisted budget.

        Raises:
            ValueError: If the supplied budget is not an initial version.
        """

        if budget.version != 1:
            raise ValueError("new budget must start at version 1")
        audit = self._budget_audit(
            budget, action="create", actor=actor, reason=reason, prior_version=None
        )
        self._repository.save_budget(
            budget,
            expected_version=None,
            audit_event=audit,
        )
        return budget

    def replace_budget(
        self,
        *,
        profile_id: UUID,
        category_id: UUID,
        expected_version: int,
        recurring_target: Money,
        overrides: tuple[tuple[YearMonth, Money], ...],
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Replace target and overrides under optimistic concurrency.

        Args:
            profile_id: Trusted owner scope.
            category_id: Budgeted stable category.
            expected_version: Version observed by the caller.
            recurring_target: Replacement ``Money`` value.
            overrides: Replacement ``YearMonth``/``Money`` pairs.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Replaced budget with its version incremented.

        Raises:
            LookupError: If the category has no configured budget.
        """

        current = self._repository.get_budget(profile_id, category_id)
        if current is None:
            raise LookupError("budget does not exist")
        updated = replace(
            current,
            recurring_target=recurring_target,
            overrides=overrides,
            version=current.version + 1,
        )
        audit = self._budget_audit(
            updated,
            action="replace",
            actor=actor,
            reason=reason,
            prior_version=current.version,
        )
        self._repository.save_budget(
            updated,
            expected_version=expected_version,
            audit_event=audit,
        )
        return updated

    def _budget_audit(
        self,
        budget: MonthlyBudget,
        *,
        action: str,
        actor: str,
        reason: str,
        prior_version: int | None,
    ) -> AuditEvent:
        """Build consistent audit metadata for one budget mutation."""

        return self._audit(
            profile_id=budget.profile_id,
            aggregate_type="monthly_budget",
            aggregate_id=budget.id,
            action=action,
            source_reference=f"budget:{budget.category_id}",
            actor=actor,
            reason=reason,
            prior_version=prior_version,
            current_version=budget.version,
        )

    def _audit(
        self,
        *,
        profile_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        action: str,
        source_reference: str,
        actor: str,
        reason: str,
        prior_version: int | None,
        current_version: int,
    ) -> AuditEvent:
        """Build one immutable administrative audit event."""

        return AuditEvent(
            id=self._ids.new(),
            profile_id=profile_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            action=action,
            actor=actor,
            source_reference=source_reference,
            occurred_at=self._clock.now(),
            prior_version=prior_version,
            current_version=current_version,
            reason=reason,
        )
