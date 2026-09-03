"""Framework-independent contracts for the paused owner category review.

A review is the one place the product deliberately stops and waits for a human.
Everything here is pure: it decides what a resuming decision must look like and
what the owner is shown, but performs no persistence, no inference, and no
graph control flow.

The decision contract is narrow on purpose. A callback names one branch of J3
and nothing else, so a channel cannot smuggle a category identifier, a profile,
or an amount into a resume payload. The only free value any branch carries is
the name the owner typed, which is resolved against the owner's own active
taxonomy rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

from money_tracker.domain.errors import DomainError
from money_tracker.workflows.messages import (
    CATEGORY_REVIEW_ACTIONS,
    CallbackAction,
)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """One owner decision resuming a paused category review.

    Attributes:
        action: The review branch the owner selected.
        category_name: Name the owner typed, required by the named-existing
            branch and forbidden everywhere else.
    """

    action: CallbackAction
    category_name: str | None = None

    def __post_init__(self) -> None:
        """Reject a decision no review branch could apply.

        Raises:
            DomainError: If the action is not a review branch, the named
                branch carries no usable name, or another branch carries one.
        """

        if self.action not in CATEGORY_REVIEW_ACTIONS:
            raise DomainError("decision does not belong to a category review")
        named = self.action is CallbackAction.USE_NAMED_EXISTING
        if named and not (self.category_name or "").strip():
            raise DomainError("naming an existing category requires a name")
        if not named and self.category_name is not None:
            raise DomainError("only the named branch carries a category name")

    @property
    def normalized_category_name(self) -> str:
        """Return the owner-typed name with collapsed whitespace.

        Returns:
            The collapsed name.

        Raises:
            DomainError: If this branch carries no name.
        """

        if self.category_name is None:
            raise DomainError("this decision carries no category name")
        return " ".join(self.category_name.split())


@dataclass(frozen=True, slots=True)
class ReviewQuestion:
    """What the owner is shown while a review is paused.

    Built from the stored proposal at pause time and carried in the interrupt
    payload, so it holds only small serializable values. It names categories
    and never identifies them, matching the rule the model boundary follows: a
    channel renders these names and sends back a branch, not an identifier.

    Attributes:
        proposal_id: Stable reference the owner's reply correlates against.
        candidate_name: Name of the candidate awaiting approval.
        parent_name: Active root the candidate would hang under, if any.
        rationale: Why the workflow judged no active category adequate.
        alternative_names: Active categories the workflow examined, offered as
            the reuse shortlist.
        can_repropose: Whether the candidate loop may still offer another name.
            False means the owner is offered existing categories and manual
            entry instead, which is the bound AD-008 requires.
    """

    proposal_id: str
    candidate_name: str
    parent_name: str | None
    rationale: str
    alternative_names: tuple[str, ...]
    can_repropose: bool

    def __post_init__(self) -> None:
        """Reject a question the owner could not act on.

        Raises:
            DomainError: If the candidate or its rationale is blank.
        """

        if not self.candidate_name.strip():
            raise DomainError("a review question requires a candidate name")
        if not self.rationale.strip():
            raise DomainError("a review question requires a rationale")

    def offered_actions(self) -> tuple[CallbackAction, ...]:
        """Return the branches this paused review currently accepts.

        Reuse of a named category and cancellation are always available.
        Approval is always available because a candidate is always pending.
        The shortlist branch appears only when the review examined something to
        reuse, and re-proposal disappears once the loop is capped.

        Returns:
            The offered branches in a stable deterministic order.
        """

        offered = [CallbackAction.APPROVE_CATEGORY]
        if self.alternative_names:
            offered.append(CallbackAction.USE_BEST_EXISTING)
        offered.append(CallbackAction.USE_NAMED_EXISTING)
        if self.can_repropose:
            offered.append(CallbackAction.REPROPOSE_CATEGORY)
        offered.append(CallbackAction.CANCEL_REVIEW)
        return tuple(offered)


__all__ = ["ReviewDecision", "ReviewQuestion"]
