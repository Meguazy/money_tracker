"""Read-only web composition for the promoted advice workflow.

The browser names the advice route by choosing this surface, so this adapter
starts the existing bounded read graph directly with the trusted ``advice``
intent.  It does not compose the root router or any mutation specialist.  The
model can select only from the promoted advice registry; every returned figure
is the unchanged JSON output of the deterministic read tool that executed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

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
from money_tracker.structured_logging import get_logger
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
    ) -> None:
        """Bind the existing promoted workflow to read-only dependencies.

        Args:
            configuration: Trusted profile vocabulary reads.
            reporting: Deterministic read-tool implementation.
            clock: Aware current instant used to resolve an omitted month.
            model: Structured-output boundary. Production uses the promoted
                local endpoint adapter when omitted.
        """

        self._configuration = configuration
        self._reporting = reporting
        self._clock = clock
        self._model = model or LocalStructuredOutputModel()

    def advise(self, *, profile_id: UUID, message: str) -> AdviceRun:
        """Interpret one advice request and return only tool-derived evidence.

        Args:
            profile_id: Trusted owner scope injected by web composition.
            message: Owner text accepted by the strict HTTP request contract.

        Returns:
            The executed tool and its exact facts, or a bounded clarification.
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
        final = graph.invoke(
            initial_read_state(
                ChannelMessage(
                    source_reference=_SOURCE_REFERENCE,
                    received_at=self._clock.now(),
                    text=message,
                ),
                profile_id=profile_id,
                routed_intent=WorkflowIntent.ADVICE,
            )
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
        _LOGGER.info(
            "web.advice.completed",
            outcome=outcome.value,
            executed_tool=None if tool is None else tool.value,
            reason_code=None if reason is None else reason.value,
        )
        return AdviceRun(
            outcome=outcome,
            executed_tool=tool,
            facts=facts,
            clarification_reason=reason,
        )


__all__ = ["AdviceConfigurationReader", "AdviceRun", "AdviceWorkflow"]
