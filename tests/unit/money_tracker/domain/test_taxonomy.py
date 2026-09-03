"""Tests for the controlled two-level purpose taxonomy."""

from uuid import uuid4

import pytest

from money_tracker.domain import (
    Category,
    CategoryProposal,
    CategoryProposalStatus,
    CategoryStatus,
    InvalidTransitionError,
    Taxonomy,
    TaxonomyError,
    initial_taxonomy,
    normalize_category_name,
)


def test_initial_taxonomy_has_accepted_shape_and_stable_ids() -> None:
    """Seed exactly ten roots and fourteen children with deterministic IDs."""

    first = initial_taxonomy()
    second = initial_taxonomy()
    roots = tuple(item for item in first.categories if item.parent_id is None)
    children = tuple(item for item in first.categories if item.parent_id is not None)

    assert len(roots) == 10
    assert len(children) == 14
    assert tuple(item.id for item in first.categories) == tuple(
        item.id for item in second.categories
    )
    assert first.resolve_active("abbonamenti").name == "Abbonamenti"
    assert all(item.description for item in first.categories)
    assert first.warnings() == ()


def test_names_and_aliases_are_accent_and_case_insensitive() -> None:
    """Resolve owner input consistently and prevent ambiguous active keys."""

    category_id = uuid4()
    category = Category(category_id, "Mobilità", "Trasporti.", aliases=("Trasporti",))
    taxonomy = Taxonomy((category,))

    assert normalize_category_name("  MOBILITÀ ") == "mobilita"
    assert taxonomy.resolve_active("mobilita") == category
    assert taxonomy.resolve_active("TRASPORTI") == category

    with pytest.raises(TaxonomyError, match="unique"):
        Taxonomy(
            (
                category,
                Category(uuid4(), "Trasporti", "Duplicato."),
            )
        )


def test_taxonomy_rejects_a_third_level() -> None:
    """Allow one root and one child level, never a grandchild."""

    root = Category(uuid4(), "Radice", "Radice di prova.")
    child = Category(uuid4(), "Figlia", "Figlia di prova.", root.id)
    grandchild = Category(uuid4(), "Nipote", "Nipote di prova.", child.id)

    with pytest.raises(TaxonomyError, match="two levels"):
        Taxonomy((root, child, grandchild))


def test_proposal_cannot_repeat_a_rejected_candidate() -> None:
    """Keep re-proposal memory in the durable proposal contract."""

    with pytest.raises(TaxonomyError, match="excluded"):
        CategoryProposal(
            id=uuid4(),
            profile_id=uuid4(),
            proposed_name="Giardinaggio",
            description="Materiali e attrezzi per il giardino.",
            parent_id=None,
            rationale="Nessuna categoria esistente descrive lo scopo.",
            alternative_category_ids=(),
            excluded_names=("GIARDINAGGIO",),
            source_reference="draft:example",
        )


def test_active_child_cannot_depend_on_an_archived_parent() -> None:
    """Prevent automatic allocations under a root unavailable for new spending."""

    root = Category(
        uuid4(), "Archiviata", "Radice storica.", status=CategoryStatus.ARCHIVED
    )
    child = Category(uuid4(), "Attiva", "Figlia attiva.", parent_id=root.id)
    with pytest.raises(TaxonomyError, match="active parent"):
        Taxonomy((root, child))


def test_taxonomy_soft_limits_warn_without_blocking_growth() -> None:
    """Return deterministic warnings above node and per-parent thresholds."""

    root = Category(uuid4(), "Radice", "Radice di prova.")
    children = tuple(
        Category(uuid4(), f"Figlia {index}", f"Figlia numero {index}.", root.id)
        for index in range(6)
    )
    extra_roots = tuple(
        Category(uuid4(), f"Extra {index}", f"Radice extra {index}.")
        for index in range(25)
    )
    warnings = Taxonomy((root, *children, *extra_roots)).warnings()

    assert {warning.code for warning in warnings} == {
        "active_category_limit",
        "children_per_parent_limit",
    }


def test_unknown_or_archived_name_does_not_resolve_as_active() -> None:
    """Exclude archived history from new stable-name resolution."""

    archived = Category(
        uuid4(),
        "Vecchia",
        "Categoria non più usata.",
        status=CategoryStatus.ARCHIVED,
    )
    taxonomy = Taxonomy((archived,))
    with pytest.raises(TaxonomyError, match="does not exist"):
        taxonomy.resolve_active("Vecchia")
    with pytest.raises(TaxonomyError, match="does not exist"):
        taxonomy.category(uuid4())


def _proposal(**overrides: object) -> CategoryProposal:
    """Build one pending proposal with explicit overrides."""

    fields: dict[str, object] = {
        "id": uuid4(),
        "profile_id": uuid4(),
        "proposed_name": "Giardinaggio",
        "description": "Materiali e attrezzi per il giardino.",
        "parent_id": None,
        "rationale": "Nessuna categoria esistente descrive lo scopo.",
        "alternative_category_ids": (),
        "excluded_names": (),
        "source_reference": "draft:example",
    }
    fields.update(overrides)
    return CategoryProposal(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("decide", "expected"),
    [
        ("approve", CategoryProposalStatus.APPROVED),
        ("reject", CategoryProposalStatus.REJECTED),
        ("cancel", CategoryProposalStatus.CANCELLED),
    ],
)
def test_pending_proposal_reaches_each_terminal_state(
    decide: str, expected: CategoryProposalStatus
) -> None:
    """Record the owner's decision without creating anything."""

    proposal = _proposal()

    decided = getattr(proposal, decide)()

    assert decided.status is expected
    assert proposal.status is CategoryProposalStatus.PENDING
    assert decided.proposed_name == proposal.proposed_name


@pytest.mark.parametrize("decide", ["approve", "reject", "cancel"])
@pytest.mark.parametrize(
    "status",
    [
        CategoryProposalStatus.APPROVED,
        CategoryProposalStatus.REJECTED,
        CategoryProposalStatus.CANCELLED,
    ],
)
def test_a_decided_proposal_is_never_decided_again(
    decide: str, status: CategoryProposalStatus
) -> None:
    """Let a replayed owner decision be recognized rather than reapplied."""

    proposal = _proposal(status=status)

    with pytest.raises(InvalidTransitionError, match="pending"):
        getattr(proposal, decide)()


def test_re_proposal_exclusions_accumulate_across_a_review() -> None:
    """Refuse every candidate the owner already rejected in this review."""

    first = _proposal(proposed_name="Giardinaggio")

    exclusions = first.exclusions_for_next()

    assert exclusions == ("Giardinaggio",)
    second = _proposal(proposed_name="Piante", excluded_names=exclusions)
    assert second.exclusions_for_next() == ("Giardinaggio", "Piante")
    with pytest.raises(TaxonomyError, match="excluded"):
        _proposal(proposed_name="giardinaggio", excluded_names=exclusions)


def test_approved_candidate_becomes_a_node_under_an_active_root() -> None:
    """Extend the taxonomy only through the aggregate that governs it."""

    taxonomy = initial_taxonomy()
    root = taxonomy.resolve_active("Casa e utenze")
    node = Category(uuid4(), "Giardinaggio", "Attrezzi.", parent_id=root.id)

    extended = taxonomy.with_category(node)

    assert extended.resolve_active("Giardinaggio").parent_id == root.id
    assert len(extended.categories) == len(taxonomy.categories) + 1
    # The source taxonomy is unchanged, so a refused extension leaves no trace.
    with pytest.raises(TaxonomyError):
        taxonomy.resolve_active("Giardinaggio")


def test_extension_refuses_a_duplicate_name_and_a_third_level() -> None:
    """Re-check every governance rule instead of trusting the caller."""

    taxonomy = initial_taxonomy()
    child = taxonomy.resolve_active("Abbonamenti")

    with pytest.raises(TaxonomyError, match="two levels"):
        taxonomy.with_category(
            Category(uuid4(), "Streaming", "Video.", parent_id=child.id)
        )
    with pytest.raises(TaxonomyError, match="unique"):
        taxonomy.with_category(Category(uuid4(), "Abbonamenti", "Doppione."))
