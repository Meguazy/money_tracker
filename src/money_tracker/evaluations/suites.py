"""Shared contracts for dataset-backed workflow evaluation suites.

The capability suite answers whether a model/server combination can use an
interface at all. These suites answer a different question: whether a workflow
built on that interface is accurate and safe enough to promote. They therefore
score labelled cases against the gates in the evaluation matrix rather than
probing a transport.

Three rules from the experiment process shape this module.

A dataset is identified by content, not by filename. Every report carries the
dataset's hash and item count, so a run can never be reinterpreted against a
changed dataset, and a dataset change is visible as a different hash rather than
as a silent edit.

A gate is blocking or diagnostic, and the distinction is data rather than
judgment. A run is eligible for promotion only when every blocking gate passes,
and this module refuses to compute that flag any other way.

A report is deterministic and public-safe. Case records carry stable identifiers,
labels, and tags, never the model's prose or an owner's text, so a committed
summary cannot reconstruct anything private.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    collect_model_metadata,
)
from money_tracker.evaluations.models import (
    DatasetMetadata,
    EnvironmentMetadata,
    ModelMetadata,
    SourceMetadata,
)


class CaseStatus(StrEnum):
    """Outcome of one labelled evaluation case.

    Attributes:
        PASSED: The workflow produced the expected label.
        FAILED: The workflow produced a different label.
        ERROR: The case could not be executed at all.
    """

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class CaseResult(BaseModel):
    """Machine-readable result for one labelled case.

    Attributes:
        case_id: Stable dataset identifier.
        status: Deterministic outcome.
        expected: Expected label as a short stable token.
        actual: Produced label, or None when the case errored.
        tags: Dataset tags, used to inspect failures by class rather than only
            in aggregate.
        latency_ms: End-to-end duration when the case reached a model.
        detail: Small non-sensitive diagnostic, such as a refusal reason code.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: CaseStatus
    expected: str
    actual: str | None = None
    tags: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    detail: str | None = None


class MetricScore(BaseModel):
    """One scored metric and the gate it is measured against.

    Attributes:
        name: Stable metric identifier matching the evaluation matrix.
        value: Computed score.
        threshold: Value the gate requires, when the metric has one.
        comparison: Whether the value must reach or stay under the threshold.
        blocking: Whether a failure blocks promotion.
        numerator: Cases counted in the numerator.
        denominator: Cases the metric applies to.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    threshold: float | None
    comparison: Literal["at_least", "at_most"] = "at_least"
    blocking: bool
    numerator: int
    denominator: int

    @property
    def passed(self) -> bool:
        """Return whether this metric satisfies its gate.

        A metric with no threshold is diagnostic and always passes; a metric
        whose denominator is zero has nothing to measure and cannot fail,
        because an absent case class is a dataset gap rather than a model
        failure. The dataset-coverage gate is what reports that gap.
        """

        if self.threshold is None or self.denominator == 0:
            return True
        if self.comparison == "at_most":
            return self.value <= self.threshold
        return self.value >= self.threshold


class SuiteReport(BaseModel):
    """Complete deterministic report emitted by one workflow suite.

    Attributes:
        manifest_schema_version: Report shape version.
        run_id: Stable run identifier.
        suite: Suite name from the catalog.
        started_at: ISO start instant.
        completed_at: ISO completion instant.
        source: Repository and interpreter identity.
        dataset: Immutable dataset identity including its content hash.
        model: Exact model, template, and server identity.
        environment: Non-sensitive host identity.
        configuration: Pinned run parameters, such as the confidence bar under
            test, so a score can never be read without the settings that
            produced it.
        metrics: Every scored metric with its gate.
        cases: Per-case outcomes for failure inspection by tag.
        blocking_gates_passed: Whether every blocking metric passed.
        meets_dataset_minimum: Whether the dataset reached the size the
            experiment process requires before promotion.
        eligible_for_promotion: Whether this run may support a promotion at all.
        local_artifact_path: Where the raw report was written.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal["1"] = "1"
    run_id: str
    suite: str
    started_at: str
    completed_at: str
    source: SourceMetadata
    dataset: DatasetMetadata
    model: ModelMetadata
    environment: EnvironmentMetadata
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    metrics: list[MetricScore]
    cases: list[CaseResult]
    blocking_gates_passed: bool
    meets_dataset_minimum: bool
    eligible_for_promotion: bool = False
    local_artifact_path: str


def model_metadata(settings: CapabilitySettings) -> ModelMetadata:
    """Collect exact model identity, tolerating an unreachable endpoint.

    A suite always emits a report, including when the local server is down, so
    a missing endpoint becomes incomplete metadata rather than a lost run. The
    `metadata_complete` flag is what a promotion decision reads.

    Args:
        settings: Local endpoint configuration.

    Returns:
        Model identity, marked incomplete when the endpoint could not be read.
    """

    try:
        return collect_model_metadata(settings, None, vision_required=False)
    except Exception:  # noqa: BLE001 - metadata gaps are reported, not raised
        return ModelMetadata(
            endpoint_kind="unknown",
            model_alias=settings.model_alias,
            gguf_filename=None,
            gguf_sha256=None,
            quantization=None,
            vision_projector_sha256=None,
            chat_template_sha256=None,
            chat_template_capabilities=[],
            modalities=[],
            context_size=None,
            server_version=None,
            server_binary_sha256=None,
            metadata_complete=False,
        )


def load_cases(path: Path) -> tuple[list[dict[str, JsonValue]], str]:
    """Load one JSONL dataset and its content hash.

    Args:
        path: Committed dataset file.

    Returns:
        The parsed cases and the hex SHA-256 of the exact file bytes.

    Raises:
        ValueError: If the dataset is empty, malformed, or repeats a case
            identifier, because a duplicate identifier would make a score
            impossible to attribute.
    """

    raw = path.read_bytes()
    cases: list[dict[str, JsonValue]] = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{number} is not valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError(f"{path.name}:{number} is not a JSON object")
        cases.append(parsed)
    if not cases:
        raise ValueError(f"{path.name} contains no cases")
    identifiers = [str(case.get("case_id")) for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{path.name} repeats a case identifier")
    return cases, sha256(raw).hexdigest()


def dataset_metadata(
    *,
    suite: str,
    path: Path,
    cases: Sequence[Mapping[str, JsonValue]],
    content_hash: str,
) -> DatasetMetadata:
    """Describe the immutable dataset one run consumed.

    Args:
        suite: Suite name from the catalog.
        path: Dataset file, used only for its stem as a stable identifier.
        cases: Parsed cases.
        content_hash: Hex SHA-256 of the dataset file.

    Returns:
        Dataset identity for the run manifest.

    Raises:
        ValueError: If the cases disagree about the dataset version, which
            would make the run impossible to reproduce.
    """

    versions = {str(case.get("dataset_version")) for case in cases}
    if len(versions) != 1:
        raise ValueError(f"{path.name} mixes dataset versions {sorted(versions)}")
    return DatasetMetadata(
        suite=suite,
        dataset_id=path.stem,
        version=versions.pop(),
        content_hash=content_hash,
        item_count=len(cases),
        private_dataset=False,
    )


def rate(numerator: int, denominator: int) -> float:
    """Return an exact ratio, treating an empty class as a perfect score.

    An absent case class means the dataset does not exercise it. That is a
    coverage gap reported by the dataset-minimum gate, not evidence of a
    failure, so it must not silently depress an accuracy metric.

    Args:
        numerator: Matching cases.
        denominator: Cases the metric applies to.

    Returns:
        The ratio rounded to four places, or 1.0 for an empty class.
    """

    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 4)


def blocking_gates_passed(metrics: Iterable[MetricScore]) -> bool:
    """Return whether every blocking metric satisfied its gate.

    Args:
        metrics: Scored metrics.

    Returns:
        Whether promotion is not blocked by a measured failure.
    """

    return all(metric.passed for metric in metrics if metric.blocking)


def write_report(report: SuiteReport, *, output_root: Path) -> Path:
    """Write one run's manifest and scores under a fresh artifact directory.

    Args:
        report: Complete suite report.
        output_root: Ignored artifact root holding raw run output.

    Returns:
        The directory the run was written to.
    """

    directory = output_root / report.run_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    _write_json(directory / "manifest.json", payload)
    _write_json(
        directory / "scores.json",
        {
            "run_id": report.run_id,
            "suite": report.suite,
            "dataset_version": report.dataset.version,
            "dataset_content_hash": report.dataset.content_hash,
            "item_count": report.dataset.item_count,
            "configuration": report.configuration,
            "metrics": [metric.model_dump(mode="json") for metric in report.metrics],
            "blocking_gates_passed": report.blocking_gates_passed,
            "meets_dataset_minimum": report.meets_dataset_minimum,
            "eligible_for_promotion": report.eligible_for_promotion,
        },
    )
    return directory


def new_run_id(prefix: str) -> str:
    """Return a sortable run identifier carrying no personal data.

    Args:
        prefix: Short uppercase suite prefix.

    Returns:
        A prefixed UTC timestamp and random suffix.
    """

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid4().hex[:8]}"


def sha256_text(value: str) -> str:
    """Return the hex SHA-256 of one instruction text.

    A score belongs to the exact text that produced it. Recording the hash
    rather than the prose keeps a run attributable without copying a prompt
    into every committed report.

    Args:
        value: Instruction text under test.

    Returns:
        Hex SHA-256 of the UTF-8 encoding.
    """

    return sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    """Return the current instant as ISO text for a manifest."""

    return datetime.now(UTC).isoformat()


def _write_json(path: Path, value: object) -> None:
    """Atomically write stable UTF-8 JSON evaluation output."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "CaseResult",
    "CaseStatus",
    "MetricScore",
    "SuiteReport",
    "blocking_gates_passed",
    "dataset_metadata",
    "load_cases",
    "model_metadata",
    "new_run_id",
    "rate",
    "sha256_text",
    "utc_now",
    "write_report",
]
