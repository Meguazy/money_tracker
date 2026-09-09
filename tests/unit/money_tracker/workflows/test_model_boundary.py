"""Tests for the strict-schema boundary and its single corrective retry."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from money_tracker.workflows import (
    CATEGORY_FIT,
    DETERMINISTIC_ONLY_INTENTS,
    EXTRACTION,
    MODEL_BOUNDARY_ATTEMPTS,
    MODEL_ROUTABLE_INTENTS,
    ROUTER,
    TOOL_SELECTION,
    RepairRequest,
    RouteClassification,
    StructuredOutputError,
    WorkflowIntent,
    default_prompt,
    parse_with_one_repair,
)
from money_tracker.workflows.prompts import CATEGORY_LEAF, CATEGORY_SUBTREE

VALID = json.dumps(
    {
        "intent": "expense",
        "confidence": 0.95,
        "evidence": "ho speso 12 euro",
    }
)
MISSING_EVIDENCE = json.dumps({"intent": "expense", "confidence": 0.95})


@dataclass
class ScriptedModel:
    """A real port implementation that replays a fixed script of completions.

    This is a lightweight implementation of the published contract, not a mock
    of the code under test: it records what the boundary asked for so the test
    can assert on the request the boundary actually made.
    """

    completions: list[str]
    calls: list[dict[str, object]] = field(default_factory=list)

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: RepairRequest | None = None,
    ) -> str:
        """Return the next scripted completion and record the request."""

        self.calls.append(
            {
                "instructions": instructions,
                "message": message,
                "schema": schema,
                "repair": repair,
            }
        )
        return self.completions[len(self.calls) - 1]


def _parse(model: ScriptedModel) -> RouteClassification:
    """Run the boundary with the router contract and candidate instructions."""

    return parse_with_one_repair(
        model,
        schema=RouteClassification,
        instructions=default_prompt(ROUTER).text,
        message="ho speso 12 euro per i croccantini",
    )


def test_retry_budget_is_one_initial_attempt_plus_one_repair() -> None:
    """Pin the budget AD-006 fixes for every structured-output boundary."""

    assert MODEL_BOUNDARY_ATTEMPTS == 2


def test_valid_first_completion_consumes_no_retry() -> None:
    """Spend one inference call when the model already satisfies the schema."""

    model = ScriptedModel([VALID])

    classification = _parse(model)

    assert classification.intent is WorkflowIntent.EXPENSE
    assert len(model.calls) == 1
    assert model.calls[0]["repair"] is None


def test_invalid_first_completion_is_repaired_once() -> None:
    """Recover a malformed completion without asking the owner."""

    model = ScriptedModel([MISSING_EVIDENCE, VALID])

    classification = _parse(model)

    assert classification.intent is WorkflowIntent.EXPENSE
    assert len(model.calls) == 2


def test_repair_request_states_the_field_and_reason() -> None:
    """Give the model enough to correct itself without a new instruction set."""

    model = ScriptedModel([MISSING_EVIDENCE, VALID])

    _parse(model)
    repair = model.calls[1]["repair"]

    assert isinstance(repair, RepairRequest)
    assert repair.invalid_output == MISSING_EVIDENCE
    assert "evidence" in repair.validation_error


def test_repair_summary_excludes_the_rejected_values() -> None:
    """Keep the repair turn free of untrusted content it need not repeat."""

    rejected = json.dumps(
        {
            "intent": "expense",
            "confidence": 0.9,
            "evidence": "ok",
            "profile_id": "leaked-scope-value",
        }
    )
    model = ScriptedModel([rejected, VALID])

    _parse(model)
    repair = model.calls[1]["repair"]

    assert isinstance(repair, RepairRequest)
    assert "leaked-scope-value" not in repair.validation_error


def test_oversized_rejected_output_is_truncated_for_the_repair() -> None:
    """Keep a runaway completion from enlarging the repair beyond the context."""

    model = ScriptedModel(["x" * 10_000, VALID])

    _parse(model)
    repair = model.calls[1]["repair"]

    assert isinstance(repair, RepairRequest)
    assert len(repair.invalid_output) == 2000


def test_repair_keeps_the_same_instructions_and_message() -> None:
    """Correct the output without changing the task or the owner input."""

    model = ScriptedModel([MISSING_EVIDENCE, VALID])

    _parse(model)

    assert model.calls[0]["instructions"] == model.calls[1]["instructions"]
    assert model.calls[0]["message"] == model.calls[1]["message"]
    assert model.calls[1]["schema"] is RouteClassification


def test_second_invalid_completion_fails_instead_of_looping() -> None:
    """Stop at the budget so a persistent failure becomes a question."""

    model = ScriptedModel([MISSING_EVIDENCE, MISSING_EVIDENCE])

    with pytest.raises(StructuredOutputError) as error:
        _parse(model)

    assert error.value.attempts == MODEL_BOUNDARY_ATTEMPTS
    assert len(model.calls) == MODEL_BOUNDARY_ATTEMPTS


def test_failure_message_carries_no_owner_or_model_text() -> None:
    """Keep a boundary failure safe to log and trace."""

    secret = "ho speso 12 euro dal veterinario"
    model = ScriptedModel([json.dumps({"evidence": secret}), "not json at all"])

    with pytest.raises(StructuredOutputError) as error:
        parse_with_one_repair(
            model,
            schema=RouteClassification,
            instructions=default_prompt(ROUTER).text,
            message=secret,
        )

    assert secret not in str(error.value)
    assert "not json at all" not in str(error.value)


def test_unparsable_output_is_treated_as_a_schema_failure() -> None:
    """Handle non-JSON exactly like an invalid document."""

    model = ScriptedModel(["I cannot help with that.", VALID])

    classification = _parse(model)

    assert classification.intent is WorkflowIntent.EXPENSE


@pytest.mark.parametrize("boundary", [ROUTER, EXTRACTION, CATEGORY_FIT, TOOL_SELECTION])
def test_promoted_instructions_keep_their_attribution_identifier(
    boundary: str,
) -> None:
    """Promote lifecycle status without breaking historical run attribution."""

    assert default_prompt(boundary).id.startswith("candidate-")
    assert default_prompt(boundary).status == "promoted"


@pytest.mark.parametrize("boundary", [CATEGORY_SUBTREE, CATEGORY_LEAF])
def test_rejected_two_stage_instructions_have_no_candidate_status(
    boundary: str,
) -> None:
    """Keep AD-015's rejected boundaries out of the promoted candidate set."""

    assert default_prompt(boundary).status == "rejected"


def test_candidate_instructions_list_every_routable_intent() -> None:
    """Prevent the offered vocabulary from drifting from the code."""

    for intent in MODEL_ROUTABLE_INTENTS:
        assert f"- {intent.value}:" in default_prompt(ROUTER).text


def test_candidate_instructions_offer_no_deterministic_route() -> None:
    """Never invite a model to claim a route it cannot be given."""

    for intent in DETERMINISTIC_ONLY_INTENTS:
        assert f"- {intent.value}:" not in default_prompt(ROUTER).text


def test_candidate_instructions_forbid_stating_finance_values() -> None:
    """Keep routing instructions from inviting extraction or authorization."""

    assert "Never state an amount" in default_prompt(ROUTER).text
    assert "verbatim span" in default_prompt(ROUTER).text
