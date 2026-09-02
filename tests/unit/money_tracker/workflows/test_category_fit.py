"""Tests for category-fit resolution against the real accepted taxonomy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from money_tracker.domain import (
    Category,
    CategoryProposalStatus,
    CategoryStatus,
    Expense,
    ExpenseStatus,
    Money,
    PaymentAllocation,
    Taxonomy,
    TaxonomyError,
    initial_taxonomy,
)
from money_tracker.workflows import (
    CATEGORY_PROVENANCE,
    CategoryFit,
    CategoryFitOutcome,
    ExpenseExtraction,
    ResolutionReason,
    UnresolvedExtractionError,
    build_category_proposal,
    resolve_category_allocations,
    resolve_existing_category,
)

TAXONOMY = initial_taxonomy()
PROFILE_ID = uuid4()


def _fit(**overrides: Any) -> CategoryFit:
    """Parse one existing-category judgment through the JSON boundary."""

    payload: dict[str, Any] = {
        "outcome": "existing",
        "category_name": "Cibo e accessori",
        "evidence": "croccantini per i gatti",
    }
    payload.update(overrides)
    return CategoryFit.model_validate_json(json.dumps(payload))


def _proposal_fit(**overrides: Any) -> CategoryFit:
    """Parse one proposal judgment through the JSON boundary."""

    payload: dict[str, Any] = {
        "outcome": "proposal",
        "proposed_name": "Assicurazione animali",
        "description": "Polizze sanitarie per animali domestici.",
        "rationale": "Nessuna categoria attiva copre le polizze per animali.",
        "parent_name": "Animali",
        "considered_category_names": ["Veterinario", "Finanza e obblighi"],
        "evidence": "polizza per il gatto",
    }
    payload.update(overrides)
    return CategoryFit.model_validate_json(json.dumps(payload))


def _extraction(**overrides: Any) -> ExpenseExtraction:
    """Build one schema-valid extraction for allocation tests."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "currency": "EUR",
        "description": "croccantini per i gatti",
        "evidence": "ho speso 12 euro per i croccantini",
    }
    payload.update(overrides)
    return ExpenseExtraction.model_validate_json(json.dumps(payload))


def _reason(callable_: Any) -> ResolutionReason:
    """Return the stable reason one unresolvable judgment reports."""

    with pytest.raises(UnresolvedExtractionError) as error:
        callable_()
    return error.value.reason


def test_existing_outcome_resolves_to_a_stable_seeded_identifier() -> None:
    """Anchor a movement to a category identifier, never to a display name."""

    resolved = resolve_existing_category(_fit(), taxonomy=TAXONOMY)

    assert resolved.name == "Cibo e accessori"
    assert resolved.seeded
    assert resolved.id == TAXONOMY.resolve_active("Cibo e accessori").id


def test_existing_outcome_resolution_is_accent_and_case_insensitive() -> None:
    """Match the owner-facing name however the model reproduced it."""

    resolved = resolve_existing_category(
        _fit(category_name="mobilità"), taxonomy=TAXONOMY
    )
    folded = resolve_existing_category(
        _fit(category_name="MOBILITA"), taxonomy=TAXONOMY
    )

    assert resolved.id == folded.id


def test_invented_category_name_is_refused() -> None:
    """Refuse a node the active taxonomy does not contain."""

    fit = _fit(category_name="Criptovalute")

    assert (
        _reason(lambda: resolve_existing_category(fit, taxonomy=TAXONOMY))
        is ResolutionReason.CATEGORY_UNRESOLVED
    )


def test_existing_resolution_rejects_a_proposal_judgment() -> None:
    """Keep the two outcomes from being resolved through the wrong path."""

    with pytest.raises(ValueError, match="does not select an existing category"):
        resolve_existing_category(_proposal_fit(), taxonomy=TAXONOMY)


def test_existing_outcome_cannot_carry_proposal_fields() -> None:
    """Refuse a judgment that claims both outcomes at once."""

    with pytest.raises(ValidationError, match="cannot carry proposal fields"):
        _fit(rationale="perché no")


def test_existing_outcome_must_name_a_category() -> None:
    """Refuse an existing outcome that resolves to nothing."""

    with pytest.raises(ValidationError, match="must name a category"):
        CategoryFit.model_validate_json(
            json.dumps({"outcome": "existing", "evidence": "croccantini"})
        )


def test_proposal_outcome_requires_its_own_fields() -> None:
    """Refuse a proposal the owner could not evaluate."""

    with pytest.raises(ValidationError, match="requires a name"):
        CategoryFit.model_validate_json(
            json.dumps({"outcome": "proposal", "evidence": "polizza"})
        )
    with pytest.raises(ValidationError, match="cannot also resolve"):
        _proposal_fit(category_name="Animali")


def test_repeated_considered_candidates_are_refused() -> None:
    """Keep the examined-candidate evidence a set."""

    with pytest.raises(ValidationError, match="must be distinct"):
        _proposal_fit(considered_category_names=["Veterinario", "veterinario"])


def test_proposal_uses_trusted_identity_and_resolves_every_named_node() -> None:
    """Take identity from context and check every word against the taxonomy."""

    proposal_id = uuid4()

    proposal = build_category_proposal(
        _proposal_fit(),
        taxonomy=TAXONOMY,
        profile_id=PROFILE_ID,
        proposal_id=proposal_id,
        source_reference="workflow:draft-1",
    )

    assert proposal.id == proposal_id
    assert proposal.profile_id == PROFILE_ID
    assert proposal.status is CategoryProposalStatus.PENDING
    assert proposal.parent_id == TAXONOMY.resolve_active("Animali").id
    assert proposal.alternative_category_ids == (
        TAXONOMY.resolve_active("Veterinario").id,
        TAXONOMY.resolve_active("Finanza e obblighi").id,
    )
    assert proposal.source_reference == "workflow:draft-1"


def test_proposal_without_a_parent_is_allowed() -> None:
    """Permit a new root when no existing purpose fits."""

    proposal = build_category_proposal(
        _proposal_fit(parent_name=None, considered_category_names=[]),
        taxonomy=TAXONOMY,
        profile_id=PROFILE_ID,
        proposal_id=uuid4(),
        source_reference="workflow:draft-1",
    )

    assert proposal.parent_id is None


def test_proposal_parent_must_be_an_active_root() -> None:
    """Hold the two-level limit before a node could ever be created."""

    fit = _proposal_fit(parent_name="Veterinario")

    assert (
        _reason(
            lambda: build_category_proposal(
                fit,
                taxonomy=TAXONOMY,
                profile_id=PROFILE_ID,
                proposal_id=uuid4(),
                source_reference="workflow:draft-1",
            )
        )
        is ResolutionReason.CATEGORY_PARENT_INVALID
    )


def test_proposal_duplicating_an_active_node_is_refused() -> None:
    """Surface a wrong judgment instead of quietly reusing the existing node."""

    fit = _proposal_fit(proposed_name="Veterinario")

    assert (
        _reason(
            lambda: build_category_proposal(
                fit,
                taxonomy=TAXONOMY,
                profile_id=PROFILE_ID,
                proposal_id=uuid4(),
                source_reference="workflow:draft-1",
            )
        )
        is ResolutionReason.CATEGORY_ALREADY_EXISTS
    )


def test_proposal_repeating_a_rejected_candidate_is_refused() -> None:
    """Exclude candidates the owner already rejected in this review."""

    fit = _proposal_fit()

    assert (
        _reason(
            lambda: build_category_proposal(
                fit,
                taxonomy=TAXONOMY,
                profile_id=PROFILE_ID,
                proposal_id=uuid4(),
                source_reference="workflow:draft-1",
                excluded_names=("assicurazione animali",),
            )
        )
        is ResolutionReason.CATEGORY_EXCLUDED
    )


def test_proposal_with_a_fabricated_alternative_is_refused() -> None:
    """Keep the recorded search evidence truthful."""

    fit = _proposal_fit(considered_category_names=["Categoria inesistente"])

    assert (
        _reason(
            lambda: build_category_proposal(
                fit,
                taxonomy=TAXONOMY,
                profile_id=PROFILE_ID,
                proposal_id=uuid4(),
                source_reference="workflow:draft-1",
            )
        )
        is ResolutionReason.CATEGORY_UNRESOLVED
    )


def test_proposal_builder_rejects_an_existing_judgment() -> None:
    """Keep the two outcomes from being built through the wrong path."""

    with pytest.raises(ValueError, match="does not propose a category"):
        build_category_proposal(
            _fit(),
            taxonomy=TAXONOMY,
            profile_id=PROFILE_ID,
            proposal_id=uuid4(),
            source_reference="workflow:draft-1",
        )


def test_category_allocation_takes_the_whole_total_with_provenance() -> None:
    """Record one purpose per text movement, with its classification origin."""

    category = resolve_existing_category(_fit(), taxonomy=TAXONOMY)

    allocations = resolve_category_allocations(
        _extraction(), category=category, evidence="croccantini"
    )

    assert len(allocations) == 1
    assert allocations[0].category_id == category.id
    assert allocations[0].amount == Money(1200)
    assert allocations[0].provenance == CATEGORY_PROVENANCE
    assert allocations[0].evidence == "croccantini"
    assert not allocations[0].owner_override


def test_provenance_records_the_boundary_version() -> None:
    """Keep stored history traceable to the contract that classified it."""

    assert CATEGORY_PROVENANCE == "workflow-category-fit:v1"


def test_owner_forced_reuse_is_recorded_on_the_allocation() -> None:
    """Distinguish an owner decision from the workflow's own judgment."""

    category = resolve_existing_category(_fit(), taxonomy=TAXONOMY)

    allocations = resolve_category_allocations(
        _extraction(), category=category, owner_override=True
    )

    assert allocations[0].owner_override


def test_refund_category_allocation_is_negative() -> None:
    """Keep both dimensions on the same polarity as the total."""

    category = resolve_existing_category(_fit(), taxonomy=TAXONOMY)

    allocations = resolve_category_allocations(
        _extraction(polarity="refund"), category=category
    )

    assert allocations[0].amount == Money(-1200)


def test_unknown_amount_blocks_the_category_dimension() -> None:
    """Refuse to allocate against a total that was never determined."""

    category = resolve_existing_category(_fit(), taxonomy=TAXONOMY)
    extraction = _extraction(
        amount_minor_units=None, currency=None, unknowns=["amount"]
    )

    assert (
        _reason(lambda: resolve_category_allocations(extraction, category=category))
        is ResolutionReason.AMOUNT_UNKNOWN
    )


def test_archived_category_cannot_receive_an_allocation() -> None:
    """Respect archival instead of writing to a retired node."""

    archived = Category(
        id=uuid4(),
        name="Vecchia categoria",
        description="Nodo ritirato.",
        status=CategoryStatus.ARCHIVED,
    )

    with pytest.raises(TaxonomyError, match="does not accept new allocations"):
        resolve_category_allocations(_extraction(), category=archived)


def test_both_dimensions_build_a_reconciled_domain_aggregate() -> None:
    """Prove the purpose dimension is accepted by production domain code."""

    category = resolve_existing_category(_fit(), taxonomy=TAXONOMY)
    extraction = _extraction()
    allocations = resolve_category_allocations(extraction, category=category)

    expense = Expense(
        id=uuid4(),
        profile_id=PROFILE_ID,
        amount=Money(1200),
        occurred_at=datetime(2026, 9, 2, 21, 30, tzinfo=UTC),
        description=extraction.description,
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=allocations,
        payment_allocations=(PaymentAllocation(uuid4(), Money(1200)),),
        source_reference="test:category",
        idempotency_key="test-category-1",
    )

    expense.require_reconciled()

    assert expense.category_allocations[0].category_id == category.id


def test_resolution_creates_no_taxonomy_node() -> None:
    """Hold the AD-008 rule that only owner approval may extend the taxonomy."""

    before = tuple(item.id for item in TAXONOMY.categories)

    build_category_proposal(
        _proposal_fit(),
        taxonomy=TAXONOMY,
        profile_id=PROFILE_ID,
        proposal_id=uuid4(),
        source_reference="workflow:draft-1",
    )

    assert tuple(item.id for item in TAXONOMY.categories) == before
    assert isinstance(TAXONOMY, Taxonomy)


def test_outcome_enum_values_are_closed() -> None:
    """Keep the judgment vocabulary limited to the two documented branches."""

    assert {item.value for item in CategoryFitOutcome} == {"existing", "proposal"}
    with pytest.raises(ValidationError):
        CategoryFit.model_validate_json(
            json.dumps({"outcome": "auto_create", "evidence": "x"})
        )
