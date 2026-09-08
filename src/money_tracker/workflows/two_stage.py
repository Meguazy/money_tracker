"""Two-stage category fit: choose the heading, then choose the level.

One call over every active category asks a model to decide two things at once,
and the measurements say it decides them with very different reliability. Three
candidates chose the correct subtree on 0.9574, 0.9291 and 0.9433 of the cases
their overall agreement scored at 0.8693, 0.7451 and 0.6732. The subtree is the
robust half of the judgment; the level is not.

Two attempts to fix the level inside one call both moved answers toward the
heading. Rendering the hierarchy as ancestor paths repeated each parent name
once per child and collapsed 116 of 122 failures onto roots; demonstrating the
rule with worked answers raised heading answers from 13 to 25 and silenced the
proposal shape entirely. The rule was already stated and illustrated in the text
both times.

The cause is structural rather than verbal: a heading and its children occupy
one enum, so any signal that makes the heading expressible also makes it
attractive, and the heading cannot simply be removed because five labelled cases
legitimately expect one. This module removes the competition instead of trying
to bias it. The first stage offers headings only, so a heading answer there
carries no information about the level. The second stage offers one heading's
children plus that heading, where it competes against two or three siblings
rather than seventeen strangers.

See AD-015 for the decision this implements and its open questions.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from money_tracker.domain.categories import Taxonomy
from money_tracker.workflows.contracts import CategoryFit, CategoryFitOutcome
from money_tracker.workflows.model import StructuredOutputModel, parse_with_one_repair
from money_tracker.workflows.wire import (
    build_leaf_choice_schema,
    build_subtree_choice_schema,
    leaf_choice_to_domain,
    subtree_choice_to_domain,
)


def active_headings(taxonomy: Taxonomy) -> tuple[tuple[str, str], ...]:
    """Return each active root category with its inclusion guidance.

    Args:
        taxonomy: Active owner taxonomy.

    Returns:
        Name and description for every root, in taxonomy order.
    """

    return tuple(
        (category.name, category.description)
        for category in taxonomy.categories
        if category.parent_id is None and category.status.value == "active"
    )


def active_children(taxonomy: Taxonomy, heading: str) -> tuple[tuple[str, str], ...]:
    """Return one heading's active children with their inclusion guidance.

    Args:
        taxonomy: Active owner taxonomy.
        heading: Root category name.

    Returns:
        Name and description for each child, in taxonomy order. Empty when the
        heading has none, which is how a caller knows to skip the second stage.
    """

    parents = {
        category.id: category.name
        for category in taxonomy.categories
        if category.parent_id is None
    }
    return tuple(
        (category.name, category.description)
        for category in taxonomy.categories
        if category.parent_id is not None
        and parents.get(category.parent_id) == heading
        and category.status.value == "active"
    )


def _render(title: str, nodes: tuple[tuple[str, str], ...]) -> str:
    """Render a named list of categories with their guidance."""

    listed = "\n".join(f"- {name}: {description}" for name, description in nodes)
    return f"\n{title}\n{listed}\n"


@dataclass(frozen=True, slots=True)
class TwoStageJudgment:
    """One judgment and how the two stages reached it.

    Attributes:
        fit: The resolved judgment, identical in shape to a one-call answer.
        heading: The heading the first stage chose, or None when it proposed.
        level_chosen_by_model: Whether the second stage ran. It does not for a
            proposal, nor for a heading that has no children, and a diagnostic
            that cannot tell those apart from a heading the model preferred
            would overstate what the second stage decided.
    """

    fit: CategoryFit
    heading: str | None
    level_chosen_by_model: bool


@dataclass(frozen=True, slots=True)
class TwoStageCategoryFit:
    """The two boundaries composed into one judgment.

    Attributes:
        subtree_version: Identifier of the first stage's instructions.
        leaf_version: Identifier of the second stage's instructions.
        subtree_instructions: First-stage text, without the heading list.
        leaf_instructions: Second-stage text, without the child list.
    """

    subtree_version: str
    leaf_version: str
    subtree_instructions: str
    leaf_instructions: str

    @property
    def version(self) -> str:
        """Return the pair identity a run records for this judgment."""

        return f"{self.subtree_version}+{self.leaf_version}"

    def subtree_schema(self, taxonomy: Taxonomy) -> type[BaseModel]:
        """Build the first stage's contract from the owner's headings."""

        return build_subtree_choice_schema(
            tuple(name for name, _ in active_headings(taxonomy))
        )

    def judge(
        self,
        model: StructuredOutputModel,
        taxonomy: Taxonomy,
        message: str,
    ) -> TwoStageJudgment:
        """Classify one message through both stages.

        Args:
            model: Narrow structured-output port.
            taxonomy: Active owner taxonomy.
            message: Untrusted owner text to interpret.

        Returns:
            The judgment and what each stage contributed.

        Raises:
            StructuredOutputError: If either stage's repaired completion is
                still invalid. The caller answers this with clarification.
        """

        headings = active_headings(taxonomy)
        first = parse_with_one_repair(
            model,
            schema=self.subtree_schema(taxonomy),
            instructions=self.subtree_instructions
            + _render("Headings you may choose from:", headings),
            message=message,
        )
        choice = first.choice  # type: ignore[attr-defined]
        if choice.kind != "subtree":
            return TwoStageJudgment(
                fit=subtree_choice_to_domain(first, ""),
                heading=None,
                level_chosen_by_model=False,
            )

        heading = str(choice.heading_name)
        children = active_children(taxonomy, heading)
        if not children:
            return TwoStageJudgment(
                fit=CategoryFit(
                    outcome=CategoryFitOutcome.EXISTING,
                    category_name=heading,
                    evidence=choice.evidence,
                ),
                heading=heading,
                level_chosen_by_model=False,
            )

        second = parse_with_one_repair(
            model,
            schema=build_leaf_choice_schema(
                heading, tuple(name for name, _ in children)
            ),
            instructions=self.leaf_instructions
            + _render(f"The heading is `{heading}`. Entries beneath it:", children),
            message=message,
        )
        return TwoStageJudgment(
            fit=leaf_choice_to_domain(second, heading),
            heading=heading,
            level_chosen_by_model=True,
        )


__all__ = [
    "TwoStageCategoryFit",
    "TwoStageJudgment",
    "active_children",
    "active_headings",
]
