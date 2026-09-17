"""Tests for publishing aggregate evaluation evidence through the tracing port."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from money_tracker.evaluations.publication import (
    PROMOTION_TRACE,
    PromotionEvidenceError,
    promotion_publication,
    publish_capability_report,
    publish_promotion,
)
from money_tracker.tracing import EvaluationPublication
from money_tracker.workflows.promotion import PROMOTED_TEXT_STACK
from tests.unit.money_tracker.evaluations.test_suites import _screen

_SCORES = Path(__file__).resolve().parents[4] / "out" / "evaluations"


@dataclass
class _RecordingPublisher:
    """A real implementation of the publication port that keeps what it gets."""

    accepted: bool = True
    publications: list[EvaluationPublication] = field(default_factory=list)

    def publish(self, publication: EvaluationPublication) -> bool:
        """Keep one publication and report the configured delivery outcome."""

        self.publications.append(publication)
        return self.accepted


def test_a_promotion_publishes_every_named_run_with_its_gates() -> None:
    """Carry the decision, the promoted configuration, and each run's evidence."""

    publisher = _RecordingPublisher()

    outcome = publish_promotion(
        PROMOTED_TEXT_STACK, out_root=_SCORES, publisher=publisher
    )

    (publication,) = publisher.publications
    assert outcome.published is True
    assert outcome.decision == "EVD-004"
    assert outcome.run_ids == dict(PROMOTED_TEXT_STACK.evaluation_runs)
    assert publication.trace_seed == outcome.trace_reference
    assert publication.trace_seed.startswith("promotion:EVD-004:")
    assert publication.trace_name == PROMOTION_TRACE
    assert publication.root.metadata["promotion_decision"] == "EVD-004"
    assert publication.root.input["versions"] == dict(PROMOTED_TEXT_STACK.versions)
    assert publication.root.output["all_blocking_gates_passed"] is True
    children = {child.metadata["suite"]: child for child in publication.children}
    assert set(children) == set(PROMOTED_TEXT_STACK.evaluation_runs)
    routing = children["routing/intent-it"]
    assert routing.input["run_id"] == "ROU-20260909-145350-b1919d44"
    assert routing.input["instructions_version"] == "candidate-router-it-v6"
    assert routing.input["dataset"]["id"] == "routing-intent-it-v1.2"
    assert routing.scores == {"gate/routing/intent-it": 1.0}
    assert {metric["name"] for metric in routing.output["metrics"]} >= {"macro_f1"}


def test_each_publication_is_a_new_trace() -> None:
    """Never reuse a trace a previous publication may be deleting."""

    first = promotion_publication(PROMOTED_TEXT_STACK, out_root=_SCORES)
    second = promotion_publication(PROMOTED_TEXT_STACK, out_root=_SCORES)

    assert first.trace_seed != second.trace_seed


def _copy_scores(tmp_path: Path) -> Path:
    """Copy the promoted model's committed runs so one can be altered."""

    root = tmp_path / "scores"
    for suite in PROMOTED_TEXT_STACK.evaluation_runs:
        source = _SCORES / suite / PROMOTED_TEXT_STACK.model_profile
        shutil.copytree(source, root / suite / PROMOTED_TEXT_STACK.model_profile)
    return root


def _alter(root: Path, suite: str, **values: object) -> None:
    """Rewrite fields of one copied run."""

    run_id = PROMOTED_TEXT_STACK.evaluation_runs[suite]
    path = root / suite / PROMOTED_TEXT_STACK.model_profile / f"{run_id}.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    run.update(values)
    path.write_text(json.dumps(run), encoding="utf-8")


@pytest.mark.parametrize(
    ("values", "rule"),
    [
        ({"eligible_for_promotion": False}, "not eligible"),
        ({"run_id": "ROU-00000000-000000-00000000"}, "identity differs"),
        ({"model": {"gguf_sha256": "sha256:other"}}, "other weights"),
    ],
)
def test_evidence_that_does_not_support_the_record_is_refused(
    tmp_path: Path, values: dict[str, object], rule: str
) -> None:
    """Publish nothing rather than a promotion the files do not support."""

    root = _copy_scores(tmp_path)
    _alter(root, "routing/intent-it", **values)
    publisher = _RecordingPublisher()

    with pytest.raises(PromotionEvidenceError, match=rule):
        publish_promotion(PROMOTED_TEXT_STACK, out_root=root, publisher=publisher)

    assert publisher.publications == []


def test_a_missing_run_is_refused(tmp_path: Path) -> None:
    """Refuse a record naming a run the repository does not hold."""

    root = _copy_scores(tmp_path)
    run_id = PROMOTED_TEXT_STACK.evaluation_runs["advice/grounding-it"]
    (
        root
        / "advice/grounding-it"
        / PROMOTED_TEXT_STACK.model_profile
        / f"{run_id}.json"
    ).unlink()

    with pytest.raises(PromotionEvidenceError, match="unreadable"):
        promotion_publication(PROMOTED_TEXT_STACK, out_root=root)


def test_a_refused_store_publishes_nothing_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the tracing endpoint rule to evaluation publication."""

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-secret-value")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    assert publish_capability_report(_screen()) is False
    outcome = publish_promotion(PROMOTED_TEXT_STACK, out_root=_SCORES)
    assert outcome.published is False


def test_a_capability_screen_publishes_its_probe_scores() -> None:
    """Keep the aggregate probe outcomes the screen always published."""

    publisher = _RecordingPublisher()
    report = _screen()

    assert publish_capability_report(report, publisher=publisher) is True

    (publication,) = publisher.publications
    assert publication.trace_seed == f"evaluation:{report.run_id}"
    assert publication.root.input == {
        "run_id": report.run_id,
        "suite": report.dataset.suite,
    }
    assert set(publication.root.scores) == {
        f"capability/{probe.name}" for probe in report.probes
    }


def test_an_undelivered_publication_is_reported_as_unpublished() -> None:
    """Say so when the store did not take the evidence within the bound."""

    publisher = _RecordingPublisher(accepted=False)

    assert publish_capability_report(_screen(), publisher=publisher) is False
    assert (
        publish_promotion(
            PROMOTED_TEXT_STACK, out_root=_SCORES, publisher=publisher
        ).published
        is False
    )
