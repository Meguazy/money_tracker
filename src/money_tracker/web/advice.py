"""Read-only web composition for the promoted advice workflow.

The browser names the advice route by choosing this surface, so this adapter
starts the existing bounded read graph directly with the trusted ``advice``
intent.  It does not compose the root router or any mutation specialist.  The
model can select only from the promoted advice registry; every returned figure
is the unchanged JSON output of the deterministic read tool that executed.

Each request is one trace named `web-advice`, rooted at an `advice-request`
span whose identity is derived from a reference generated for that request
alone. The message the graph sees keeps its fixed `web:advice` source
reference, so tracing changes nothing the model or the graph receives.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from money_tracker.application import ReportingService
from money_tracker.application.ports import Clock
from money_tracker.domain import (
    CategoryStatus,
    FundingSource,
    FundingSourceStatus,
    Taxonomy,
)
from money_tracker.runtime import LocalStructuredOutputModel, build_read_graph
from money_tracker.runtime.read_graph import DEFAULT_SELECTION
from money_tracker.runtime.trace_metadata import (
    read_decision_metadata,
    workflow_version_set,
)
from money_tracker.structured_logging import get_logger
from money_tracker.tracing import DISABLED_TRACER, WEB_ADVICE_TRACE, Tracer
from money_tracker.workflows import (
    DEFAULT_READ_SELECTION_POLICY,
    TOOL_SELECTION,
    ChannelMessage,
    ReadOutcome,
    ReadReason,
    ReadTool,
    StructuredOutputModel,
    WorkflowIntent,
    default_prompt,
    initial_read_state,
)

_LOGGER = get_logger(__name__)
_SOURCE_REFERENCE = "web:advice"
_TRACE_METADATA = {
    "entry_point": "money-tracker-api",
    "route": "POST /api/v1/advice",
    **workflow_version_set(
        selection=DEFAULT_SELECTION,
        selection_policy=DEFAULT_READ_SELECTION_POLICY,
    ),
}


class AdviceConfigurationReader(Protocol):
    """Read the trusted display vocabulary used by advice selection."""

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the profile's complete validated taxonomy."""

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the profile's configured funding sources."""


@dataclass(frozen=True, slots=True)
class AdviceRun:
    """Safe result of one promoted advice workflow invocation.

    Attributes:
        outcome: Whether one deterministic tool answered or clarification is
            required.
        executed_tool: Tool that produced ``facts``, when one executed.
        facts: JSON-compatible deterministic tool output, never model prose.
        clarification_reason: Stable workflow reason when no tool executed.
    """

    outcome: ReadOutcome
    executed_tool: ReadTool | None
    facts: dict[str, Any] | None
    clarification_reason: ReadReason | None


class AdviceWorkflow:
    """Run only the promoted, read-only advice graph for the browser.

    The object graph contains the structured-output model, a reporting service,
    two configuration reads, and a clock.  It contains no finance,
    administration, review, receipt, messaging, or other mutation service.
    Category and funding-source display names are loaded for every request so a
    rename or archive cannot leave the deterministic selection policy stale.
    """

    def __init__(
        self,
        configuration: AdviceConfigurationReader,
        reporting: ReportingService,
        *,
        clock: Clock,
        model: StructuredOutputModel | None = None,
        tracer: Tracer = DISABLED_TRACER,
    ) -> None:
        """Bind the existing promoted workflow to read-only dependencies.

        Args:
            configuration: Trusted profile vocabulary reads.
            reporting: Deterministic read-tool implementation.
            clock: Aware current instant used to resolve an omitted month.
            model: Structured-output boundary. Production uses the promoted
                local endpoint adapter when omitted.
            tracer: Tracing port chosen by the entry point; records nothing by
                default. Inject the same tracer into the model adapter.
        """

        self._configuration = configuration
        self._reporting = reporting
        self._clock = clock
        self._model = model or LocalStructuredOutputModel(tracer=tracer)
        self._tracer = tracer

    def advise(self, *, profile_id: UUID, message: str) -> AdviceRun:
        """Interpret one advice request and return only tool-derived evidence.

        Args:
            profile_id: Trusted owner scope injected by web composition.
            message: Owner text accepted by the strict HTTP request contract.

        Returns:
            The executed tool and its exact facts, or a bounded clarification.
        """

        request_reference = f"{_SOURCE_REFERENCE}:{uuid4()}"
        received_at = self._clock.now()
        with self._tracer.span(
            "advice-request",
            trace_seed=request_reference,
            trace_name=WEB_ADVICE_TRACE,
            input={
                "request_reference": request_reference,
                "message": message,
                "profile_id": str(profile_id),
                "received_at": received_at.isoformat(),
            },
            metadata=_TRACE_METADATA,
        ) as span:
            run, final = self._advise(profile_id, message, received_at)
            if self._tracer.enabled:
                span.record_metadata(
                    {
                        "routing_intent": WorkflowIntent.ADVICE.value,
                        "routing_source": "interface",
                        "specialist": "read",
                        "outcome_status": run.outcome.value,
                        **{
                            key: value
                            for key, value in read_decision_metadata(
                                WorkflowIntent.ADVICE, final
                            ).items()
                            if value is not None
                        },
                    }
                )
                span.record_output(
                    {
                        "outcome": run.outcome.value,
                        "executed_tool": (
                            None
                            if run.executed_tool is None
                            else run.executed_tool.value
                        ),
                        "facts": run.facts,
                        "clarification_reason": (
                            None
                            if run.clarification_reason is None
                            else run.clarification_reason.value
                        ),
                    }
                )
        _LOGGER.info(
            "web.advice.completed",
            outcome=run.outcome.value,
            executed_tool=None
            if run.executed_tool is None
            else run.executed_tool.value,
            reason_code=(
                None
                if run.clarification_reason is None
                else run.clarification_reason.value
            ),
            request_reference=request_reference,
        )
        return run

    def _advise(
        self, profile_id: UUID, message: str, received_at: datetime
    ) -> tuple[AdviceRun, Mapping[str, Any]]:
        """Run the read graph once and project its final state.

        Args:
            profile_id: Trusted owner scope injected by web composition.
            message: Owner text accepted by the strict HTTP request contract.
            received_at: Aware instant the request is interpreted at.

        Returns:
            The safe result and the graph's final state, which only the trace
            reads further.
        """

        taxonomy = self._configuration.get_taxonomy(profile_id)
        sources = self._configuration.get_funding_sources(profile_id)
        graph = build_read_graph(
            model=self._model,
            reporting=self._reporting,
            selection_instructions=default_prompt(TOOL_SELECTION).text,
            selection_boundary=DEFAULT_SELECTION,
            category_names=tuple(
                category.name
                for category in taxonomy.categories
                if category.status is CategoryStatus.ACTIVE
            ),
            funding_source_names=tuple(
                source.name
                for source in sources
                if source.status is FundingSourceStatus.ACTIVE
            ),
            selection_policy=DEFAULT_READ_SELECTION_POLICY,
        )
        state = initial_read_state(
            ChannelMessage(
                source_reference=_SOURCE_REFERENCE,
                received_at=received_at,
                text=message,
            ),
            profile_id=profile_id,
            routed_intent=WorkflowIntent.ADVICE,
        )
        # Callbacks are the only key a tracer contributes. An untraced request
        # invokes the graph exactly as it did before tracing existed.
        config = self._tracer.graph_config(graph="read")
        final = (
            graph.invoke(state, cast(RunnableConfig, config))
            if config
            else graph.invoke(state)
        )
        outcome = ReadOutcome(cast(str, final["outcome"]))
        tool_value = final.get("executed_tool")
        facts_value = final.get("answer_json")
        reason_value = final.get("clarification_reason")
        facts = (
            None
            if not isinstance(facts_value, str)
            else cast(dict[str, Any], json.loads(facts_value))
        )
        tool = None if not isinstance(tool_value, str) else ReadTool(tool_value)
        reason = None if not isinstance(reason_value, str) else ReadReason(reason_value)
        run = AdviceRun(
            outcome=outcome,
            executed_tool=tool,
            facts=facts,
            clarification_reason=reason,
        )
        return run, final


__all__ = ["AdviceConfigurationReader", "AdviceRun", "AdviceWorkflow"]
