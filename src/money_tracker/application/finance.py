"""Transactional finance use cases built only on domain and repository ports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from money_tracker.application.ports import Clock, FinanceRepository, IdGenerator
from money_tracker.domain import (
    AuditEvent,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
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
        idempotency_key: str,
        request_hash: str,
        actor: str,
        reason: str,
        receipt_approved: bool = False,
    ) -> Expense:
        """Post a reconciled draft, returning the first result on replay.

        Posting is a channel command like correction and undo, so AD-018 needs
        it to survive a redelivery: a crash between the finance commit and the
        channel response re-dispatches it, and a second attempt must return the
        posted movement rather than fail because the draft is gone.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.
            expected_version: Version shown to the caller.
            idempotency_key: Stable external command key for replay safety.
            request_hash: Canonical lowercase SHA-256 of the target semantics.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.
            receipt_approved: Explicit approval for an image-backed draft.

        Returns:
            Newly posted aggregate, or the stored result of an identical replay.
        """

        replayed = self._repository.get_idempotent_expense_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type="post_expense",
            request_hash=request_hash,
            expense_id=expense_id,
        )
        if replayed is not None:
            return replayed
        current = self._repository.get_expense(profile_id, expense_id)
        posted = current.post(
            taxonomy=self._repository.get_taxonomy(profile_id),
            funding_sources=self._repository.get_funding_sources(profile_id),
            receipt_approved=receipt_approved,
        )
        return self._replace_idempotently(
            posted,
            expected_version=expected_version,
            prior_version=current.version,
            action="post",
            idempotency_key=idempotency_key,
            command_type="post_expense",
            request_hash=request_hash,
            actor=actor,
            reason=reason,
        )

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

    def cancel_expense(
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
        """End one movement, choosing the transition its state allows.

        The owner has a single intent here and one control for it. The domain
        has two transitions, because voiding reverses something that already
        counted and a draft never did, so only a posted movement can be voided
        and only a draft can be discarded. Resolving that from the stored
        status keeps the distinction where it belongs and off the owner, who
        would otherwise be offered a control that cannot work: every Telegram
        movement starts as a draft, and asking to void one raised
        `InvalidTransitionError` and answered with a clarification.

        One command key covers both, so a redelivered callback returns the
        first stored result instead of re-resolving a status the first run
        already changed. The audit records which transition actually happened.

        Args:
            profile_id: Trusted owner scope.
            expense_id: Stable aggregate identifier.
            expected_version: Version shown to the caller.
            idempotency_key: Stable external command key for replay safety.
            request_hash: Canonical lowercase SHA-256 of the target semantics.
            actor: Trusted application or owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            Voided or discarded aggregate with incremented version.

        Raises:
            InvalidTransitionError: If the movement is already terminal.
        """

        replayed = self._repository.get_idempotent_expense_command(
            profile_id=profile_id,
            idempotency_key=idempotency_key,
            command_type="cancel_expense",
            request_hash=request_hash,
            expense_id=expense_id,
        )
        if replayed is not None:
            return replayed
        current = self._repository.get_expense(profile_id, expense_id)
        drafted = current.status is ExpenseStatus.DRAFT
        ended = current.discard() if drafted else current.void()
        return self._replace_idempotently(
            ended,
            expected_version=expected_version,
            prior_version=current.version,
            action="discard" if drafted else "void",
            idempotency_key=idempotency_key,
            command_type="cancel_expense",
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
