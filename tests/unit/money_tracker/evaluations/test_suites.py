"""Tests for the shared dataset-backed suite contracts and scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from money_tracker.evaluations.models import (
    DatasetMetadata,
    EnvironmentMetadata,
    ModelMetadata,
    SourceMetadata,
)
from money_tracker.evaluations.suites import (
    CaseResult,
    CaseStatus,
    MetricScore,
    SuiteReport,
    blocking_gates_passed,
    dataset_metadata,
    load_cases,
    new_run_id,
    rate,
    utc_now,
    write_report,
)


def _dataset(tmp_path: Path, *lines: dict[str, object]) -> Path:
    """Write one JSONL dataset fixture."""

    path = tmp_path / "suite-v1.jsonl"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


def _metric(**overrides: object) -> MetricScore:
    """Build one scored metric with explicit overrides."""

    fields: dict[str, object] = {
        "name": "macro_f1",
        "value": 0.96,
        "threshold": 0.95,
        "blocking": True,
        "numerator": 96,
        "denominator": 100,
    }
    fields.update(overrides)
    return MetricScore(**fields)  # type: ignore[arg-type]


def test_a_dataset_is_identified_by_content_not_by_name(tmp_path: Path) -> None:
    """Make a dataset change visible as a different hash, never a silent edit."""

    first = _dataset(tmp_path, {"case_id": "A", "dataset_version": "1.0.0"})
    _, one = load_cases(first)
    first.write_text(
        json.dumps({"case_id": "A", "dataset_version": "1.0.0", "x": 1}) + "\n"
    )
    _, two = load_cases(first)

    assert one != two
    assert len(one) == 64


def test_blank_lines_are_ignored_but_an_empty_dataset_is_refused(
    tmp_path: Path,
) -> None:
    """Accept trailing whitespace, refuse a dataset with nothing to score."""

    path = tmp_path / "suite-v1.jsonl"
    path.write_text('\n{"case_id": "A", "dataset_version": "1.0.0"}\n\n')
    cases, _ = load_cases(path)
    assert len(cases) == 1

    path.write_text("\n\n")
    with pytest.raises(ValueError, match="no cases"):
        load_cases(path)


def test_a_malformed_line_names_its_position(tmp_path: Path) -> None:
    """Point a reviewer at the line rather than at the whole file."""

    path = tmp_path / "suite-v1.jsonl"
    path.write_text('{"case_id": "A", "dataset_version": "1.0.0"}\nnot json\n')

    with pytest.raises(ValueError, match="suite-v1.jsonl:2"):
        load_cases(path)

    path.write_text("[1, 2]\n")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_cases(path)


def test_a_repeated_case_identifier_is_refused(tmp_path: Path) -> None:
    """Keep every score attributable to exactly one case."""

    path = _dataset(
        tmp_path,
        {"case_id": "A", "dataset_version": "1.0.0"},
        {"case_id": "A", "dataset_version": "1.0.0"},
    )

    with pytest.raises(ValueError, match="repeats a case identifier"):
        load_cases(path)


def test_mixed_dataset_versions_are_refused(tmp_path: Path) -> None:
    """Refuse a run that could not be reproduced against one version."""

    path = _dataset(
        tmp_path,
        {"case_id": "A", "dataset_version": "1.0.0"},
        {"case_id": "B", "dataset_version": "1.1.0"},
    )
    cases, content_hash = load_cases(path)

    with pytest.raises(ValueError, match="mixes dataset versions"):
        dataset_metadata(suite="s", path=path, cases=cases, content_hash=content_hash)


def test_dataset_metadata_records_size_and_version(tmp_path: Path) -> None:
    """Carry the identity a run manifest needs to be reproducible."""

    path = _dataset(
        tmp_path,
        {"case_id": "A", "dataset_version": "1.0.0"},
        {"case_id": "B", "dataset_version": "1.0.0"},
    )
    cases, content_hash = load_cases(path)

    metadata = dataset_metadata(
        suite="routing/intent-it", path=path, cases=cases, content_hash=content_hash
    )

    assert metadata.item_count == 2
    assert metadata.version == "1.0.0"
    assert metadata.content_hash == content_hash
    assert metadata.private_dataset is False


@pytest.mark.parametrize(
    ("value", "threshold", "comparison", "expected"),
    [
        (0.96, 0.95, "at_least", True),
        (0.94, 0.95, "at_least", False),
        (0.0, 0.0, "at_most", True),
        (3.0, 0.0, "at_most", False),
        (0.005, 0.01, "at_most", True),
    ],
)
def test_a_gate_is_data_rather_than_judgment(
    value: float, threshold: float, comparison: str, expected: bool
) -> None:
    """Decide pass or fail from the recorded comparison, never by opinion."""

    metric = _metric(value=value, threshold=threshold, comparison=comparison)

    assert metric.passed is expected


def test_a_diagnostic_metric_cannot_block() -> None:
    """Let a metric inform without gating when it has no threshold."""

    metric = _metric(threshold=None, value=0.1, blocking=False)

    assert metric.passed is True
    assert blocking_gates_passed([metric]) is True


def test_an_empty_case_class_is_a_coverage_gap_not_a_failure() -> None:
    """Never let an unexercised class silently depress an accuracy score."""

    metric = _metric(value=1.0, numerator=0, denominator=0)

    assert metric.passed is True
    assert rate(0, 0) == 1.0


def test_promotion_is_blocked_by_any_failing_blocking_metric() -> None:
    """Require every blocking gate, not a majority of them."""

    good = _metric(name="a", value=1.0, threshold=0.95)
    bad = _metric(name="b", value=0.5, threshold=0.95)
    diagnostic = _metric(name="c", value=0.0, threshold=0.95, blocking=False)

    assert blocking_gates_passed([good]) is True
    assert blocking_gates_passed([good, bad]) is False
    assert blocking_gates_passed([good, diagnostic]) is True


def test_rate_is_exact_to_four_places() -> None:
    """Keep a reported ratio stable across runs and machines."""

    assert rate(1, 3) == 0.3333
    assert rate(96, 100) == 0.96
    assert rate(3, 3) == 1.0


def test_a_run_id_sorts_and_carries_no_personal_data() -> None:
    """Make artifacts orderable without encoding anything about the owner."""

    one = new_run_id("ROU")

    assert one.startswith("ROU-")
    assert one != new_run_id("ROU")
    assert len(one.split("-")) == 4


def _report(tmp_path: Path, **overrides: object) -> SuiteReport:
    """Build one complete report for artifact tests."""

    fields: dict[str, object] = {
        "run_id": "ROU-20260903-000000-abcdef12",
        "suite": "routing/intent-it",
        "started_at": utc_now(),
        "completed_at": utc_now(),
        "source": SourceMetadata(
            git_commit="0" * 40,
            working_tree_clean=True,
            application_version="0.1.0",
            python_version="3.12.3",
            uv_lock_hash="a" * 64,
        ),
        "dataset": DatasetMetadata(
            suite="routing/intent-it",
            dataset_id="routing-intent-it-v1",
            version="1.0.0",
            content_hash="b" * 64,
            item_count=112,
        ),
        "model": ModelMetadata(
            endpoint_kind="llamacpp",
            model_alias="local-model",
            gguf_filename=None,
            gguf_sha256=None,
            quantization=None,
            vision_projector_sha256=None,
            chat_template_sha256=None,
            chat_template_capabilities=[],
            modalities=[],
            context_size=8192,
            server_version=None,
            server_binary_sha256=None,
            metadata_complete=False,
        ),
        "environment": EnvironmentMetadata(
            os="Linux",
            kernel="6.11.0",
            machine="x86_64",
            gpu=None,
            driver_version=None,
            vram_mib=None,
            timezone="Europe/Rome",
        ),
        "configuration": {"minimum_confidence": 0.5},
        "metrics": [_metric()],
        "cases": [
            CaseResult(
                case_id="ROU-EXP-001",
                status=CaseStatus.PASSED,
                expected="expense",
                actual="expense",
                tags=["synthetic"],
            )
        ],
        "blocking_gates_passed": True,
        "meets_dataset_minimum": False,
        "eligible_for_promotion": False,
        "local_artifact_path": str(tmp_path),
    }
    fields.update(overrides)
    return SuiteReport(**fields)  # type: ignore[arg-type]


def test_a_run_writes_a_manifest_and_a_compact_score_summary(
    tmp_path: Path,
) -> None:
    """Separate the reviewable summary from the full per-case manifest."""

    directory = write_report(_report(tmp_path), output_root=tmp_path)

    manifest = json.loads((directory / "manifest.json").read_text())
    scores = json.loads((directory / "scores.json").read_text())
    assert manifest["cases"][0]["case_id"] == "ROU-EXP-001"
    assert "cases" not in scores
    assert scores["dataset_content_hash"] == "b" * 64
    assert scores["configuration"]["minimum_confidence"] == 0.5
    assert scores["metrics"][0]["name"] == "macro_f1"


def test_the_score_summary_states_why_promotion_is_blocked(
    tmp_path: Path,
) -> None:
    """Make a passing gate set insufficient on its own, and say so."""

    directory = write_report(_report(tmp_path), output_root=tmp_path)

    scores = json.loads((directory / "scores.json").read_text())
    assert scores["blocking_gates_passed"] is True
    assert scores["meets_dataset_minimum"] is False
    assert scores["eligible_for_promotion"] is False


def test_a_case_record_carries_labels_rather_than_prose(tmp_path: Path) -> None:
    """Keep a committed summary unable to reconstruct anything private."""

    fields = set(CaseResult.model_fields)

    assert "text" not in fields
    assert "completion" not in fields
    assert "message" not in fields
    assert fields == {
        "case_id",
        "status",
        "expected",
        "actual",
        "tags",
        "latency_ms",
        "detail",
    }
