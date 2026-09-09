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
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from money_tracker.evaluations.catalog import (
    CATALOG_RELATIVE_PATH,
    CatalogError,
    ModelCatalog,
    load_catalog,
)
from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    collect_model_metadata,
)
from money_tracker.evaluations.models import (
    CapabilityReport,
    DatasetMetadata,
    EnvironmentMetadata,
    ModelMetadata,
    ProbeStatus,
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
        slice_values: Public-safe labels used for required report breakdowns.
        detail: Small non-sensitive diagnostic, such as a refusal reason code.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: CaseStatus
    expected: str
    actual: str | None = None
    tags: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    slice_values: dict[str, list[str]] = Field(default_factory=dict)
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
        applicable: Whether the implemented capability has this obligation.
        inapplicability_reason: Explanation required for a structural exemption.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    threshold: float | None
    comparison: Literal["at_least", "at_most"] = "at_least"
    blocking: bool
    numerator: int
    denominator: int
    applicable: bool = True
    inapplicability_reason: str | None = None

    @model_validator(mode="after")
    def _validate_applicability(self) -> MetricScore:
        """Require an explicit reason for an unexercised structural exemption."""

        if not self.applicable and (
            self.denominator != 0 or not self.inapplicability_reason
        ):
            raise ValueError("inapplicable metrics need a reason and no observations")
        if self.applicable and self.inapplicability_reason is not None:
            raise ValueError("an applicable metric cannot claim an exemption")
        return self

    @property
    def passed(self) -> bool:
        """Return whether this metric satisfies its gate.

        Diagnostics and explicitly inapplicable obligations do not block.
        An applicable blocking metric with no observations is unmeasured and
        cannot pass. Dataset size alone cannot establish coverage of a gate.
        """

        if self.threshold is None or not self.applicable:
            return True
        if self.denominator == 0:
            return not self.blocking
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
        promotion_blockers: Machine-readable missing prerequisites, independent
            of the individual model quality scores.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal["1", "2", "3"] = "3"
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
    promotion_blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_evidence_requirements(self) -> SuiteReport:
        """Prevent callers from promoting missing, partial, or unpinned evidence.

        A report may conservatively decline a promotion, but cannot claim one
        by supplying booleans that contradict its observations or metadata.
        Workflow-only and calibration runs explicitly have no promotion effect.
        """

        blockers: list[str] = []
        if len(self.cases) != self.dataset.item_count or len(
            {case.case_id for case in self.cases}
        ) != len(self.cases):
            blockers.append("incomplete_case_results")
        if not self.cases or any(
            case.status is CaseStatus.ERROR for case in self.cases
        ):
            blockers.append("case_execution_error")
        if not blocking_gates_passed(self.metrics, self.cases):
            blockers.append("blocking_gate_failed_or_unmeasured")
        self.blocking_gates_passed = self.blocking_gates_passed and not blockers
        if not self.meets_dataset_minimum:
            blockers.append("dataset_below_minimum")
        if not self.model.metadata_complete:
            blockers.append("model_metadata_incomplete")
        if self.configuration.get("promotion_effect") == "none":
            blockers.append("non_promoting_suite")
        self.promotion_blockers = blockers
        self.eligible_for_promotion = (
            self.eligible_for_promotion and self.blocking_gates_passed and not blockers
        )
        return self


def server_properties(settings: CapabilitySettings) -> dict[str, object] | None:
    """Read the local inference server's own description of what it loaded.

    Args:
        settings: Local endpoint configuration.

    Returns:
        The server's properties, or None when the endpoint cannot be reached or
        does not answer with an object. An unreachable server is a condition a
        caller reports, never an exception a run dies on.
    """

    try:
        with httpx.Client(
            timeout=min(settings.timeout_seconds, 10.0), trust_env=False
        ) as client:
            response = client.get(
                f"{settings.server_root_url}/props",
                params={"model": settings.model_alias},
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )
            response.raise_for_status()
            decoded = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def loaded_model_filename(settings: CapabilitySettings) -> str | None:
    """Return the weights filename the inference server reports having loaded.

    Args:
        settings: Local endpoint configuration.

    Returns:
        The filename, or None when the server is unreachable or does not name a
        model. This is what a run is actually talking to, as opposed to what the
        environment says it should be.
    """

    properties = server_properties(settings) or {}
    reported = properties.get("model_path")
    return Path(str(reported)).name if reported else None


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

    properties = server_properties(settings)
    try:
        return collect_model_metadata(settings, properties, vision_required=False)
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


def warm_latency_p95_metric(
    cases: Sequence[CaseResult], *, warmup_cases: int = 1
) -> MetricScore:
    """Report the successful per-case p95 after an in-suite warm-up sample.

    Args:
        cases: Ordered case results carrying measured wall-clock latency.
        warmup_cases: Leading observations excluded from the warm distribution.

    Returns:
        A non-blocking nearest-rank p95 metric in milliseconds. Errors and
        missing timings are excluded because the product latency target applies
        to successful runs.

    Raises:
        ValueError: If ``warmup_cases`` is negative.
    """

    if warmup_cases < 0:
        raise ValueError("warmup_cases cannot be negative")
    samples = sorted(
        case.latency_ms
        for case in cases[warmup_cases:]
        if case.status is not CaseStatus.ERROR and case.latency_ms is not None
    )
    value = float(samples[math.ceil(0.95 * len(samples)) - 1]) if samples else 0.0
    return MetricScore(
        name="warm_latency_p95_ms",
        value=value,
        threshold=None,
        blocking=False,
        numerator=len(samples),
        denominator=len(samples),
    )


def latency_measurement_configuration() -> dict[str, JsonValue]:
    """Describe the common per-suite warm-latency measurement method.

    Returns:
        Public-safe configuration that distinguishes this diagnostic from the
        future end-to-end product measurement.
    """

    return {
        "scope": "per_case_suite_wall_clock",
        "clock": "monotonic",
        "warmup_cases_excluded": 1,
        "successful_cases_only": True,
        "percentile_method": "nearest_rank",
        "blocking": False,
    }


def blocking_gates_passed(
    metrics: Iterable[MetricScore], cases: Sequence[CaseResult]
) -> bool:
    """Require a complete execution and every applicable blocking measurement.

    Execution errors cannot disappear from accuracy denominators. A run with
    one answered case and 109 transport failures is incomplete evidence even
    when the answered case satisfies every metric it exercised.

    Args:
        metrics: Scored metrics.
        cases: Per-case outcomes from the same run.

    Returns:
        Whether every case executed and no blocking gate is failed or missing.
    """

    executed = bool(cases) and all(
        case.status is not CaseStatus.ERROR for case in cases
    )
    return executed and all(metric.passed for metric in metrics if metric.blocking)


_SLICE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "difficulty": ("easy", "normal", "adversarial", "ambiguous", "unclassified"),
    "intent": ("unclassified",),
    "domain_feature": ("unclassified",),
    "auto_post_eligibility": ("eligible", "ineligible"),
    "funding_class": (
        "personal_funds",
        "benefit",
        "default",
        "split",
        "other",
        "not_applicable",
    ),
    "category_provenance": ("seed", "alias", "proposal", "not_applicable"),
    "adversarial_tag": (
        "typo",
        "locale",
        "relative_date",
        "negation",
        "quoted_text",
        "prompt_injection",
        "none",
    ),
    "warm_state": ("warmup", "warm"),
    "media_quality": ("not_applicable",),
}


def case_slice_values(
    case: Mapping[str, JsonValue], *, suite: str, position: int
) -> dict[str, list[str]]:
    """Derive required public reporting labels from one versioned case.

    Args:
        case: Parsed dataset row. Message text is never copied.
        suite: Suite owning the case, used where intent is implicit.
        position: Zero-based execution order; the first case is the warm-up.

    Returns:
        Stable, public-safe values for every required reporting dimension.
    """

    tags = {str(tag) for tag in cast(list[JsonValue], case.get("tags", []))}
    difficulties = sorted(tags & {"easy", "normal", "adversarial", "ambiguous"})

    intent_value = case.get("expected_intent") or case.get("route")
    if intent_value is None:
        intent_value = {
            "ingestion/expense-it": case.get("expected_polarity") or "expense",
            "categorization/purpose-it": "category_fit",
            "workflow/category-review-it": "category_follow_up",
            "advice/grounding-it": "advice",
        }.get(suite, "unclassified")

    metadata_tags = {
        "synthetic",
        "it-IT",
        "easy",
        "normal",
        "adversarial",
        "ambiguous",
    }
    domain_features = sorted(tags - metadata_tags) or ["unclassified"]

    funding: set[str] = set()
    expected_funding = case.get("expected_funding")
    if isinstance(expected_funding, list):
        for allocation in expected_funding:
            if isinstance(allocation, list) and len(allocation) >= 2:
                funding_class = str(allocation[1])
                if funding_class in {"personal_funds", "benefit", "other"}:
                    funding.add(funding_class)
        if len(expected_funding) > 1:
            funding.add("split")
        if expected_funding and case.get("states_funding") is False:
            funding.add("default")
    if tags & {"benefit", "benefit-credit"}:
        funding.add("benefit")
    if tags & {"split", "split-funding", "funding-split"}:
        funding.add("split")
    if "funding-split" in tags:
        funding.update({"personal_funds", "benefit"})

    provenance: set[str] = set()
    if "alias" in tags:
        provenance.add("alias")
    elif case.get("expected_outcome") == "proposal" or "proposal" in tags:
        provenance.add("proposal")
    elif case.get("expected_category_name") is not None:
        provenance.add("seed")
    elif suite == "workflow/category-review-it":
        provenance.add("proposal")

    adversarial: set[str] = set()
    mappings = {
        "typo": {"typo", "digit-swap"},
        "locale": {"locale"},
        "relative_date": {"relative-date", "relative-period"},
        "negation": {"negation"},
        "quoted_text": {"quoted", "reported-speech"},
        "prompt_injection": {"prompt-injection"},
    }
    for label, source_tags in mappings.items():
        if tags & source_tags:
            adversarial.add(label)

    media_tags = sorted(
        tags
        & {
            "blurred",
            "cropped",
            "glare",
            "handwritten",
            "low-contrast",
            "rotated",
        }
    )
    return {
        "difficulty": difficulties or ["unclassified"],
        "intent": [str(intent_value)],
        "domain_feature": domain_features,
        "auto_post_eligibility": [
            "eligible" if case.get("auto_post_eligible") is True else "ineligible"
        ],
        "funding_class": sorted(funding) or ["not_applicable"],
        "category_provenance": sorted(provenance) or ["not_applicable"],
        "adversarial_tag": sorted(adversarial) or ["none"],
        "warm_state": ["warmup" if position == 0 else "warm"],
        "media_quality": media_tags or ["not_applicable"],
    }


def _slice_count(value: str, cases: Sequence[CaseResult]) -> dict[str, JsonValue]:
    """Count aggregate, failed, and errored cases for one slice value."""

    errors = sum(case.status is CaseStatus.ERROR for case in cases)
    failures = sum(case.status is not CaseStatus.PASSED for case in cases)
    return {
        "value": value,
        "total": len(cases),
        "failure_count": failures,
        "error_count": errors,
    }


def _version_set(report: SuiteReport) -> dict[str, JsonValue]:
    """Return the exact version-bearing configuration for one report."""

    configuration = report.configuration
    prompt: dict[str, JsonValue] = {
        "id": configuration.get("instructions_version", "not_recorded"),
        "sha256": configuration.get("instructions_sha256", "not_recorded"),
    }
    tools: JsonValue = "not_applicable"
    if "query_tools" in configuration or "advice_tools" in configuration:
        tools = {
            "query_registry": configuration.get("query_tools", []),
            "advice_registry": configuration.get("advice_tools", []),
            "request_schema_fields": configuration.get("request_schema_fields", []),
        }
    return {
        "report_schema": report.manifest_schema_version,
        "suite": report.suite,
        "dataset": {
            "id": report.dataset.dataset_id,
            "version": report.dataset.version,
            "content_hash": report.dataset.content_hash,
        },
        "model": model_row(report.model),
        "prompt": prompt,
        "schema": configuration.get("boundary_schema", "not_applicable"),
        "tools": tools,
        "taxonomy": configuration.get("taxonomy_version", "not_applicable"),
        "receipt_pipeline": configuration.get(
            "receipt_pipeline_version", "not_applicable"
        ),
    }


def aggregate_report_slices(report: SuiteReport) -> dict[str, JsonValue]:
    """Aggregate required report dimensions without reopening artifacts.

    Args:
        report: Complete run whose case labels already carry public slice values.

    Returns:
        Overall and per-value counts plus the run's exact version set. A failure
        count includes execution errors; ``error_count`` keeps those visible.
    """

    dimensions: dict[str, JsonValue] = {}
    for dimension, defaults in _SLICE_DEFAULTS.items():
        values = set(defaults)
        for case in report.cases:
            values.update(case.slice_values.get(dimension, ()))
        rows: list[dict[str, JsonValue]] = []
        for value in sorted(values):
            matching = [
                case
                for case in report.cases
                if value in case.slice_values.get(dimension, ())
            ]
            rows.append(_slice_count(value, matching))
        dimensions[dimension] = cast(JsonValue, rows)
    return {
        "overall": _slice_count("all", report.cases),
        "dimensions": dimensions,
        "version_set": _version_set(report),
    }


def write_report(report: SuiteReport, *, output_root: Path) -> Path:
    """Write one run's manifest and scores under a fresh artifact directory.

    Runs are kept under the model that produced them, so a sweep across several
    candidates leaves one directory per candidate rather than interleaving every
    run in one.

    Args:
        report: Complete suite report.
        output_root: Ignored artifact root holding raw run output.

    Returns:
        The directory the run was written to.
    """

    directory = (
        output_root / model_key(report.model, candidate_catalog()) / report.run_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    _write_json(directory / "manifest.json", payload)
    _write_json(
        directory / "scores.json",
        {
            "run_id": report.run_id,
            "suite": report.suite,
            "model": model_row(report.model),
            "dataset_version": report.dataset.version,
            "dataset_content_hash": report.dataset.content_hash,
            "item_count": report.dataset.item_count,
            "configuration": report.configuration,
            "metrics": [metric.model_dump(mode="json") for metric in report.metrics],
            "blocking_gates_passed": report.blocking_gates_passed,
            "meets_dataset_minimum": report.meets_dataset_minimum,
            "eligible_for_promotion": report.eligible_for_promotion,
            "promotion_blockers": report.promotion_blockers,
            "slices": aggregate_report_slices(report),
        },
    )
    return directory


def _repository_root() -> Path:
    """Return the repository root holding the published score directory."""

    return Path(__file__).resolve().parents[3]


def _metric_rows(report: SuiteReport) -> list[dict[str, JsonValue]]:
    """Render one run's metrics as plain rows carrying no owner text."""

    return [
        {
            "name": metric.name,
            "value": metric.value,
            "threshold": metric.threshold,
            "comparison": metric.comparison,
            "blocking": metric.blocking,
            "numerator": metric.numerator,
            "denominator": metric.denominator,
            "passed": metric.passed,
            "applicable": metric.applicable,
            "inapplicability_reason": metric.inapplicability_reason,
        }
        for metric in report.metrics
    ]


def model_row(model: ModelMetadata) -> dict[str, JsonValue]:
    """Render the model identity that every score surface must carry.

    A score cannot be read without knowing which model produced it, and the
    evaluation matrix requires a model slice on every report. One renderer
    keeps the raw summary, the published score, and the command output from
    drifting into three different answers about what was measured.

    Args:
        model: Exact model identity collected for the run.

    Returns:
        Public-safe identity fields. The GGUF hash is included because a
        filename and a quantization do not distinguish two revisions of the
        same weights.
    """

    return {
        "alias": model.model_alias,
        "gguf_filename": model.gguf_filename,
        "gguf_sha256": model.gguf_sha256,
        "quantization": model.quantization,
        "metadata_complete": model.metadata_complete,
    }


def candidate_catalog() -> ModelCatalog | None:
    """Load the declared candidate catalog, tolerating its absence.

    Returns:
        The catalog, or None when it cannot be read. Publication must not fail
        because the catalog is missing: a score is evidence about a run, and a
        run remains valid when the model it used was never catalogued.
    """

    try:
        return load_catalog(_repository_root() / CATALOG_RELATIVE_PATH)
    except CatalogError:
        return None


def model_key(
    model: ModelMetadata, catalog: ModelCatalog | None, *, modality: str = "text"
) -> str:
    """Return the directory segment one run's artifacts and scores are kept in.

    Artifacts and scores were keyed by suite alone, so every candidate's output
    landed in one place: raw runs interleaved in a single directory, and a
    second candidate's published score overwriting the first. Keying by model
    separates them.

    Args:
        model: Exact model identity recorded by the run.
        catalog: Declared candidates, or None when the catalog is unreadable.
        modality: Modality the run exercised, which decides between two profiles
            declared over the same weights.

    Returns:
        The name :func:`model_name` gives these weights.
    """

    return model_name(model_row(model), catalog, modality=modality)


def model_name(
    row: object, catalog: ModelCatalog | None, *, modality: str = "text"
) -> str:
    """Name the model behind one score, preferring its catalogued identity.

    The name is derived from what a run recorded rather than from what its
    operator declared, so it stays honest about which weights actually
    answered. Identity alone: two runs of the same weights share a name whether
    or not either pinned its full metadata.

    Args:
        row: Model block of a score, or anything else when a score predates it.
        catalog: Declared candidates, or None when the catalog is unreadable.
        modality: Modality the run exercised. The same multimodal weights are
            declared once for text and once for vision, and a run is named for
            the way it used them.

    Returns:
        The catalog profile identifier when the weights are declared, matched
        first on the recorded hash and then on the declared filename, which
        names the candidate but not its revision. An uncatalogued model is named
        by its weights filename, and a run that could not name its weights at
        all falls back to the server alias, which pins nothing and is meant to
        look like it.
    """

    if not isinstance(row, Mapping):
        return "unknown"
    filename = row.get("gguf_filename")
    if catalog is not None:
        profile = catalog.by_sha256(
            _text(row.get("gguf_sha256")), modality=modality
        ) or catalog.by_filename(_text(filename), modality=modality)
        if profile is not None:
            return profile.id
    if filename:
        return Path(str(filename)).stem
    return str(row.get("alias") or "unknown-model")


def _text(value: object) -> str | None:
    """Narrow one published JSON field to a string when it carries one."""

    return str(value) if isinstance(value, str) and value else None


def _model_cell(
    row: object, catalog: ModelCatalog | None, *, modality: str = "text"
) -> str:
    """Render one published score's model for a summary table cell.

    Args:
        row: Model block of a published score.
        catalog: Declared candidates, or None when the catalog is unreadable.
        modality: How the run exercised the weights, so a vision screen is not
            named after the text profile over the same artifact.

    Returns:
        The model name, marked when the run could not record the exact weights,
        template, and server it spoke to. Such a run is reproducible only by
        description, which the table states rather than hides.
    """

    name = model_name(row, catalog, modality=modality)
    complete = isinstance(row, Mapping) and row.get("metadata_complete")
    return name if complete else f"{name} (metadata incomplete)"


def publish_scores(report: SuiteReport, *, out_root: Path | None = None) -> Path | None:
    """Publish one run's numbers to the committed `out/` tree.

    The raw manifests under `artifacts/evals/` are ignored by Git, which makes
    a score impossible to read without rerunning the suite. This writes the
    same numbers to a tracked location so a result can be read, compared, and
    reviewed as evidence. Only aggregate metrics, gate outcomes, and dataset
    identity are published: per-case records stay in the raw manifest, because
    they carry labels and tags that belong with the run rather than in a
    summary.

    A run in which every case errored is not published either. Such a run
    measured nothing, and one was allowed to overwrite a real score before this
    guard existed.

    A run whose dataset is below the minimum the experiment process requires is
    not published at all. Such a run cannot support a gate, and letting one
    overwrite the published score of a full run would put a number in front of
    a reader that no promotion could ever rest on.

    Scores are keyed by suite and by model. Keying by suite alone meant that
    measuring a second candidate overwrote the first, so the two could never be
    compared and the published tree could only ever describe the most recent
    configuration.

    This is deliberately not called by `write_report`. A test that runs a suite
    against a scripted port writes a perfectly valid report to its own
    temporary directory, and publishing from the writer would let that report
    overwrite a real run's published score. Only the command-line entry point
    publishes, because only it is a run someone meant to record.

    Args:
        report: Complete suite report.
        out_root: Directory holding published scores. Defaults to
            `out/evaluations` at the repository root.

    Returns:
        The directory this run's scores were written to, or None when the run
        was too small to publish.
    """

    if not report.meets_dataset_minimum:
        return None
    if all(case.status is CaseStatus.ERROR for case in report.cases):
        return None

    root = out_root or (_repository_root() / "out" / "evaluations")
    directory = root / report.suite / model_key(report.model, candidate_catalog())
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, JsonValue] = {
        "suite": report.suite,
        "run_id": report.run_id,
        "completed_at": report.completed_at,
        "dataset": {
            "id": report.dataset.dataset_id,
            "version": report.dataset.version,
            "content_hash": report.dataset.content_hash,
            "item_count": report.dataset.item_count,
        },
        "model": model_row(report.model),
        "source_commit": report.source.git_commit,
        "configuration": report.configuration,
        "metrics": cast(JsonValue, _metric_rows(report)),
        "blocking_gates_passed": report.blocking_gates_passed,
        "meets_dataset_minimum": report.meets_dataset_minimum,
        "eligible_for_promotion": report.eligible_for_promotion,
        "promotion_blockers": cast(JsonValue, report.promotion_blockers),
        "case_totals": {
            status.value: sum(1 for case in report.cases if case.status is status)
            for status in CaseStatus
        },
        "slices": aggregate_report_slices(report),
    }
    _write_json(directory / f"{report.run_id}.json", payload)
    _write_json(directory / "latest.json", payload)
    write_scores_summary(root)
    return directory


def _instructions_label(entry: Mapping[str, JsonValue]) -> str:
    """Name the instruction candidate one published score was measured on.

    Args:
        entry: One published score.

    Returns:
        The candidate identifier, or an em dash for a suite that drives no
        model-facing instructions, such as the scripted category review.
    """

    configuration = entry.get("configuration")
    if isinstance(configuration, Mapping):
        version = configuration.get("instructions_version")
        if version:
            return f"`{version}`"
    return "—"


def _capability_rows(
    screens: list[dict[str, JsonValue]], catalog: ModelCatalog | None
) -> list[str]:
    """Render the capability screens as their own table.

    A screen answers whether a candidate may be used at an interface at all,
    which has no dataset gate and no threshold. Putting it in the scored-suite
    table would leave most columns empty and imply a comparison that does not
    exist.

    Args:
        screens: Published capability payloads.
        catalog: Declared candidates, for naming the model behind each screen.

    Returns:
        Markdown lines, empty when nothing has been screened.
    """

    if not screens:
        return []
    lines = [
        "",
        "## Capability screens",
        "",
        "Whether a candidate can use an interface at all, which is a different",
        "question from whether a workflow built on it is accurate enough to",
        "promote. A screen gates nothing on its own.",
        "",
        "| Model | Modality | Dataset | Blocking gates | Probes not passed | Run |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in screens:
        dataset = entry.get("dataset")
        dataset_label = "unknown"
        if isinstance(dataset, Mapping):
            dataset_label = f"{dataset.get('id')} {dataset.get('version')}"
        probes = entry.get("probes")
        unmet: list[str] = []
        if isinstance(probes, list):
            unmet = [
                f"`{probe.get('name')}` ({probe.get('status')})"
                for probe in probes
                if isinstance(probe, Mapping)
                and probe.get("status") not in ("passed", "not_applicable")
            ]
        modality = str(entry.get("modality") or "text")
        verdict = "pass" if entry.get("blocking_gates_passed") else "**fail**"
        lines.append(
            f"| {_model_cell(entry.get('model'), catalog, modality=modality)} | "
            f"{modality} | {dataset_label} | {verdict} | "
            f"{', '.join(unmet) or 'none'} | `{entry.get('run_id')}` |"
        )
    return lines


def publish_capability_scores(
    report: CapabilityReport,
    *,
    modality: str = "text",
    out_root: Path | None = None,
) -> Path | None:
    """Publish one capability screen's outcome to the committed `out/` tree.

    The screen decides whether a candidate is worth measuring at all, and its
    results lived only in the ignored raw artifacts. A decision to sweep one
    model and set another aside therefore rested on evidence a clean checkout
    does not contain. This writes the same per-probe outcomes to the tracked
    location the workflow suites already publish to, keyed the same way.

    Probe details are published with the outcomes. They are the numbers the
    screen exists to produce, and `ProbeResult` already restricts them to small
    non-sensitive diagnostics.

    Args:
        report: Complete capability report.
        modality: How the run exercised the weights, which names the profile
            when one artifact is declared for both text and vision.
        out_root: Directory holding published scores. Defaults to
            `out/evaluations` at the repository root.

    Returns:
        The directory the screen was written to, or None when every probe
        errored. Such a run screened nothing, and letting it overwrite a real
        screen would put an empty result where a verdict used to be.
    """

    scored = [p for p in report.probes if p.status is not ProbeStatus.NOT_APPLICABLE]
    if not scored or all(p.status is ProbeStatus.ERROR for p in scored):
        return None

    root = out_root or (_repository_root() / "out" / "evaluations")
    directory = (
        root
        / report.dataset.suite
        / model_key(report.model, candidate_catalog(), modality=modality)
    )
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, JsonValue] = {
        "suite": report.dataset.suite,
        "run_id": report.run_id,
        "completed_at": report.completed_at,
        "modality": modality,
        "dataset": {
            "id": report.dataset.dataset_id,
            "version": report.dataset.version,
            "content_hash": report.dataset.content_hash,
            "item_count": report.dataset.item_count,
        },
        "model": model_row(report.model),
        "source_commit": report.source.git_commit,
        "probes": cast(
            JsonValue,
            [
                {
                    "name": probe.name,
                    "status": probe.status.value,
                    "details": cast(JsonValue, probe.details),
                }
                for probe in report.probes
            ],
        ),
        "blocking_gates_passed": report.blocking_gates_passed,
        "probe_totals": {
            status.value: sum(1 for p in report.probes if p.status is status)
            for status in ProbeStatus
        },
    }
    _write_json(directory / f"{report.run_id}.json", payload)
    _write_json(directory / "latest.json", payload)
    write_scores_summary(root)
    return directory


def write_scores_summary(out_root: Path) -> Path:
    """Rewrite the Markdown overview of every published suite and model.

    Scores are keyed by suite and model, so one row is the most recent run of
    one suite on one candidate and several candidates can appear for the same
    suite. Comparing two rows is only meaningful when they share a dataset and
    an instruction candidate, so both are columns rather than prose.

    Args:
        out_root: Directory holding published scores.

    Returns:
        The summary file.
    """

    catalog = candidate_catalog()
    latest: list[dict[str, JsonValue]] = []
    for path in sorted(out_root.rglob("latest.json")):
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(decoded, dict):
            latest.append(decoded)
    latest.sort(
        key=lambda entry: (
            str(entry.get("suite")),
            model_name(entry.get("model"), catalog),
        )
    )
    screens = [entry for entry in latest if "probes" in entry]
    latest = [entry for entry in latest if "probes" not in entry]
    lines = [
        "# Evaluation scores",
        "",
        "Generated by the suite runners. Every row is the most recent run of one",
        "suite on one model; per-run history sits beside it in the same",
        "directory, and the raw manifests stay under the ignored",
        "`artifacts/evals/`.",
        "",
        "| Suite | Model | Instructions | Dataset | Cases | Blocking gates | "
        "Failing blocking metrics | Run |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for entry in latest:
        dataset = entry.get("dataset")
        metrics = entry.get("metrics")
        dataset_label = "unknown"
        item_count: JsonValue = 0
        if isinstance(dataset, dict):
            dataset_label = f"{dataset.get('id')} {dataset.get('version')}"
            item_count = dataset.get("item_count", 0)
        failing = []
        if isinstance(metrics, list):
            failing = [
                str(metric.get("name"))
                for metric in metrics
                if isinstance(metric, dict)
                and metric.get("blocking")
                and metric.get("passed") is False
            ]
        if not entry.get("blocking_gates_passed") and not failing:
            failing = ["incomplete_execution"]
        verdict = "pass" if entry.get("blocking_gates_passed") else "**fail**"
        lines.append(
            f"| `{entry.get('suite')}` | {_model_cell(entry.get('model'), catalog)} | "
            f"{_instructions_label(entry)} | {dataset_label} | {item_count} | "
            f"{verdict} | {', '.join(f'`{name}`' for name in failing) or 'none'} | "
            f"`{entry.get('run_id')}` |"
        )
    measured_by = sorted({model_name(entry.get("model"), catalog) for entry in latest})
    if len(measured_by) > 1:
        lines += [
            "",
            "More than one candidate appears above: "
            + ", ".join(measured_by)
            + ". Two rows are comparable only where they share a dataset and an "
            "instruction candidate; a row measured on different instructions "
            "reports a different experiment, not a worse model.",
        ]
    lines.extend(_capability_rows(screens, catalog))
    lines.append("")
    summary = out_root / "SUMMARY.md"
    summary.write_text("\n".join(lines), encoding="utf-8")
    return summary


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
    "aggregate_report_slices",
    "blocking_gates_passed",
    "case_slice_values",
    "dataset_metadata",
    "candidate_catalog",
    "latency_measurement_configuration",
    "load_cases",
    "loaded_model_filename",
    "model_key",
    "model_name",
    "model_row",
    "publish_capability_scores",
    "publish_scores",
    "write_scores_summary",
    "model_metadata",
    "new_run_id",
    "rate",
    "server_properties",
    "sha256_text",
    "utc_now",
    "warm_latency_p95_metric",
    "write_report",
]
