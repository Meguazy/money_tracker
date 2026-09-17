"""Tests holding the promotion record to the evidence it names."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from money_tracker.runtime.read_graph import DEFAULT_SELECTION
from money_tracker.runtime.trace_metadata import workflow_version_set
from money_tracker.worker.cli import worker_version_set
from money_tracker.workflows import (
    CATEGORY_FIT,
    DEFAULT_READ_SELECTION_POLICY,
    EXTRACTION,
    ROUTER,
    TOOL_SELECTION,
    ReadSelectionPolicyVersion,
    default_prompt,
)
from money_tracker.workflows.candidates import (
    DEFAULT_CATEGORY_FIT_CANDIDATE,
    DEFAULT_EXTRACTION_CANDIDATE,
)
from money_tracker.workflows.promotion import PROMOTED_TEXT_STACK, PROMOTIONS

_ROOT = Path(__file__).resolve().parents[4]
_SCORES = _ROOT / "out" / "evaluations"


def _run(suite: str) -> dict[str, Any]:
    """Read the committed published run the record names for one suite."""

    run_id = PROMOTED_TEXT_STACK.evaluation_runs[suite]
    path = _SCORES / suite / PROMOTED_TEXT_STACK.model_profile / f"{run_id}.json"
    decoded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return decoded


@pytest.mark.parametrize("suite", sorted(PROMOTED_TEXT_STACK.evaluation_runs))
def test_every_named_run_is_published_eligible_and_on_the_promoted_weights(
    suite: str,
) -> None:
    """Refuse a record whose runs the repository does not hold as evidence."""

    run = _run(suite)

    assert run["run_id"] == PROMOTED_TEXT_STACK.evaluation_runs[suite]
    assert run["suite"] == suite
    assert run["eligible_for_promotion"] is True
    assert run["blocking_gates_passed"] is True
    assert run["model"]["gguf_sha256"] == PROMOTED_TEXT_STACK.gguf_sha256


def test_the_decision_log_names_the_same_decision_runs_and_weights() -> None:
    """Keep the code record and the decision log describing one decision."""

    log = (_ROOT / "docs" / "experiments" / "decision-log.md").read_text(
        encoding="utf-8"
    )
    entry = log[log.index(f"## {PROMOTED_TEXT_STACK.decision} ") :]
    entry = entry[: entry.find("\n## ", 1) if "\n## " in entry[1:] else len(entry)]

    assert PROMOTED_TEXT_STACK.decided_on in entry.splitlines()[0]
    assert PROMOTED_TEXT_STACK.gguf_sha256.removeprefix("sha256:") in entry
    assert PROMOTED_TEXT_STACK.model_profile in entry
    for run_id in PROMOTED_TEXT_STACK.evaluation_runs.values():
        assert run_id in entry


def test_the_record_pins_the_runtime_defaults() -> None:
    """Describe exactly the defaults the entry points compose."""

    versions = PROMOTED_TEXT_STACK.versions

    assert versions["router_prompt"] == default_prompt(ROUTER).id
    assert versions["extraction_prompt"] == default_prompt(EXTRACTION).id
    assert versions["extraction_prompt"] == DEFAULT_EXTRACTION_CANDIDATE.version
    assert versions["category_fit_prompt"] == default_prompt(CATEGORY_FIT).id
    assert versions["category_fit_prompt"] == DEFAULT_CATEGORY_FIT_CANDIDATE.version
    assert versions["tool_selection_prompt"] == default_prompt(TOOL_SELECTION).id
    assert versions["tool_selection_prompt"] == DEFAULT_SELECTION.version
    assert versions["read_selection_policy"] == DEFAULT_READ_SELECTION_POLICY.value
    assert PROMOTIONS[PROMOTED_TEXT_STACK.decision] is PROMOTED_TEXT_STACK


@pytest.mark.parametrize(
    ("suite", "boundary"),
    [
        ("routing/intent-it", ROUTER),
        ("ingestion/expense-it", EXTRACTION),
        ("tools/selection-and-use", TOOL_SELECTION),
    ],
)
def test_the_runtime_instructions_are_the_measured_instructions(
    suite: str, boundary: str
) -> None:
    """Match the default text byte for byte with what the promoted run hashed.

    The category-fit run hashed its instructions with the fixture taxonomy
    rendered in, so only its version identifier is comparable.
    """

    configuration = _run(suite)["configuration"]
    text = default_prompt(boundary).text

    assert configuration["instructions_version"] == default_prompt(boundary).id
    assert (
        configuration["instructions_sha256"]
        == hashlib.sha256(text.encode("utf-8")).hexdigest()
    )


def test_the_measured_policy_and_threshold_are_the_pinned_ones() -> None:
    """Hold the routing bar and read policy to the runs that measured them."""

    assert (
        _run("routing/intent-it")["configuration"]["minimum_confidence"]
        == (PROMOTED_TEXT_STACK.versions["routing_minimum_confidence"])
    )
    assert (
        _run("tools/selection-and-use")["configuration"]["selection_policy_version"]
        == (PROMOTED_TEXT_STACK.versions["read_selection_policy"])
    )
    assert (
        _run("categorization/purpose-it")["configuration"]["instructions_version"]
        == (PROMOTED_TEXT_STACK.versions["category_fit_prompt"])
    )


def test_the_worker_runs_the_promoted_configuration() -> None:
    """Link every worker trace to the decision and its runs."""

    versions = worker_version_set()

    assert versions["promotion_decision"] == "EVD-004"
    assert versions["promotion_evaluation_runs"] == dict(
        PROMOTED_TEXT_STACK.evaluation_runs
    )
    assert versions["promotion_gguf_sha256"] == PROMOTED_TEXT_STACK.gguf_sha256


def test_an_assembly_off_the_promoted_configuration_names_what_differs() -> None:
    """Report the rollback policy or another bar as not promoted."""

    rollback = workflow_version_set(
        router_prompt=default_prompt(ROUTER),
        minimum_confidence=0.8,
        selection=DEFAULT_SELECTION,
        selection_policy=ReadSelectionPolicyVersion.MODEL_ONLY_V0,
    )

    assert rollback["promotion_decision"] == "none"
    assert rollback["promotion_mismatch"] == [
        "read_selection_policy",
        "routing_minimum_confidence",
    ]
    assert "promotion_evaluation_runs" not in rollback


def test_a_partial_assembly_is_linked_by_the_boundaries_it_composes() -> None:
    """Let routing-only diagnostics and browser advice name the decision."""

    routing_only = workflow_version_set(
        router_prompt=default_prompt(ROUTER), minimum_confidence=0.5
    )
    advice = workflow_version_set(
        selection=DEFAULT_SELECTION, selection_policy=DEFAULT_READ_SELECTION_POLICY
    )

    assert routing_only["promotion_decision"] == "EVD-004"
    assert advice["promotion_decision"] == "EVD-004"
