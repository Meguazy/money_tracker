"""Application service for owner decisions on a pending category review.

This is the only component that can extend the taxonomy. AD-008 reserves node
creation for an explicit owner decision, so every operation here starts from a
proposal the owner has actually seen and ends in one terminal review state.

Each resolving decision settles the review and completes the movement in one
transaction. That is what J3 requires of approval, and the same guarantee is
applied to the two reuse branches: the owner answered the only open question,
so leaving the movement a draft would strand it with no journey to finish it.

Posting here is not the auto-post policy AD-012 gates. That policy decides
whether to post *without asking*; this path posts only what the owner
explicitly authorized, after asking.

Replay safety uses a stable command record for each proposal decision. An
identical resumed decision returns the first movement or successor proposal;
another branch reusing the proposal key conflicts without applying twice.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from money_tracker.application.ports import (
    CategoryReviewRepository,
    Clock,
    FinanceRepository,
    IdGenerator,
)
from money_tracker.domain import (
    MAX_REVIEW_PROPOSALS,
    AuditEvent,
    Category,
    CategoryAllocation,
    CategoryProposal,
    CategoryProposalStatus,
    Expense,
    Taxonomy,
    TaxonomyError,
    normalize_category_name,
)
from money_tracker.structured_logging import get_logger

OWNER_REVIEW_PROVENANCE = "owner-category-review"
"""Provenance recorded when the owner, not a model, settled the purpose.

Distinct from the category-fit boundary's versioned provenance, because a
stored allocation must say whether a human or a prompt chose it.
"""


_LOGGER = get_logger("money_tracker.application.review")


class CategoryReviewService:
    """Settle owner category decisions and complete the reviewed movement."""

    def __init__(
        self,
        repository: CategoryReviewRepository,
        finance: FinanceRepository,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Bind review settlement to replaceable persistence and runtime ports.

        Args:
            repository: Review settlement transaction boundary.
            finance: Read access to the reviewed movement and its taxonomy.
            clock: Aware authoritative timestamp provider.
            ids: Stable identifier provider for nodes, proposals, and audits.
        """

        self._repository = repository
        self._finance = finance
        self._clock = clock
        self._ids = ids

    def approve(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """Create the proposed node and post the movement atomically.

        The node is validated by extending the real taxonomy aggregate rather
        than by restating its rules, so an approved candidate that would make
        the taxonomy invalid is refused before anything is written.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Review the owner approved.
            expense_id: Movement awaiting its purpose.
            actor: Trusted owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            The posted movement carrying its new purpose allocation.

        Raises:
            TaxonomyError: If the approved node would violate governance.
            ValueError: If the review is not pending.
        """

        identity = _review_identity(
            proposal_id,
            expense_id,
            action="approve",
        )
        replayed = self._replayed_expense(profile_id, expense_id, identity)
        if replayed is not None:
            return replayed
        proposal = self._pending(profile_id, proposal_id)
        expense = self._finance.get_expense(profile_id, expense_id)
        taxonomy = self._finance.get_taxonomy(profile_id)
        category = Category(
            id=self._ids.new(),
            name=proposal.proposed_name,
            description=proposal.description,
            parent_id=proposal.parent_id,
        )
        extended = taxonomy.with_category(category)
        approved = proposal.approve()
        # The proposed name is model output about the owner's spending, so the
        # record names the new node by identifier and leaves the text in the
        # taxonomy where the owner can read it.
        _LOGGER.info(
            "category_review.approved",
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense_id,
            category_id=category.id,
            rooted=proposal.parent_id is not None,
        )
        posted = self._with_purpose(
            expense,
            category=category,
            taxonomy=extended,
            evidence=proposal.rationale,
            owner_override=False,
        )
        return self._repository.settle_category_review(
            approved,
            expense=posted,
            expected_version=expense.version,
            category=category,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            proposal_audit=self._proposal_audit(
                approved, action="approve", actor=actor, reason=reason
            ),
            expense_audit=self._expense_audit(
                posted, action="post", actor=actor, reason=reason
            ),
        )

    def use_existing(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        category_name: str,
        actor: str,
        reason: str,
    ) -> Expense:
        """Refuse the candidate and post against an existing category instead.

        This serves both reuse branches of J3. `Usa una delle categorie
        esistenti` supplies the best alternative the review already examined;
        `Usa la categoria X` supplies the name the owner typed. Both resolve
        through the active taxonomy's names and aliases, never an identifier a
        caller chose, and both record that the owner forced the classification.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Review the owner resolved by reuse.
            expense_id: Movement awaiting its purpose.
            category_name: Active category name or alias the owner chose.
            actor: Trusted owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            The posted movement carrying its purpose allocation.

        Raises:
            TaxonomyError: If no active category matches the name.
            ValueError: If the review is not pending.
        """

        identity = _review_identity(
            proposal_id,
            expense_id,
            action="use_existing",
            category_name=category_name,
        )
        replayed = self._replayed_expense(profile_id, expense_id, identity)
        if replayed is not None:
            return replayed
        proposal = self._pending(profile_id, proposal_id)
        expense = self._finance.get_expense(profile_id, expense_id)
        taxonomy = self._finance.get_taxonomy(profile_id)
        category = taxonomy.resolve_active(category_name)
        posted = self._with_purpose(
            expense,
            category=category,
            taxonomy=taxonomy,
            evidence=category_name,
            owner_override=True,
        )
        rejected = proposal.reject()
        _LOGGER.info(
            "category_review.reused_existing",
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense_id,
            category_id=category.id,
        )
        return self._repository.settle_category_review(
            rejected,
            expense=posted,
            expected_version=expense.version,
            category=None,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            proposal_audit=self._proposal_audit(
                rejected, action="use_existing", actor=actor, reason=reason
            ),
            expense_audit=self._expense_audit(
                posted, action="post", actor=actor, reason=reason
            ),
        )

    def cancel(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
        actor: str,
        reason: str,
    ) -> Expense:
        """End the review with no category and no posted movement.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Review the owner cancelled.
            expense_id: Movement to void with it.
            actor: Trusted owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            The discarded movement.

        Raises:
            ValueError: If the review is not pending.
        """

        identity = _review_identity(
            proposal_id,
            expense_id,
            action="cancel",
        )
        replayed = self._replayed_expense(profile_id, expense_id, identity)
        if replayed is not None:
            return replayed
        proposal = self._pending(profile_id, proposal_id)
        expense = self._finance.get_expense(profile_id, expense_id)
        discarded = expense.discard()
        cancelled = proposal.cancel()
        _LOGGER.info(
            "category_review.cancelled",
            profile_id=profile_id,
            proposal_id=proposal_id,
            expense_id=expense_id,
        )
        return self._repository.settle_category_review(
            cancelled,
            expense=discarded,
            expected_version=expense.version,
            category=None,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            proposal_audit=self._proposal_audit(
                cancelled, action="cancel", actor=actor, reason=reason
            ),
            expense_audit=self._expense_audit(
                discarded, action="discard", actor=actor, reason=reason
            ),
        )

    def repropose(
        self,
        *,
        profile_id: UUID,
        proposal_id: UUID,
        proposed_name: str,
        description: str,
        parent_name: str | None,
        rationale: str,
        actor: str,
        reason: str,
    ) -> CategoryProposal:
        """Refuse this candidate and open a distinct replacement.

        The successor inherits every name already refused in this review, so a
        model cannot offer the same candidate twice, and the movement is left
        untouched because the owner asked for a different name rather than a
        different movement.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Review whose candidate the owner refused.
            proposed_name: Replacement candidate name.
            description: Proposed inclusion guidance.
            parent_name: Active root the candidate belongs under, if any.
            rationale: Why no active category is adequate.
            actor: Trusted owner actor label.
            reason: Non-sensitive audit explanation.

        Returns:
            The new pending proposal.

        Raises:
            TaxonomyError: If the replacement is exhausted, duplicates an
                active name, or names a parent that is not an active root.
            ValueError: If the review is not pending.
        """

        identity = _review_identity(
            proposal_id,
            None,
            action="repropose",
        )
        replayed_id = self._repository.get_idempotent_category_review_command(
            profile_id=profile_id,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
        )
        if replayed_id is not None:
            _LOGGER.info(
                "category_review.command.replayed",
                profile_id=profile_id,
                proposal_id=proposal_id,
                command_type=identity[1],
            )
            return self._repository.get_category_proposal(profile_id, replayed_id)
        proposal = self._pending(profile_id, proposal_id)
        exclusions = proposal.exclusions_for_next()
        if len(exclusions) >= MAX_REVIEW_PROPOSALS:
            # AD-008 caps the loop, so this is the designed end of the branch
            # rather than a defect: the owner is offered reuse or manual entry.
            _LOGGER.warning(
                "category_review.proposals_exhausted",
                profile_id=profile_id,
                proposal_id=proposal_id,
                proposals=len(exclusions),
            )
            raise TaxonomyError("category review has exhausted its proposals")
        taxonomy = self._finance.get_taxonomy(profile_id)
        successor = CategoryProposal(
            id=self._ids.new(),
            profile_id=profile_id,
            proposed_name=proposed_name,
            description=description,
            parent_id=self._active_root(taxonomy, parent_name),
            rationale=rationale,
            alternative_category_ids=proposal.alternative_category_ids,
            excluded_names=exclusions,
            source_reference=proposal.source_reference,
        )
        self._reject_duplicate(taxonomy, proposed_name)
        rejected = proposal.reject()
        _LOGGER.info(
            "category_review.reproposed",
            profile_id=profile_id,
            proposal_id=proposal_id,
            successor_id=successor.id,
            excluded_names=len(exclusions),
        )
        return self._repository.repropose_category(
            rejected,
            successor,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
            rejected_audit=self._proposal_audit(
                rejected, action="repropose", actor=actor, reason=reason
            ),
            successor_audit=self._proposal_audit(
                successor, action="propose", actor=actor, reason=reason
            ),
        )

    def exhausted(self, *, profile_id: UUID, proposal_id: UUID) -> bool:
        """Return whether this review may still offer another candidate.

        AD-008 caps the proposal loop. Once it is reached the owner is offered
        existing categories and manual entry instead of a further candidate.

        Args:
            profile_id: Trusted owner scope.
            proposal_id: Review to test.

        Returns:
            Whether the review has run out of proposals.
        """

        proposal = self._repository.get_category_proposal(profile_id, proposal_id)
        return len(proposal.exclusions_for_next()) >= MAX_REVIEW_PROPOSALS

    def _pending(self, profile_id: UUID, proposal_id: UUID) -> CategoryProposal:
        """Load one review and refuse a decision it has already received."""

        proposal = self._repository.get_category_proposal(profile_id, proposal_id)
        if proposal.status is not CategoryProposalStatus.PENDING:
            _LOGGER.warning(
                "category_review.not_pending",
                profile_id=profile_id,
                proposal_id=proposal_id,
                status=proposal.status.value,
            )
            raise ValueError("category review is no longer pending")
        return proposal

    def _replayed_expense(
        self,
        profile_id: UUID,
        expense_id: UUID,
        identity: tuple[str, str, str],
    ) -> Expense | None:
        """Return an identical review result before reading mutable state."""

        result_id = self._repository.get_idempotent_category_review_command(
            profile_id=profile_id,
            idempotency_key=identity[0],
            command_type=identity[1],
            request_hash=identity[2],
        )
        if result_id is None:
            return None
        if result_id != expense_id:
            # A replay that resolves to a different movement means the command
            # key is being reused across targets, which would silently apply a
            # decision to the wrong movement if it were allowed through.
            _LOGGER.error(
                "category_review.replay_target_changed",
                profile_id=profile_id,
                expense_id=expense_id,
                stored_expense_id=result_id,
                command_type=identity[1],
            )
            raise ValueError("category review replay result target changed")
        _LOGGER.info(
            "category_review.command.replayed",
            profile_id=profile_id,
            expense_id=expense_id,
            command_type=identity[1],
        )
        return self._finance.get_expense(profile_id, result_id)

    @staticmethod
    def _active_root(taxonomy: Taxonomy, parent_name: str | None) -> UUID | None:
        """Resolve an optional parent name to an active root identifier."""

        if parent_name is None:
            return None
        parent = taxonomy.resolve_active(parent_name)
        if parent.parent_id is not None:
            raise TaxonomyError("a proposal parent must be an active root")
        return parent.id

    @staticmethod
    def _reject_duplicate(taxonomy: Taxonomy, proposed_name: str) -> None:
        """Refuse a replacement that an active node or alias already covers."""

        try:
            taxonomy.resolve_active(proposed_name)
        except TaxonomyError:
            return
        raise TaxonomyError("proposal name duplicates an active category")

    def _with_purpose(
        self,
        expense: Expense,
        *,
        category: Category,
        taxonomy: Taxonomy,
        evidence: str,
        owner_override: bool,
    ) -> Expense:
        """Attach the whole signed total to one purpose and post the movement.

        The category is known active: approval builds a fresh node and reuse
        resolves one through `resolve_active`, which refuses an archived name.
        No status guard is repeated here, because an unreachable one could
        never be exercised and would only claim a check that never runs.
        """

        corrected = expense.corrected(
            amount=expense.amount,
            occurred_at=expense.occurred_at,
            description=expense.description,
            merchant=expense.merchant,
            category_allocations=(
                CategoryAllocation(
                    category_id=category.id,
                    amount=expense.amount,
                    provenance=OWNER_REVIEW_PROVENANCE,
                    evidence=evidence,
                    owner_override=owner_override,
                ),
            ),
            payment_allocations=expense.payment_allocations,
        )
        return corrected.post(
            taxonomy=taxonomy,
            funding_sources=self._finance.get_funding_sources(expense.profile_id),
        )

    def _proposal_audit(
        self,
        proposal: CategoryProposal,
        *,
        action: str,
        actor: str,
        reason: str,
    ) -> AuditEvent:
        """Build the immutable record of one owner review decision."""

        created = proposal.status is CategoryProposalStatus.PENDING
        return AuditEvent(
            id=self._ids.new(),
            profile_id=proposal.profile_id,
            aggregate_type="category_proposal",
            aggregate_id=proposal.id,
            action=action,
            actor=actor,
            source_reference=proposal.source_reference,
            occurred_at=self._clock.now(),
            prior_version=None if created else 1,
            current_version=1 if created else 2,
            reason=reason,
        )

    def _expense_audit(
        self,
        expense: Expense,
        *,
        action: str,
        actor: str,
        reason: str,
    ) -> AuditEvent:
        """Build the immutable record of the reviewed movement's mutation."""

        return AuditEvent(
            id=self._ids.new(),
            profile_id=expense.profile_id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action=action,
            actor=actor,
            source_reference=expense.source_reference,
            occurred_at=self._clock.now(),
            prior_version=expense.version - 1,
            current_version=expense.version,
            reason=reason,
        )


def _review_identity(
    proposal_id: UUID,
    expense_id: UUID | None,
    *,
    action: str,
    category_name: str | None = None,
) -> tuple[str, str, str]:
    """Build one stable identity shared by every replay of an owner decision.

    The key is proposal-wide so competing buttons conflict. The hash omits a
    re-proposal model candidate deliberately: the owner's command means "offer
    another candidate", and a retry must return the candidate first committed
    even if a later model completion differs.
    """

    payload: dict[str, str] = {
        "action": action,
        "proposal_id": str(proposal_id),
    }
    if expense_id is not None:
        payload["expense_id"] = str(expense_id)
    if category_name is not None:
        payload["category_name"] = normalize_category_name(category_name)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        f"category-review:{proposal_id}",
        f"category_review_{action}",
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


__all__ = ["OWNER_REVIEW_PROVENANCE", "CategoryReviewService"]
