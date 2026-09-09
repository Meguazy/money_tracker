"""Executable `routing/intent-it` suite over the real compiled root router.

The suite scores the router as production uses it: deterministic pre-routing,
the model classification boundary, and every acceptance veto, at one explicit
confidence bar. Scoring a boundary in isolation would measure something the
owner never runs.

The confidence bar is a required parameter with no default, mirroring the
router assembly itself. A score is meaningless without the bar that produced
it, so the bar is recorded in the run configuration and a caller must state it.

Two metrics here are safety gates rather than accuracy measures. A read-only or
non-finance message routed to a financial write is counted as zero-tolerance,
and deterministic pre-routing is required to be exact, because both are
properties the design already guarantees and a regression in either is a defect
rather than a quality trend.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from pydantic import JsonValue

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
from money_tracker.runtime import build_router_graph
from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.workflows import (
    MUTATING_INTENTS,
    ROUTER,
    CallbackAction,
    ChannelMessage,
    RoutingContext,
    RoutingSource,
    WorkflowIntent,
    default_prompt,
    initial_router_state,
    routing_decision_from_state,
)

DEFAULT_ROUTER_INSTRUCTIONS = default_prompt(ROUTER)
"""The router instruction version a run uses when none is named."""

SUITE = "routing/intent-it"
"""Suite name from the catalog in the experiment process."""

DATASET_MINIMUM = 200
"""Cases the experiment process requires before this suite may support promotion."""

READ_ONLY_INTENTS = frozenset(
    {
        WorkflowIntent.QUERY,
        WorkflowIntent.ADVICE,
        WorkflowIntent.NON_FINANCE,
        WorkflowIntent.UNSUPPORTED,
    }
)
"""Labels whose input must never reach a financial write."""

_REVIEW_ID = UUID("11111111-1111-4111-8111-111111111111")
_SECOND_REVIEW_ID = UUID("22222222-2222-4222-8222-222222222222")
_MOVEMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
_RECEIVED_AT = datetime.fromisoformat("2026-09-15T12:00:00+02:00")


def _message(case: Mapping[str, JsonValue], index: int) -> ChannelMessage:
    """Build one normalized inbound record from a labelled case.

    Correlation identifiers come from the fixture rather than the dataset, so a
    dataset can express *that* a review is pending without carrying identifiers
    a reader might mistake for real ones.

    Args:
        case: Parsed dataset case.
        index: Position, used only for a unique source reference.

    Returns:
        The inbound work unit the router receives.
    """

    spec = cast(Mapping[str, JsonValue], case["message"])
    context = cast(Mapping[str, JsonValue], case["context"])
    pending = int(cast(int, context.get("pending_category_reviews", 0)))
    action = spec.get("callback_action")
    pending_action = spec.get("pending_action_id")
    reply_to = spec.get("reply_to_action_id")
    return ChannelMessage(
        source_reference=f"eval:routing:{index}",
        received_at=_RECEIVED_AT,
        text=cast(str | None, spec.get("text")),
        callback_action=(None if action is None else CallbackAction(cast(str, action))),
        pending_action_id=_correlation(cast(str | None, pending_action), pending),
        reply_to_action_id=_correlation(cast(str | None, reply_to), pending),
        has_image=bool(spec.get("has_image", False)),
    )


def _correlation(name: str | None, pending: int) -> UUID | None:
    """Resolve a fixture correlation name to its stable identifier."""

    if name is None:
        return None
    if name == "movement":
        return _MOVEMENT_ID
    return _REVIEW_ID if pending else uuid4()


def _context(case: Mapping[str, JsonValue]) -> RoutingContext:
    """Build the trusted routing context one labelled case describes."""

    spec = cast(Mapping[str, JsonValue], case["context"])
    pending = int(cast(int, spec.get("pending_category_reviews", 0)))
    reviews = (_REVIEW_ID, _SECOND_REVIEW_ID)[:pending]
    return RoutingContext(
        pending_category_review_ids=reviews,
        latest_correctable_movement_id=(
            _MOVEMENT_ID if spec.get("has_correctable_movement") else None
        ),
    )


def _macro_f1(
    results: Sequence[tuple[str, str | None]],
) -> tuple[float, int, int]:
    """Compute macro-F1 over the labels the dataset actually contains.

    Averaging over labels rather than cases keeps a rare but safety-relevant
    intent from being hidden by a common one, which is what the evaluation
    matrix asks for when it forbids a high aggregate from concealing a
    query-to-write failure.

    Args:
        results: Expected and produced label pairs.

    Returns:
        The macro-F1, the number of exactly matched cases, and the total.
    """

    labels = sorted({expected for expected, _ in results})
    scores: list[float] = []
    for label in labels:
        true_positive = sum(1 for e, a in results if e == label and a == label)
        false_positive = sum(1 for e, a in results if e != label and a == label)
        false_negative = sum(1 for e, a in results if e == label and a != label)
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        scores.append(
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
    matched = sum(1 for e, a in results if e == a)
    return (
        round(sum(scores) / len(scores), 4) if scores else 1.0,
        matched,
        len(results),
    )


def _metrics(
    cases: Sequence[Mapping[str, JsonValue]],
    results: Sequence[CaseResult],
) -> list[MetricScore]:
    """Score every gate the evaluation matrix defines for intent routing.

    Args:
        cases: Parsed dataset cases, in the same order as the results.
        results: Executed case outcomes.

    Returns:
        Blocking and diagnostic metrics with their thresholds.
    """

    pairs = [(result.expected, result.actual) for result in results]
    macro_f1, matched, total = _macro_f1(pairs)

    write_labels = {intent.value for intent in MUTATING_INTENTS}
    read_labels = {intent.value for intent in READ_ONLY_INTENTS}

    read_only_inputs = [
        (case, result)
        for case, result in zip(cases, results, strict=True)
        if result.expected in read_labels
    ]
    escalated = sum(
        1 for _, result in read_only_inputs if result.actual in write_labels
    )
    write_inputs = [
        (case, result)
        for case, result in zip(cases, results, strict=True)
        if result.expected
        in {WorkflowIntent.EXPENSE.value, WorkflowIntent.REFUND.value}
    ]
    demoted = sum(1 for _, result in write_inputs if result.actual in read_labels)
    deterministic = [
        (case, result)
        for case, result in zip(cases, results, strict=True)
        if case.get("expected_source") == RoutingSource.DETERMINISTIC.value
    ]
    deterministic_exact = sum(
        1 for _, result in deterministic if result.status is CaseStatus.PASSED
    )
    ambiguous = [
        (case, result)
        for case, result in zip(cases, results, strict=True)
        if result.expected
        in {WorkflowIntent.CLARIFICATION.value, WorkflowIntent.UNSUPPORTED.value}
    ]
    clarified = sum(
        1
        for _, result in ambiguous
        if result.actual
        in {WorkflowIntent.CLARIFICATION.value, WorkflowIntent.UNSUPPORTED.value}
    )
    return [
        MetricScore(
            name="macro_f1",
            value=macro_f1,
            threshold=0.95,
            blocking=True,
            numerator=matched,
            denominator=total,
        ),
        MetricScore(
            name="read_only_input_routed_to_write",
            value=float(escalated),
            threshold=0.0,
            comparison="at_most",
            blocking=True,
            numerator=escalated,
            denominator=len(read_only_inputs),
        ),
        MetricScore(
            name="write_input_routed_as_read_only",
            value=rate(demoted, len(write_inputs)),
            threshold=0.01,
            comparison="at_most",
            blocking=True,
            numerator=demoted,
            denominator=len(write_inputs),
        ),
        MetricScore(
            name="deterministic_pre_routing_exact",
            value=rate(deterministic_exact, len(deterministic)),
            threshold=1.0,
            blocking=True,
            numerator=deterministic_exact,
            denominator=len(deterministic),
        ),
        MetricScore(
            name="ambiguous_safely_clarified",
            value=rate(clarified, len(ambiguous)),
            threshold=0.98,
            blocking=True,
            numerator=clarified,
            denominator=len(ambiguous),
        ),
        MetricScore(
            name="exact_label_agreement",
            value=rate(matched, total),
            threshold=None,
            blocking=False,
            numerator=matched,
            denominator=total,
        ),
    ]


def run_routing_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    minimum_confidence: float,
    repository_root: Path,
    instructions: str = DEFAULT_ROUTER_INSTRUCTIONS.text,
    instructions_version: str = DEFAULT_ROUTER_INSTRUCTIONS.id,
) -> SuiteReport:
    """Execute the routing suite and always emit a deterministic report.

    Args:
        settings: Local endpoint configuration.
        dataset_path: Committed labelled dataset.
        output_root: Ignored artifact root for raw run output.
        minimum_confidence: Confidence bar under test, recorded with the score.
        repository_root: Repository directory, for source metadata.
        instructions: Router instruction text under test. Unpromoted by
            default, and recorded so a score is attributable to one text.
        instructions_version: Identifier of that text, recorded beside its hash
            so a published score names the candidate a reader can look up.

    Returns:
        The complete report, already written to its artifact directory.

    Raises:
        ValueError: If the confidence bar is outside the unit interval, which
            the router itself also refuses.
    """

    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum confidence must be between 0 and 1")
    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    graph = build_router_graph(
        model=LocalStructuredOutputModel(),
        instructions=instructions,
        minimum_confidence=minimum_confidence,
    )
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        results.append(_execute(graph, case, index))
    metrics = [*_metrics(cases, results), warm_latency_p95_metric(results)]
    passed = blocking_gates_passed(metrics, results)
    meets_minimum = dataset.item_count >= DATASET_MINIMUM
    report = SuiteReport(
        run_id=new_run_id("ROU"),
        suite=SUITE,
        started_at=started_at,
        completed_at=utc_now(),
        source=collect_source_metadata(repository_root),
        dataset=dataset,
        model=model_metadata(settings),
        environment=collect_environment_metadata(),
        configuration={
            "minimum_confidence": minimum_confidence,
            "instructions_version": instructions_version,
            "instructions_sha256": sha256_text(instructions),
            "dataset_minimum": DATASET_MINIMUM,
            "latency_measurement": latency_measurement_configuration(),
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


def _execute(graph: object, case: Mapping[str, JsonValue], index: int) -> CaseResult:
    """Route one labelled case through the compiled router."""

    case_id = str(case["case_id"])
    expected = str(case["expected_intent"])
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    started = time.monotonic()
    try:
        state = graph.invoke(  # type: ignore[attr-defined]
            initial_router_state(_message(case, index), context=_context(case))
        )
        decision = routing_decision_from_state(state)
        if decision is None:
            raise ValueError("router ended without a decision")
    except Exception as error:  # noqa: BLE001 - a suite records, never raises
        return CaseResult(
            case_id=case_id,
            status=CaseStatus.ERROR,
            expected=expected,
            tags=tags,
            latency_ms=round((time.monotonic() - started) * 1000),
            detail=type(error).__name__,
        )
    actual = decision.intent.value
    return CaseResult(
        case_id=case_id,
        status=CaseStatus.PASSED if actual == expected else CaseStatus.FAILED,
        expected=expected,
        actual=actual,
        tags=tags,
        latency_ms=round((time.monotonic() - started) * 1000),
        detail=decision.reason.value,
    )


def intent_distribution(cases: Sequence[Mapping[str, JsonValue]]) -> dict[str, int]:
    """Return how many cases each label has, for coverage inspection.

    Args:
        cases: Parsed dataset cases.

    Returns:
        Label counts in descending order of frequency.
    """

    counts = Counter(str(case["expected_intent"]) for case in cases)
    return dict(counts.most_common())


__all__ = [
    "DATASET_MINIMUM",
    "READ_ONLY_INTENTS",
    "SUITE",
    "intent_distribution",
    "run_routing_suite",
]
