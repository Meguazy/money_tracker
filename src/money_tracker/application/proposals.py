"""Application service for auditable pending category proposals."""

from __future__ import annotations

from money_tracker.application.ports import (
    CategoryProposalRepository,
    Clock,
    IdGenerator,
)
from money_tracker.domain import AuditEvent, CategoryProposal, CategoryProposalStatus
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.application.proposals")


class CategoryProposalService:
    """Submit candidates while reserving every owner decision for later workflows."""

    def __init__(
        self,
        repository: CategoryProposalRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind proposal submission to replaceable persistence and runtime ports.

        Args:
            repository: Pending-proposal transaction boundary.
            clock: Aware authoritative timestamp provider.
            ids: Stable audit identifier provider.
        """

        self._repository = repository
        self._clock = clock
        self._ids = ids

    def submit(
        self,
        proposal: CategoryProposal,
        *,
        actor: str,
        reason: str,
    ) -> CategoryProposal:
        """Persist a pending candidate without creating a category.

        Args:
            proposal: Validated candidate and review context.
            actor: Workflow or trusted caller identity.
            reason: Non-sensitive explanation for the audit trail.

        Returns:
            The persisted pending proposal.

        Raises:
            ValueError: If submission attempts to pre-resolve owner review.
        """

        if proposal.status is not CategoryProposalStatus.PENDING:
            _LOGGER.error(
                "category_proposal.not_pending",
                profile_id=proposal.profile_id,
                proposal_id=proposal.id,
                status=proposal.status.value,
            )
            raise ValueError("new category proposal must be pending")
        audit = AuditEvent(
            id=self._ids.new(),
            profile_id=proposal.profile_id,
            aggregate_type="category_proposal",
            aggregate_id=proposal.id,
            action="create",
            actor=actor,
            source_reference=proposal.source_reference,
            occurred_at=self._clock.now(),
            prior_version=None,
            current_version=1,
            reason=reason,
        )
        self._repository.create_category_proposal(proposal, audit_event=audit)
        # The candidate name is model output about owner spending and stays out
        # of the record; the review is identified by its own id.
        _LOGGER.info(
            "category_proposal.submitted",
            profile_id=proposal.profile_id,
            proposal_id=proposal.id,
            alternatives=len(proposal.alternative_category_ids),
        )
        return proposal
