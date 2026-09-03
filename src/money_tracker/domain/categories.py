"""Controlled two-level spending-purpose taxonomy contracts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from money_tracker.domain.errors import (
    DomainError,
    InvalidTransitionError,
    TaxonomyError,
)

MAX_ACTIVE_CATEGORIES = 30
MAX_CHILDREN_PER_PARENT = 5
MAX_REVIEW_PROPOSALS = 3
"""Candidates one review may offer before it stops proposing.

AD-008 forbids an unbounded proposal loop. After this many rejected candidates
the review offers the owner existing categories and manual entry instead, so a
model that cannot find an adequate name cannot keep asking.
"""


class CategoryStatus(StrEnum):
    """Whether a category may receive new allocations."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class CategoryProposalStatus(StrEnum):
    """Owner-review lifecycle for a candidate category."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


def normalize_category_name(value: str) -> str:
    """Normalize a category name for stable duplicate and alias checks.

    Args:
        value: Human-readable category name or alias.

    Returns:
        Case-folded, accent-insensitive text with normalized whitespace.

    Raises:
        DomainError: If the name is empty after normalization.
    """

    display_value = " ".join(value.strip().split())
    if not display_value:
        raise DomainError("category name cannot be empty")
    decomposed = unicodedata.normalize("NFKD", display_value)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_marks.casefold())


@dataclass(frozen=True, slots=True)
class Category:
    """Stable purpose node in the bounded V1 taxonomy.

    Attributes:
        id: Stable identifier anchoring allocations and budgets.
        name: Owner-facing Italian display name.
        description: Inclusion guidance for deterministic and model-assisted fit.
        parent_id: Root identifier for a second-level node, otherwise ``None``.
        aliases: Alternative owner-facing names.
        status: Whether the node accepts new allocations.
        seeded: Whether the node belongs to the accepted initial taxonomy.
    """

    id: UUID
    name: str
    description: str
    parent_id: UUID | None = None
    aliases: tuple[str, ...] = ()
    status: CategoryStatus = CategoryStatus.ACTIVE
    seeded: bool = False

    def __post_init__(self) -> None:
        """Validate local category fields independent of repository state."""

        normalize_category_name(self.name)
        if not self.description.strip():
            raise DomainError("category description cannot be empty")
        normalized_aliases = tuple(
            normalize_category_name(alias) for alias in self.aliases
        )
        if len(set(normalized_aliases)) != len(normalized_aliases):
            raise TaxonomyError("category aliases must be unique")
        if normalize_category_name(self.name) in normalized_aliases:
            raise TaxonomyError("category alias cannot duplicate its name")

    @property
    def normalized_name(self) -> str:
        """Return the canonical comparison form of the display name."""

        return normalize_category_name(self.name)


@dataclass(frozen=True, slots=True)
class CategoryProposal:
    """Auditable category candidate that does not create a taxonomy node.

    Attributes:
        id: Stable proposal identifier.
        profile_id: Owner scope.
        proposed_name: Candidate display name.
        description: Proposed inclusion guidance.
        parent_id: Optional existing root category.
        rationale: Reason no existing category is adequate.
        alternative_category_ids: Existing nodes considered by the workflow.
        excluded_names: Candidates rejected during the same review.
        source_reference: Stable draft or workflow reference.
        status: Owner-review state.
    """

    id: UUID
    profile_id: UUID
    proposed_name: str
    description: str
    parent_id: UUID | None
    rationale: str
    alternative_category_ids: tuple[UUID, ...]
    excluded_names: tuple[str, ...]
    source_reference: str
    status: CategoryProposalStatus = CategoryProposalStatus.PENDING

    def __post_init__(self) -> None:
        """Reject incomplete or repeated proposal state."""

        normalized_name = normalize_category_name(self.proposed_name)
        if not self.description.strip() or not self.rationale.strip():
            raise DomainError("category proposal requires description and rationale")
        if not self.source_reference.strip():
            raise DomainError("category proposal requires a source reference")
        normalized_exclusions = tuple(
            normalize_category_name(name) for name in self.excluded_names
        )
        if normalized_name in normalized_exclusions:
            raise TaxonomyError("proposal cannot reuse an excluded candidate")
        if len(set(self.alternative_category_ids)) != len(
            self.alternative_category_ids
        ):
            raise TaxonomyError("proposal alternatives must be unique")

    def _decided(self, status: CategoryProposalStatus) -> CategoryProposal:
        """Return this proposal in one terminal review state.

        Args:
            status: Terminal state the owner's decision selects.

        Returns:
            A new proposal carrying the decided status.

        Raises:
            InvalidTransitionError: If this proposal is not pending. A decided
                proposal is never re-decided, so a replayed owner decision is
                recognized by its stored status rather than applied twice.
        """

        if self.status is not CategoryProposalStatus.PENDING:
            raise InvalidTransitionError("only a pending proposal can be decided")
        return replace(self, status=status)

    def approve(self) -> CategoryProposal:
        """Accept the candidate so a taxonomy node may be created from it.

        Approval alone creates nothing. `Taxonomy.with_category` builds the node
        and re-validates the whole taxonomy, so governance stays in one place.

        Returns:
            The approved proposal.

        Raises:
            InvalidTransitionError: If this proposal is not pending.
        """

        return self._decided(CategoryProposalStatus.APPROVED)

    def reject(self) -> CategoryProposal:
        """Refuse this candidate while the review continues.

        The owner asked for a different candidate, so the review stays open and
        the next proposal must exclude this name.

        Returns:
            The rejected proposal.

        Raises:
            InvalidTransitionError: If this proposal is not pending.
        """

        return self._decided(CategoryProposalStatus.REJECTED)

    def cancel(self) -> CategoryProposal:
        """End the review without a category and without a posted movement.

        Returns:
            The cancelled proposal.

        Raises:
            InvalidTransitionError: If this proposal is not pending.
        """

        return self._decided(CategoryProposalStatus.CANCELLED)

    def exclusions_for_next(self) -> tuple[str, ...]:
        """Return the candidate names a successor proposal may not reuse.

        A re-proposal must differ from every candidate already refused in this
        review, so this accumulates rather than replaces: AD-008 excludes a
        rejected candidate from subsequent suggestions in the same review.

        Returns:
            This proposal's own exclusions extended by its own name.
        """

        return (*self.excluded_names, self.proposed_name)


@dataclass(frozen=True, slots=True)
class TaxonomyWarning:
    """Non-blocking signal that the taxonomy crossed a soft complexity limit.

    Attributes:
        code: Stable machine-readable warning code.
        message: Operator-facing explanation.
    """

    code: str
    message: str


class Taxonomy:
    """Validate category relationships, names, aliases, and soft guardrails."""

    def __init__(self, categories: tuple[Category, ...]) -> None:
        """Build a validated immutable taxonomy view.

        Args:
            categories: Complete category set in one profile scope.

        Raises:
            TaxonomyError: If identifiers, names, aliases, or hierarchy conflict.
        """

        self._categories = categories
        self._by_id = {category.id: category for category in categories}
        if len(self._by_id) != len(categories):
            raise TaxonomyError("category identifiers must be unique")
        self._validate_hierarchy()
        self._validate_active_names_and_aliases()

    @property
    def categories(self) -> tuple[Category, ...]:
        """Return categories in their supplied deterministic order."""

        return self._categories

    def category(self, category_id: UUID) -> Category:
        """Resolve a stable category identifier.

        Args:
            category_id: Identifier to resolve.

        Returns:
            Matching category.

        Raises:
            TaxonomyError: If the category does not exist.
        """

        try:
            return self._by_id[category_id]
        except KeyError as error:
            raise TaxonomyError("category does not exist") from error

    def resolve_active(self, name_or_alias: str) -> Category:
        """Resolve one active category by normalized name or alias.

        Args:
            name_or_alias: Owner-supplied display name or alias.

        Returns:
            Matching active category.

        Raises:
            TaxonomyError: If no active category matches.
        """

        needle = normalize_category_name(name_or_alias)
        for category in self._categories:
            if category.status is not CategoryStatus.ACTIVE:
                continue
            keys = {
                category.normalized_name,
                *(normalize_category_name(a) for a in category.aliases),
            }
            if needle in keys:
                return category
        raise TaxonomyError("active category does not exist")

    def with_category(self, category: Category) -> Taxonomy:
        """Return this taxonomy extended by one new node.

        Every governance rule is re-checked by rebuilding the whole taxonomy
        rather than restating a subset here: identifier uniqueness, the
        two-level depth limit, an existing active parent, and unambiguous
        active names and aliases. A node that would violate any of them is
        refused, so an approved proposal cannot widen the taxonomy in a way a
        freshly loaded profile would reject.

        Args:
            category: New node to add.

        Returns:
            A validated taxonomy including the new node.

        Raises:
            TaxonomyError: If the extended taxonomy would violate governance.
        """

        return Taxonomy((*self._categories, category))

    def ancestor_ids(self, category_id: UUID) -> tuple[UUID, ...]:
        """Return the assigned category and its optional root for roll-ups.

        Args:
            category_id: Assigned category identifier.

        Returns:
            Child then parent for a subcategory, otherwise only the root.
        """

        category = self.category(category_id)
        if category.parent_id is None:
            return (category.id,)
        return (category.id, category.parent_id)

    def warnings(self) -> tuple[TaxonomyWarning, ...]:
        """Return soft complexity warnings without mutating the taxonomy."""

        active = tuple(
            category
            for category in self._categories
            if category.status is CategoryStatus.ACTIVE
        )
        warnings: list[TaxonomyWarning] = []
        if len(active) > MAX_ACTIVE_CATEGORIES:
            warnings.append(
                TaxonomyWarning(
                    code="active_category_limit",
                    message=(
                        f"active taxonomy has {len(active)} nodes; "
                        f"review above {MAX_ACTIVE_CATEGORIES}"
                    ),
                )
            )
        child_counts: dict[UUID, int] = {}
        for category in active:
            if category.parent_id is not None:
                child_counts[category.parent_id] = (
                    child_counts.get(category.parent_id, 0) + 1
                )
        for parent_id, count in sorted(
            child_counts.items(), key=lambda item: str(item[0])
        ):
            if count > MAX_CHILDREN_PER_PARENT:
                warnings.append(
                    TaxonomyWarning(
                        code="children_per_parent_limit",
                        message=(
                            f"category {parent_id} has {count} active children; "
                            f"review above {MAX_CHILDREN_PER_PARENT}"
                        ),
                    )
                )
        return tuple(warnings)

    def _validate_hierarchy(self) -> None:
        """Enforce existing active roots and a maximum of two levels."""

        for category in self._categories:
            if category.parent_id is None:
                continue
            parent = self._by_id.get(category.parent_id)
            if parent is None:
                raise TaxonomyError("category parent does not exist")
            if parent.parent_id is not None:
                raise TaxonomyError("category hierarchy cannot exceed two levels")
            if (
                category.status is CategoryStatus.ACTIVE
                and parent.status is not CategoryStatus.ACTIVE
            ):
                raise TaxonomyError("an active child requires an active parent")

    def _validate_active_names_and_aliases(self) -> None:
        """Keep all active names and aliases unambiguous."""

        owners: dict[str, UUID] = {}
        for category in self._categories:
            if category.status is not CategoryStatus.ACTIVE:
                continue
            keys = (category.normalized_name,) + tuple(
                normalize_category_name(alias) for alias in category.aliases
            )
            for key in keys:
                existing = owners.get(key)
                if existing is not None and existing != category.id:
                    raise TaxonomyError(
                        "active category names and aliases must be unique"
                    )
                owners[key] = category.id
