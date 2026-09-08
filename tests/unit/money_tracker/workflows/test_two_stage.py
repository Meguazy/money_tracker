"""Tests for the two-stage category judgment proposed in AD-015."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from money_tracker.domain.taxonomy_seed import initial_taxonomy
from money_tracker.workflows.contracts import CategoryFitOutcome
from money_tracker.workflows.model import RepairRequest, StructuredOutputError
from money_tracker.workflows.prompts import (
    CATEGORY_LEAF,
    CATEGORY_SUBTREE,
    default_prompt,
)
from money_tracker.workflows.two_stage import (
    TwoStageCategoryFit,
    active_children,
    active_headings,
)


class _Scripted:
    """A structured-output port that replays prepared answers."""

    def __init__(self, *answers: dict[str, object]) -> None:
        self.answers = list(answers)
        self.instructions: list[str] = []

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: RepairRequest | None = None,
    ) -> str:
        self.instructions.append(instructions)
        return json.dumps(self.answers.pop(0))


def _candidate() -> TwoStageCategoryFit:
    """Build the two-stage judgment from the committed catalogs."""

    subtree = default_prompt(CATEGORY_SUBTREE)
    leaf = default_prompt(CATEGORY_LEAF)
    return TwoStageCategoryFit(subtree.id, leaf.id, subtree.text, leaf.text)


def test_the_first_stage_offers_headings_and_never_a_child() -> None:
    """Keep a heading answer in stage one silent about the level.

    This is the property the decision rests on: the failure it addresses is a
    heading and its children competing in one enum.
    """

    taxonomy = initial_taxonomy()
    schema = _candidate().subtree_schema(taxonomy)
    offered = set(
        schema.model_json_schema()["$defs"]["BoundSubtreeChoice"]["properties"][
            "heading_name"
        ]["enum"]
    )
    headings = {name for name, _ in active_headings(taxonomy)}
    children = {
        name for heading in headings for name, _ in active_children(taxonomy, heading)
    }

    assert offered == headings
    assert not offered & children


def test_the_second_stage_offers_only_the_chosen_headings_children() -> None:
    """Keep the level a choice among siblings rather than among strangers."""

    taxonomy = initial_taxonomy()
    port = _Scripted(
        {"choice": {"kind": "subtree", "heading_name": "Mobilità", "evidence": "x"}},
        {
            "choice": {
                "kind": "child",
                "category_name": "Auto e moto",
                "evidence": "x",
            }
        },
    )

    result = _candidate().judge(port, taxonomy, "Pedaggio autostradale 18,40")

    offered = {
        line.split(":")[0].removeprefix("- ")
        for line in port.instructions[1].splitlines()
        if line.startswith("- ")
    }
    assert offered == {"Trasporto pubblico", "Auto e moto"}
    assert result.fit.category_name == "Auto e moto"
    assert result.heading == "Mobilità"
    assert result.level_chosen_by_model is True


def test_a_heading_wins_only_against_its_own_children() -> None:
    """Keep the legitimate parent answer reachable, which a leaf enum forbids."""

    port = _Scripted(
        {"choice": {"kind": "subtree", "heading_name": "Mobilità", "evidence": "taxi"}},
        {
            "choice": {
                "kind": "the_heading",
                "why_no_child_fits": "neither public transport nor an owned vehicle",
                "evidence": "taxi",
            }
        },
    )

    result = _candidate().judge(port, initial_taxonomy(), "Taxi fino a casa 18 euro")

    assert result.fit.outcome is CategoryFitOutcome.EXISTING
    assert result.fit.category_name == "Mobilità"


def test_a_childless_heading_costs_one_call_not_two() -> None:
    """Skip a stage that has nothing to decide."""

    port = _Scripted(
        {
            "choice": {
                "kind": "subtree",
                "heading_name": "Formazione",
                "evidence": "corso",
            }
        }
    )

    result = _candidate().judge(port, initial_taxonomy(), "Corso di inglese 120 euro")

    assert len(port.instructions) == 1
    assert result.fit.category_name == "Formazione"
    assert result.level_chosen_by_model is False


def test_a_proposal_ends_the_judgment_at_the_first_stage() -> None:
    """Let a purpose no heading covers propose, with a heading as its parent."""

    port = _Scripted(
        {
            "choice": {
                "kind": "no_heading_fits",
                "closest_heading": "Animali",
                "why_closest_is_inadequate": "covers food and veterinary care only",
                "proposed_name": "Toelettatura",
                "parent_name": "Animali",
                "description": "Grooming services for pets",
                "evidence": "toelettatura",
            }
        }
    )

    result = _candidate().judge(port, initial_taxonomy(), "Toelettatura del cane 35")

    assert len(port.instructions) == 1
    assert result.fit.outcome is CategoryFitOutcome.PROPOSAL
    assert result.fit.proposed_name == "Toelettatura"
    assert result.fit.parent_name == "Animali"
    assert result.heading is None
    assert result.level_chosen_by_model is False


def test_a_proposal_parent_must_be_a_heading() -> None:
    """Refuse a proposal parented on a child, which the taxonomy forbids.

    `Veterinario` is a child of `Animali`, and AD-008 keeps proposals parented
    on a heading. The first stage's enum makes the mistake unexpressible rather
    than leaving it to deterministic resolution to catch.
    """

    parented_on_a_child = {
        "choice": {
            "kind": "no_heading_fits",
            "closest_heading": "Animali",
            "why_closest_is_inadequate": "not covered",
            "proposed_name": "Toelettatura",
            "parent_name": "Veterinario",
            "description": "Grooming",
            "evidence": "toelettatura",
        }
    }
    # Twice, because the boundary grants one corrective retry before refusing.
    port = _Scripted(parented_on_a_child, parented_on_a_child)

    with pytest.raises(StructuredOutputError):
        _candidate().judge(port, initial_taxonomy(), "Toelettatura del cane 35")
