"""Assemble one complete draft movement from resolved model output.

This is where the separate resolvers become a domain aggregate. It stays pure:
identity, profile scope, taxonomy, and funding sources all arrive as trusted
arguments, so nothing here reads a repository, calls a model, or decides
authorization.

Two product rules shape the result. A movement is always created as a draft,
because the auto-post eligibility policy is calibrated on the evaluation set
and none is promoted yet, so no text message may post itself. And a movement
whose purpose needs a new category is still prepared, with its payment
dimension resolved and its purpose dimension deliberately empty, because
AD-008 lets only owner approval extend the taxonomy while the product baseline
expects the draft to survive the pause.

The command key is derived from the durable inbound record rather than
generated, so a redelivered message claims the same key and the repository
returns the first result instead of creating a second movement.

That guarantee has a precondition on the caller: a redelivery must replay the
stored record unchanged, its channel timestamp included. A message with no
temporal words occurs when it was sent, so re-reading the clock instead of the
record would change the request digest and turn a harmless replay into an
idempotency conflict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from money_tracker.domain import (
    CategoryAllocation,
    CategoryProposal,
    Expense,
    ExpenseStatus,
    FundingSource,
    Money,
    PaymentAllocation,
    Taxonomy,
)
from money_tracker.workflows.allocation import (
    build_category_proposal,
    resolve_category_allocations,
    resolve_existing_category,
    resolve_payment_allocations,
)
from money_tracker.workflows.contracts import (
    CategoryFit,
    CategoryFitOutcome,
    ExpenseExtraction,
)
from money_tracker.workflows.messages import ChannelMessage
from money_tracker.workflows.resolution import resolve_amount, resolve_occurrence_for

EXPENSE_COMMAND_PREFIX = "expense"
"""Namespace for the movement-creation command key.

The repository keys idempotency on profile and key alone, so prefixing by
command keeps a future command on the same inbound record from colliding with
this one.
"""


def expense_command_key(source_reference: str) -> str:
    """Derive the stable creation key for one durable inbound record.

    Args:
        source_reference: Stable reference to the inbound record.

    Returns:
        A key that is identical for every redelivery of that record.

    Raises:
        ValueError: If the source reference is blank.
    """

    reference = source_reference.strip()
    if not reference:
        raise ValueError("command key requires a source reference")
    return f"{EXPENSE_COMMAND_PREFIX}:{reference}"


def canonical_request_hash(expense: Expense) -> str:
    """Compute the canonical digest of one movement's request semantics.

    The digest covers what the owner asked for, not how the record is stored:
    lifecycle status, version, and generated identifiers are excluded, while
    the occurrence instant is normalized to UTC so the same moment expressed
    with different offsets hashes identically. A retry of the same request
    therefore matches, and a changed request is detected as a conflict.

    Args:
        expense: Fully resolved aggregate.

    Returns:
        Lowercase SHA-256 of the canonical request representation.
    """

    payload = {
        "amount_minor_units": expense.amount.minor_units,
        "category_allocations": sorted(
            (str(item.category_id), item.amount.minor_units)
            for item in expense.category_allocations
        ),
        "currency": expense.amount.currency,
        "description": expense.description,
        "merchant": expense.merchant,
        "occurred_at": expense.occurred_at.astimezone(UTC).isoformat(),
        "original_expense_id": (
            None
            if expense.original_expense_id is None
            else str(expense.original_expense_id)
        ),
        "payment_allocations": sorted(
            (str(item.funding_source_id), item.amount.minor_units)
            for item in expense.payment_allocations
        ),
        "profile_id": str(expense.profile_id),
        "receipt_id": (None if expense.receipt_id is None else str(expense.receipt_id)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedExpense:
    """One draft movement with the command identity its creation needs.

    Attributes:
        expense: Draft aggregate carrying its stable command key.
        request_hash: Canonical digest the repository compares on retry.
        proposal: Pending category candidate when the purpose dimension awaits
            owner approval, otherwise None.
    """

    expense: Expense
    request_hash: str
    proposal: CategoryProposal | None = None

    def __post_init__(self) -> None:
        """Keep a pending proposal and a resolved purpose mutually exclusive.

        Raises:
            ValueError: If a proposal accompanies a resolved purpose dimension,
                or a draft has neither.
        """

        has_purpose = bool(self.expense.category_allocations)
        if (self.proposal is None) != has_purpose:
            raise ValueError(
                "a prepared movement has either a resolved purpose or a "
                "pending proposal, never both and never neither"
            )

    @property
    def awaits_category_approval(self) -> bool:
        """Return whether owner approval is required before this can post."""

        return self.proposal is not None


def prepare_expense_draft(
    extraction: ExpenseExtraction,
    fit: CategoryFit,
    *,
    message: ChannelMessage,
    profile_id: UUID,
    expense_id: UUID,
    proposal_id: UUID,
    taxonomy: Taxonomy,
    funding_sources: tuple[FundingSource, ...],
    excluded_category_names: tuple[str, ...] = (),
) -> PreparedExpense:
    """Turn one classified message into a complete draft movement.

    Args:
        extraction: Schema-valid expense or refund extraction.
        fit: Schema-valid purpose judgment.
        message: The inbound record this movement came from.
        profile_id: Trusted owner scope.
        expense_id: Stable identifier from trusted context.
        proposal_id: Stable identifier used only if a proposal is needed.
        taxonomy: The profile's active taxonomy from trusted context.
        funding_sources: The profile's configured sources from trusted context.
        excluded_category_names: Candidates already rejected in this review.

    Returns:
        A draft movement, possibly awaiting category approval.

    Raises:
        UnresolvedExtractionError: If any part of the message cannot become a
            trusted domain value. The reason selects the question to ask.
        DomainError: If the assembled aggregate violates a domain invariant.
    """

    occurred_at = resolve_occurrence_for(extraction, received_at=message.received_at)
    amount = resolve_amount(extraction)
    payment_allocations = resolve_payment_allocations(
        extraction, funding_sources=funding_sources
    )

    category_allocations: tuple[CategoryAllocation, ...] = ()
    proposal: CategoryProposal | None = None
    if fit.outcome is CategoryFitOutcome.EXISTING:
        category_allocations = resolve_category_allocations(
            extraction,
            category=resolve_existing_category(fit, taxonomy=taxonomy),
            evidence=fit.evidence,
        )
    else:
        proposal = build_category_proposal(
            fit,
            taxonomy=taxonomy,
            profile_id=profile_id,
            proposal_id=proposal_id,
            source_reference=message.source_reference,
            excluded_names=excluded_category_names,
        )

    expense = _draft(
        expense_id=expense_id,
        profile_id=profile_id,
        amount=amount,
        occurred_at=occurred_at,
        extraction=extraction,
        message=message,
        category_allocations=category_allocations,
        payment_allocations=payment_allocations,
    )
    return PreparedExpense(
        expense=expense,
        request_hash=canonical_request_hash(expense),
        proposal=proposal,
    )


def _draft(
    *,
    expense_id: UUID,
    profile_id: UUID,
    amount: Money,
    occurred_at: datetime,
    extraction: ExpenseExtraction,
    message: ChannelMessage,
    category_allocations: tuple[CategoryAllocation, ...],
    payment_allocations: tuple[PaymentAllocation, ...],
) -> Expense:
    """Build the draft aggregate with trusted identity and command key."""

    return Expense(
        id=expense_id,
        profile_id=profile_id,
        amount=amount,
        occurred_at=occurred_at,
        description=extraction.description,
        merchant=extraction.merchant,
        status=ExpenseStatus.DRAFT,
        category_allocations=category_allocations,
        payment_allocations=payment_allocations,
        source_reference=message.source_reference,
        idempotency_key=expense_command_key(message.source_reference),
    )
