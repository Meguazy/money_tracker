"""LangGraph assembly of the read-only question and advice routes.

This is the bounded agent loop AD-006 permits, and its bound is the point. The
model gets exactly one decision: which registered tool answers the message, and
which category the owner referred to. Everything after that is deterministic.

There is no write path here and no service that has one. The graph holds a
read-only reporting port, so a mutation is not merely disallowed, it is absent
from the object graph the nodes can reach.

Every figure comes from executing the tool against stored records. The model
sees no amount, produces no amount, and cannot reach a node that would let it
claim one, which is what makes "numeric statements traceable to tool output" a
property of this topology rather than an accuracy to measure.

The registry is selected from the trusted routed intent, never from the model's
own answer, so a model cannot widen its scope by claiming a different route.
The two registries differ, so the same message on the wrong route is refused
rather than quietly answered from the other route's tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from money_tracker.application import ReportingService
from money_tracker.application.reporting import (
    BudgetsAtRisk,
    CategoryBudget,
    CategorySpending,
    PeriodSplit,
)
from money_tracker.domain import (
    BudgetStatus,
    DomainError,
    FundingClassSplit,
    Money,
    TaxonomyError,
    YearMonth,
)
from money_tracker.workflows import (
    TOOL_SELECTION,
    ReadOutcome,
    ReadReason,
    ReadTool,
    ReadWorkflowState,
    StructuredOutputError,
    StructuredOutputModel,
    ToolRequest,
    WorkflowIntent,
    accept_tool_request,
    asks_for_an_unavailable_budget,
    default_prompt,
    parse_with_one_repair,
    prompt_versions,
    read_message_from_state,
    render_selection_categories,
    render_tool_vocabulary,
)
from money_tracker.workflows.wire import WireToolRequest

SELECT_NODE = "select_tool"
EXECUTE_NODE = "execute_tool"


@dataclass(frozen=True, slots=True)
class SelectionBoundary:
    """One read-tool selection boundary: its schema and its conversion.

    The graph asks a model for a tool and then validates the answer against
    the same deterministic acceptance either way, so a boundary whose schema
    differs from `ToolRequest` is admitted on exactly the same terms.

    Attributes:
        version: Identifier recorded by an evaluation run.
        schema: Contract the completion must satisfy.
        to_request: Pure conversion to the domain request. It receives the
            month the runtime resolved from the message timestamp, which the
            model is never shown, so a boundary may offer a relative period. It
            returns None when the boundary declined to choose a tool, which the
            graph answers with the same clarification as an unregistered one.
    """

    version: str
    schema: type[BaseModel]
    to_request: Callable[[BaseModel, YearMonth], ToolRequest | None]


_SELECTION_SHAPES: dict[
    str, tuple[type[BaseModel], Callable[[BaseModel, YearMonth], ToolRequest | None]]
] = {
    "v1": (ToolRequest, lambda parsed, _current: cast(ToolRequest, parsed)),
    "v2": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
    "v3": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
    "v4": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
    "v5": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
    "v7": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
    "v6": (
        WireToolRequest,
        lambda parsed, current: cast(WireToolRequest, parsed).to_domain(
            current=current
        ),
    ),
}
"""The schema each selection instruction version is paired with."""

SELECTION_BOUNDARIES = {
    version.short_name: SelectionBoundary(
        version=version.id,
        schema=_SELECTION_SHAPES[version.short_name][0],
        to_request=_SELECTION_SHAPES[version.short_name][1],
    )
    for version in prompt_versions(TOOL_SELECTION)
}
"""Read-tool selection boundaries a run may select, by short candidate name."""

FLAT_SELECTION = SELECTION_BOUNDARIES["v1"]
"""The flat boundary, whose period is a string the model writes or omits."""

DEFAULT_SELECTION = SELECTION_BOUNDARIES[default_prompt(TOOL_SELECTION).short_name]
"""The boundary a run uses when none is named."""


def build_read_graph(
    *,
    model: StructuredOutputModel,
    reporting: ReportingService,
    selection_instructions: str,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    selection_boundary: SelectionBoundary = FLAT_SELECTION,
    category_names: tuple[str, ...] = (),
) -> CompiledStateGraph[ReadWorkflowState, None, ReadWorkflowState, ReadWorkflowState]:
    """Compile the read-only question and advice workflow.

    The instruction text is a required argument with no default, for the same
    reason every other assembly requires one: no prompt is promoted, so the
    assembly must not imply a production configuration.

    Args:
        model: Narrow structured-output port for the selection boundary.
        reporting: Read-only service executing the registered tools.
        selection_instructions: Candidate text for the selection boundary.
        checkpointer: Persistent checkpointer for resumable threads.
        selection_boundary: Schema and conversion the selection node asks for.
            Defaults to the flat contract, so an existing caller is unchanged.
        category_names: Active category display names shown to the selection
            boundary. Empty by default, which renders nothing and leaves the
            boundary asking for a category it has not been shown.

    Returns:
        A compiled graph that maps one routed question to grounded figures or
        to a clarification.
    """

    def run_select(state: ReadWorkflowState) -> ReadWorkflowState:
        """Choose one tool from the calling route's registry."""

        message = read_message_from_state(state)
        intent = WorkflowIntent(state["routed_intent"])
        try:
            request = selection_boundary.to_request(
                parse_with_one_repair(
                    model,
                    schema=selection_boundary.schema,
                    instructions=(
                        selection_instructions
                        + render_tool_vocabulary(
                            intent, version=selection_boundary.version
                        )
                        + render_selection_categories(category_names)
                    ),
                    message=message.normalized_text,
                ),
                YearMonth.from_datetime(message.received_at),
            )
        except StructuredOutputError:
            return _clarify(ReadReason.MODEL_OUTPUT_INVALID)
        if request is None:
            return _clarify(ReadReason.TOOL_NOT_REGISTERED)
        try:
            accept_tool_request(request, intent=intent)
        except DomainError:
            return _clarify(ReadReason.TOOL_NOT_REGISTERED)
        if asks_for_an_unavailable_budget(
            request, intent=intent, message=message.normalized_text
        ):
            return _clarify(ReadReason.BUDGET_TOOL_UNAVAILABLE)
        return ReadWorkflowState(tool_request_json=request.model_dump_json())

    def run_execute(state: ReadWorkflowState) -> ReadWorkflowState:
        """Execute the admitted tool and record the figures it produced."""

        message = read_message_from_state(state)
        intent = WorkflowIntent(state["routed_intent"])
        request = ToolRequest.model_validate_json(_required(state, "tool_request_json"))
        tool = accept_tool_request(request, intent=intent)
        profile_id = UUID(state["profile_id"])
        period = request.resolved_period(
            current=YearMonth.from_datetime(message.received_at)
        )
        try:
            facts = _execute(reporting, tool, request, profile_id, period)
        except TaxonomyError:
            return _clarify(ReadReason.UNKNOWN_CATEGORY)
        return ReadWorkflowState(
            outcome=ReadOutcome.ANSWERED.value,
            executed_tool=tool.value,
            answer_json=json.dumps(facts, sort_keys=True),
            clarification_reason=None,
        )

    def continue_to_execute(state: ReadWorkflowState) -> str:
        """Read only while no clarification is recorded."""

        return END if state.get("outcome") is not None else EXECUTE_NODE

    builder: StateGraph[
        ReadWorkflowState, None, ReadWorkflowState, ReadWorkflowState
    ] = StateGraph(ReadWorkflowState)
    builder.add_node(SELECT_NODE, run_select)
    builder.add_node(EXECUTE_NODE, run_execute)
    builder.add_edge(START, SELECT_NODE)
    builder.add_conditional_edges(
        SELECT_NODE,
        continue_to_execute,
        {EXECUTE_NODE: EXECUTE_NODE, END: END},
    )
    builder.add_edge(EXECUTE_NODE, END)
    return builder.compile(checkpointer=checkpointer)


def _execute(
    reporting: ReportingService,
    tool: ReadTool,
    request: ToolRequest,
    profile_id: UUID,
    period: YearMonth,
) -> dict[str, Any]:
    """Run one admitted tool and render its figures as JSON primitives.

    Args:
        reporting: Read-only service owning every read model.
        tool: Tool deterministic acceptance admitted.
        request: The validated model request supplying the category, if any.
        profile_id: Trusted owner scope.
        period: Resolved Rome calendar month.

    Returns:
        The tool's figures as JSON primitives.

    Raises:
        TaxonomyError: If a named category matches no active category.
    """

    if tool is ReadTool.CATEGORY_SPENDING:
        return _spending_facts(
            reporting.category_spending(
                profile_id=profile_id,
                category_name=_named(request),
                period=period,
            )
        )
    if tool is ReadTool.BUDGET_STATUS:
        return _budget_facts(
            reporting.category_budget(
                profile_id=profile_id,
                category_name=_named(request),
                period=period,
            )
        )
    if tool is ReadTool.FUNDING_CLASS_SPLIT:
        return _split_facts(
            reporting.period_split(profile_id=profile_id, period=period)
        )
    return _risk_facts(reporting.budgets_at_risk(profile_id=profile_id, period=period))


def _named(request: ToolRequest) -> str:
    """Return the category name a category tool is guaranteed to carry.

    Raises:
        KeyError: If the contract stopped guaranteeing it, which its own
            cross-field validation makes unreachable today.
    """

    if request.category_name is None:
        raise KeyError("a category tool reached execution without a category")
    return request.category_name


def _money(amount: Money) -> dict[str, Any]:
    """Render one exact amount without turning it into a float."""

    return {"minor_units": amount.minor_units, "currency": amount.currency}


def _period(period: YearMonth) -> str:
    """Render one calendar month the way the boundary accepts it."""

    return f"{period.year:04d}-{period.month:02d}"


def _split(split: FundingClassSplit) -> dict[str, Any]:
    """Render one economic source split."""

    return {
        "personal_funds": _money(split.personal_funds),
        "benefit": _money(split.benefit),
        "other": _money(split.other),
    }


def _status(status: BudgetStatus) -> dict[str, Any]:
    """Render one budget position, overspend included as a negative."""

    return {
        "target": _money(status.target),
        "posted_spend": _money(status.posted_spend),
        "remaining": _money(status.remaining),
    }


def _spending_facts(result: CategorySpending) -> dict[str, Any]:
    """Render the category roll-up answer."""

    return {
        "period": _period(result.period),
        "category_name": result.category_name,
        "posted_spend": _money(result.posted_spend),
        "split": _split(result.split),
    }


def _budget_facts(result: CategoryBudget) -> dict[str, Any]:
    """Render the budget answer, stating plainly when none is configured."""

    return {
        "period": _period(result.period),
        "category_name": result.category_name,
        "status": None if result.status is None else _status(result.status),
    }


def _split_facts(result: PeriodSplit) -> dict[str, Any]:
    """Render the whole-month split answer."""

    return {
        "period": _period(result.period),
        "split": _split(result.split),
        "sources": [
            {
                "name": source.name,
                "funding_class": source.funding_class.value,
                "posted_spend": _money(amount),
            }
            for source, amount in result.sources
        ],
    }


def _risk_facts(result: BudgetsAtRisk) -> dict[str, Any]:
    """Render the survey answer, worst position first and gaps named."""

    return {
        "period": _period(result.period),
        "statuses": [
            {"category_name": name, **_status(status)}
            for name, status in result.statuses
        ],
        "unbudgeted_category_names": list(result.unbudgeted_category_names),
    }


def _clarify(reason: ReadReason) -> ReadWorkflowState:
    """Record a run that read nothing and owes one targeted question."""

    return ReadWorkflowState(
        outcome=ReadOutcome.CLARIFICATION.value,
        clarification_reason=reason.value,
    )


def _required(state: ReadWorkflowState, field: str) -> str:
    """Read one state value a node cannot proceed without.

    Args:
        state: Checkpointed read workflow state.
        field: Key to read.

    Returns:
        The stored value.

    Raises:
        KeyError: If the value is absent, which would mean the graph reached a
            node its own edges should have made unreachable.
    """

    value = state.get(field)
    if not isinstance(value, str):
        raise KeyError(f"read workflow reached execution without {field}")
    return value


__all__ = ["EXECUTE_NODE", "SELECT_NODE", "build_read_graph"]
