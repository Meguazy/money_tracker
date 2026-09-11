"""Tests for the union-shaped model boundary and its conversion."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from money_tracker.domain import YearMonth
from money_tracker.runtime.model import strict_json_schema
from money_tracker.workflows.contracts import CategoryFitOutcome, ExpensePolarity
from money_tracker.workflows.prompts import (
    render_active_category_tree,
    render_selection_categories,
)
from money_tracker.workflows.wire import (
    WireCategoryFit,
    WireExpenseExtraction,
    WireToolRequest,
    build_category_fit_schema,
)


def _payload(**overrides: object) -> str:
    """Serialize one wire completion with explicit overrides."""

    payload: dict[str, object] = {
        "polarity": "expense",
        "amount": {"kind": "amount_stated", "minor_units": 2500},
        "occurrence": {"kind": "time_not_stated"},
        "description": "benzina",
        "merchant": {"kind": "merchant_not_named"},
        "funding": {"kind": "funding_not_stated"},
        "evidence": "25 euro di benzina",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_an_unstated_field_converts_to_the_absence_the_resolvers_read() -> None:
    """Keep the meaning of silence exactly what the domain contract expects."""

    extraction = WireExpenseExtraction.model_validate_json(_payload()).to_domain()

    assert extraction.polarity is ExpensePolarity.EXPENSE
    assert extraction.amount_minor_units == 2500
    assert extraction.occurred_at_text is None
    assert extraction.merchant is None
    assert extraction.payment_parts == ()


def test_every_stated_field_survives_conversion() -> None:
    """Carry each variant's payload through to the domain contract."""

    extraction = WireExpenseExtraction.model_validate_json(
        _payload(
            occurrence={"kind": "time_stated", "text": "ieri"},
            merchant={"kind": "merchant_named", "name": "Coop"},
            funding={"kind": "funding_one_method", "funding_source_text": "carta"},
        )
    ).to_domain()

    assert extraction.occurred_at_text == "ieri"
    assert extraction.merchant == "Coop"
    assert len(extraction.payment_parts) == 1
    assert extraction.payment_parts[0].funding_source_text == "carta"
    assert extraction.payment_parts[0].amount_minor_units is None


def test_a_single_named_method_carries_no_amount_by_shape() -> None:
    """The one-method variant has nowhere to put an amount, so none is invented."""

    with pytest.raises(ValidationError):
        WireExpenseExtraction.model_validate_json(
            _payload(
                funding={
                    "kind": "funding_one_method",
                    "funding_source_text": "carta",
                    "minor_units": 2500,
                }
            )
        )


def test_a_split_must_quantify_every_part_by_shape() -> None:
    """An unquantified split is unexpressible rather than rejected afterwards."""

    with pytest.raises(ValidationError):
        WireExpenseExtraction.model_validate_json(
            _payload(
                funding={
                    "kind": "funding_split",
                    "parts": [
                        {"funding_source_text": "carta", "minor_units": 3000},
                        {"funding_source_text": "contanti"},
                    ],
                }
            )
        )


def test_a_split_needs_at_least_two_distinct_methods() -> None:
    """One portion is not a split, and a repeated method could not reconcile."""

    with pytest.raises(ValidationError):
        WireExpenseExtraction.model_validate_json(
            _payload(
                funding={
                    "kind": "funding_split",
                    "parts": [{"funding_source_text": "carta", "minor_units": 3000}],
                }
            )
        )
    with pytest.raises(ValidationError, match="distinct payment methods"):
        WireExpenseExtraction.model_validate_json(
            _payload(
                funding={
                    "kind": "funding_split",
                    "parts": [
                        {"funding_source_text": "carta", "minor_units": 3000},
                        {"funding_source_text": "Carta", "minor_units": 2000},
                    ],
                }
            )
        )


def test_a_quantified_split_converts_to_reconcilable_parts() -> None:
    """Carry every portion and its amount into the domain contract."""

    extraction = WireExpenseExtraction.model_validate_json(
        _payload(
            amount={"kind": "amount_stated", "minor_units": 5000},
            funding={
                "kind": "funding_split",
                "parts": [
                    {"funding_source_text": "carta", "minor_units": 3000},
                    {"funding_source_text": "contanti", "minor_units": 2000},
                ],
            },
        )
    ).to_domain()

    amounts = [part.amount_minor_units for part in extraction.payment_parts]

    assert amounts == [3000, 2000]
    assert sum(amount for amount in amounts if amount) == 5000


def test_an_unknown_variant_or_extra_field_is_refused() -> None:
    """Keep the boundary closed, so no shape outside the union can arrive."""

    with pytest.raises(ValidationError):
        WireExpenseExtraction.model_validate_json(
            _payload(merchant={"kind": "merchant_maybe", "name": "Coop"})
        )
    with pytest.raises(ValidationError):
        WireExpenseExtraction.model_validate_json(_payload(category="alimentari"))


def test_the_boundary_states_no_currency_and_no_account() -> None:
    """Keep operational configuration out of what the model is asked for."""

    fields = set(WireExpenseExtraction.model_fields)

    assert "currency" not in fields
    assert not fields & {"funding_source_id", "category_name", "profile_id"}


def test_every_optional_slot_is_rendered_as_a_union_of_shapes() -> None:
    """Make filling an unstated field unexpressible rather than merely wrong."""

    schema = strict_json_schema(WireExpenseExtraction)
    properties = schema["properties"]

    for slot in ("amount", "occurrence", "merchant", "funding"):
        assert "oneOf" in properties[slot], slot
    assert schema["required"] == sorted(properties)
    for name in ("AmountNotStated", "TimeNotStated", "MerchantNotNamed"):
        assert schema["$defs"][name]["required"] == ["kind"]


def _fit_payload(judgment: dict[str, object]) -> str:
    """Serialize one category judgment."""

    return json.dumps({"judgment": judgment})


def test_an_existing_choice_carries_no_proposal_half() -> None:
    """The half the outcome does not select has nowhere to be written."""

    fit = WireCategoryFit.model_validate_json(
        _fit_payload(
            {
                "kind": "existing_category",
                "category_name": "Spesa alimentare",
                "evidence": "spesa al supermercato",
            }
        )
    ).to_domain()

    assert fit.outcome is CategoryFitOutcome.EXISTING
    assert fit.category_name == "Spesa alimentare"
    assert fit.proposed_name is None
    assert fit.rationale is None


def test_a_proposal_must_name_the_closest_existing_category() -> None:
    """Make the comparison a field, not a step the model may skip."""

    with pytest.raises(ValidationError):
        WireCategoryFit.model_validate_json(
            _fit_payload(
                {
                    "kind": "new_category",
                    "proposed_name": "Asilo nido",
                    "parent_name": "Acquisti personali",
                    "description": "rette",
                    "evidence": "asilo nido",
                }
            )
        )


def test_a_proposal_converts_its_comparison_into_the_domain_judgment() -> None:
    """Carry the closest category and its rejection into stored provenance."""

    fit = WireCategoryFit.model_validate_json(
        _fit_payload(
            {
                "kind": "new_category",
                "closest_existing_category": "Formazione",
                "why_closest_is_inadequate": "non copre la custodia dei figli",
                "proposed_name": "Asilo nido",
                "parent_name": "Acquisti personali",
                "description": "rette per la custodia",
                "evidence": "asilo nido",
            }
        )
    ).to_domain()

    assert fit.outcome is CategoryFitOutcome.PROPOSAL
    assert fit.category_name is None
    assert fit.proposed_name == "Asilo nido"
    assert fit.rationale == "non copre la custodia dei figli"
    assert fit.considered_category_names == ("Formazione",)


def test_an_answer_carrying_both_halves_is_refused() -> None:
    """A field of the branch not chosen is unexpressible, not merely invalid."""

    with pytest.raises(ValidationError):
        WireCategoryFit.model_validate_json(
            _fit_payload(
                {
                    "kind": "existing_category",
                    "category_name": "Spesa alimentare",
                    "proposed_name": "Spesa settimanale",
                    "evidence": "spesa",
                }
            )
        )


def _tool_payload(request: dict[str, object]) -> str:
    """Serialize one tool selection."""

    return json.dumps({"request": request})


def test_an_unstated_month_resolves_to_the_month_in_progress() -> None:
    """Keep the absent period meaning exactly what it meant before."""

    request = WireToolRequest.model_validate_json(
        _tool_payload(
            {
                "kind": "category_tool",
                "tool": "category_spending",
                "category_name": "spesa alimentare",
                "period": {"kind": "current_month"},
                "evidence": "questo mese",
            }
        )
    ).to_domain(current=YearMonth(2026, 9))

    assert request.period is None
    assert request.resolved_period(current=YearMonth(2026, 9)) == YearMonth(2026, 9)


def test_a_relative_month_is_resolved_without_showing_the_model_a_date() -> None:
    """Make `il mese scorso` expressible, which the flat boundary could not."""

    def previous(current: YearMonth) -> str | None:
        return (
            WireToolRequest.model_validate_json(
                _tool_payload(
                    {
                        "kind": "overall_tool",
                        "tool": "budgets_at_risk",
                        "period": {"kind": "previous_month"},
                        "evidence": "il mese scorso",
                    }
                )
            )
            .to_domain(current=current)
            .period
        )

    assert previous(YearMonth(2026, 9)) == "2026-08"
    assert previous(YearMonth(2026, 1)) == "2025-12"


def test_a_named_month_is_carried_through_exactly() -> None:
    """A month the owner names outright stays the month it names."""

    request = WireToolRequest.model_validate_json(
        _tool_payload(
            {
                "kind": "overall_tool",
                "tool": "funding_class_split",
                "period": {"kind": "named_month", "year": 2026, "month": 3},
                "evidence": "a marzo",
            }
        )
    ).to_domain(current=YearMonth(2026, 9))

    assert request.period == "2026-03"


def test_the_tool_shape_carries_its_own_argument_rule() -> None:
    """A category tool without a category has no shape, and neither has the reverse."""

    with pytest.raises(ValidationError):
        WireToolRequest.model_validate_json(
            _tool_payload(
                {
                    "kind": "category_tool",
                    "tool": "category_spending",
                    "period": {"kind": "current_month"},
                    "evidence": "spesa",
                }
            )
        )
    with pytest.raises(ValidationError):
        WireToolRequest.model_validate_json(
            _tool_payload(
                {
                    "kind": "overall_tool",
                    "tool": "budgets_at_risk",
                    "category_name": "spesa alimentare",
                    "period": {"kind": "current_month"},
                    "evidence": "budget",
                }
            )
        )


def test_a_tool_cannot_be_requested_under_the_wrong_shape() -> None:
    """Each variant offers only the tools whose arguments it carries."""

    with pytest.raises(ValidationError):
        WireToolRequest.model_validate_json(
            _tool_payload(
                {
                    "kind": "overall_tool",
                    "tool": "category_spending",
                    "period": {"kind": "current_month"},
                    "evidence": "spesa",
                }
            )
        )


def test_the_category_tree_shows_which_entries_are_specific() -> None:
    """A flat list asks a model to choose from a hierarchy it cannot see."""

    rendered = render_active_category_tree(
        (
            ("Casa e utenze", "Abitazione e servizi.", None),
            ("Bollette e comunicazioni", "Energia e acqua.", "Casa e utenze"),
            ("Formazione", "Corsi e libri.", None),
        )
    )

    assert "- Casa e utenze: Abitazione e servizi." in rendered
    assert "  - Bollette e comunicazioni: Energia e acqua." in rendered
    assert "\n- Formazione: Corsi e libri." in rendered
    with pytest.raises(ValueError, match="at least one active category"):
        render_active_category_tree(())


def test_selection_categories_render_nothing_when_none_are_supplied() -> None:
    """An absent taxonomy must add no empty heading to the instructions."""

    assert render_selection_categories(()) == ""
    assert "- Spesa alimentare" in render_selection_categories(("Spesa alimentare",))


def test_a_month_without_a_year_is_the_most_recent_one() -> None:
    """`ad agosto` must not force the model to invent a year."""

    def bare(month: int, current: YearMonth) -> str | None:
        return (
            WireToolRequest.model_validate_json(
                _tool_payload(
                    {
                        "kind": "overall_tool",
                        "tool": "budgets_at_risk",
                        "period": {"kind": "month_this_year", "month": month},
                        "evidence": "ad agosto",
                    }
                )
            )
            .to_domain(current=current)
            .period
        )

    assert bare(8, YearMonth(2026, 9)) == "2026-08"
    assert bare(9, YearMonth(2026, 9)) == "2026-09"
    assert bare(11, YearMonth(2026, 9)) == "2025-11"


def test_a_split_that_does_not_add_up_is_carried_through_unchanged() -> None:
    """Never let the boundary correct the owner's own arithmetic.

    A split whose parts do not match the stated total is usually the owner
    mis-stating one of them, and the deterministic side answers by asking. If
    the boundary refused it, the model would adjust a figure until it
    reconciled and the question would never be asked.
    """

    extraction = WireExpenseExtraction.model_validate_json(
        _payload(
            amount={"kind": "amount_stated", "minor_units": 5000},
            funding={
                "kind": "funding_split",
                "parts": [
                    {"funding_source_text": "buoni pasto", "minor_units": 3000},
                    {"funding_source_text": "carta", "minor_units": 3000},
                ],
            },
        )
    ).to_domain()

    assert extraction.amount_minor_units == 5000
    assert [part.amount_minor_units for part in extraction.payment_parts] == [
        3000,
        3000,
    ]


def test_a_split_needs_no_total_to_reconcile_against() -> None:
    """A message stating parts but no total still has a reconcilable answer."""

    extraction = WireExpenseExtraction.model_validate_json(
        _payload(
            amount={"kind": "amount_not_stated"},
            funding={
                "kind": "funding_split",
                "parts": [
                    {"funding_source_text": "buoni pasto", "minor_units": 750},
                    {"funding_source_text": "carta", "minor_units": 500},
                ],
            },
        )
    ).to_domain()

    assert extraction.amount_minor_units is None
    assert [part.amount_minor_units for part in extraction.payment_parts] == [750, 500]


def test_the_boundary_can_decline_to_choose_a_tool() -> None:
    """A question no offered tool answers must have a shape of its own.

    Without one the model answers from whichever tool is nearest, which is how
    six adversarial cases that exist to be refused were answered instead.
    """

    declined = WireToolRequest.model_validate_json(
        _tool_payload(
            {"kind": "no_answerable_tool", "evidence": "come vanno tutti i budget"}
        )
    )

    assert declined.to_domain(current=YearMonth(2026, 9)) is None


def test_a_declined_selection_carries_no_tool_or_period() -> None:
    """Keep the refusal empty, so it cannot smuggle a request through."""

    with pytest.raises(ValidationError):
        WireToolRequest.model_validate_json(
            _tool_payload(
                {
                    "kind": "no_answerable_tool",
                    "tool": "budgets_at_risk",
                    "evidence": "x",
                }
            )
        )


def test_a_proposal_can_only_ask_for_a_root_it_may_actually_have() -> None:
    """Make an unusable parent unexpressible rather than merely refused.

    The domain admits only an active root as a proposal's parent. The schema
    admitted any string, so the model answered with the nearest category, which
    is usually a leaf, and every such proposal was refused with
    `category_parent_invalid` leaving the owner nowhere to go. Approving a
    category made it worse: the new leaf became the most attractive parent for
    everything related to it, so using the review broke the review.
    """

    schema = build_category_fit_schema(
        ("Casa e utenze", "Bollette", "Alimentazione"),
        ("Casa e utenze", "Alimentazione"),
    )

    proposal = next(
        definition
        for name, definition in schema.model_json_schema()["$defs"].items()
        if name == "BoundNewCategoryProposal"
    )

    assert proposal["properties"]["parent_name"]["enum"] == [
        "Casa e utenze",
        "Alimentazione",
    ]
    assert proposal["properties"]["closest_existing_category"]["enum"] == [
        "Casa e utenze",
        "Bollette",
        "Alimentazione",
    ]
