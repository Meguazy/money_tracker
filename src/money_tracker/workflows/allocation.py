"""Build reconciled payment allocations from extraction and trusted profile data.

This module is separated from `resolution` by what it depends on. Occurrence
time and amount sign need only the message itself, whereas an allocation needs
the profile's configured funding sources. Those arrive from the repository as
trusted runtime context, never from the model, so a model can neither invent a
funding source nor select the default.

Reconciliation happens here rather than at the model boundary. The extraction
schema rejects an unquantified split because such a split could never
reconcile, but portions that simply do not add up are a statement by the owner,
not a schema violation: they must reach the owner as one targeted question
instead of counting against output-schema validity.

Everything in this module is pure. It reads no repository, calls no model, and
holds no state.
"""

from __future__ import annotations

from money_tracker.domain import (
    FundingSource,
    FundingSourceStatus,
    Money,
    PaymentAllocation,
    sum_money,
)
from money_tracker.workflows.contracts import ExpenseExtraction, ExtractionField
from money_tracker.workflows.errors import (
    ResolutionReason,
    UnresolvedExtractionError,
)
from money_tracker.workflows.resolution import normalize_owner_text, resolve_amount


def resolve_funding_source(
    stated_text: str | None,
    *,
    funding_sources: tuple[FundingSource, ...],
) -> FundingSource:
    """Resolve owner funding words to one active configured source.

    Matching is exact on the folded configured name, so the owner's own naming
    decides. No synonym or fuzzy guess is applied: silently charging the wrong
    resource, in particular spending a benefit balance instead of personal
    funds, is worse than asking one question.

    Args:
        stated_text: Verbatim funding words, or None when none were stated.
        funding_sources: The profile's configured sources from trusted context.

    Returns:
        The matching active source, or the active default when no words were
        stated.

    Raises:
        UnresolvedExtractionError: If no active default is configured, or the
            stated words match no active source.
    """

    active = tuple(
        source
        for source in funding_sources
        if source.status is FundingSourceStatus.ACTIVE
    )
    if stated_text is None:
        for source in active:
            if source.is_default:
                return source
        raise UnresolvedExtractionError(ResolutionReason.NO_DEFAULT_FUNDING_SOURCE)
    needle = normalize_owner_text(stated_text)
    for source in active:
        if normalize_owner_text(source.name) == needle:
            return source
    raise UnresolvedExtractionError(ResolutionReason.FUNDING_SOURCE_UNRESOLVED)


def resolve_payment_allocations(
    extraction: ExpenseExtraction,
    *,
    funding_sources: tuple[FundingSource, ...],
) -> tuple[PaymentAllocation, ...]:
    """Turn extracted funding portions into a reconciled payment dimension.

    Three shapes are supported, matching the product journeys. No stated
    funding means the profile default carries the whole total. One named source
    carries the whole total, whether or not the owner repeated the amount. Two
    or more portions describe a split that must sum exactly to the total.

    Every allocation carries the total's sign, so a refund returns value to its
    sources instead of inverting one dimension against the other.

    Args:
        extraction: Schema-valid extraction.
        funding_sources: The profile's configured sources from trusted context.

    Returns:
        Allocations whose signed amounts sum exactly to the resolved total.

    Raises:
        UnresolvedExtractionError: If the funding arrangement or the total was
            declared unknown, a source cannot be resolved, two portions resolve
            to one source, or the stated portions do not reconcile.
    """

    if ExtractionField.PAYMENT in extraction.unknowns:
        raise UnresolvedExtractionError(ResolutionReason.PAYMENT_UNKNOWN)
    total = resolve_amount(extraction)
    sign = -1 if total.minor_units < 0 else 1

    if not extraction.payment_parts:
        source = resolve_funding_source(None, funding_sources=funding_sources)
        return (PaymentAllocation(source.id, total),)

    resolved: list[PaymentAllocation] = []
    seen: set[str] = set()
    for part in extraction.payment_parts:
        source = resolve_funding_source(
            part.funding_source_text, funding_sources=funding_sources
        )
        if str(source.id) in seen:
            raise UnresolvedExtractionError(ResolutionReason.FUNDING_SOURCE_REPEATED)
        seen.add(str(source.id))
        amount = (
            total
            if part.amount_minor_units is None
            else Money(sign * part.amount_minor_units, total.currency)
        )
        resolved.append(PaymentAllocation(source.id, amount))

    allocations = tuple(resolved)
    if (
        sum_money(tuple(item.amount for item in allocations), currency=total.currency)
        != total
    ):
        raise UnresolvedExtractionError(ResolutionReason.SPLIT_DOES_NOT_RECONCILE)
    return allocations
