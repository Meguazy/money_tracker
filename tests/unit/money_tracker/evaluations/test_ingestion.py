"""Tests for the ingestion suite's scoring and its committed dataset."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from money_tracker.domain import FundingSourceStatus
from money_tracker.evaluations.ingestion import (
    DATASET_MINIMUM,
    RESOLVED,
    SUITE,
    fixture_funding_sources,
    run_ingestion_suite,
)
from money_tracker.evaluations.metadata import CapabilitySettings
from money_tracker.evaluations.suites import dataset_metadata, load_cases
from money_tracker.workflows import (
    PRODUCT_TIMEZONE,
    ExpenseExtraction,
    ResolutionReason,
    UnresolvedExtractionError,
    resolve_occurrence_time,
    resolve_payment_allocations,
)
from money_tracker.workflows.wire import WireExpenseExtraction

DATASET = Path("evals/datasets/ingestion-expense-it-v1.jsonl")
DEFAULT_RECEIVED_AT = datetime.fromisoformat("2026-09-15T12:00:00+02:00")
MERCHANT_EXPECTATIONS = {"named", "absent", "unscored"}


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


def _extraction(**overrides: object) -> str:
    """Serialize one boundary completion from flat, readable overrides.

    The overrides stay in the domain contract's vocabulary while the payload is
    written in the union shape the default candidate actually asks for, so a
    test says what the boundary produced rather than how the boundary is
    currently shaped.
    """

    flat: dict[str, object] = {
        "polarity": "expense",
        "amount_minor_units": 1200,
        "occurred_at_text": None,
        "merchant": None,
        "payment_parts": [],
    }
    flat.update(overrides)
    amount = flat["amount_minor_units"]
    occurred = flat["occurred_at_text"]
    merchant = flat["merchant"]
    parts = flat["payment_parts"]
    assert isinstance(parts, list)
    if not parts:
        funding: dict[str, object] = {"kind": "funding_not_stated"}
    elif len(parts) == 1:
        funding = {
            "kind": "funding_one_method",
            "funding_source_text": parts[0]["funding_source_text"],
        }
    else:
        funding = {
            "kind": "funding_split",
            "parts": [
                {
                    "funding_source_text": part["funding_source_text"],
                    "minor_units": part["amount_minor_units"],
                }
                for part in parts
            ],
        }
    payload: dict[str, object] = {
        "polarity": flat["polarity"],
        "amount": (
            {"kind": "amount_not_stated"}
            if amount is None
            else {"kind": "amount_stated", "minor_units": amount}
        ),
        "occurrence": (
            {"kind": "time_not_stated"}
            if occurred is None
            else {"kind": "time_stated", "text": occurred}
        ),
        "description": "spesa",
        "merchant": (
            {"kind": "merchant_not_named"}
            if merchant is None
            else {"kind": "merchant_named", "name": merchant}
        ),
        "funding": funding,
        "evidence": "12 euro",
    }
    return json.dumps(payload)


def _run(tmp_path: Path, *completions: str) -> dict[str, Any]:
    """Run the suite against a scripted model and index its metrics."""

    report = run_ingestion_suite(
        _settings(),
        dataset_path=DATASET,
        output_root=tmp_path,
        repository_root=Path.cwd(),
        model=ScriptedModel(list(completions)),
    )
    return {"report": report, "metrics": {m.name: m for m in report.metrics}}


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


def test_no_labelled_day_falls_after_the_message_that_reports_it(
    cases: list[dict[str, Any]],
) -> None:
    """Refuse a date label the deterministic resolver would itself reject.

    A movement has already happened, so the resolver refuses any day after the
    message. A label past that day would measure the dataset rather than the
    model.
    """

    for case in cases:
        received_at = (
            DEFAULT_RECEIVED_AT
            if case.get("received_at") is None
            else datetime.fromisoformat(case["received_at"])
        )
        expected = case["expected_occurred_on"]
        if expected is None:
            continue
        resolved = resolve_occurrence_time(None, received_at=received_at)
        assert expected <= resolved.astimezone(PRODUCT_TIMEZONE).date().isoformat(), (
            f"{case['case_id']} labels a day after the message"
        )


def test_a_labelled_outcome_agrees_with_the_facets_it_leaves_unlabelled(
    cases: list[dict[str, Any]],
) -> None:
    """Keep every refusal label consistent with what production could report."""

    for case in cases:
        outcome = case["expected_outcome"]
        identifier = case["case_id"]
        assert outcome in {RESOLVED} | {r.value for r in ResolutionReason}, identifier
        if outcome == RESOLVED:
            assert case["expected_amount_minor_units"] is not None, identifier
            assert case["expected_occurred_on"] is not None, identifier
            assert case["expected_funding"] is not None, identifier
        if outcome == ResolutionReason.AMOUNT_UNKNOWN.value:
            # An unresolved total makes the funding dimension unresolvable too,
            # because every allocation is a share of that total.
            assert case["expected_amount_minor_units"] is None, identifier
            assert case["expected_funding"] is None, identifier


def test_every_labelled_split_reconciles_against_its_total(
    cases: list[dict[str, Any]],
) -> None:
    """Never label a split the domain itself would refuse to assemble."""

    splits = [c for c in cases if c["expected_funding"]]

    assert sum(1 for c in splits if len(c["expected_funding"]) > 1) >= 10
    for case in splits:
        total = sum(part[2] for part in case["expected_funding"])
        assert total == case["expected_amount_minor_units"], case["case_id"]


def test_every_labelled_source_is_an_active_fixture_source(
    cases: list[dict[str, Any]],
) -> None:
    """Refuse a funding label resolution could never produce."""

    active = {
        source.name: source.funding_class.value
        for source in fixture_funding_sources()
        if source.status is FundingSourceStatus.ACTIVE
    }

    for case in cases:
        for name, funding_class, _ in case["expected_funding"] or ():
            assert active.get(name) == funding_class, case["case_id"]


def test_an_unstated_funding_label_resolves_to_the_configured_default(
    cases: list[dict[str, Any]],
) -> None:
    """Hold the dataset to the rule the resolver applies for silent messages."""

    default = next(s for s in fixture_funding_sources() if s.is_default)

    for case in cases:
        if case["states_funding"] or not case["expected_funding"]:
            continue
        assert [part[0] for part in case["expected_funding"]] == [default.name], case[
            "case_id"
        ]


def test_the_merchant_expectation_is_one_of_three_declared_values(
    cases: list[dict[str, Any]],
) -> None:
    """Keep the unscored class explicit rather than implied by a missing name.

    A generic place such as `al bar` is neither a named merchant nor a case
    where producing one is fabrication, and the fairness of the zero-tolerance
    fabrication gate depends on that third value existing in the data.
    """

    for case in cases:
        expectation = case["merchant_expectation"]
        assert expectation in MERCHANT_EXPECTATIONS, case["case_id"]
        assert (case["expected_merchant"] is not None) == (expectation == "named"), (
            case["case_id"]
        )
    assert sum(1 for c in cases if c["merchant_expectation"] == "unscored") >= 10


def test_the_dataset_exercises_every_refusal_it_claims_to_measure(
    cases: list[dict[str, Any]],
) -> None:
    """Keep the ambiguity gate measuring more than one kind of refusal."""

    refusals = {c["expected_outcome"] for c in cases} - {RESOLVED}

    assert refusals == {
        ResolutionReason.AMOUNT_UNKNOWN.value,
        ResolutionReason.OCCURRENCE_UNREADABLE.value,
        ResolutionReason.OCCURRENCE_IN_FUTURE.value,
        ResolutionReason.FUNDING_SOURCE_UNRESOLVED.value,
        ResolutionReason.SPLIT_DOES_NOT_RECONCILE.value,
    }


def test_the_boundary_exposes_no_currency_to_measure() -> None:
    """Record why the matrix's currency half cannot be scored here.

    The matrix asks for amount and currency exact match. V1 operates in one
    currency and the boundary offers the model no field for it, so the gate can
    only be about magnitude and sign. This asserts that exclusion is structural
    rather than an omission, which is what the run configuration reports.
    """

    assert "currency" not in ExpenseExtraction.model_fields


def test_an_archived_source_is_refused_rather_than_matched() -> None:
    """Keep a configured but archived name outside the resolvable set.

    The archived fixture source is the only way the dataset can state a name
    the owner really did configure and the resolver must still refuse, which is
    what makes the unresolvable-funding class more than a typo test.
    """

    archived = next(
        source
        for source in fixture_funding_sources()
        if source.status is FundingSourceStatus.ARCHIVED
    )
    extraction = WireExpenseExtraction.model_validate_json(
        _extraction(payment_parts=[{"funding_source_text": archived.name}])
    ).to_domain()

    with pytest.raises(UnresolvedExtractionError) as error:
        resolve_payment_allocations(
            extraction, funding_sources=fixture_funding_sources()
        )

    assert error.value.reason is ResolutionReason.FUNDING_SOURCE_UNRESOLVED


def test_an_exact_default_extraction_scores_the_facets_it_gets_right(
    tmp_path: Path,
) -> None:
    """Credit amount, date, and funding separately rather than as one verdict."""

    result = _run(tmp_path, _extraction())
    metrics = result["metrics"]

    assert metrics["amount_exact_match"].numerator > 0
    assert metrics["occurrence_semantic_exactness"].numerator > 0
    assert metrics["funding_source_and_class_match"].numerator > 0
    assert metrics["expense_refund_polarity"].numerator > 0
    assert metrics["schema_validity_after_one_repair"].value == 1.0


def test_an_invented_merchant_fails_the_fabrication_gate(tmp_path: Path) -> None:
    """Count a value the message does not state, however plausible it reads."""

    result = _run(tmp_path, _extraction(merchant="Esselunga"))
    metrics = result["metrics"]

    assert metrics["fabricated_merchant_date_or_payment_field"].value > 0.01
    assert metrics["fabricated_merchant_date_or_payment_field"].passed is False
    assert result["report"].blocking_gates_passed is False


def test_an_invented_date_fails_the_fabrication_gate(tmp_path: Path) -> None:
    """Treat a supplied temporal phrase as fabrication when none was stated.

    Substituting a date silently misdates a movement the owner never placed in
    the past, which is why omission has to mean the message timestamp.
    """

    result = _run(tmp_path, _extraction(occurred_at_text="ieri"))

    assert (
        result["metrics"]["fabricated_merchant_date_or_payment_field"].passed is False
    )


def test_a_facet_still_scores_when_an_earlier_facet_refuses(tmp_path: Path) -> None:
    """Keep one weak step from hiding every later one behind it.

    Production stops at the first refusal. Scoring each facet independently is
    what makes the report say which step is actually weak.
    """

    result = _run(tmp_path, _extraction(occurred_at_text="qualche giorno fa"))
    metrics = result["metrics"]

    assert metrics["occurrence_semantic_exactness"].value == 0.0
    assert metrics["amount_exact_match"].numerator > 0
    assert metrics["funding_source_and_class_match"].numerator > 0


def test_an_unparseable_completion_is_recorded_rather_than_raised(
    tmp_path: Path,
) -> None:
    """Emit a report even when the boundary never produces a valid document."""

    result = _run(tmp_path, "not json")
    metrics = result["metrics"]

    assert metrics["schema_validity_after_one_repair"].value == 0.0
    assert metrics["schema_validity_after_one_repair"].passed is False
    assert result["report"].blocking_gates_passed is False
    assert all(case.actual is None for case in result["report"].cases)


def test_a_wrong_polarity_is_measured_apart_from_the_amount(tmp_path: Path) -> None:
    """Separate a misread direction from a misread magnitude."""

    result = _run(tmp_path, _extraction(polarity="refund"))
    metrics = result["metrics"]

    assert metrics["expense_refund_polarity"].passed is False
    assert metrics["amount_exact_match"].denominator > 0


def test_the_run_records_the_settings_a_score_cannot_be_read_without(
    tmp_path: Path,
) -> None:
    """Keep a score inseparable from the fixture and text that produced it."""

    report = _run(tmp_path, _extraction())["report"]

    assert report.configuration["boundary_exposes_currency"] is False
    assert report.configuration["auto_post_policy_promoted"] is False
    assert (
        report.configuration["default_received_at"] == DEFAULT_RECEIVED_AT.isoformat()
    )
    assert len(str(report.configuration["instructions_sha256"])) == 64
    assert report.dataset.item_count >= DATASET_MINIMUM


def test_the_per_case_detail_cannot_disagree_with_the_aggregate(
    tmp_path: Path,
) -> None:
    """Derive the case record and the gate from one comparison, not two.

    A report whose per-case detail contradicted its own metrics would send a
    reviewer looking for a model failure that the numbers do not contain, so
    both readings come from the same facet scoring.
    """

    result = _run(tmp_path, _extraction(merchant="Esselunga", occurred_at_text="ieri"))
    report = result["report"]
    metrics = result["metrics"]

    flagged = sum(
        1 for case in report.cases if "fabrication" in (case.detail or "").split(",")
    )

    assert flagged == metrics["fabricated_merchant_date_or_payment_field"].numerator
    assert flagged > 0


def test_a_passing_case_records_no_missed_facet(tmp_path: Path) -> None:
    """Keep the detail empty when nothing missed, so a value always means one."""

    report = _run(tmp_path, _extraction())["report"]
    clean = [
        case
        for case in report.cases
        if case.status.value == "passed" and case.detail is None
    ]

    assert clean


def test_a_fabrication_the_resolver_accepts_is_counted_apart(tmp_path: Path) -> None:
    """Separate a guess that fails safe from one that charges a real source.

    A funding word invented out of a purpose noun is refused deterministically.
    One that happens to name a configured source resolves silently and would
    charge a resource the message never mentioned, so the two cannot share a
    single number.
    """

    refused = _run(
        tmp_path / "refused",
        _extraction(payment_parts=[{"funding_source_text": "affitto"}]),
    )["metrics"]
    accepted = _run(
        tmp_path / "accepted",
        _extraction(payment_parts=[{"funding_source_text": "Carta"}]),
    )["metrics"]

    assert refused["fabricated_funding_silently_resolved"].value == 0.0
    assert accepted["fabricated_funding_silently_resolved"].value == 1.0
    assert accepted["fabricated_funding_silently_resolved"].denominator > 0


def test_an_invented_amount_is_named_rather_than_left_to_one_gate(
    tmp_path: Path,
) -> None:
    """Report a figure produced from nothing, which no matrix gate scopes.

    The fabrication gate covers merchant, date, and payment fields only, so an
    invented total would otherwise surface only as an unreported ambiguity.
    """

    result = _run(tmp_path, _extraction(amount_minor_units=4200))
    metrics = result["metrics"]

    assert metrics["amount_invented_where_none_stated"].value == 1.0
    assert metrics["required_ambiguity_reported"].passed is False
