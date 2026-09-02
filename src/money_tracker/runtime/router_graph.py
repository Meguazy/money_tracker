"""LangGraph assembly of the typed root router.

The graph is deliberately thin. Every decision it makes is already implemented
and unit-tested in `money_tracker.workflows`; this module only wires those
functions to nodes, edges, and a checkpointer. Keeping the assembly free of
policy is what allows the routing rules to be verified without LangGraph, a
model, or a database.

The topology mirrors the two halves of the router. `pre_route` decides from
stated channel facts alone and, when it succeeds, the graph ends without ever
reaching an inference call. Only unresolved free text continues to `classify`,
which asks the model through the strict boundary and then re-imposes
deterministic policy on the answer.

A boundary failure is not an error path here: AD-006 requires routing to fall
back to clarification rather than loop, so `classify` converts an exhausted
retry budget into a clarification decision carrying a stable reason code.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from money_tracker.workflows import (
    RouteClassification,
    RoutingReason,
    RoutingSource,
    StructuredOutputError,
    StructuredOutputModel,
    accept_classification,
    clarify,
    parse_with_one_repair,
    pre_route,
)
from money_tracker.workflows.state import (
    RouterState,
    channel_message_from_state,
    routing_context_from_state,
    routing_update,
)

PRE_ROUTE_NODE = "pre_route"
CLASSIFY_NODE = "classify"


def build_router_graph(
    *,
    model: StructuredOutputModel,
    instructions: str,
    minimum_confidence: float,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[RouterState, None, RouterState, RouterState]:
    """Compile the root router.

    Both the instruction text and the confidence bar are required arguments
    with no defaults. Neither is promoted, so the graph cannot imply a
    production configuration; a caller must state which candidate it is
    exercising, and an evaluation run records both in its manifest.

    Args:
        model: Narrow structured-output port for the classification half.
        instructions: Candidate instruction text for the router boundary.
        minimum_confidence: Inclusive confidence bar in the unit interval.
        checkpointer: Persistent checkpointer for resumable threads. Omit only
            for a single-shot invocation that never needs to resume.

    Returns:
        A compiled graph that maps one inbound message to one routing decision.
    """

    def run_pre_route(state: RouterState) -> RouterState:
        """Decide from stated channel facts, or contribute nothing."""

        decision = pre_route(channel_message_from_state(state))
        if decision is None:
            return RouterState()
        return routing_update(decision)

    def run_classify(state: RouterState) -> RouterState:
        """Ask the model, then re-impose deterministic routing policy."""

        message = channel_message_from_state(state)
        try:
            classification = parse_with_one_repair(
                model,
                schema=RouteClassification,
                instructions=instructions,
                message=message.normalized_text,
            )
        except StructuredOutputError:
            return routing_update(
                clarify(RoutingReason.INVALID_MODEL_OUTPUT, RoutingSource.MODEL)
            )
        return routing_update(
            accept_classification(
                classification,
                message=message,
                context=routing_context_from_state(state),
                minimum_confidence=minimum_confidence,
            )
        )

    def needs_classification(state: RouterState) -> str:
        """Continue to the model only when pre-routing reached no decision."""

        return END if state.get("intent") is not None else CLASSIFY_NODE

    builder: StateGraph[RouterState, None, RouterState, RouterState] = StateGraph(
        RouterState
    )
    builder.add_node(PRE_ROUTE_NODE, run_pre_route)
    builder.add_node(CLASSIFY_NODE, run_classify)
    builder.add_edge(START, PRE_ROUTE_NODE)
    builder.add_conditional_edges(
        PRE_ROUTE_NODE,
        needs_classification,
        {CLASSIFY_NODE: CLASSIFY_NODE, END: END},
    )
    builder.add_edge(CLASSIFY_NODE, END)
    return builder.compile(checkpointer=checkpointer)
