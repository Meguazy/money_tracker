"""Tests for owner decisions on a pending category review."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from uuid import UUID, uuid4

import pytest

from money_tracker.application import (
    OWNER_REVIEW_PROVENANCE,
    CategoryReviewService,
)
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    Category,
    CategoryAllocation,
    CategoryProposal,
    CategoryProposalStatus,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Taxonomy,
    TaxonomyError,
    initial_taxonomy,
)

PROFILE = uuid4()
OCCURRED_AT = datetime(2026, 9, 3, 12, tzinfo=UTC)


@dataclass
class _Repository:
    """In-memory implementation of both contracts the review service needs.

    One object backs the review port and the finance reads because they share
    the same profile state; splitting them would only duplicate the fixtures.
    """

    taxonomy: Taxonomy
    expenses: dict[UUID, Expense] = field(default_factory=dict)
    proposals: dict[UUID, CategoryProposal] = field(default_factory=dict)
    funding_sources: tuple[FundingSource, ...] = ()
    audits: list[AuditEvent] = field(default_factory=list)
    settlements: int = 0

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Return the stored movement in its profile scope."""

        return self.expenses[expense_id]

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the current active taxonomy view."""

        return self.taxonomy

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the configured funding sources."""

        return self.funding_sources

    def get_category_proposal(
        self, profile_id: UUID, proposal_id: UUID
    ) -> CategoryProposal:
        """Return one proposal in its profile scope."""

        return self.proposals[proposal_id]

    def get_pending_category_proposals(
        self, profile_id: UUID
    ) -> tuple[CategoryProposal, ...]:
        """Return every open review in insertion order."""

        return tuple(
            proposal
            for proposal in self.proposals.values()
            if proposal.status is CategoryProposalStatus.PENDING
        )

    def settle_category_review(
        self,
        proposal: CategoryProposal,
        *,
        expense: Expense,
        expected_version: int,
        category: Category | None,
        proposal_audit: AuditEvent,
        expense_audit: AuditEvent,
    ) -> None:
        """Apply the whole settlement or none of it, as one transaction does."""

        stored = self.proposals[proposal.id]
        if stored.status is not CategoryProposalStatus.PENDING:
            raise ValueError("category review is no longer pending")
        if self.expenses[expense.id].version != expected_version:
            raise ValueError("expense changed since the requested version")
        if category is not None:
            self.taxonomy = self.taxonomy.with_category(category)
        self.proposals[proposal.id] = proposal
        self.expenses[expense.id] = expense
        self.audits.extend((proposal_audit, expense_audit))
        self.settlements += 1

    def repropose_category(
        self,
        rejected: CategoryProposal,
        successor: CategoryProposal,
        *,
        rejected_audit: AuditEvent,
        successor_audit: AuditEvent,
    ) -> None:
        """Refuse one candidate and open its replacement together."""

        stored = self.proposals[rejected.id]
        if stored.status is not CategoryProposalStatus.PENDING:
            raise ValueError("category review is no longer pending")
        self.proposals[rejected.id] = rejected
        self.proposals[successor.id] = successor
        self.audits.extend((rejected_audit, successor_audit))
        self.settlements += 1


@dataclass
class _Clock:
    """Return one deterministic audit timestamp."""

    value: datetime = datetime(2026, 9, 3, 20, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the configured aware instant."""

        return self.value


class _Ids:
    """Return distinct deterministic identifiers in call order."""

    def __init__(self) -> None:
        """Start the deterministic identifier sequence."""

        self._counter = count(1)

    def new(self) -> UUID:
        """Return the next identifier."""

        return UUID(int=next(self._counter))


def _card() -> FundingSource:
    """Build one configured default funding source."""

    return FundingSource(
        id=uuid4(),
        profile_id=PROFILE,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )


def _draft(card: FundingSource) -> Expense:
    """Build one stored draft whose purpose dimension is still empty."""

    return Expense(
        id=uuid4(),
        profile_id=PROFILE,
        amount=Money(1200),
        occurred_at=OCCURRED_AT,
        description="croccantini per i gatti",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=card.id, amount=Money(1200)),
        ),
        source_reference="channel:1",
        idempotency_key="key:1",
    )


def _proposal(**overrides: object) -> CategoryProposal:
    """Build one pending proposal with explicit overrides."""

    fields: dict[str, object] = {
        "id": uuid4(),
        "profile_id": PROFILE,
        "proposed_name": "Giardinaggio",
        "description": "Attrezzi e materiali per il giardino.",
        "parent_id": None,
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "alternative_category_ids": (),
        "excluded_names": (),
        "source_reference": "channel:1",
    }
    fields.update(overrides)
    return CategoryProposal(**fields)  # type: ignore[arg-type]


def _service(
    repository: _Repository,
) -> CategoryReviewService:
    """Bind the service to the in-memory repository and deterministic runtime."""

    return CategoryReviewService(repository, repository, clock=_Clock(), ids=_Ids())


def _fixture(
    **proposal_overrides: object,
) -> tuple[_Repository, CategoryReviewService, Expense, CategoryProposal]:
    """Seed one profile with a draft awaiting one pending review."""

    card = _card()
    draft = _draft(card)
    proposal = _proposal(**proposal_overrides)
    repository = _Repository(
        taxonomy=initial_taxonomy(),
        expenses={draft.id: draft},
        proposals={proposal.id: proposal},
        funding_sources=(card,),
    )
    return repository, _service(repository), draft, proposal


def test_approval_creates_the_node_and_posts_the_movement() -> None:
    """Complete J3's approve branch as one settlement."""

    repository, service, draft, proposal = _fixture()

    posted = service.approve(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner approved the candidate",
    )

    assert posted.status is ExpenseStatus.POSTED
    posted.require_reconciled()
    created = repository.taxonomy.resolve_active("Giardinaggio")
    assert posted.category_allocations[0].category_id == created.id
    assert posted.category_allocations[0].amount == Money(1200)
    assert posted.category_allocations[0].provenance == OWNER_REVIEW_PROVENANCE
    assert posted.category_allocations[0].owner_override is False
    assert repository.proposals[proposal.id].status is CategoryProposalStatus.APPROVED
    assert repository.settlements == 1


def test_approved_node_hangs_under_the_proposed_active_root() -> None:
    """Keep the taxonomy two levels deep through the owner decision too."""

    taxonomy = initial_taxonomy()
    root = taxonomy.resolve_active("Casa e utenze")
    repository, service, draft, proposal = _fixture(parent_id=root.id)

    service.approve(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner approved the candidate",
    )

    assert repository.taxonomy.resolve_active("Giardinaggio").parent_id == root.id


def test_approval_refuses_a_candidate_the_taxonomy_would_reject() -> None:
    """Validate through the aggregate rather than trusting the stored name."""

    repository, service, draft, proposal = _fixture(proposed_name="Abbonamenti")

    with pytest.raises(TaxonomyError, match="unique"):
        service.approve(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            actor="owner",
            reason="owner approved the candidate",
        )

    assert repository.settlements == 0
    assert repository.expenses[draft.id].status is ExpenseStatus.DRAFT
    assert repository.proposals[proposal.id].status is CategoryProposalStatus.PENDING


@pytest.mark.parametrize("name", ["Cibo e accessori", "cibo e accessori"])
def test_reuse_posts_against_an_existing_category_and_records_the_override(
    name: str,
) -> None:
    """Serve both reuse branches through names and aliases, never identifiers."""

    repository, service, draft, proposal = _fixture()
    before = len(repository.taxonomy.categories)

    posted = service.use_existing(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=draft.id,
        category_name=name,
        actor="owner",
        reason="owner chose an existing category",
    )

    assert posted.status is ExpenseStatus.POSTED
    assert posted.category_allocations[0].owner_override is True
    assert (
        posted.category_allocations[0].category_id
        == repository.taxonomy.resolve_active(name).id
    )
    assert len(repository.taxonomy.categories) == before
    assert repository.proposals[proposal.id].status is CategoryProposalStatus.REJECTED


def test_reuse_refuses_a_category_the_owner_does_not_have() -> None:
    """Never invent a node from a name that resolves to nothing."""

    repository, service, draft, proposal = _fixture()

    with pytest.raises(TaxonomyError):
        service.use_existing(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            category_name="Categoria inesistente",
            actor="owner",
            reason="owner chose an existing category",
        )

    assert repository.settlements == 0


def test_cancel_voids_the_movement_and_creates_nothing() -> None:
    """Complete J3's cancel branch with no category and no posted movement."""

    repository, service, draft, proposal = _fixture()
    before = len(repository.taxonomy.categories)

    discarded = service.cancel(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner cancelled the review",
    )

    assert discarded.status is ExpenseStatus.DISCARDED
    assert repository.proposals[proposal.id].status is CategoryProposalStatus.CANCELLED
    assert len(repository.taxonomy.categories) == before


def test_repropose_excludes_the_refused_candidate_and_keeps_the_draft() -> None:
    """Offer a distinct candidate without touching the reviewed movement."""

    repository, service, draft, proposal = _fixture()

    successor = service.repropose(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        proposed_name="Piante e fiori",
        description="Piante, fiori e terriccio.",
        parent_name="Casa e utenze",
        rationale="Il candidato precedente era troppo generico.",
        actor="owner",
        reason="owner asked for another candidate",
    )

    assert successor.status is CategoryProposalStatus.PENDING
    assert successor.excluded_names == ("Giardinaggio",)
    assert successor.source_reference == proposal.source_reference
    assert repository.proposals[proposal.id].status is CategoryProposalStatus.REJECTED
    assert repository.expenses[draft.id].status is ExpenseStatus.DRAFT


def test_repropose_refuses_a_duplicate_and_a_non_root_parent() -> None:
    """Apply the same taxonomy governance the first proposal had to satisfy."""

    repository, service, _, proposal = _fixture()

    with pytest.raises(TaxonomyError, match="duplicates"):
        service.repropose(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            proposed_name="Abbonamenti",
            description="Doppione.",
            parent_name=None,
            rationale="Motivo.",
            actor="owner",
            reason="owner asked for another candidate",
        )
    with pytest.raises(TaxonomyError, match="active root"):
        service.repropose(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            proposed_name="Streaming",
            description="Video.",
            parent_name="Abbonamenti",
            rationale="Motivo.",
            actor="owner",
            reason="owner asked for another candidate",
        )
    assert repository.settlements == 0


def test_review_stops_proposing_after_three_refused_candidates() -> None:
    """Bound the proposal loop as AD-008 requires."""

    repository, service, _, proposal = _fixture(
        proposed_name="Terriccio",
        excluded_names=("Giardinaggio", "Piante e fiori"),
    )

    assert service.exhausted(profile_id=PROFILE, proposal_id=proposal.id) is True
    with pytest.raises(TaxonomyError, match="exhausted"):
        service.repropose(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            proposed_name="Terriccio",
            description="Terriccio.",
            parent_name=None,
            rationale="Motivo.",
            actor="owner",
            reason="owner asked for another candidate",
        )
    assert repository.settlements == 0


def test_a_review_below_the_cap_may_still_propose() -> None:
    """Keep the bound a cap on the loop rather than on the first refusal."""

    _, service, _, proposal = _fixture(
        proposed_name="Piante e fiori", excluded_names=("Giardinaggio",)
    )

    assert service.exhausted(profile_id=PROFILE, proposal_id=proposal.id) is False


@pytest.mark.parametrize(
    "status",
    [
        CategoryProposalStatus.APPROVED,
        CategoryProposalStatus.REJECTED,
        CategoryProposalStatus.CANCELLED,
    ],
)
def test_a_settled_review_refuses_every_replayed_decision(
    status: CategoryProposalStatus,
) -> None:
    """Make a resumed thread safe: a decided review is never decided twice."""

    repository, service, draft, proposal = _fixture(status=status)

    for decide in (
        lambda: service.approve(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            actor="owner",
            reason="replay",
        ),
        lambda: service.use_existing(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            category_name="Cibo e accessori",
            actor="owner",
            reason="replay",
        ),
        lambda: service.cancel(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            actor="owner",
            reason="replay",
        ),
        lambda: service.repropose(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            proposed_name="Piante",
            description="Piante.",
            parent_name=None,
            rationale="Motivo.",
            actor="owner",
            reason="replay",
        ),
    ):
        with pytest.raises(ValueError, match="no longer pending"):
            decide()
    assert repository.settlements == 0


def test_settlement_records_both_decisions_in_the_audit_trail() -> None:
    """Keep the owner decision and the movement mutation traceable together."""

    repository, service, draft, proposal = _fixture()

    service.approve(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=draft.id,
        actor="owner",
        reason="owner approved the candidate",
    )

    actions = {(audit.aggregate_type, audit.action) for audit in repository.audits}
    assert actions == {("category_proposal", "approve"), ("expense", "post")}
    assert all(audit.actor == "owner" for audit in repository.audits)
    assert all(audit.source_reference == "channel:1" for audit in repository.audits)


def test_an_archived_category_cannot_receive_the_reviewed_purpose() -> None:
    """Respect archival on the owner path exactly as on the workflow path.

    Resolution is what enforces it: an archived name is not an active name, so
    the owner cannot reach a retired node by typing it.
    """

    archived = Category(
        uuid4(), "Vecchia categoria", "Ritirata.", status=CategoryStatus.ARCHIVED
    )
    card = _card()
    draft = _draft(card)
    proposal = _proposal()
    repository = _Repository(
        taxonomy=Taxonomy((*initial_taxonomy().categories, archived)),
        expenses={draft.id: draft},
        proposals={proposal.id: proposal},
        funding_sources=(card,),
    )

    with pytest.raises(TaxonomyError):
        _service(repository).use_existing(
            profile_id=PROFILE,
            proposal_id=proposal.id,
            expense_id=draft.id,
            category_name="Vecchia categoria",
            actor="owner",
            reason="owner chose an existing category",
        )

    assert repository.settlements == 0


def test_reviewed_allocation_carries_the_refund_sign() -> None:
    """Keep both dimensions on the movement's own polarity."""

    card = _card()
    refund = Expense(
        id=uuid4(),
        profile_id=PROFILE,
        amount=Money(-1500),
        occurred_at=OCCURRED_AT,
        description="rimborso treno",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=card.id, amount=Money(-1500)),
        ),
        source_reference="channel:2",
        idempotency_key="key:2",
    )
    proposal = _proposal(source_reference="channel:2")
    repository = _Repository(
        taxonomy=initial_taxonomy(),
        expenses={refund.id: refund},
        proposals={proposal.id: proposal},
        funding_sources=(card,),
    )

    posted = _service(repository).use_existing(
        profile_id=PROFILE,
        proposal_id=proposal.id,
        expense_id=refund.id,
        category_name="Cibo e accessori",
        actor="owner",
        reason="owner chose an existing category",
    )

    assert posted.category_allocations[0].amount == Money(-1500)
    posted.require_reconciled()


def test_allocation_provenance_distinguishes_owner_from_model() -> None:
    """Let stored history say whether a human or a prompt chose the purpose."""

    assert OWNER_REVIEW_PROVENANCE != "workflow-category-fit:v1"
    allocation = CategoryAllocation(
        category_id=uuid4(),
        amount=Money(1200),
        provenance=OWNER_REVIEW_PROVENANCE,
    )
    assert allocation.provenance == OWNER_REVIEW_PROVENANCE
