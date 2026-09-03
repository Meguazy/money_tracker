"""Real PostgreSQL tests for owner decisions on a pending category review."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from psycopg import connect

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryProposal,
    CategoryProposalStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    TaxonomyError,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository

OCCURRED_AT = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _bootstrap(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, Profile, FundingSource]:
    """Migrate and seed one profile, its taxonomy, and one default source."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    return repository, profile, card


def _services(
    repository: PostgresFinanceRepository,
) -> tuple[CategoryProposalService, CategoryReviewService]:
    """Bind both proposal services to the real repository."""

    clock = SystemClock()
    ids = UUID4Generator()
    return (
        CategoryProposalService(repository, clock=clock, ids=ids),
        CategoryReviewService(repository, repository, clock=clock, ids=ids),
    )


def _draft(
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
    reference: str = "integration:review:1",
) -> Expense:
    """Store one draft whose purpose dimension is deliberately empty."""

    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=OCCURRED_AT,
        description="croccantini per i gatti",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=card.id, amount=Money(1200)),
        ),
        source_reference=reference,
        idempotency_key=f"key:{reference}",
    )
    return repository.create_expense(
        expense,
        audit_event=_audit(expense, "create"),
        request_hash=sha256(reference.encode()).hexdigest(),
    )


def _audit(expense: Expense, action: str) -> AuditEvent:
    """Build the creation audit the repository requires."""

    return AuditEvent(
        id=uuid4(),
        profile_id=expense.profile_id,
        aggregate_type="expense",
        aggregate_id=expense.id,
        action=action,
        actor="test",
        source_reference=expense.source_reference,
        occurred_at=SystemClock().now(),
        prior_version=None,
        current_version=1,
        reason="integration fixture",
    )


def _propose(
    proposals: CategoryProposalService,
    profile: Profile,
    expense: Expense,
    **overrides: object,
) -> CategoryProposal:
    """Submit one pending candidate against a stored draft."""

    fields: dict[str, object] = {
        "id": uuid4(),
        "profile_id": profile.id,
        "proposed_name": "Giardinaggio",
        "description": "Attrezzi e materiali per il giardino.",
        "parent_id": None,
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "alternative_category_ids": (),
        "excluded_names": (),
        "source_reference": expense.source_reference,
    }
    fields.update(overrides)
    return proposals.submit(
        CategoryProposal(**fields),  # type: ignore[arg-type]
        actor="workflow",
        reason="no active category is adequate",
    )


def _category_count(postgres_url: str, profile_id: UUID) -> int:
    """Count taxonomy nodes owned by one profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.categories WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])


@pytest.mark.postgres
def test_approval_creates_the_node_and_posts_in_one_transaction(
    postgres_url: str,
) -> None:
    """Complete J3's approve branch against the real schema."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    draft = _draft(repository, profile, card)
    proposal = _propose(proposals, profile, draft)
    before = _category_count(postgres_url, profile.id)

    posted = review.approve(
        profile_id=profile.id,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner approved the candidate",
    )

    assert posted.status is ExpenseStatus.POSTED
    stored = repository.get_expense(profile.id, draft.id)
    stored.require_reconciled()
    assert stored.status is ExpenseStatus.POSTED
    created = repository.get_taxonomy(profile.id).resolve_active("Giardinaggio")
    assert stored.category_allocations[0].category_id == created.id
    assert created.seeded is False
    assert _category_count(postgres_url, profile.id) == before + 1
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.APPROVED
    )


@pytest.mark.postgres
def test_a_refused_approval_leaves_the_database_untouched(
    postgres_url: str,
) -> None:
    """Prove the settlement is atomic rather than two independent writes.

    Two reviews may hold the same candidate name while neither has been
    approved, because the duplicate check at submission runs against active
    categories. Approving the first creates the node, which must then make the
    second approval fail whole: no node, no post, no settled review.
    """

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    first = _draft(repository, profile, card, reference="integration:review:1")
    second = _draft(repository, profile, card, reference="integration:review:2")
    winner = _propose(proposals, profile, first)
    loser = _propose(proposals, profile, second)
    review.approve(
        profile_id=profile.id,
        proposal_id=winner.id,
        expense_id=first.id,
        actor="owner",
        reason="owner approved the candidate",
    )
    after = _category_count(postgres_url, profile.id)

    with pytest.raises(TaxonomyError, match="unique"):
        review.approve(
            profile_id=profile.id,
            proposal_id=loser.id,
            expense_id=second.id,
            actor="owner",
            reason="owner approved the candidate",
        )

    assert _category_count(postgres_url, profile.id) == after
    assert repository.get_expense(profile.id, second.id).status is ExpenseStatus.DRAFT
    assert (
        repository.get_category_proposal(profile.id, loser.id).status
        is CategoryProposalStatus.PENDING
    )


@pytest.mark.postgres
def test_reuse_posts_without_extending_the_taxonomy(postgres_url: str) -> None:
    """Serve the two reuse branches without creating a node."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    draft = _draft(repository, profile, card)
    proposal = _propose(proposals, profile, draft)
    before = _category_count(postgres_url, profile.id)

    posted = review.use_existing(
        profile_id=profile.id,
        proposal_id=proposal.id,
        expense_id=draft.id,
        category_name="Cibo e accessori",
        actor="owner",
        reason="owner chose an existing category",
    )

    assert posted.status is ExpenseStatus.POSTED
    stored = repository.get_expense(profile.id, draft.id)
    assert stored.category_allocations[0].owner_override is True
    assert _category_count(postgres_url, profile.id) == before
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.REJECTED
    )


@pytest.mark.postgres
def test_cancel_discards_the_draft_and_creates_nothing(postgres_url: str) -> None:
    """Complete J3's cancel branch through the new terminal state."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    draft = _draft(repository, profile, card)
    proposal = _propose(proposals, profile, draft)
    before = _category_count(postgres_url, profile.id)

    discarded = review.cancel(
        profile_id=profile.id,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner cancelled the review",
    )

    assert discarded.status is ExpenseStatus.DISCARDED
    stored = repository.get_expense(profile.id, draft.id)
    assert stored.status is ExpenseStatus.DISCARDED
    assert stored.category_allocations == ()
    assert _category_count(postgres_url, profile.id) == before
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.CANCELLED
    )


@pytest.mark.postgres
def test_repropose_opens_a_successor_and_keeps_the_draft(postgres_url: str) -> None:
    """Refuse one candidate and offer a distinct replacement atomically."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    draft = _draft(repository, profile, card)
    proposal = _propose(proposals, profile, draft)

    successor = review.repropose(
        profile_id=profile.id,
        proposal_id=proposal.id,
        proposed_name="Piante e fiori",
        description="Piante, fiori e terriccio.",
        parent_name="Casa e utenze",
        rationale="Il candidato precedente era troppo generico.",
        actor="owner",
        reason="owner asked for another candidate",
    )

    pending = repository.get_pending_category_proposals(profile.id)
    assert tuple(item.id for item in pending) == (successor.id,)
    assert pending[0].excluded_names == ("Giardinaggio",)
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.REJECTED
    )
    assert repository.get_expense(profile.id, draft.id).status is ExpenseStatus.DRAFT


@pytest.mark.postgres
def test_a_replayed_decision_settles_the_review_only_once(
    postgres_url: str,
) -> None:
    """Make a resumed thread safe against deciding the same review twice."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    draft = _draft(repository, profile, card)
    proposal = _propose(proposals, profile, draft)
    review.approve(
        profile_id=profile.id,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner approved the candidate",
    )
    after = _category_count(postgres_url, profile.id)

    with pytest.raises(ValueError, match="no longer pending"):
        review.approve(
            profile_id=profile.id,
            proposal_id=proposal.id,
            expense_id=draft.id,
            actor="owner",
            reason="owner approved the candidate",
        )

    assert _category_count(postgres_url, profile.id) == after
    assert repository.get_pending_category_proposals(profile.id) == ()


@pytest.mark.postgres
def test_pending_reviews_are_listed_for_routing_correlation(
    postgres_url: str,
) -> None:
    """Give routing the open reviews it correlates an owner reply against."""

    repository, profile, card = _bootstrap(postgres_url)
    proposals, review = _services(repository)
    first = _draft(repository, profile, card, reference="integration:review:1")
    second = _draft(repository, profile, card, reference="integration:review:2")
    one = _propose(proposals, profile, first)
    two = _propose(proposals, profile, second, proposed_name="Piante e fiori")

    pending = repository.get_pending_category_proposals(profile.id)

    assert {item.id for item in pending} == {one.id, two.id}
    review.cancel(
        profile_id=profile.id,
        proposal_id=one.id,
        expense_id=first.id,
        actor="owner",
        reason="owner cancelled the review",
    )
    assert tuple(
        item.id for item in repository.get_pending_category_proposals(profile.id)
    ) == (two.id,)
