"""Transactional finance use cases built only on domain and repository ports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from money_tracker.application.ports import Clock, FinanceRepository, IdGenerator
from money_tracker.domain import (
    AuditEvent,
    CategoryAllocation,
    Expense,
    Money,
    PaymentAllocation,
)


class FinanceService:
    """Coordinate idempotent creation, posting, correction, and undo."""

    def __init__(
        self,
        repository: FinanceRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind deterministic use cases to replaceable infrastructure ports.

        Args:
            repository: Authoritative aggregate and audit persistence port.
            clock: Mutation timestamp provider.
            ids: Audit identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Persist a draft or prevalidated aggregate exactly once.

        Args:
            expense: Complete aggregate carrying the external idempotency key.
            request_hash: Canonical lowercase SHA-256 of the mutation request.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            Newly stored expense or the prior result of an identical command.
        """

        audit = self._audit_event(
            expense=expense,
            action="create",
            actor=actor,
            reason=reason,
            prior_version=None,
        )
        return self._repository.create_expense(
            expense,
            request_hash=request_hash,
            audit_event=audit,
        )

    def post_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        actor: str,
        reason: str,
        receipt_approved: bool = False,
    ) -> Expense:
        """Post a reconciled draft under optimistic concurrency.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.
            expected_version: Version shown to the caller.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.
            receipt_approved: Explicit approval for an image-backed draft.

        Returns:
            Newly posted aggregate.
        """

        current = self._repository.get_expense(profile_id, expense_id)
        posted = current.post(
            taxonomy=self._repository.get_taxonomy(profile_id),
            funding_sources=self._repository.get_funding_sources(profile_id),
            receipt_approved=receipt_approved,
        )
        self._replace(
            posted,
            expected_version=expected_version,
            prior_version=current.version,
            action="post",
            actor=actor,
            reason=reason,
        )
        return posted

    def correct_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        amount: Money,
        occurred_at: datetime,
        description: str,
        merchant: str | None,
        category_allocations: tuple[CategoryAllocation, ...],
        payment_allocations: tuple[PaymentAllocation, ...],
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Replace all editable expense fields and retain audit history.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.
            expected_version: Version shown to the caller.
            amount: Replacement exact total.
            occurred_at: Replacement aware occurrence timestamp.
            description: Replacement description.
            merchant: Replacement merchant metadata.
            category_allocations: Complete replacement purpose dimension.
            payment_allocations: Complete replacement funding dimension.
            idempotency_key: Stable external command key for replay safety.
            request_hash: Canonical lowercase SHA-256 of replacement semantics.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            Corrected aggregate with incremented version.
        """

        replayed = self._repository.get_idempotent_expense_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type="correct_expense",
            request_hash=request_hash,
            expense_id=expense_id,
        )
        if replayed is not None:
            return replayed
        current = self._repository.get_expense(profile_id, expense_id)
        corrected = current.corrected(
            amount=amount,
            occurred_at=occurred_at,
            description=description,
            merchant=merchant,
            category_allocations=category_allocations,
            payment_allocations=payment_allocations,
        )
        return self._replace_idempotently(
            corrected,
            expected_version=expected_version,
            prior_version=current.version,
            action="correct",
            idempotency_key=idempotency_key,
            command_type="correct_expense",
            request_hash=request_hash,
            actor=actor,
            reason=reason,
        )

    def void_expense(
        self,
        *,
        profile_id: UUID,
        expense_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Void one posted aggregate without deleting it.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.
            expected_version: Version shown to the caller.
            idempotency_key: Stable external command key for replay safety.
            request_hash: Canonical lowercase SHA-256 of the target semantics.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            Voided aggregate with incremented version.
        """

        replayed = self._repository.get_idempotent_expense_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type="void_expense",
            request_hash=request_hash,
            expense_id=expense_id,
        )
        if replayed is not None:
            return replayed
        current = self._repository.get_expense(profile_id, expense_id)
        voided = current.void()
        return self._replace_idempotently(
            voided,
            expected_version=expected_version,
            prior_version=current.version,
            action="void",
            idempotency_key=idempotency_key,
            command_type="void_expense",
            request_hash=request_hash,
            actor=actor,
            reason=reason,
        )

    def _replace_idempotently(
        self,
        expense: Expense,
        *,
        expected_version: int,
        prior_version: int,
        action: str,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Persist one replay-safe replacement and its audit atomically."""

        audit = self._audit_event(
            expense=expense,
            action=action,
            actor=actor,
            reason=reason,
            prior_version=prior_version,
        )
        return self._repository.replace_expense_idempotently(
            expense,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_hash=request_hash,
            audit_event=audit,
        )

    def _replace(
        self,
        expense: Expense,
        *,
        expected_version: int,
        prior_version: int,
        action: str,
        actor: str,
        reason: str,
    ) -> None:
        """Persist one replacement and matching audit event atomically."""

        audit = self._audit_event(
            expense=expense,
            action=action,
            actor=actor,
            reason=reason,
            prior_version=prior_version,
        )
        self._repository.replace_expense(
            expense,
            expected_version=expected_version,
            audit_event=audit,
        )

    def _audit_event(
        self,
        *,
        expense: Expense,
        action: str,
        actor: str,
        reason: str,
        prior_version: int | None,
    ) -> AuditEvent:
        """Build consistent audit metadata for one expense mutation."""

        return AuditEvent(
            id=self._ids.new(),
            profile_id=expense.profile_id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action=action,
            actor=actor,
            source_reference=expense.source_reference,
            occurred_at=self._clock.now(),
            prior_version=prior_version,
            current_version=expense.version,
            reason=reason,
        )
