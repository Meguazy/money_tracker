"""Tests for expense application-service coordination and audit semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    FinanceService,
    IdempotencyConflictError,
    OptimisticConcurrencyError,
)
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    DomainError,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    Taxonomy,
    initial_taxonomy,
)


@dataclass
class _FakeClock:
    """Frozen aware clock used to verify audit timestamps."""

    value: datetime = datetime(2026, 9, 2, 12, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the frozen test instant."""

        return self.value


@dataclass
class _FakeIds:
    """Deterministic UUID sequence for audit creation."""

    values: list[UUID] = field(default_factory=lambda: [uuid4() for _ in range(10)])

    def new(self) -> UUID:
        """Return and consume the next prepared identifier."""

        return self.values.pop(0)


@dataclass
class _FakeRepository:
    """In-memory implementation of the finance repository contract."""

    taxonomy: Taxonomy
    sources: tuple[FundingSource, ...]
    expenses: dict[UUID, Expense] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    idempotency: dict[str, tuple[str, UUID]] = field(default_factory=dict)
    mutation_commands: dict[str, tuple[str, str, UUID]] = field(default_factory=dict)

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Load one aggregate only when it belongs to the requested profile."""

        expense = self.expenses[expense_id]
        if expense.profile_id != profile_id:
            raise KeyError(expense_id)
        return expense

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the prepared profile taxonomy."""

        return self.taxonomy

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return sources belonging to the requested profile."""

        return tuple(
            source for source in self.sources if source.profile_id == profile_id
        )

    def create_expense(
        self,
        expense: Expense,
        *,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Apply identical-retry idempotency and append one creation audit."""

        prior = self.idempotency.get(expense.idempotency_key)
        if prior is not None:
            if prior[0] != request_hash:
                raise IdempotencyConflictError("different request")
            return self.expenses[prior[1]]
        self.idempotency[expense.idempotency_key] = (request_hash, expense.id)
        self.expenses[expense.id] = expense
        self.audits.append(audit_event)
        return expense

    def replace_expense(
        self,
        expense: Expense,
        *,
        expected_version: int,
        audit_event: AuditEvent,
    ) -> None:
        """Replace exactly one observed version and append its audit."""

        current = self.expenses[expense.id]
        if current.version != expected_version:
            raise OptimisticConcurrencyError("stale version")
        self.expenses[expense.id] = expense
        self.audits.append(audit_event)

    def get_idempotent_expense_command(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        expense_id: UUID,
    ) -> Expense | None:
        """Return a prior identical mutation from the in-memory command store."""

        prior = self.mutation_commands.get(idempotency_key)
        if prior is None:
            return None
        if prior != (command_type, request_hash, expense_id):
            raise IdempotencyConflictError("different mutation")
        return self.get_expense(profile_id, expense_id)

    def replace_expense_idempotently(
        self,
        expense: Expense,
        *,
        expected_version: int,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
        audit_event: AuditEvent,
    ) -> Expense:
        """Claim and apply one replacement in memory."""

        replayed = self.get_idempotent_expense_command(
            profile_id=expense.profile_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            request_hash=request_hash,
            expense_id=expense.id,
        )
        if replayed is not None:
            return replayed
        self.replace_expense(
            expense,
            expected_version=expected_version,
            audit_event=audit_event,
        )
        self.mutation_commands[idempotency_key] = (
            command_type,
            request_hash,
            expense.id,
        )
        return expense

    def bootstrap_profile(
        self,
        profile: Profile,
        *,
        taxonomy: Taxonomy,
        occurred_at: datetime,
    ) -> None:
        """Satisfy the port; bootstrap is outside these service tests."""


def _fixture() -> tuple[FinanceService, _FakeRepository, Expense, FundingSource]:
    """Build one service with real domain objects and an in-memory repository."""

    profile = Profile(uuid4())
    taxonomy = initial_taxonomy()
    source = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta test",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
    )
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        description="Application service test",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(taxonomy.categories[0].id, Money(1200), "owner"),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(1200)),),
        source_reference="unit:test",
        idempotency_key="unit:command",
    )
    repository = _FakeRepository(taxonomy, (source,))
    service = FinanceService(repository, clock=_FakeClock(), ids=_FakeIds())
    return service, repository, expense, source


def test_create_is_idempotent_and_audited_once() -> None:
    """Return the same aggregate and do not append audit on an identical retry."""

    service, repository, expense, _ = _fixture()
    first = service.create_expense(
        expense,
        request_hash="a" * 64,
        actor="owner",
        reason="manual create",
    )
    retry = service.create_expense(
        expense,
        request_hash="a" * 64,
        actor="owner",
        reason="manual create",
    )

    assert retry == first
    assert len(repository.audits) == 1
    assert repository.audits[0].action == "create"
    assert repository.audits[0].prior_version is None


def test_post_uses_domain_validation_and_records_versioned_audit() -> None:
    """Post through real domain rules and persist the matching version transition."""

    service, repository, expense, _ = _fixture()
    service.create_expense(
        expense,
        request_hash="a" * 64,
        actor="owner",
        reason="manual create",
    )
    posted = service.post_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=1,
        idempotency_key="posting:channel:1",
        request_hash="c" * 64,
        actor="owner",
        reason="approved",
    )

    assert posted.status is ExpenseStatus.POSTED
    assert repository.audits[-1].action == "post"
    assert repository.audits[-1].prior_version == 1
    assert repository.audits[-1].current_version == 2


def test_stale_post_does_not_append_an_audit() -> None:
    """Keep mutation and audit atomic when optimistic concurrency rejects a writer."""

    service, repository, expense, _ = _fixture()
    repository.expenses[expense.id] = expense

    with pytest.raises(OptimisticConcurrencyError, match="stale"):
        service.post_expense(
            profile_id=expense.profile_id,
            expense_id=expense.id,
            expected_version=999,
            idempotency_key="posting:channel:stale",
            request_hash="d" * 64,
            actor="owner",
            reason="stale screen",
        )
    assert repository.audits == []


def test_correction_replaces_both_dimensions_and_preserves_identity() -> None:
    """Pass the complete replacement aggregate through one repository mutation."""

    service, repository, expense, source = _fixture()
    repository.expenses[expense.id] = expense
    corrected = service.correct_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=1,
        amount=Money(1500),
        occurred_at=expense.occurred_at,
        description="Corrected",
        merchant="Merchant",
        category_allocations=(
            CategoryAllocation(
                expense.category_allocations[0].category_id,
                Money(1500),
                "owner-correction",
                owner_override=True,
            ),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(1500)),),
        idempotency_key="correct:unit",
        request_hash="b" * 64,
        actor="owner",
        reason="fixed total",
    )

    assert corrected.id == expense.id
    assert corrected.version == 2
    assert corrected.amount == Money(1500)
    assert repository.audits[-1].action == "correct"


def test_posted_correction_rejects_unavailable_funding_source() -> None:
    """Keep authoritative corrections within active owner-scoped references."""

    service, repository, expense, source = _fixture()
    repository.expenses[expense.id] = expense
    posted = service.post_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=1,
        idempotency_key="posting:before-invalid-correction",
        request_hash="c" * 64,
        actor="owner",
        reason="approved",
    )
    repository.sources = ()

    with pytest.raises(
        DomainError,
        match="posted allocation requires an active funding source",
    ):
        service.correct_expense(
            profile_id=expense.profile_id,
            expense_id=expense.id,
            expected_version=posted.version,
            amount=Money(1500),
            occurred_at=expense.occurred_at,
            description="Invalid corrected source",
            merchant=None,
            category_allocations=(
                CategoryAllocation(
                    expense.category_allocations[0].category_id,
                    Money(1500),
                    "owner:web:v1",
                    owner_override=True,
                ),
            ),
            payment_allocations=(PaymentAllocation(source.id, Money(1500)),),
            idempotency_key="correct:invalid-source",
            request_hash="d" * 64,
            actor="owner",
            reason="manual web correction",
        )

    assert repository.expenses[expense.id] == posted
    assert repository.audits[-1].action == "post"


def test_cancelling_a_posted_movement_voids_and_appends_audit() -> None:
    """Undo by status transition without deleting the stored record."""

    service, repository, expense, _ = _fixture()
    repository.expenses[expense.id] = expense
    posted = service.post_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=1,
        idempotency_key="posting:channel:1",
        request_hash="c" * 64,
        actor="owner",
        reason="approved",
    )
    voided = service.cancel_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=posted.version,
        idempotency_key="undo:unit",
        request_hash="c" * 64,
        actor="owner",
        reason="undo",
    )

    assert voided.status is ExpenseStatus.VOIDED
    assert repository.expenses[expense.id] == voided
    assert [event.action for event in repository.audits] == ["post", "void"]


def test_cancelling_a_draft_discards_it_instead_of_failing() -> None:
    """Give `Annulla` a transition on the state every movement starts in.

    Voiding reverses something that counted, so the domain refuses it for a
    draft. Every Telegram movement is created as a draft and its summary offers
    the control, which therefore could never work: the owner pressed it and got
    a clarification while the movement stayed a draft forever.
    """

    service, repository, expense, _ = _fixture()
    repository.expenses[expense.id] = expense

    cancelled = service.cancel_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=1,
        idempotency_key="undo:draft",
        request_hash="d" * 64,
        actor="owner",
        reason="undo",
    )

    assert cancelled.status is ExpenseStatus.DISCARDED
    assert repository.expenses[expense.id] == cancelled
    assert [event.action for event in repository.audits] == ["discard"]


def test_cancelling_needs_review_discards_it_instead_of_voiding() -> None:
    """Keep the browser's cancel action valid for every unposted state."""

    service, repository, expense, _ = _fixture()
    review = expense.mark_needs_review()
    repository.expenses[expense.id] = review

    cancelled = service.cancel_expense(
        profile_id=expense.profile_id,
        expense_id=expense.id,
        expected_version=review.version,
        idempotency_key="undo:needs-review",
        request_hash="e" * 64,
        actor="owner",
        reason="manual browser cancellation",
    )

    assert cancelled.status is ExpenseStatus.DISCARDED
    assert repository.expenses[expense.id] == cancelled
    assert repository.audits[-1].action == "discard"
    assert repository.audits[-1].prior_version == review.version


def test_cancelling_twice_returns_the_first_result_without_a_second_audit() -> None:
    """Keep one command key across both transitions so a redelivery is safe.

    The first run changes the very status the second would branch on, so
    resolving the transition again would pick the other one and fail. The
    stored result has to answer instead.
    """

    service, repository, expense, _ = _fixture()
    repository.expenses[expense.id] = expense
    arguments = {
        "profile_id": expense.profile_id,
        "expense_id": expense.id,
        "expected_version": 1,
        "idempotency_key": "undo:redelivered",
        "request_hash": "e" * 64,
        "actor": "owner",
        "reason": "undo",
    }

    first = service.cancel_expense(**arguments)
    second = service.cancel_expense(**arguments)

    assert first == second
    assert [event.action for event in repository.audits] == ["discard"]
