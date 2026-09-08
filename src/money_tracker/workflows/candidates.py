"""Bind candidate instructions, schemas, and conversions for every caller.

Runtime graphs and evaluations select the same immutable boundary definition.
A candidate is experimental until the decision log promotes its complete
configuration; moving it here grants no posting or taxonomy authority.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from money_tracker.domain import CategoryStatus, Taxonomy
from money_tracker.workflows.contracts import CategoryFit, ExpenseExtraction
from money_tracker.workflows.prompts import (
    CATEGORY_FIT,
    EXTRACTION,
    default_prompt,
    prompt_versions,
    render_active_categories,
    render_active_category_tree,
)
from money_tracker.workflows.wire import (
    WireCategoryFit,
    WireExpenseExtraction,
    build_category_fit_schema,
    category_fit_to_domain,
)


def _active_nodes(taxonomy: Taxonomy) -> tuple[tuple[str, str, str | None], ...]:
    """Render the owner's active taxonomy as name, guidance, and parent name.

    Args:
        taxonomy: The profile's taxonomy.

    Returns:
        One entry per active category, in taxonomy order, whose third element
        is the parent's display name or None for a heading.
    """

    by_id = {category.id: category for category in taxonomy.categories}
    return tuple(
        (
            category.name,
            category.description,
            None if category.parent_id is None else by_id[category.parent_id].name,
        )
        for category in taxonomy.categories
        if category.status is CategoryStatus.ACTIVE
    )


def active_category_names(taxonomy: Taxonomy) -> tuple[str, ...]:
    """Render the owner's active category names for the boundary."""

    return tuple(
        category.name
        for category in taxonomy.categories
        if category.status is CategoryStatus.ACTIVE
    )


@dataclass(frozen=True, slots=True)
class ExtractionCandidate:
    """One extraction boundary under test: its schema, text, and conversion.

    The suite scores what the deterministic resolvers receive, so a boundary
    whose schema differs from the domain contract is compared on exactly the
    same footing as one whose schema is the contract itself. Only the shape the
    model is asked for changes.

    Attributes:
        version: Identifier recorded with the score.
        instructions: Instruction text paired with the schema.
        schema: Contract the completion must satisfy.
        convert: Pure conversion from that contract to the domain extraction.
    """

    version: str
    instructions: str
    schema: type[BaseModel]
    convert: Callable[[BaseModel], ExpenseExtraction]


_EXTRACTION_SHAPES: dict[
    str, tuple[type[BaseModel], Callable[[BaseModel], ExpenseExtraction]]
] = {
    "v2": (ExpenseExtraction, lambda parsed: cast(ExpenseExtraction, parsed)),
    "v3": (
        WireExpenseExtraction,
        lambda parsed: cast(WireExpenseExtraction, parsed).to_domain(),
    ),
    "v4": (
        WireExpenseExtraction,
        lambda parsed: cast(WireExpenseExtraction, parsed).to_domain(),
    ),
    "v5": (
        WireExpenseExtraction,
        lambda parsed: cast(WireExpenseExtraction, parsed).to_domain(),
    ),
    "v6": (
        WireExpenseExtraction,
        lambda parsed: cast(WireExpenseExtraction, parsed).to_domain(),
    ),
}
"""The schema each instruction version is paired with.

The text lives in the prompt catalog and the shape lives here, because a
version is the pair: v2 asks the flat contract for a field it may omit, and v3
onward ask a union in which omitting is not a shape that exists.
"""

EXTRACTION_CANDIDATES = {
    version.short_name: ExtractionCandidate(
        version=version.id,
        instructions=version.text,
        schema=_EXTRACTION_SHAPES[version.short_name][0],
        convert=_EXTRACTION_SHAPES[version.short_name][1],
    )
    for version in prompt_versions(EXTRACTION)
}
"""Extraction boundaries a run may select, by short candidate name."""

DEFAULT_EXTRACTION_CANDIDATE = EXTRACTION_CANDIDATES[
    default_prompt(EXTRACTION).short_name
]
"""The boundary a run uses when none is named."""


@dataclass(frozen=True, slots=True)
class CategoryFitCandidate:
    """One category-fit boundary under test: schema, text, and conversion.

    Both candidates are scored against the same labels through the same
    deterministic resolution, so only the shape the model is asked for differs.

    Attributes:
        version: Identifier recorded with the score.
        instructions: Instruction text paired with the schema.
        build_schema: Contract the completion must satisfy, built from the
            owner's taxonomy. A candidate may bind the category names into
            the schema, which is data rather than a fixed declaration.
        convert: Pure conversion from that contract to the domain judgment.
        render_taxonomy: How this candidate shows the owner's categories. A
            flat list of names and a hierarchy carrying each node's guidance
            are different questions, so the rendering belongs to the candidate
            rather than to the suite.
    """

    version: str
    instructions: str
    build_schema: Callable[[Taxonomy], type[BaseModel]]
    convert: Callable[[BaseModel], CategoryFit]
    render_taxonomy: Callable[[Taxonomy], str]


def _flat_taxonomy(taxonomy: Taxonomy) -> str:
    """Render the name-only context used by the original candidates."""

    return render_active_categories(active_category_names(taxonomy))


def _tree_taxonomy(taxonomy: Taxonomy) -> str:
    """Render the hierarchy and inclusion guidance paired with later candidates."""

    return render_active_category_tree(_active_nodes(taxonomy))


_CATEGORY_FIT_SHAPES: dict[
    str,
    tuple[
        Callable[[Taxonomy], type[BaseModel]],
        Callable[[BaseModel], CategoryFit],
        Callable[[Taxonomy], str],
    ],
] = {
    "v1": (
        lambda _taxonomy: CategoryFit,
        lambda parsed: cast(CategoryFit, parsed),
        _flat_taxonomy,
    ),
    "v2": (
        lambda _taxonomy: WireCategoryFit,
        lambda parsed: cast(WireCategoryFit, parsed).to_domain(),
        _flat_taxonomy,
    ),
    "v3": (
        lambda _taxonomy: WireCategoryFit,
        lambda parsed: cast(WireCategoryFit, parsed).to_domain(),
        _tree_taxonomy,
    ),
    "v4": (
        lambda _taxonomy: WireCategoryFit,
        lambda parsed: cast(WireCategoryFit, parsed).to_domain(),
        _tree_taxonomy,
    ),
    "v5": (
        lambda _taxonomy: WireCategoryFit,
        lambda parsed: cast(WireCategoryFit, parsed).to_domain(),
        _tree_taxonomy,
    ),
    "v6": (
        lambda taxonomy: build_category_fit_schema(active_category_names(taxonomy)),
        category_fit_to_domain,
        _tree_taxonomy,
    ),
}
"""The schema and taxonomy rendering each instruction version is paired with."""

CATEGORY_FIT_CANDIDATES = {
    version.short_name: CategoryFitCandidate(
        version=version.id,
        instructions=version.text,
        build_schema=_CATEGORY_FIT_SHAPES[version.short_name][0],
        convert=_CATEGORY_FIT_SHAPES[version.short_name][1],
        render_taxonomy=_CATEGORY_FIT_SHAPES[version.short_name][2],
    )
    for version in prompt_versions(CATEGORY_FIT)
}
"""Category-fit boundaries a run may select, by short candidate name."""

DEFAULT_CATEGORY_FIT_CANDIDATE = CATEGORY_FIT_CANDIDATES[
    default_prompt(CATEGORY_FIT).short_name
]
"""The boundary a run uses when none is named."""
