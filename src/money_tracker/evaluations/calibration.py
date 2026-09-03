"""Calibrate the routing confidence bar on a held-out split.

The bar decides when a model answer is trusted enough to route on, and no value
is promoted. Increment 3 of Step 3 requires it calibrated on held-out labels
rather than on the model's own self-reported confidence, which the first routing
run showed to be coarse and nearly constant.

The design rests on one observation: the bar is applied deterministically after
the model has answered. `accept_classification` compares a confidence the model
already produced against a number the runtime supplies, so every bar can be
evaluated from a single pass. This module makes that pass once, records each
message's intent and confidence, and then sweeps the whole grid offline. Sweeping
by re-running the model would cost one full run per candidate and, because the
endpoint is not perfectly deterministic, would also confound the bar's effect
with run-to-run variation.

Leakage is prevented by construction rather than by discipline. The split is
derived from a hash of each case identifier, so it is stable across runs, does
not depend on dataset order, and cannot be nudged by rerunning. The selection
rule is stated here in code before any number is seen, and the holdout is scored
once at the chosen bar.

The rule is safety first. A bar is admissible only if it routes no read-only or
non-finance message to a financial write, because that gate has zero tolerance
and no aggregate may compensate for it. Among admissible bars the smallest is
preferred, since a higher bar buys safety by asking more questions, and the
smallest admissible bar is the least intrusive way to reach the same guarantee.
Macro-F1 breaks a tie. If no bar is admissible the module says so and selects
none, which is the expected outcome while the router lacks a concept the dataset
requires.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    collect_environment_metadata,
    collect_source_metadata,
)
from money_tracker.evaluations.routing import (
    DATASET_MINIMUM,
    READ_ONLY_INTENTS,
    _context,
    _macro_f1,
    _message,
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
    sha256_text,
    utc_now,
    write_report,
)
from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.workflows import (
    CANDIDATE_ROUTER_INSTRUCTIONS,
    MUTATING_INTENTS,
    RouteClassification,
    RoutingSource,
    StructuredOutputError,
    StructuredOutputModel,
    WorkflowIntent,
    accept_classification,
    parse_with_one_repair,
    pre_route,
)

SUITE = "routing/confidence-bar-calibration"
"""Name this calibration is recorded under; it promotes nothing by itself."""

HOLDOUT_FRACTION = 0.4
"""Share of cases reserved for the single scoring run at the selected bar."""

CANDIDATE_BARS: tuple[float, ...] = tuple(round(0.05 * step, 2) for step in range(21))
"""Every bar considered, from accepting any answer to demanding certainty."""

SAFETY_GATE = "read_only_input_routed_to_write"
"""The zero-tolerance gate that decides whether a bar is admissible at all."""


@dataclass(frozen=True, slots=True)
class Observation:
    """One message's routing evidence, recorded once and reused at every bar.

    Attributes:
        case_id: Stable dataset identifier.
        expected: Labelled intent.
        split: Whether the case calibrates or is held out.
        deterministic: Whether pre-routing decided without the model, in which
            case the bar cannot change the outcome.
        intent: Intent the model requested, absent when pre-routing decided or
            the boundary produced nothing valid.
        confidence: Confidence the model reported, on the same condition.
        fixed_intent: Outcome that holds at every bar, for a deterministic or
            unparseable case.
    """

    case_id: str
    expected: str
    split: str
    deterministic: bool
    intent: str | None
    confidence: float | None
    fixed_intent: str | None


def assign_split(case_id: str, *, holdout_fraction: float = HOLDOUT_FRACTION) -> str:
    """Assign one case to calibration or holdout, stably and without order.

    Deriving the split from the identifier's hash keeps it identical across
    runs and machines, independent of dataset order, and impossible to shift by
    rerunning until a convenient split appears.

    Args:
        case_id: Stable dataset identifier.
        holdout_fraction: Share reserved for the final scoring run.

    Returns:
        Either `calibration` or `holdout`.
    """

    digest = sha256(case_id.encode("utf-8")).digest()
    position = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return "holdout" if position < holdout_fraction else "calibration"


def _decide(observation: Observation, case: Mapping[str, JsonValue], bar: float) -> str:
    """Return the intent this case routes to at one candidate bar."""

    if observation.fixed_intent is not None:
        return observation.fixed_intent
    if observation.intent is None or observation.confidence is None:
        return WorkflowIntent.CLARIFICATION.value
    classification = RouteClassification(
        intent=WorkflowIntent(observation.intent),
        confidence=observation.confidence,
        evidence="calibration",
    )
    decision = accept_classification(
        classification,
        message=_message(case, 0),
        context=_context(case),
        minimum_confidence=bar,
    )
    return decision.intent.value


def _score(
    pairs: Sequence[tuple[str, str]],
) -> dict[str, float]:
    """Score one bar's routing outcomes with the gates the matrix defines."""

    write_labels = {intent.value for intent in MUTATING_INTENTS}
    read_labels = {intent.value for intent in READ_ONLY_INTENTS}
    macro_f1, matched, total = _macro_f1([(e, a) for e, a in pairs])
    read_only = [(e, a) for e, a in pairs if e in read_labels]
    escalated = sum(1 for _, a in read_only if a in write_labels)
    writes = [
        (e, a)
        for e, a in pairs
        if e in {WorkflowIntent.EXPENSE.value, WorkflowIntent.REFUND.value}
    ]
    demoted = sum(1 for _, a in writes if a in read_labels)
    ambiguous = [
        (e, a)
        for e, a in pairs
        if e in {WorkflowIntent.CLARIFICATION.value, WorkflowIntent.UNSUPPORTED.value}
    ]
    clarified = sum(
        1
        for _, a in ambiguous
        if a in {WorkflowIntent.CLARIFICATION.value, WorkflowIntent.UNSUPPORTED.value}
    )
    clarification_rate = sum(
        1 for _, a in pairs if a == WorkflowIntent.CLARIFICATION.value
    )
    return {
        "macro_f1": macro_f1,
        SAFETY_GATE: float(escalated),
        "write_input_routed_as_read_only": rate(demoted, len(writes)),
        "ambiguous_safely_clarified": rate(clarified, len(ambiguous)),
        "exact_label_agreement": rate(matched, total),
        "clarification_rate": rate(clarification_rate, len(pairs)),
    }


def select_bar(sweep: Mapping[float, Mapping[str, float]]) -> float | None:
    """Choose the bar by the rule stated before any number was seen.

    Safety first: a bar is admissible only when it escalates no read-only or
    non-finance message to a financial write. Among admissible bars the smallest
    is preferred, because a higher bar buys the same guarantee by asking the
    owner more questions, and candidate bars are distinct so that rule is
    already total. An earlier version claimed macro-F1 broke a tie, which was
    unreachable: two admissible bars can never be equally small.

    Admissibility is not usefulness, and this rule deliberately does not judge
    the latter. A bar that clarifies nearly every message escalates nothing and
    is therefore admissible while being useless, so the selected bar must always
    be read next to the clarification rate reported beside it.

    Args:
        sweep: Scores for every candidate bar.

    Returns:
        The selected bar, or None when no bar is admissible.
    """

    admissible = [bar for bar, scores in sweep.items() if scores[SAFETY_GATE] == 0.0]
    if not admissible:
        return None
    return min(admissible)


def run_calibration(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    repository_root: Path,
    model: StructuredOutputModel | None = None,
    instructions: str = CANDIDATE_ROUTER_INSTRUCTIONS,
) -> SuiteReport:
    """Sweep every candidate bar from one model pass and score the holdout once.

    Args:
        settings: Local endpoint configuration.
        dataset_path: Committed labelled routing dataset.
        output_root: Ignored artifact root for raw run output.
        repository_root: Repository directory, for source metadata.
        model: Structured-output port. The local adapter is used when omitted.
        instructions: Router instruction text under test.

    Returns:
        The complete report, already written to its artifact directory.
    """

    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    port = model or LocalStructuredOutputModel()

    observations: list[Observation] = []
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        observation, result = _observe(port, instructions, case, index)
        observations.append(observation)
        results.append(result)

    by_split: dict[str, list[tuple[Observation, Mapping[str, JsonValue]]]] = {
        "calibration": [],
        "holdout": [],
    }
    for observation, case in zip(observations, cases, strict=True):
        by_split[observation.split].append((observation, case))

    sweep = {
        bar: _score(
            [
                (observation.expected, _decide(observation, case, bar))
                for observation, case in by_split["calibration"]
            ]
        )
        for bar in CANDIDATE_BARS
    }
    selected = select_bar(sweep)
    holdout = (
        None
        if selected is None
        else _score(
            [
                (observation.expected, _decide(observation, case, selected))
                for observation, case in by_split["holdout"]
            ]
        )
    )

    metrics = _metrics(sweep, holdout, selected, observations)
    report = SuiteReport(
        run_id=new_run_id("CAL"),
        suite=SUITE,
        started_at=started_at,
        completed_at=utc_now(),
        source=collect_source_metadata(repository_root),
        dataset=dataset,
        model=model_metadata(settings),
        environment=collect_environment_metadata(),
        configuration={
            "instructions_sha256": sha256_text(instructions),
            "holdout_fraction": HOLDOUT_FRACTION,
            "calibration_cases": len(by_split["calibration"]),
            "holdout_cases": len(by_split["holdout"]),
            "candidate_bars": list(CANDIDATE_BARS),
            "selection_rule": (
                "smallest bar escalating no read-only input to a write; "
                "admissibility is not usefulness, so read the selected bar "
                "next to its clarification rate"
            ),
            "selected_bar": selected,
            "sweep": {
                f"{bar:.2f}": cast(JsonValue, dict(scores))
                for bar, scores in sweep.items()
            },
            "distinct_confidences": cast(
                list[JsonValue],
                sorted(
                    {
                        observation.confidence
                        for observation in observations
                        if observation.confidence is not None
                    }
                ),
            ),
        },
        metrics=metrics,
        cases=results,
        blocking_gates_passed=blocking_gates_passed(metrics),
        meets_dataset_minimum=dataset.item_count >= DATASET_MINIMUM,
        # A calibration never promotes anything by itself. It selects a value
        # that a later experiment may promote through the decision log, and
        # `routing/intent-it` remains the suite that gates routing, so this
        # stays false even when every gate here passes.
        eligible_for_promotion=False,
        local_artifact_path="",
    )
    directory = write_report(report, output_root=output_root)
    report = report.model_copy(update={"local_artifact_path": str(directory)})
    write_report(report, output_root=output_root)
    return report


def _metrics(
    sweep: Mapping[float, Mapping[str, float]],
    holdout: Mapping[str, float] | None,
    selected: float | None,
    observations: Sequence[Observation],
) -> list[MetricScore]:
    """Report what the sweep found, and the holdout only when a bar was chosen.

    Every metric here is diagnostic. A calibration selects a value; it does not
    pass a gate, and the suite that gates routing is `routing/intent-it`. The
    one exception is admissibility, which is blocking precisely so that a run
    finding no usable bar cannot be read as a successful calibration.
    """

    admissible = [bar for bar, scores in sweep.items() if scores[SAFETY_GATE] == 0.0]
    spread = {
        observation.confidence
        for observation in observations
        if observation.confidence is not None
    }
    metrics = [
        MetricScore(
            name="an_admissible_bar_exists",
            value=float(len(admissible)),
            threshold=1.0,
            blocking=True,
            numerator=len(admissible),
            denominator=len(sweep),
        ),
        MetricScore(
            name="distinct_self_reported_confidences",
            value=float(len(spread)),
            threshold=None,
            blocking=False,
            numerator=len(spread),
            denominator=len(observations),
        ),
    ]
    if holdout is not None and selected is not None:
        metrics.extend(
            MetricScore(
                name=f"holdout_{name}",
                value=value,
                threshold=None,
                blocking=False,
                numerator=0,
                denominator=0,
            )
            for name, value in sorted(holdout.items())
        )
    return metrics


def _observe(
    model: StructuredOutputModel,
    instructions: str,
    case: Mapping[str, JsonValue],
    index: int,
) -> tuple[Observation, CaseResult]:
    """Record one message's routing evidence with a single model call."""

    case_id = str(case["case_id"])
    expected = str(case["expected_intent"])
    split = assign_split(case_id)
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    message = _message(case, index)
    started = time.monotonic()

    decided = pre_route(message)
    if decided is not None:
        observation = Observation(
            case_id=case_id,
            expected=expected,
            split=split,
            deterministic=True,
            intent=None,
            confidence=None,
            fixed_intent=decided.intent.value,
        )
        return observation, _record(observation, tags, started, "deterministic")

    try:
        classification = parse_with_one_repair(
            model,
            schema=RouteClassification,
            instructions=instructions,
            message=message.normalized_text,
        )
    except StructuredOutputError:
        observation = Observation(
            case_id=case_id,
            expected=expected,
            split=split,
            deterministic=False,
            intent=None,
            confidence=None,
            fixed_intent=WorkflowIntent.CLARIFICATION.value,
        )
        return observation, _record(observation, tags, started, "invalid_model_output")

    observation = Observation(
        case_id=case_id,
        expected=expected,
        split=split,
        deterministic=False,
        intent=classification.intent.value,
        confidence=classification.confidence,
        fixed_intent=None,
    )
    return observation, _record(observation, tags, started, None)


def _record(
    observation: Observation,
    tags: Sequence[str],
    started: float,
    detail: str | None,
) -> CaseResult:
    """Render one observation as a public-safe case record.

    The recorded outcome is the model's requested intent before any bar is
    applied, because the bar is what this run is choosing. A case's status is
    therefore not a pass or a failure but evidence, and is reported as such.
    """

    produced = observation.intent or observation.fixed_intent
    return CaseResult(
        case_id=observation.case_id,
        status=(
            CaseStatus.PASSED if produced == observation.expected else CaseStatus.FAILED
        ),
        expected=observation.expected,
        actual=produced,
        tags=[*tags, observation.split],
        latency_ms=round((time.monotonic() - started) * 1000),
        detail=detail,
    )


def deterministic_source() -> str:
    """Return the routing source label pre-routed cases carry."""

    return RoutingSource.DETERMINISTIC.value


__all__ = [
    "CANDIDATE_BARS",
    "HOLDOUT_FRACTION",
    "SAFETY_GATE",
    "SUITE",
    "Observation",
    "assign_split",
    "run_calibration",
    "select_bar",
]
