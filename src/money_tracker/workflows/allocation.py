"""Build both allocation dimensions from extraction and trusted profile data.

This module is separated from `resolution` by what it depends on. Occurrence
time and amount sign need only the message itself, whereas an allocation needs
the profile's configured funding sources and active taxonomy. Those arrive from
the repository as trusted runtime context, never from the model, so a model can
neither invent a funding source nor a category, and cannot select the default
source or anchor stored history to a display name.

Reconciliation happens here rather than at the model boundary. The extraction
schema rejects an unquantified split because such a split could never
reconcile, but portions that simply do not add up are a statement by the owner,
not a schema violation: they must reach the owner as one targeted question
instead of counting against output-schema validity.

Nothing here creates a taxonomy node. A proposal is only assembled for the
owner-approval interrupt that AD-008 requires, and every deterministic refusal
carries a reason code so the workflow asks the matching question.

Everything in this module is pure. It reads no repository, calls no model, and
holds no state.
"""

from __future__ import annotations

from uuid import UUID

from money_tracker.domain import (
    Category,
    CategoryAllocation,
    CategoryProposal,
    CategoryStatus,
    FundingSource,
    FundingSourceStatus,
    Money,
    PaymentAllocation,
    Taxonomy,
    TaxonomyError,
    normalize_category_name,
    sum_money,
)
from money_tracker.workflows.contracts import (
    CATEGORY_FIT_CONTRACT_VERSION,
    CategoryFit,
    CategoryFitOutcome,
    ExpenseExtraction,
    ExtractionField,
)
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


CATEGORY_PROVENANCE = f"workflow-category-fit:{CATEGORY_FIT_CONTRACT_VERSION}"
"""Classification origin recorded on every allocation this module builds.

Stored history therefore states which boundary version classified a movement,
so a later contract change stays traceable instead of silently reinterpreting
past allocations.
"""


def resolve_existing_category(
    fit: CategoryFit,
    *,
    taxonomy: Taxonomy,
) -> Category:
    """Resolve a named existing category to its stable active node.

    Args:
        fit: Judgment whose outcome selects an existing category.
        taxonomy: The profile's active taxonomy from trusted context.

    Returns:
        The matching active category.

    Raises:
        ValueError: If the judgment does not select an existing category.
        UnresolvedExtractionError: If the name matches no active node.
    """

    if fit.outcome is not CategoryFitOutcome.EXISTING or fit.category_name is None:
        raise ValueError("judgment does not select an existing category")
    try:
        return taxonomy.resolve_active(fit.category_name)
    except TaxonomyError as error:
        raise UnresolvedExtractionError(ResolutionReason.CATEGORY_UNRESOLVED) from error


def build_category_proposal(
    fit: CategoryFit,
    *,
    taxonomy: Taxonomy,
    profile_id: UUID,
    proposal_id: UUID,
    source_reference: str,
    excluded_names: tuple[str, ...] = (),
) -> CategoryProposal:
    """Assemble one pending proposal for the owner-approval interrupt.

    Identity, scope, and the workflow reference come from trusted context. The
    model supplies only words, and every word it supplied is checked against
    the active taxonomy before a proposal exists.

    A proposed name that already names an active node is refused rather than
    quietly converted into an existing-category resolution. The adequate node
    exists, so the judgment was wrong, and hiding that would both misreport the
    reason to the owner and suppress a measurable model failure.

    Args:
        fit: Judgment whose outcome proposes a candidate.
        taxonomy: The profile's active taxonomy from trusted context.
        profile_id: Trusted owner scope.
        proposal_id: Stable identifier from trusted context.
        source_reference: Stable draft or workflow reference.
        excluded_names: Candidates already rejected in this review.

    Returns:
        A pending proposal that creates no taxonomy node.

    Raises:
        ValueError: If the judgment does not propose a candidate.
        UnresolvedExtractionError: If the name duplicates an active node, was
            already rejected in this review, names an invalid parent, or lists
            an alternative that does not exist.
    """

    if (
        fit.outcome is not CategoryFitOutcome.PROPOSAL
        or fit.proposed_name is None
        or fit.description is None
        or fit.rationale is None
    ):
        raise ValueError("judgment does not propose a category")

    normalized = normalize_category_name(fit.proposed_name)
    if normalized in {normalize_category_name(name) for name in excluded_names}:
        raise UnresolvedExtractionError(ResolutionReason.CATEGORY_EXCLUDED)
    try:
        taxonomy.resolve_active(fit.proposed_name)
    except TaxonomyError:
        pass
    else:
        raise UnresolvedExtractionError(ResolutionReason.CATEGORY_ALREADY_EXISTS)

    parent_id: UUID | None = None
    if fit.parent_name is not None:
        parent = _resolve_active(fit.parent_name, taxonomy)
        if parent.parent_id is not None:
            raise UnresolvedExtractionError(ResolutionReason.CATEGORY_PARENT_INVALID)
        parent_id = parent.id

    alternatives = tuple(
        _resolve_active(name, taxonomy).id for name in fit.considered_category_names
    )
    return CategoryProposal(
        id=proposal_id,
        profile_id=profile_id,
        proposed_name=fit.proposed_name,
        description=fit.description,
        parent_id=parent_id,
        rationale=fit.rationale,
        alternative_category_ids=tuple(dict.fromkeys(alternatives)),
        excluded_names=excluded_names,
        source_reference=source_reference,
    )


def resolve_category_allocations(
    extraction: ExpenseExtraction,
    *,
    category: Category,
    evidence: str | None = None,
    owner_override: bool = False,
) -> tuple[CategoryAllocation, ...]:
    """Build the purpose dimension for one text movement.

    A text movement carries a single purpose, so one allocation takes the whole
    signed total and reconciles by construction. Multi-purpose splitting is a
    receipt-line concern and is not produced here.

    Args:
        extraction: Schema-valid extraction.
        category: Active category resolved from trusted taxonomy.
        evidence: Concise review evidence to store with the allocation.
        owner_override: Whether the owner forced this category over the
            workflow's own judgment.

    Returns:
        One allocation equal to the resolved total.

    Raises:
        UnresolvedExtractionError: If the total was declared unknown.
        TaxonomyError: If the category no longer accepts allocations.
    """

    if category.status is not CategoryStatus.ACTIVE:
        raise TaxonomyError("category does not accept new allocations")
    total = resolve_amount(extraction)
    return (
        CategoryAllocation(
            category_id=category.id,
            amount=total,
            provenance=CATEGORY_PROVENANCE,
            evidence=evidence,
            owner_override=owner_override,
        ),
    )


def _resolve_active(name: str, taxonomy: Taxonomy) -> Category:
    """Resolve one active category name or raise the workflow reason code."""

    try:
        return taxonomy.resolve_active(name)
    except TaxonomyError as error:
        raise UnresolvedExtractionError(ResolutionReason.CATEGORY_UNRESOLVED) from error
