"""Administrative wallet and budget use cases for Step 2 entry points."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from uuid import UUID

from money_tracker.application.errors import AggregateNotFoundError
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
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.application.administration")

CREATE_BUDGET_COMMAND = "create_budget"
REPLACE_BUDGET_COMMAND = "replace_budget"


def budget_request_hash(
    *,
    profile_id: UUID,
    category_id: UUID,
    recurring_target: Money,
    overrides: tuple[tuple[YearMonth, Money], ...],
) -> str:
    """Hash the complete semantics of one budget command.

    The digest covers the scope, the budgeted category, and every exact target
    the command would store. Version is deliberately absent, because a replay
    must match the first attempt even though that attempt advanced it. Override
    months are sorted so that two orderings of the same plan are one command
    rather than two.

    Args:
        profile_id: Trusted owner scope.
        category_id: Budgeted stable category.
        recurring_target: Default exact monthly target.
        overrides: Explicit month and exact target pairs.

    Returns:
        Lowercase SHA-256 of the canonical command representation.
    """

    canonical = json.dumps(
        {
            "category_id": str(category_id),
            "overrides": sorted(
                [
                    [period.year, period.month, target.minor_units, target.currency]
                    for period, target in overrides
                ]
            ),
            "profile_id": str(profile_id),
            "recurring_target": [
                recurring_target.minor_units,
                recurring_target.currency,
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        # Wallet rows are immutable and change a tracked balance, so each one
        # is recorded by identity and kind. The amount stays in the ledger.
        _LOGGER.info(
            "wallet_entry.appended",
            profile_id=entry.profile_id,
            wallet_entry_id=entry.id,
            funding_source_id=entry.funding_source_id,
            entry_type=entry.entry_type.value,
        )
        return entry

    def create_budget(
        self,
        budget: MonthlyBudget,
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
    ) -> MonthlyBudget:
        """Create one category budget and its overrides atomically.

        Args:
            budget: Validated initial budget at version one.
            idempotency_key: Stable external command key for replay safety.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Persisted budget, or the budget an identical replay already stored.

        Raises:
            ValueError: If the supplied budget is not an initial version.
            IdempotencyConflictError: If the key named different semantics.
        """

        if budget.version != 1:
            _LOGGER.error(
                "budget.initial_version_invalid",
                profile_id=budget.profile_id,
                category_id=budget.category_id,
                version=budget.version,
            )
            raise ValueError("new budget must start at version 1")
        request_hash = budget_request_hash(
            profile_id=budget.profile_id,
            category_id=budget.category_id,
            recurring_target=budget.recurring_target,
            overrides=budget.overrides,
        )
        replayed = self._repository.get_idempotent_budget_command(
            profile_id=budget.profile_id,
            idempotency_key=idempotency_key,
            command_type=CREATE_BUDGET_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        audit = self._budget_audit(
            budget, action="create", actor=actor, reason=reason, prior_version=None
        )
        self._repository.save_budget(
            budget,
            expected_version=None,
            idempotency_key=idempotency_key,
            command_type=CREATE_BUDGET_COMMAND,
            request_hash=request_hash,
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
        idempotency_key: str,
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
            idempotency_key: Stable external command key for replay safety.
            actor: Trusted owner or administrative actor.
            reason: Non-sensitive audit explanation.

        Returns:
            Replaced budget with its version incremented, or the budget an
            identical replay already stored.

        Raises:
            AggregateNotFoundError: If the category has no configured budget.
            IdempotencyConflictError: If the key named different semantics.
        """

        request_hash = budget_request_hash(
            profile_id=profile_id,
            category_id=category_id,
            recurring_target=recurring_target,
            overrides=overrides,
        )
        replayed = self._repository.get_idempotent_budget_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type=REPLACE_BUDGET_COMMAND,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        current = self._repository.get_budget(profile_id, category_id)
        if current is None:
            _LOGGER.warning(
                "budget.not_found", profile_id=profile_id, category_id=category_id
            )
            raise AggregateNotFoundError("budget does not exist in profile")
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
            idempotency_key=idempotency_key,
            command_type=REPLACE_BUDGET_COMMAND,
            request_hash=request_hash,
            audit_event=audit,
        )
        _LOGGER.info(
            "budget.replaced",
            profile_id=profile_id,
            category_id=category_id,
            prior_version=current.version,
            version=updated.version,
            overrides=len(updated.overrides),
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
