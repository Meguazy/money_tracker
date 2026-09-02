"""Tests for pending category-proposal submission."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from money_tracker.application import CategoryProposalService
from money_tracker.domain import (
    AuditEvent,
    CategoryProposal,
    CategoryProposalStatus,
)


@dataclass
class _ProposalRepository:
    """In-memory implementation of the proposal submission contract."""

    proposals: list[CategoryProposal] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    def create_category_proposal(
        self,
        proposal: CategoryProposal,
        *,
        audit_event: AuditEvent,
    ) -> None:
        """Append the proposal and audit in the same fake transaction."""

        self.proposals.append(proposal)
        self.audits.append(audit_event)


@dataclass
class _Clock:
    """Return one deterministic audit timestamp."""

    value: datetime = datetime(2026, 9, 2, 20, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the configured aware instant."""

        return self.value


@dataclass
class _Ids:
    """Return one prepared audit identifier."""

    value: UUID = field(default_factory=uuid4)

    def new(self) -> UUID:
        """Return the prepared identifier."""

        return self.value


def _proposal() -> CategoryProposal:
    """Build a valid pending candidate with review evidence."""

    return CategoryProposal(
        id=uuid4(),
        profile_id=uuid4(),
        proposed_name="Mobilità dolce",
        description="Biciclette, monopattini e relativa manutenzione.",
        parent_id=uuid4(),
        rationale="La distinzione cambia il report dei trasporti.",
        alternative_category_ids=(uuid4(),),
        excluded_names=("Micromobilità occasionale",),
        source_reference="workflow:proposal:42",
    )


def test_submit_persists_pending_proposal_with_matching_audit() -> None:
    """Keep proposal creation distinct from category approval."""

    repository = _ProposalRepository()
    ids = _Ids()
    service = CategoryProposalService(repository, clock=_Clock(), ids=ids)
    proposal = _proposal()

    result = service.submit(
        proposal,
        actor="category-workflow",
        reason="no adequate active category",
    )

    assert result is proposal
    assert repository.proposals == [proposal]
    audit = repository.audits[0]
    assert audit.id == ids.value
    assert audit.aggregate_type == "category_proposal"
    assert audit.aggregate_id == proposal.id
    assert audit.action == "create"
    assert audit.source_reference == proposal.source_reference
    assert audit.prior_version is None
    assert audit.current_version == 1


@pytest.mark.parametrize(
    "status",
    [
        CategoryProposalStatus.APPROVED,
        CategoryProposalStatus.REJECTED,
        CategoryProposalStatus.CANCELLED,
    ],
)
def test_submit_rejects_pre_resolved_status_without_writes(
    status: CategoryProposalStatus,
) -> None:
    """Require a later owner workflow to make every resolution decision."""

    repository = _ProposalRepository()
    service = CategoryProposalService(repository, clock=_Clock(), ids=_Ids())

    with pytest.raises(ValueError, match="must be pending"):
        service.submit(
            replace(_proposal(), status=status),
            actor="invalid-caller",
            reason="attempted pre-resolution",
        )

    assert repository.proposals == []
    assert repository.audits == []
