"""Tests for the committed routing/intent-it dataset and its scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from money_tracker.evaluations.routing import (
    DATASET_MINIMUM,
    READ_ONLY_INTENTS,
    SUITE,
    intent_distribution,
)
from money_tracker.evaluations.suites import dataset_metadata, load_cases
from money_tracker.workflows import (
    DETERMINISTIC_ONLY_INTENTS,
    MUTATING_INTENTS,
    RoutingSource,
    WorkflowIntent,
)

DATASET = Path("evals/datasets/routing-intent-it-v1.jsonl")


@pytest.fixture(scope="module")
def cases() -> list[dict[str, object]]:
    """Load the committed dataset once for every assertion about it."""

    parsed, _ = load_cases(DATASET)
    return parsed


def test_the_committed_dataset_parses_and_hashes(
    cases: list[dict[str, object]],
) -> None:
    """Keep the shipped dataset loadable by the runner that scores it."""

    parsed, content_hash = load_cases(DATASET)
    metadata = dataset_metadata(
        suite=SUITE, path=DATASET, cases=parsed, content_hash=content_hash
    )

    assert metadata.item_count == len(cases)
    assert metadata.version == "1.0.0"
    assert len(metadata.content_hash) == 64


def test_every_expected_label_is_a_real_intent(
    cases: list[dict[str, object]],
) -> None:
    """Refuse a label the router could never produce."""

    for case in cases:
        WorkflowIntent(case["expected_intent"])
        RoutingSource(case["expected_source"])


def test_the_dataset_covers_every_routable_intent(
    cases: list[dict[str, object]],
) -> None:
    """Measure every destination, not only the common ones."""

    labelled = set(intent_distribution(cases))

    for intent in WorkflowIntent:
        assert intent.value in labelled, f"no case labelled {intent.value}"


def test_deterministic_labels_are_reserved_for_deterministic_cases(
    cases: list[dict[str, object]],
) -> None:
    """Keep a route the model may never choose out of the model's scoring."""

    for case in cases:
        intent = WorkflowIntent(case["expected_intent"])
        if intent in DETERMINISTIC_ONLY_INTENTS:
            assert case["expected_source"] == RoutingSource.DETERMINISTIC.value


def test_every_safety_case_is_tagged_for_failure_inspection(
    cases: list[dict[str, object]],
) -> None:
    """Let a reviewer inspect failures by class, as the process requires.

    Every read-only label is a safety case, including a greeting: routing one
    to a financial write is the same zero-tolerance failure whether the message
    asked a question or said good night.
    """

    read_only = [
        case
        for case in cases
        if WorkflowIntent(case["expected_intent"]) in READ_ONLY_INTENTS
    ]

    assert read_only
    for case in read_only:
        tags = case["tags"]
        assert isinstance(tags, list)
        assert "safety" in tags, case["case_id"]


def test_the_dataset_exercises_the_documented_risk_classes(
    cases: list[dict[str, object]],
) -> None:
    """Cover the failure modes the evaluation matrix names explicitly."""

    tags = {tag for case in cases for tag in case["tags"]}  # type: ignore[union-attr]

    for required in (
        "negation",
        "hypothetical",
        "reported-speech",
        "compound",
        "prompt-injection",
        "benefit-credit",
        "relative-date",
        "split-funding",
        "deterministic",
    ):
        assert required in tags, f"no case tagged {required}"


def test_read_only_and_mutating_labels_stay_disjoint() -> None:
    """Keep the safety metric's two sides from overlapping."""

    assert not READ_ONLY_INTENTS & MUTATING_INTENTS


def test_the_dataset_is_below_its_promotion_minimum(
    cases: list[dict[str, object]],
) -> None:
    """State the coverage gap in a test rather than only in prose.

    The experiment process requires 200 cases before this suite may support a
    promotion. The committed dataset is smaller, so a run of it can pass every
    blocking gate and still be ineligible. This test fails when the dataset
    reaches the minimum, at which point the roadmap and decision log claims
    about eligibility must be revisited in the same change.
    """

    assert len(cases) < DATASET_MINIMUM


def test_no_case_carries_a_personal_identifier(
    cases: list[dict[str, object]],
) -> None:
    """Keep the committed dataset synthetic, as the privacy policy requires."""

    for case in cases:
        assert case["provenance"] == "authored"
        assert "synthetic" in case["tags"]  # type: ignore[operator]
        message = case["message"]
        assert isinstance(message, dict)
        assert set(message) == {
            "text",
            "callback_action",
            "pending_action_id",
            "reply_to_action_id",
            "has_image",
        }
