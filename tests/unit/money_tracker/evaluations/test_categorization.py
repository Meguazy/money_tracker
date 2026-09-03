"""Tests for the categorization suite's scoring and its committed dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from money_tracker.domain import CategoryStatus, initial_taxonomy
from money_tracker.evaluations.categorization import (
    DATASET_MINIMUM,
    SUITE,
    run_categorization_suite,
)
from money_tracker.evaluations.metadata import CapabilitySettings
from money_tracker.evaluations.suites import dataset_metadata, load_cases

DATASET = Path("evals/datasets/categorization-purpose-it-v1.jsonl")
ACTIVE = {
    c.name for c in initial_taxonomy().categories if c.status is CategoryStatus.ACTIVE
}


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    """Load the committed dataset once."""

    parsed, _ = load_cases(DATASET)
    return parsed


class ScriptedModel:
    """A real port implementation replaying one completion per call."""

    def __init__(self, completions: list[str]) -> None:
        """Store the script and the call counter."""

        self.completions = completions
        self.calls = 0

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: Any = None,
    ) -> str:
        """Return the next scripted completion, repeating the last one."""

        self.calls += 1
        index = min(self.calls - 1, len(self.completions) - 1)
        return self.completions[index]


def _settings() -> CapabilitySettings:
    """Build endpoint settings that intentionally reach nothing."""

    return CapabilitySettings(
        base_url="http://127.0.0.1:1",
        api_key="none",
        model_alias="test-model",
        timeout_seconds=1,
        model_path=None,
        server_binary=None,
        vision_projector_path=None,
        context_size=None,
    )


def test_the_committed_dataset_parses_and_reaches_its_minimum(
    cases: list[dict[str, Any]],
) -> None:
    """Keep the shipped dataset loadable and large enough to gate on."""

    parsed, content_hash = load_cases(DATASET)
    metadata = dataset_metadata(
        suite=SUITE, path=DATASET, cases=parsed, content_hash=content_hash
    )

    assert metadata.item_count == len(cases)
    assert metadata.item_count >= DATASET_MINIMUM
    assert metadata.version == "1.0.0"


def test_every_expected_category_is_an_active_seed_node(
    cases: list[dict[str, Any]],
) -> None:
    """Refuse a label the taxonomy could never resolve."""

    for case in cases:
        name = case["expected_category_name"]
        assert case["expected_outcome"] in {"existing", "proposal"}
        if case["expected_outcome"] == "existing":
            assert name in ACTIVE, f"{case['case_id']} labels unknown {name!r}"
        else:
            assert name is None


def test_every_safety_case_names_the_tokens_it_forbids(
    cases: list[dict[str, Any]],
) -> None:
    """Make the zero-tolerance gate checkable rather than a matter of reading.

    AD-007 keeps purpose and funding independent, so a category named after a
    merchant or a payment method collapses them. Each safety case carries the
    exact tokens its message foregrounds so a proposal echoing one is detected
    deterministically.
    """

    safety = [c for c in cases if "safety" in c["tags"]]

    assert len(safety) >= 15
    for case in safety:
        tokens = case["forbidden_name_tokens"]
        assert tokens, case["case_id"]
        assert all(token.strip() for token in tokens), case["case_id"]
        # At least one token must appear literally, anchoring the case to what
        # its message actually foregrounds. The rest may be morphological
        # variants, because the observed failure this gate exists for proposed
        # "Spesa con buoni e carte" from a message that said "carta".
        assert any(token.lower() in case["message"].lower() for token in tokens), case[
            "case_id"
        ]


def test_the_dataset_covers_most_of_the_active_taxonomy(
    cases: list[dict[str, Any]],
) -> None:
    """Measure the taxonomy broadly rather than only its common nodes."""

    covered = {
        c["expected_category_name"] for c in cases if c["expected_category_name"]
    }

    assert len(covered) >= 20
    assert covered <= ACTIVE


def test_a_correct_existing_judgment_scores_as_agreement(tmp_path: Path) -> None:
    """Score a resolved existing category against its label."""

    completion = json.dumps(
        {
            "outcome": "existing",
            "category_name": "Spesa alimentare",
            "proposed_name": None,
            "parent_name": None,
            "description": None,
            "rationale": None,
            "considered_category_names": [],
            "evidence": "spesa",
        }
    )
    report = run_categorization_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel([completion]),
    )

    metrics = {m.name: m for m in report.metrics}
    assert metrics["purpose_label_agreement"].numerator > 0
    assert metrics["false_new_category_proposal"].value == 0.0
    assert metrics["proposals_offered"].value == 0.0


def test_a_proposal_echoing_a_payment_method_fails_the_safety_gate(
    tmp_path: Path,
) -> None:
    """Fail the zero-tolerance gate on the dimension collapse it exists for."""

    completion = json.dumps(
        {
            "outcome": "proposal",
            "category_name": None,
            "proposed_name": "Spesa con buoni e carte",
            "parent_name": "Alimentazione",
            "description": "Spesa pagata con buoni e carte.",
            "rationale": "Nessuna categoria descrive il metodo di pagamento.",
            "considered_category_names": [],
            "evidence": "buoni pasto",
        }
    )
    report = run_categorization_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel([completion]),
    )

    metrics = {m.name: m for m in report.metrics}
    assert metrics["merchant_or_payment_method_proposed_as_category"].value > 0
    assert metrics["merchant_or_payment_method_proposed_as_category"].passed is False
    assert report.blocking_gates_passed is False


def test_a_duplicate_proposal_is_counted_and_refused(tmp_path: Path) -> None:
    """Catch a candidate the taxonomy already covers under the same name."""

    completion = json.dumps(
        {
            "outcome": "proposal",
            "category_name": None,
            "proposed_name": "Spesa alimentare",
            "parent_name": "Alimentazione",
            "description": "Spesa al supermercato.",
            "rationale": "Serve una categoria per la spesa.",
            "considered_category_names": [],
            "evidence": "spesa",
        }
    )
    report = run_categorization_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel([completion]),
    )

    metrics = {m.name: m for m in report.metrics}
    assert metrics["duplicate_or_alias_equivalent_proposal"].value == 1.0
    assert metrics["duplicate_or_alias_equivalent_proposal"].passed is False


def test_the_diagnostic_separates_knowing_from_choosing(tmp_path: Path) -> None:
    """Distinguish a model that cannot find the category from one that can.

    A proposal that also names a real active category means the model found the
    answer and selected the wrong discriminator. That needs a different fix
    from a model that genuinely could not classify the purpose.
    """

    completion = json.dumps(
        {
            "outcome": "proposal",
            "category_name": "Spesa alimentare",
            "proposed_name": "Alimentari settimanali",
            "parent_name": "Alimentazione",
            "description": "Spesa settimanale.",
            "rationale": "Motivo.",
            "considered_category_names": [],
            "evidence": "spesa",
        }
    )
    report = run_categorization_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel([completion]),
    )

    metrics = {m.name: m for m in report.metrics}
    assert metrics["existing_category_named_while_proposing"].value == 1.0
    assert metrics["existing_category_named_while_proposing"].blocking is False


def test_the_auto_post_gates_are_visible_but_unmeasured(tmp_path: Path) -> None:
    """Keep an unmeasured gate visible rather than omitting it.

    No auto-post policy is promoted, so the eligible subset is empty by
    construction. Emitting the gates at zero denominator states that; omitting
    them would hide that they exist at all.
    """

    completion = json.dumps(
        {
            "outcome": "existing",
            "category_name": "Spesa alimentare",
            "proposed_name": None,
            "parent_name": None,
            "description": None,
            "rationale": None,
            "considered_category_names": [],
            "evidence": "spesa",
        }
    )
    report = run_categorization_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel([completion]),
    )

    metrics = {m.name: m for m in report.metrics}
    for name in (
        "auto_post_subset_category_precision",
        "auto_post_subset_invariant_validity",
    ):
        assert metrics[name].denominator == 0
        assert metrics[name].blocking is True
        assert metrics[name].passed is True
    assert report.configuration["auto_post_policy_promoted"] is False
