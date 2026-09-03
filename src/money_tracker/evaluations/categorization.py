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

from pydantic import JsonValue

from money_tracker.domain import (
    CategoryStatus,
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
    dataset_metadata,
    load_cases,
    model_metadata,
    new_run_id,
    rate,
    utc_now,
    write_report,
)
from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.workflows import (
    CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
    CategoryFit,
    CategoryFitOutcome,
    StructuredOutputError,
    StructuredOutputModel,
    UnresolvedExtractionError,
    build_category_proposal,
    parse_with_one_repair,
    render_active_categories,
    resolve_existing_category,
)

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


def _active_names(taxonomy: Taxonomy) -> tuple[str, ...]:
    """Render the owner's active category names for the boundary."""

    return tuple(
        category.name
        for category in taxonomy.categories
        if category.status is CategoryStatus.ACTIVE
    )


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

    The boundary is a flat schema with conditional fields, chosen because no
    local candidate is capability-screened for union output. That shape lets a
    model fill every field and then select the wrong discriminator, so this
    separates "could not find the category" from "found it and proposed anyway".
    The two failures need different fixes, and only the second is measurable
    without a better model.
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
            value=rate(sum(named_while_proposing.values()), proposals_offered),
            threshold=None,
            blocking=False,
            numerator=sum(named_while_proposing.values()),
            denominator=proposals_offered,
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
    instructions: str = CANDIDATE_CATEGORY_FIT_INSTRUCTIONS,
) -> SuiteReport:
    """Execute the categorization suite and always emit a deterministic report.

    Args:
        settings: Local endpoint configuration.
        dataset_path: Committed labelled dataset.
        output_root: Ignored artifact root for raw run output.
        repository_root: Repository directory, for source metadata.
        model: Structured-output port. The local adapter is used when omitted.
        instructions: Purpose-boundary instruction text under test. Unpromoted
            by default, and recorded so a score is attributable to one text.

    Returns:
        The complete report, already written to its artifact directory.
    """

    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    taxonomy = initial_taxonomy()
    rendered = instructions + render_active_categories(_active_names(taxonomy))
    port = model or LocalStructuredOutputModel()

    results: list[CaseResult] = []
    proposed_names: dict[str, str | None] = {}
    named_while_proposing: dict[str, bool] = {}
    for case in cases:
        result, proposed, named = _execute(port, rendered, case, taxonomy)
        results.append(result)
        proposed_names[result.case_id] = proposed
        named_while_proposing[result.case_id] = named

    metrics = _metrics(cases, results, proposed_names, named_while_proposing, taxonomy)
    passed = blocking_gates_passed(metrics)
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
            "instructions_sha256": _sha256_text(instructions),
            "taxonomy_active_categories": len(_active_names(taxonomy)),
            "dataset_minimum": DATASET_MINIMUM,
            "auto_post_policy_promoted": False,
        },
        metrics=metrics,
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
    instructions: str,
    case: Mapping[str, JsonValue],
    taxonomy: Taxonomy,
) -> tuple[CaseResult, str | None, bool]:
    """Classify one labelled message and resolve the judgment."""

    case_id = str(case["case_id"])
    expected = str(case.get("expected_category_name") or PROPOSAL)
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    started = time.monotonic()
    try:
        fit = parse_with_one_repair(
            model,
            schema=CategoryFit,
            instructions=instructions,
            message=str(case["message"]),
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


def _sha256_text(value: str) -> str:
    """Return the hex SHA-256 of one instruction text."""

    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()


__all__ = ["DATASET_MINIMUM", "SUITE", "run_categorization_suite"]
