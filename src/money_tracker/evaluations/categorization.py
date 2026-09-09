"""Executable `categorization/purpose-it` suite over the real purpose boundary.

The suite scores what the expense workflow actually runs: the versioned
category-fit boundary followed by deterministic resolution against the owner's
active taxonomy. A judgment the taxonomy would refuse is a failure here, not a
near miss, because that is exactly how production treats it.

One gate deserves explanation because it is the reason this suite exists. The
evaluation matrix forbids a merchant, brand, or payment method from being
proposed as a category, with zero tolerance. AD-007 keeps purpose and funding
as independent dimensions, so a category named after how something was paid for
would collapse them. Each safety case therefore carries the exact tokens its
message foregrounds, and a proposal echoing one of them fails the gate no
matter how plausible its rationale reads.

The auto-post gates in the matrix are emitted with nothing to measure. No
auto-post policy is promoted, so the eligible subset is empty by construction.
Emitting them at zero denominator keeps the absence visible in every report
rather than hiding it by omission.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from money_tracker.domain import (
    TAXONOMY_VERSION,
    DomainError,
    Taxonomy,
    initial_taxonomy,
)
from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    collect_environment_metadata,
    collect_source_metadata,
)
from money_tracker.evaluations.suites import (
    CaseResult,
    CaseStatus,
    MetricScore,
    SuiteReport,
    blocking_gates_passed,
    case_slice_values,
    dataset_metadata,
    latency_measurement_configuration,
    load_cases,
    model_metadata,
    new_run_id,
    rate,
    sha256_text,
    utc_now,
    warm_latency_p95_metric,
    write_report,
)
from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.workflows import (
    CategoryFit,
    CategoryFitOutcome,
    StructuredOutputError,
    StructuredOutputModel,
    UnresolvedExtractionError,
    build_category_proposal,
    parse_with_one_repair,
    resolve_existing_category,
)
from money_tracker.workflows.candidates import (
    CATEGORY_FIT_CANDIDATES as CATEGORY_FIT_CANDIDATES,
)
from money_tracker.workflows.candidates import (
    DEFAULT_CATEGORY_FIT_CANDIDATE as DEFAULT_CATEGORY_FIT_CANDIDATE,
)
from money_tracker.workflows.candidates import (
    CategoryFitCandidate as CategoryFitCandidate,
)
from money_tracker.workflows.candidates import (
    active_category_names as _active_names,
)
from money_tracker.workflows.two_stage import TwoStageCategoryFit

SUITE = "categorization/purpose-it"
"""Suite name from the catalog in the experiment process."""

DATASET_MINIMUM = 150
"""Cases the experiment process requires before this suite may support promotion."""

PROPOSAL = "proposal"
"""Label for a case whose purpose the active taxonomy does not cover."""


def _fold(value: str) -> str:
    """Normalize text for token comparison the way the taxonomy does."""

    decomposed = unicodedata.normalize("NFKD", " ".join(value.split()))
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).casefold()


def _outcome(
    fit: CategoryFit,
    taxonomy: Taxonomy,
) -> tuple[str, str | None]:
    """Resolve one judgment the way deterministic assembly would.

    Args:
        fit: Parsed model judgment.
        taxonomy: The owner's active taxonomy.

    Returns:
        The produced label and a stable refusal detail when resolution failed.
        An existing outcome resolves to its category name; a proposal that the
        taxonomy accepts resolves to `proposal`.
    """

    if fit.outcome is CategoryFitOutcome.EXISTING:
        try:
            return resolve_existing_category(fit, taxonomy=taxonomy).name, None
        except (UnresolvedExtractionError, DomainError) as error:
            return "unresolved", type(error).__name__
    try:
        build_category_proposal(
            fit,
            taxonomy=taxonomy,
            profile_id=uuid4(),
            proposal_id=uuid4(),
            source_reference="eval:categorization",
        )
    except (UnresolvedExtractionError, DomainError, ValueError) as error:
        reason = getattr(error, "reason", None)
        return "refused_proposal", (
            reason.value if reason is not None else type(error).__name__
        )
    return PROPOSAL, None


def _proposed_name(fit: CategoryFit) -> str | None:
    """Return the candidate name a proposal offered, if it offered one."""

    if fit.outcome is not CategoryFitOutcome.PROPOSAL:
        return None
    return fit.proposed_name


def _named_while_proposing(fit: CategoryFit, taxonomy: Taxonomy) -> bool:
    """Return whether a proposal also named a real existing category.

    This separates "could not find the category" from "found it and proposed
    anyway". The two failures need different fixes, and on the flat schema the
    second one was the dominant failure: half of all proposals also carried the
    correct existing name.

    It measures a property of the flat schema alone. A union boundary has no
    field in which a proposal could name an existing category, so the question
    is not merely unobserved there but unaskable, and the metric is emitted at
    zero denominator rather than as a zero rate. A rate of zero would read as
    the model never knowing the answer, which is the opposite of what the union
    established.
    """

    if fit.outcome is not CategoryFitOutcome.PROPOSAL or fit.category_name is None:
        return False
    return _fold(fit.category_name) in {_fold(n) for n in _active_names(taxonomy)}


def _metrics(
    cases: Sequence[Mapping[str, JsonValue]],
    results: Sequence[CaseResult],
    proposed_names: Mapping[str, str | None],
    named_while_proposing: Mapping[str, bool],
    taxonomy: Taxonomy,
    *,
    both_halves_expressible: bool,
) -> list[MetricScore]:
    """Score every gate the evaluation matrix defines for categorization."""

    paired = list(zip(cases, results, strict=True))
    matched = sum(1 for _, result in paired if result.status is CaseStatus.PASSED)

    existing_cases = [
        (case, result) for case, result in paired if result.expected != PROPOSAL
    ]
    false_proposals = sum(
        1
        for _, result in existing_cases
        if result.actual in {PROPOSAL, "refused_proposal"}
    )

    safety_cases = [
        (case, result)
        for case, result in paired
        if "safety" in cast(list[JsonValue], case.get("tags", []))
    ]
    dimension_collapses = 0
    for case, result in safety_cases:
        name = proposed_names.get(result.case_id)
        if name is None:
            continue
        folded = _fold(name)
        forbidden = cast(list[JsonValue], case.get("forbidden_name_tokens", []))
        if any(_fold(str(token)) in folded for token in forbidden):
            dimension_collapses += 1

    active = {_fold(name) for name in _active_names(taxonomy)}
    duplicates = sum(
        1
        for name in proposed_names.values()
        if name is not None and _fold(name) in active
    )
    proposals_offered = sum(1 for name in proposed_names.values() if name is not None)

    return [
        MetricScore(
            name="purpose_label_agreement",
            value=rate(matched, len(paired)),
            threshold=0.85,
            blocking=True,
            numerator=matched,
            denominator=len(paired),
        ),
        MetricScore(
            name="false_new_category_proposal",
            value=rate(false_proposals, len(existing_cases)),
            threshold=0.05,
            comparison="at_most",
            blocking=True,
            numerator=false_proposals,
            denominator=len(existing_cases),
        ),
        MetricScore(
            name="merchant_or_payment_method_proposed_as_category",
            value=float(dimension_collapses),
            threshold=0.0,
            comparison="at_most",
            blocking=True,
            numerator=dimension_collapses,
            denominator=len(safety_cases),
        ),
        MetricScore(
            name="duplicate_or_alias_equivalent_proposal",
            value=rate(duplicates, proposals_offered),
            threshold=0.02,
            comparison="at_most",
            blocking=True,
            numerator=duplicates,
            denominator=proposals_offered,
        ),
        MetricScore(
            name="auto_post_subset_category_precision",
            value=1.0,
            threshold=0.98,
            blocking=True,
            numerator=0,
            denominator=0,
        ),
        MetricScore(
            name="auto_post_subset_invariant_validity",
            value=1.0,
            threshold=1.0,
            blocking=True,
            numerator=0,
            denominator=0,
        ),
        MetricScore(
            name="existing_category_named_while_proposing",
            value=rate(
                sum(named_while_proposing.values()) if both_halves_expressible else 0,
                proposals_offered if both_halves_expressible else 0,
            ),
            threshold=None,
            blocking=False,
            numerator=(
                sum(named_while_proposing.values()) if both_halves_expressible else 0
            ),
            denominator=proposals_offered if both_halves_expressible else 0,
        ),
        MetricScore(
            name="proposals_offered",
            value=float(proposals_offered),
            threshold=None,
            blocking=False,
            numerator=proposals_offered,
            denominator=len(paired),
        ),
    ]


def run_categorization_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    repository_root: Path,
    model: StructuredOutputModel | None = None,
    candidate: CategoryFitCandidate = DEFAULT_CATEGORY_FIT_CANDIDATE,
    two_stage: TwoStageCategoryFit | None = None,
) -> SuiteReport:
    """Execute the categorization suite and always emit a deterministic report.

    Args:
        settings: Local endpoint configuration.
        dataset_path: Committed labelled dataset.
        output_root: Ignored artifact root for raw run output.
        repository_root: Repository directory, for source metadata.
        model: Structured-output port. The local adapter is used when omitted.
        candidate: Boundary under test: its schema, instruction text, and
            conversion. Unpromoted, and recorded so a score is attributable to
            one boundary shape.

    Returns:
        The complete report, already written to its artifact directory.
    """

    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    taxonomy = initial_taxonomy()
    rendered = candidate.instructions + candidate.render_taxonomy(taxonomy)
    schema = candidate.build_schema(taxonomy)
    port = model or LocalStructuredOutputModel()

    results: list[CaseResult] = []
    proposed_names: dict[str, str | None] = {}
    named_while_proposing: dict[str, bool] = {}
    headings: dict[str, str | None] = {}
    for index, case in enumerate(cases):
        if two_stage is None:
            result, proposed, named = _execute(
                port, candidate, schema, rendered, case, taxonomy
            )
            heading: str | None = None
        else:
            result, proposed, named, heading = _execute_two_stage(
                port, two_stage, case, taxonomy
            )
        result = result.model_copy(
            update={
                "slice_values": case_slice_values(case, suite=SUITE, position=index)
            }
        )
        results.append(result)
        proposed_names[result.case_id] = proposed
        named_while_proposing[result.case_id] = named
        headings[result.case_id] = heading

    metrics = [
        *_metrics(
            cases,
            results,
            proposed_names,
            named_while_proposing,
            taxonomy,
            both_halves_expressible=schema is CategoryFit,
        ),
        warm_latency_p95_metric(results),
    ]
    passed = blocking_gates_passed(metrics, results)
    meets_minimum = dataset.item_count >= DATASET_MINIMUM
    report = SuiteReport(
        run_id=new_run_id("CAT"),
        suite=SUITE,
        started_at=started_at,
        completed_at=utc_now(),
        source=collect_source_metadata(repository_root),
        dataset=dataset,
        model=model_metadata(settings),
        environment=collect_environment_metadata(),
        configuration={
            "instructions_version": (
                candidate.version if two_stage is None else two_stage.version
            ),
            "instructions_sha256": sha256_text(
                rendered
                if two_stage is None
                else two_stage.subtree_instructions + two_stage.leaf_instructions
            ),
            "boundary_schema": (
                schema.__name__ if two_stage is None else "TwoStageCategoryFit"
            ),
            "model_calls_per_case": 1 if two_stage is None else "one or two",
            "taxonomy_active_categories": len(_active_names(taxonomy)),
            "dataset_minimum": DATASET_MINIMUM,
            "latency_measurement": latency_measurement_configuration(),
            "taxonomy_version": TAXONOMY_VERSION,
            "auto_post_policy_promoted": False,
        },
        metrics=metrics + _two_stage_diagnostics(results, headings, taxonomy),
        cases=results,
        blocking_gates_passed=passed,
        meets_dataset_minimum=meets_minimum,
        eligible_for_promotion=passed and meets_minimum,
        local_artifact_path="",
    )
    directory = write_report(report, output_root=output_root)
    report = report.model_copy(update={"local_artifact_path": str(directory)})
    write_report(report, output_root=output_root)
    return report


def _execute(
    model: StructuredOutputModel,
    candidate: CategoryFitCandidate,
    schema: type[BaseModel],
    rendered_instructions: str,
    case: Mapping[str, JsonValue],
    taxonomy: Taxonomy,
) -> tuple[CaseResult, str | None, bool]:
    """Classify one labelled message and resolve the judgment."""

    case_id = str(case["case_id"])
    expected = str(case.get("expected_category_name") or PROPOSAL)
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    started = time.monotonic()
    try:
        fit = candidate.convert(
            parse_with_one_repair(
                model,
                schema=schema,
                instructions=rendered_instructions,
                message=str(case["message"]),
            )
        )
    except StructuredOutputError:
        return (
            CaseResult(
                case_id=case_id,
                status=CaseStatus.ERROR,
                expected=expected,
                tags=tags,
                latency_ms=round((time.monotonic() - started) * 1000),
                detail="invalid_model_output",
            ),
            None,
            False,
        )
    actual, detail = _outcome(fit, taxonomy)
    return (
        CaseResult(
            case_id=case_id,
            status=CaseStatus.PASSED if actual == expected else CaseStatus.FAILED,
            expected=expected,
            actual=actual,
            tags=tags,
            latency_ms=round((time.monotonic() - started) * 1000),
            detail=detail,
        ),
        _proposed_name(fit),
        _named_while_proposing(fit, taxonomy),
    )


def _subtree_of(name: str, taxonomy: Taxonomy) -> str | None:
    """Return the root a category name sits under, or itself when it is a root."""

    by_name = {category.name: category for category in taxonomy.categories}
    by_id = {category.id: category for category in taxonomy.categories}
    node = by_name.get(name)
    if node is None:
        return None
    return by_id[node.parent_id].name if node.parent_id else node.name


def _two_stage_diagnostics(
    results: Sequence[CaseResult],
    headings: Mapping[str, str | None],
    taxonomy: Taxonomy,
) -> list[MetricScore]:
    """Report what each stage contributed, for a two-stage run only.

    The decision behind the two stages rests on two numbers that were computed
    by hand from a manifest. A run that uses the stages reports them itself.

    Args:
        results: Per-case outcomes.
        headings: Heading the first stage chose, by case, None when it proposed.
        taxonomy: Active owner taxonomy.

    Returns:
        Two diagnostics, or nothing when no case reached the second stage.
    """

    scored = [
        result
        for result in results
        if headings.get(result.case_id) and result.expected != PROPOSAL
    ]
    if not scored:
        return []
    subtree_right = [
        result
        for result in scored
        if headings[result.case_id] == _subtree_of(str(result.expected), taxonomy)
    ]
    leaf_right = sum(
        1 for result in subtree_right if result.status is CaseStatus.PASSED
    )
    return [
        MetricScore(
            name="subtree_selected_correctly",
            value=rate(len(subtree_right), len(scored)),
            threshold=None,
            blocking=False,
            numerator=len(subtree_right),
            denominator=len(scored),
        ),
        MetricScore(
            name="level_correct_given_the_subtree",
            value=rate(leaf_right, len(subtree_right)),
            threshold=None,
            blocking=False,
            numerator=leaf_right,
            denominator=len(subtree_right),
        ),
    ]


def _execute_two_stage(
    model: StructuredOutputModel,
    two_stage: TwoStageCategoryFit,
    case: Mapping[str, JsonValue],
    taxonomy: Taxonomy,
) -> tuple[CaseResult, str | None, bool, str | None]:
    """Classify one labelled message through both stages."""

    case_id = str(case["case_id"])
    expected = str(case.get("expected_category_name") or PROPOSAL)
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    started = time.monotonic()
    try:
        judgment = two_stage.judge(model, taxonomy, str(case["message"]))
    except StructuredOutputError:
        return (
            CaseResult(
                case_id=case_id,
                status=CaseStatus.ERROR,
                expected=expected,
                tags=tags,
                latency_ms=round((time.monotonic() - started) * 1000),
                detail="invalid_model_output",
            ),
            None,
            False,
            None,
        )
    actual, detail = _outcome(judgment.fit, taxonomy)
    return (
        CaseResult(
            case_id=case_id,
            status=CaseStatus.PASSED if actual == expected else CaseStatus.FAILED,
            expected=expected,
            actual=actual,
            tags=tags,
            latency_ms=round((time.monotonic() - started) * 1000),
            detail=detail,
        ),
        _proposed_name(judgment.fit),
        _named_while_proposing(judgment.fit, taxonomy),
        judgment.heading,
    )


__all__ = ["DATASET_MINIMUM", "SUITE", "run_categorization_suite"]
