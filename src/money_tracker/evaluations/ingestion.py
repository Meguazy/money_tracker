"""Executable `ingestion/expense-it` suite over the real extraction boundary.

The suite scores the pair the expense workflow actually runs: the versioned
`ExpenseExtraction` boundary, then the deterministic resolution of occurrence
time, signed amount, and the funding dimension against a fixture profile. A
value the resolver refuses is a failure here, not a near miss, because that is
exactly how the workflow treats it.

Facets are resolved independently rather than through `prepare_expense_draft`.
Production stops at the first refusal, which would hide every later facet
behind an earlier failure and make one weak step look like several. Scoring
each facet on its own says which step is actually weak, while the reported
outcome still follows production's order so a run stays readable as the owner
would experience it.

Two entries in the matrix are satisfied by construction rather than by
measurement, and saying so is part of the report. Currency is not a field of
the boundary at all, so `amount and currency exact match` can only be about the
magnitude and sign. No auto-post policy is promoted, so no fabricated field can
reach an auto-posted movement.

Two diagnostics exist because the aggregate gates do not separate a fabrication
the resolver refuses from one it accepts. A funding word invented out of a
purpose noun is caught deterministically; one that happens to name a configured
source is not, and silently charges a resource the owner never mentioned. The
same applies to an amount invented where the message states none, which the
fabrication gate does not cover at all because the matrix scopes that gate to
merchant, date, and payment fields.

The merchant gate carries a third label beyond named and absent. `Al bar` and
`al supermercato` name a kind of place rather than a business, and the
boundary's instructions draw that line without defining it. Penalising either
answer would measure the label rather than the model, so those cases are
recorded as unscored and stay visible in the dataset instead of being resolved
by a scoring rule.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from money_tracker.domain import (
    BalanceMode,
    DomainError,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
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
    sha256_text,
    utc_now,
    write_report,
)
from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.workflows import (
    CANDIDATE_EXTRACTION_INSTRUCTIONS,
    PRODUCT_TIMEZONE,
    ExpenseExtraction,
    StructuredOutputError,
    StructuredOutputModel,
    UnresolvedExtractionError,
    parse_with_one_repair,
    resolve_amount,
    resolve_occurrence_time,
    resolve_payment_allocations,
)

SUITE = "ingestion/expense-it"
"""Suite name from the catalog in the experiment process."""

DATASET_MINIMUM = 150
"""Cases the experiment process requires before this suite may support promotion."""

RESOLVED = "resolved"
"""Outcome label for a case every deterministic step accepted."""

INVALID_OUTPUT = "invalid_model_output"
"""Outcome label for a case the boundary could not parse within its budget."""

_PROFILE_ID = UUID("44444444-4444-4444-8444-444444444444")
_RECEIVED_AT = datetime.fromisoformat("2026-09-15T12:00:00+02:00")

_FIXTURE_SOURCES: tuple[tuple[str, FundingClass, FundingSourceStatus, bool], ...] = (
    ("Carta", FundingClass.PERSONAL_FUNDS, FundingSourceStatus.ACTIVE, True),
    ("Contanti", FundingClass.PERSONAL_FUNDS, FundingSourceStatus.ACTIVE, False),
    ("Buoni pasto", FundingClass.BENEFIT, FundingSourceStatus.ACTIVE, False),
    ("Carta aziendale", FundingClass.OTHER, FundingSourceStatus.ACTIVE, False),
    ("Vecchia carta", FundingClass.PERSONAL_FUNDS, FundingSourceStatus.ARCHIVED, False),
)
"""Configured sources every case resolves against.

The archived entry is deliberate. Matching is exact on the folded configured
name, so an archived source is the only way a name the owner really did
configure can still be refused, and the dataset must be able to state that
case.
"""


def fixture_funding_sources() -> tuple[FundingSource, ...]:
    """Build the profile's configured sources with stable identifiers.

    Returns:
        Active and archived sources in the order the dataset names them.
    """

    return tuple(
        FundingSource(
            id=UUID(int=index + 1),
            profile_id=_PROFILE_ID,
            name=name,
            funding_class=funding_class,
            balance_mode=BalanceMode.UNTRACKED,
            status=status,
            is_default=is_default,
        )
        for index, (name, funding_class, status, is_default) in enumerate(
            _FIXTURE_SOURCES
        )
    )


@dataclass(frozen=True, slots=True)
class _Judgment:
    """What one executed case contributes to every facet metric.

    Attributes:
        outcome: Production's outcome, being the first refusal or `resolved`.
        polarity: Direction the boundary reported.
        amount_minor_units: Signed total, or None when it did not resolve.
        occurred_on: ISO Rome date, or None when it did not resolve.
        funding: Resolved portions as name, class, and signed minor units.
        merchant: Merchant the boundary reported, if any.
        stated_time: Whether the boundary copied any temporal words.
        stated_funding: Whether the boundary reported any funding portion.
    """

    outcome: str
    polarity: str | None = None
    amount_minor_units: int | None = None
    occurred_on: str | None = None
    funding: tuple[tuple[str, str, int], ...] | None = None
    merchant: str | None = None
    stated_time: bool = False
    stated_funding: bool = False


def _resolve(
    extraction: ExpenseExtraction,
    *,
    received_at: datetime,
    funding_sources: tuple[FundingSource, ...],
) -> _Judgment:
    """Resolve every facet independently and report production's outcome.

    Args:
        extraction: Schema-valid boundary output.
        received_at: Aware timestamp the channel assigned to the message.
        funding_sources: The profile's configured sources.

    Returns:
        One judgment carrying each facet and the outcome production would show.
    """

    by_id = {source.id: source for source in funding_sources}
    reasons: list[str] = []

    occurred_on: str | None = None
    try:
        occurred_on = (
            resolve_occurrence_time(
                extraction.occurred_at_text, received_at=received_at
            )
            .astimezone(PRODUCT_TIMEZONE)
            .date()
            .isoformat()
        )
    except UnresolvedExtractionError as error:
        reasons.append(error.reason.value)

    amount: int | None = None
    try:
        amount = resolve_amount(extraction).minor_units
    except UnresolvedExtractionError as error:
        reasons.append(error.reason.value)

    funding: tuple[tuple[str, str, int], ...] | None = None
    try:
        funding = tuple(
            (
                by_id[allocation.funding_source_id].name,
                by_id[allocation.funding_source_id].funding_class.value,
                allocation.amount.minor_units,
            )
            for allocation in resolve_payment_allocations(
                extraction, funding_sources=funding_sources
            )
        )
    except (UnresolvedExtractionError, DomainError) as error:
        reason = getattr(error, "reason", None)
        reasons.append(reason.value if reason is not None else type(error).__name__)

    return _Judgment(
        outcome=reasons[0] if reasons else RESOLVED,
        polarity=extraction.polarity.value,
        amount_minor_units=amount,
        occurred_on=occurred_on,
        funding=funding,
        merchant=extraction.merchant,
        stated_time=extraction.occurred_at_text is not None,
        stated_funding=bool(extraction.payment_parts),
    )


def _labelled_funding(
    case: Mapping[str, JsonValue],
) -> tuple[tuple[str, str, int], ...] | None:
    """Read the funding portions one case expects, if it expects any."""

    raw = case.get("expected_funding")
    if raw is None:
        return None
    return tuple(
        (str(part[0]), str(part[1]), int(cast(int, part[2])))
        for part in cast(list[list[JsonValue]], raw)
    )


def _fabricated(case: Mapping[str, JsonValue], judgment: _Judgment) -> bool:
    """Return whether the boundary supplied a field the message does not state.

    Fabrication is counted once per case however many fields it touched. The
    gate asks how often a message produces an invented value at all, and a
    single message inventing two fields is one such message.
    """

    if not case.get("states_time", False) and judgment.stated_time:
        return True
    if not case.get("states_funding", False) and judgment.stated_funding:
        return True
    return (
        case.get("merchant_expectation") == "absent" and judgment.merchant is not None
    )


def _facets(case: Mapping[str, JsonValue], judgment: _Judgment) -> dict[str, bool]:
    """Score every facet one case exercises, keyed by the gate it feeds.

    A facet the case does not exercise is absent rather than false, so a metric
    can read its own denominator from this mapping instead of recomputing which
    cases it applies to. Every aggregate and the per-case `detail` are derived
    from this one comparison, which is what keeps them from diverging.

    Args:
        case: Parsed dataset case.
        judgment: What the boundary and the resolvers produced for it.

    Returns:
        Facet name to whether the produced value matched its label.
    """

    facets: dict[str, bool] = {
        "polarity": judgment.polarity == case.get("expected_polarity"),
        "fabrication": not _fabricated(case, judgment),
    }
    if case.get("expected_amount_minor_units") is not None:
        facets["amount"] = (
            judgment.amount_minor_units == case["expected_amount_minor_units"]
        )
    if case.get("expected_occurred_on") is not None:
        facets["occurrence"] = judgment.occurred_on == case["expected_occurred_on"]
    expected_funding = _labelled_funding(case)
    if expected_funding is not None:
        produced = judgment.funding
        facets["funding"] = produced is not None and {
            (name, kind) for name, kind, _ in produced
        } == {(name, kind) for name, kind, _ in expected_funding}
        if len(expected_funding) > 1:
            facets["split"] = produced is not None and sorted(produced) == sorted(
                expected_funding
            )
    if case.get("merchant_expectation") == "named":
        facets["merchant"] = (
            judgment.merchant is not None
            and judgment.merchant.casefold()
            == str(case["expected_merchant"]).casefold()
        )
    return facets


def _facet_metric(
    facet: str,
    scored: Sequence[Mapping[str, bool]],
    *,
    name: str,
    threshold: float | None,
    blocking: bool,
    comparison: str = "at_least",
) -> MetricScore:
    """Score one facet from the per-case comparisons that produced it.

    Args:
        facet: Key `_facets` scores this gate under.
        scored: Facet comparisons for every executed case.
        name: Metric identifier from the evaluation matrix.
        threshold: Value the gate requires, or None for a diagnostic.
        blocking: Whether a failure blocks promotion.
        comparison: Whether the reported value counts matches or misses.

    Returns:
        The scored metric with its gate.
    """

    denominator = sum(1 for facets in scored if facet in facets)
    numerator = sum(1 for facets in scored if facets.get(facet) is True)
    if comparison == "at_most":
        numerator = denominator - numerator
    return MetricScore(
        name=name,
        value=rate(numerator, denominator),
        threshold=threshold,
        comparison="at_most" if comparison == "at_most" else "at_least",
        blocking=blocking,
        numerator=numerator,
        denominator=denominator,
    )


def _metrics(
    cases: Sequence[Mapping[str, JsonValue]],
    results: Sequence[CaseResult],
    judgments: Mapping[str, _Judgment],
) -> list[MetricScore]:
    """Score every gate the evaluation matrix defines for text extraction."""

    paired = list(zip(cases, results, strict=True))
    executed = [
        _facets(case, judgments[result.case_id])
        for case, result in paired
        if result.status is not CaseStatus.ERROR
    ]

    ambiguous = [
        (case, result)
        for case, result in paired
        if case.get("expected_outcome") != RESOLVED
    ]
    ambiguity_reported = sum(
        1 for _, result in ambiguous if result.status is CaseStatus.PASSED
    )
    outcome_exact = sum(1 for _, result in paired if result.status is CaseStatus.PASSED)

    invented_funding = [
        judgments[result.case_id]
        for case, result in paired
        if result.status is not CaseStatus.ERROR
        and not case.get("states_funding", False)
        and judgments[result.case_id].stated_funding
    ]
    silently_resolved = sum(1 for j in invented_funding if j.funding is not None)
    unstated_amount = [
        judgments[result.case_id]
        for case, result in paired
        if result.status is not CaseStatus.ERROR
        and case.get("expected_amount_minor_units") is None
    ]
    invented_amounts = sum(
        1 for j in unstated_amount if j.amount_minor_units is not None
    )

    schema_validity = MetricScore(
        name="schema_validity_after_one_repair",
        value=rate(len(executed), len(paired)),
        threshold=1.0,
        blocking=True,
        numerator=len(executed),
        denominator=len(paired),
    )
    ambiguity = MetricScore(
        name="required_ambiguity_reported",
        value=rate(ambiguity_reported, len(ambiguous)),
        threshold=0.98,
        blocking=True,
        numerator=ambiguity_reported,
        denominator=len(ambiguous),
    )
    agreement = MetricScore(
        name="outcome_agreement",
        value=rate(outcome_exact, len(paired)),
        threshold=None,
        blocking=False,
        numerator=outcome_exact,
        denominator=len(paired),
    )

    return [
        schema_validity,
        _facet_metric(
            "amount", executed, name="amount_exact_match", threshold=0.99, blocking=True
        ),
        _facet_metric(
            "occurrence",
            executed,
            name="occurrence_semantic_exactness",
            threshold=0.97,
            blocking=True,
        ),
        _facet_metric(
            "funding",
            executed,
            name="funding_source_and_class_match",
            threshold=0.97,
            blocking=True,
        ),
        _facet_metric(
            "split",
            executed,
            name="split_parts_exact_and_reconciled",
            threshold=0.99,
            blocking=True,
        ),
        _facet_metric(
            "polarity",
            executed,
            name="expense_refund_polarity",
            threshold=0.99,
            blocking=True,
        ),
        ambiguity,
        _facet_metric(
            "fabrication",
            executed,
            name="fabricated_merchant_date_or_payment_field",
            threshold=0.01,
            blocking=True,
            comparison="at_most",
        ),
        _facet_metric(
            "merchant",
            executed,
            name="merchant_semantic_match",
            threshold=None,
            blocking=False,
        ),
        MetricScore(
            name="fabricated_funding_silently_resolved",
            value=rate(silently_resolved, len(invented_funding)),
            threshold=None,
            blocking=False,
            numerator=silently_resolved,
            denominator=len(invented_funding),
        ),
        MetricScore(
            name="amount_invented_where_none_stated",
            value=rate(invented_amounts, len(unstated_amount)),
            threshold=None,
            blocking=False,
            numerator=invented_amounts,
            denominator=len(unstated_amount),
        ),
        agreement,
    ]


def run_ingestion_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    repository_root: Path,
    model: StructuredOutputModel | None = None,
    instructions: str = CANDIDATE_EXTRACTION_INSTRUCTIONS,
) -> SuiteReport:
    """Execute the ingestion suite and always emit a deterministic report.

    Args:
        settings: Local endpoint configuration.
        dataset_path: Committed labelled dataset.
        output_root: Ignored artifact root for raw run output.
        repository_root: Repository directory, for source metadata.
        model: Structured-output port. The local adapter is used when omitted.
        instructions: Extraction instruction text under test. Unpromoted by
            default, and recorded so a score is attributable to one text.

    Returns:
        The complete report, already written to its artifact directory.
    """

    started_at = utc_now()
    cases, content_hash = load_cases(dataset_path)
    dataset = dataset_metadata(
        suite=SUITE, path=dataset_path, cases=cases, content_hash=content_hash
    )
    sources = fixture_funding_sources()
    port = model or LocalStructuredOutputModel()

    results: list[CaseResult] = []
    judgments: dict[str, _Judgment] = {}
    for case in cases:
        result, judgment = _execute(port, instructions, case, sources)
        results.append(result)
        judgments[result.case_id] = judgment

    metrics = _metrics(cases, results, judgments)
    passed = blocking_gates_passed(metrics)
    meets_minimum = dataset.item_count >= DATASET_MINIMUM
    report = SuiteReport(
        run_id=new_run_id("ING"),
        suite=SUITE,
        started_at=started_at,
        completed_at=utc_now(),
        source=collect_source_metadata(repository_root),
        dataset=dataset,
        model=model_metadata(settings),
        environment=collect_environment_metadata(),
        configuration={
            "instructions_sha256": sha256_text(instructions),
            "fixture_funding_sources": [name for name, _, _, _ in _FIXTURE_SOURCES],
            "default_received_at": _RECEIVED_AT.isoformat(),
            "dataset_minimum": DATASET_MINIMUM,
            "boundary_exposes_currency": "currency" in ExpenseExtraction.model_fields,
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
    funding_sources: tuple[FundingSource, ...],
) -> tuple[CaseResult, _Judgment]:
    """Extract one labelled message and resolve every facet of the result."""

    case_id = str(case["case_id"])
    expected = str(case["expected_outcome"])
    tags = [str(tag) for tag in cast(list[JsonValue], case.get("tags", []))]
    received_at = (
        _RECEIVED_AT
        if case.get("received_at") is None
        else datetime.fromisoformat(str(case["received_at"]))
    )
    started = time.monotonic()
    try:
        extraction = parse_with_one_repair(
            model,
            schema=ExpenseExtraction,
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
                detail=INVALID_OUTPUT,
            ),
            _Judgment(outcome=INVALID_OUTPUT),
        )
    judgment = _resolve(
        extraction, received_at=received_at, funding_sources=funding_sources
    )
    missed = sorted(name for name, ok in _facets(case, judgment).items() if not ok)
    return (
        CaseResult(
            case_id=case_id,
            status=(
                CaseStatus.PASSED if judgment.outcome == expected else CaseStatus.FAILED
            ),
            expected=expected,
            actual=judgment.outcome,
            tags=tags,
            latency_ms=round((time.monotonic() - started) * 1000),
            detail=",".join(missed) or None,
        ),
        judgment,
    )


__all__ = [
    "DATASET_MINIMUM",
    "INVALID_OUTPUT",
    "RESOLVED",
    "SUITE",
    "fixture_funding_sources",
    "run_ingestion_suite",
]
