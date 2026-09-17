"""Tests for the browser composition of the promoted advice workflow."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from money_tracker.application import ReportingService
from money_tracker.web.advice import AdviceWorkflow
from money_tracker.workflows import (
    TOOL_SELECTION,
    ReadOutcome,
    ReadTool,
    default_prompt,
)
from tests.unit.money_tracker.runtime.test_read_graph import (
    CARD,
    PROFILE_ID,
    InMemoryReporting,
    ScriptedModel,
    _budget,
    _posted,
)
from tests.unit.money_tracker.tracing.memory_tracer import InMemoryTracer


@dataclass(frozen=True)
class FrozenClock:
    """Return one aware instant for deterministic month resolution."""

    instant: datetime

    def now(self) -> datetime:
        """Return the configured current instant."""

        return self.instant


@dataclass
class RecordingRepository(InMemoryReporting):
    """Read-only in-memory repository recording trusted profile scope."""

    requested_profiles: list[UUID] | None = None

    def __post_init__(self) -> None:
        """Allocate per-instance recording state."""

        self.requested_profiles = []

    def get_taxonomy(self, profile_id: UUID):
        """Record scope and delegate the actual taxonomy read."""

        assert self.requested_profiles is not None
        self.requested_profiles.append(profile_id)
        return super().get_taxonomy(profile_id)

    def get_funding_sources(self, profile_id: UUID):
        """Record scope and delegate the actual funding-source read."""

        assert self.requested_profiles is not None
        self.requested_profiles.append(profile_id)
        return super().get_funding_sources(profile_id)


def test_web_advice_reuses_promoted_prompt_policy_and_reporting_facts() -> None:
    """Select with the model once, then return only deterministic tool JSON."""

    repository = RecordingRepository(
        expenses=(_posted("Spesa alimentare", 11250, funding_source=CARD),),
        budgets=(_budget("Spesa alimentare", 10000),),
    )
    model_completion = json.dumps(
        {
            "request": {
                "kind": "overall_tool",
                "tool": "budgets_at_risk",
                "period": {"kind": "current_month"},
                "evidence": "come vanno tutti i miei budget",
            }
        }
    )
    model = ScriptedModel([model_completion])
    workflow = AdviceWorkflow(
        repository,
        ReportingService(repository),
        clock=FrozenClock(datetime(2026, 9, 15, 12, tzinfo=ZoneInfo("Europe/Rome"))),
        model=model,
    )

    result = workflow.advise(
        profile_id=PROFILE_ID,
        message="Come vanno tutti i miei budget?",
    )

    assert result.outcome is ReadOutcome.ANSWERED
    assert result.executed_tool is ReadTool.BUDGETS_AT_RISK
    assert result.facts == {
        "period": "2026-09",
        "statuses": [
            {
                "category_name": "Spesa alimentare",
                "target": {"minor_units": 10000, "currency": "EUR"},
                "posted_spend": {"minor_units": 11250, "currency": "EUR"},
                "remaining": {"minor_units": -1250, "currency": "EUR"},
            }
        ],
        "unbudgeted_category_names": [],
    }
    assert model.calls == 1
    assert len(model.instructions) == 1
    assert model.instructions[0].startswith(default_prompt(TOOL_SELECTION).text)
    assert repository.requested_profiles
    assert set(repository.requested_profiles) == {PROFILE_ID}


def _budget_workflow(
    tracer: InMemoryTracer | None,
) -> tuple[AdviceWorkflow, ScriptedModel]:
    """Compose advice over one at-risk budget and a scripted selection."""

    repository = RecordingRepository(
        expenses=(_posted("Spesa alimentare", 11250, funding_source=CARD),),
        budgets=(_budget("Spesa alimentare", 10000),),
    )
    completion = json.dumps(
        {
            "request": {
                "kind": "overall_tool",
                "tool": "budgets_at_risk",
                "period": {"kind": "current_month"},
                "evidence": "come vanno tutti i miei budget",
            }
        }
    )
    model = ScriptedModel([completion, completion])
    clock = FrozenClock(datetime(2026, 9, 15, 12, tzinfo=ZoneInfo("Europe/Rome")))
    if tracer is None:
        return (
            AdviceWorkflow(
                repository, ReportingService(repository), clock=clock, model=model
            ),
            model,
        )
    return (
        AdviceWorkflow(
            repository,
            ReportingService(repository),
            clock=clock,
            model=model,
            tracer=tracer,
        ),
        model,
    )


def test_each_traced_advice_request_is_its_own_named_trace() -> None:
    """Seed a fresh request reference, never the fixed channel reference."""

    tracer = InMemoryTracer()
    workflow, _model = _budget_workflow(tracer)

    workflow.advise(profile_id=PROFILE_ID, message="Come vanno tutti i miei budget?")
    workflow.advise(profile_id=PROFILE_ID, message="Come vanno tutti i miei budget?")

    first, second = tracer.named("advice-request")
    assert first.trace_seed != second.trace_seed
    assert first.trace_seed is not None
    assert first.trace_seed.startswith("web:advice:")
    assert first.trace_seed != "web:advice"
    assert first.trace_name == "web-advice"
    assert isinstance(first.input, dict)
    assert first.input["message"] == "Come vanno tutti i miei budget?"
    assert first.input["request_reference"] == first.trace_seed
    assert tracer.graph_configs == ["read", "read"]


def test_traced_advice_records_its_versions_and_grounded_decision() -> None:
    """Record the selection versions, admitted registry, and tool facts."""

    tracer = InMemoryTracer()
    workflow, _model = _budget_workflow(tracer)

    result = workflow.advise(
        profile_id=PROFILE_ID, message="Come vanno tutti i miei budget?"
    )

    (span,) = tracer.named("advice-request")
    assert span.metadata["tool_selection_prompt"]
    assert span.metadata["read_selection_policy"] == "explicit-finance-scope-v1"
    assert span.metadata["routing_intent"] == "advice"
    assert span.metadata["routing_source"] == "interface"
    assert span.metadata["specialist"] == "read"
    assert span.metadata["read_executed_tool"] == "budgets_at_risk"
    assert span.metadata["read_grounded"] is True
    assert "budgets_at_risk" in span.metadata["read_admitted_tools"]  # type: ignore[operator]
    assert "router_prompt" not in span.metadata
    assert isinstance(span.output, dict)
    assert span.output["facts"] == result.facts


def test_tracing_changes_nothing_the_model_is_asked() -> None:
    """Send the selection boundary identical requests with and without tracing."""

    traced, traced_model = _budget_workflow(InMemoryTracer())
    plain, plain_model = _budget_workflow(None)

    traced_result = traced.advise(
        profile_id=PROFILE_ID, message="Come vanno tutti i miei budget?"
    )
    plain_result = plain.advise(
        profile_id=PROFILE_ID, message="Come vanno tutti i miei budget?"
    )

    assert traced_model.instructions == plain_model.instructions
    assert traced_result == plain_result
