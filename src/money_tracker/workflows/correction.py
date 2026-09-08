"""Pure preparation and command identity for correction and undo workflows.

A correction reuses the evaluated extraction and category-fit boundaries. This
module turns their untrusted answers into one complete replacement while the
current aggregate, profile configuration, and stable target all come from
trusted repositories. It imports no LangGraph, model, repository, or transport
adapter.

Fields omitted from a correction preserve the authoritative value. If the total
changes and the existing payment dimension has one source, that source receives
the new total. A multi-source payment must be restated when the total changes,
because guessing how to redistribute the difference would violate the funding
dimension.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from money_tracker.domain import (
    CategoryAllocation,
    Expense,
    FundingSource,
    Money,
    PaymentAllocation,
    Taxonomy,
)
from money_tracker.workflows.allocation import (
    resolve_category_allocations,
    resolve_existing_category,
    resolve_payment_allocations,
)
from money_tracker.workflows.contracts import (
    CategoryFit,
    CategoryFitOutcome,
    ExpenseExtraction,
)
from money_tracker.workflows.errors import (
    ResolutionReason,
    UnresolvedExtractionError,
)
from money_tracker.workflows.intents import WorkflowIntent
from money_tracker.workflows.messages import ChannelMessage
from money_tracker.workflows.preparation import canonical_request_hash
from money_tracker.workflows.resolution import resolve_amount, resolve_occurrence_time

CORRECTION_COMMAND_PREFIX = "correction"
UNDO_COMMAND_PREFIX = "undo"


@dataclass(frozen=True, slots=True)
class PreparedCorrection:
    """A validated full replacement and its canonical request digest.

    Attributes:
        expense: Replacement aggregate preserving stable identity and lifecycle.
        request_hash: SHA-256 of the replacement semantics.
    """

    expense: Expense
    request_hash: str


def movement_command_key(intent: WorkflowIntent, source_reference: str) -> str:
    """Derive a replay-stable key for one correction or undo command.

    Args:
        intent: Mutation route owning the command.
        source_reference: Stable reference to the inbound work unit.

    Returns:
        A command key namespaced by the mutation kind.

    Raises:
        ValueError: If the intent is unsupported or the reference is blank.
    """

    reference = source_reference.strip()
    if not reference:
        raise ValueError("movement command key requires a source reference")
    prefixes = {
        WorkflowIntent.CORRECTION: CORRECTION_COMMAND_PREFIX,
        WorkflowIntent.UNDO: UNDO_COMMAND_PREFIX,
    }
    try:
        prefix = prefixes[intent]
    except KeyError as error:
        raise ValueError("movement command key requires correction or undo") from error
    return f"{prefix}:{reference}"


def undo_request_hash(*, profile_id: UUID, expense_id: UUID) -> str:
    """Hash the complete semantics of one undo command.

    Args:
        profile_id: Trusted owner scope.
        expense_id: Stable movement to void.

    Returns:
        Lowercase SHA-256 of the canonical target representation.
    """

    canonical = json.dumps(
        {"expense_id": str(expense_id), "profile_id": str(profile_id)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_expense_correction(
    current: Expense,
    extraction: ExpenseExtraction,
    fit: CategoryFit,
    *,
    message: ChannelMessage,
    taxonomy: Taxonomy,
    funding_sources: tuple[FundingSource, ...],
) -> PreparedCorrection:
    """Resolve a correction into one complete replacement aggregate.

    Omitted amount, time, merchant, and funding fields preserve the stored
    values. Description and category come from the same evaluated boundaries
    used to create a movement, so the replacement remains a full validated
    aggregate rather than a database patch.

    Args:
        current: Authoritative movement selected by trusted runtime context.
        extraction: Schema-valid replacement facts from the owner message.
        fit: Schema-valid replacement purpose judgment.
        message: Normalized inbound correction work unit.
        taxonomy: Current profile taxonomy.
        funding_sources: Current profile funding configuration.

    Returns:
        The complete corrected aggregate and canonical request hash.

    Raises:
        UnresolvedExtractionError: If stated replacement data cannot resolve,
            a new category would need review, or a changed split is incomplete.
        DomainError: If the complete replacement violates domain invariants.
    """

    amount = (
        current.amount
        if extraction.amount_minor_units is None
        else resolve_amount(extraction)
    )
    occurred_at = (
        current.occurred_at
        if extraction.occurred_at_text is None
        else resolve_occurrence_time(
            extraction.occurred_at_text,
            received_at=message.received_at,
        )
    )
    category_allocations = _replacement_category_allocations(
        extraction,
        fit,
        taxonomy=taxonomy,
        amount_minor_units=amount.minor_units,
    )
    payment_allocations = _replacement_payment_allocations(
        current,
        extraction,
        funding_sources=funding_sources,
        amount_minor_units=amount.minor_units,
    )
    corrected = current.corrected(
        amount=amount,
        occurred_at=occurred_at,
        description=extraction.description,
        merchant=current.merchant
        if extraction.merchant is None
        else extraction.merchant,
        category_allocations=category_allocations,
        payment_allocations=payment_allocations,
    )
    return PreparedCorrection(
        expense=corrected,
        request_hash=canonical_request_hash(corrected),
    )


def _replacement_category_allocations(
    extraction: ExpenseExtraction,
    fit: CategoryFit,
    *,
    taxonomy: Taxonomy,
    amount_minor_units: int,
) -> tuple[CategoryAllocation, ...]:
    """Resolve the full replacement purpose dimension."""

    if fit.outcome is CategoryFitOutcome.PROPOSAL:
        raise UnresolvedExtractionError(ResolutionReason.CATEGORY_APPROVAL_REQUIRED)
    normalized = extraction.model_copy(
        update={"amount_minor_units": abs(amount_minor_units)}
    )
    return resolve_category_allocations(
        normalized,
        category=resolve_existing_category(fit, taxonomy=taxonomy),
        evidence=fit.evidence,
    )


def _replacement_payment_allocations(
    current: Expense,
    extraction: ExpenseExtraction,
    *,
    funding_sources: tuple[FundingSource, ...],
    amount_minor_units: int,
) -> tuple[PaymentAllocation, ...]:
    """Resolve stated funding or preserve a safe existing allocation."""

    if extraction.payment_parts:
        normalized = extraction.model_copy(
            update={"amount_minor_units": abs(amount_minor_units)}
        )
        return resolve_payment_allocations(
            normalized,
            funding_sources=funding_sources,
        )
    if amount_minor_units == current.amount.minor_units:
        return current.payment_allocations
    if len(current.payment_allocations) != 1:
        raise UnresolvedExtractionError(ResolutionReason.SPLIT_DOES_NOT_RECONCILE)
    return (
        PaymentAllocation(
            current.payment_allocations[0].funding_source_id,
            Money(
                amount_minor_units,
                current.amount.currency,
            ),
        ),
    )
