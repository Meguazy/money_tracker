"""LangGraph assembly of the expense and refund specialist workflow.

Like the root router, this module wires implemented functions to nodes and
holds no policy of its own. Extraction and purpose classification are model
boundaries behind the strict retry contract; everything that decides what gets
written is the deterministic assembly already tested without a graph.

The topology is a straight line with one safe exit. Each node either advances
or records a clarification carrying the stable reason that selects the question
to ask. Nothing is written before every part of the message has become a
trusted domain value, so a run either stores a complete draft or stores nothing.

Each boundary's answer is carried forward in the state as its own wire form.
That keeps a resumed thread from re-asking the model, and keeps the state to
small serializable values: these are the model's own JSON contracts, not domain
records, and the stored movement is referenced by identifier only.

Routing and extraction judge the direction of money independently. When they
disagree the run clarifies rather than picking one, because recording a
purchase as a reimbursement is exactly the ambiguity the product baseline says
must not reach a write.

What blocks a draft is not decided here. Deterministic assembly already
reports the precise reason for an unresolved amount, occurrence, or funding
arrangement, and already treats an unnamed merchant as absent rather than
blocking, so this module reports whatever reason assembly raises.

Keeping every write-deciding rule inside that one atomic function costs one
inference call on a failure path: funding words are resolved during assembly,
so a message naming an unresolvable funding source still reaches the purpose
boundary first. That is a deliberate trade. Splitting assembly to fail earlier
would move policy into this module, and the latency target applies to
successful runs.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from money_tracker.application import CategoryProposalService, FinanceService
from money_tracker.application.ports import IdGenerator
from money_tracker.domain import CategoryStatus, FundingSource, Taxonomy
from money_tracker.workflows import (
    CategoryFit,
    ExpenseExtraction,
    ExpenseOutcome,
    ExpensePolarity,
    ExpenseWorkflowState,
    ResolutionReason,
    StructuredOutputError,
    StructuredOutputModel,
    UnresolvedExtractionError,
    WorkflowIntent,
    expense_message_from_state,
    parse_with_one_repair,
    prepare_expense_draft,
    render_active_categories,
)

EXTRACT_NODE = "extract"
CLASSIFY_CATEGORY_NODE = "classify_category"
PERSIST_NODE = "persist"

_EXPECTED_POLARITY = {
    WorkflowIntent.EXPENSE: ExpensePolarity.EXPENSE,
    WorkflowIntent.REFUND: ExpensePolarity.REFUND,
}


class ProfileConfigurationReader(Protocol):
    """Read the trusted profile configuration the boundaries resolve against."""

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the profile's complete category view."""

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the profile's configured funding sources."""


def build_expense_graph(
    *,
    model: StructuredOutputModel,
    configuration: ProfileConfigurationReader,
    finance: FinanceService,
    proposals: CategoryProposalService,
    ids: IdGenerator,
    extraction_instructions: str,
    category_instructions: str,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[
    ExpenseWorkflowState, None, ExpenseWorkflowState, ExpenseWorkflowState
]:
    """Compile the expense and refund workflow.

    Both instruction texts are required arguments with no defaults. Neither is
    promoted, so the assembly cannot imply a production configuration; a caller
    states which candidate it is exercising and an evaluation run records both.

    Args:
        model: Narrow structured-output port for both model boundaries.
        configuration: Trusted taxonomy and funding-source reader.
        finance: Application service owning movement creation.
        proposals: Application service owning candidate submission.
        ids: Stable identifier provider for the movement and any candidate.
        extraction_instructions: Candidate text for the extraction boundary.
        category_instructions: Candidate text for the purpose boundary.
        checkpointer: Persistent checkpointer for resumable threads.

    Returns:
        A compiled graph that maps one routed message to a stored draft, a
        draft awaiting category approval, or a clarification.
    """

    def run_extract(state: ExpenseWorkflowState) -> ExpenseWorkflowState:
        """Extract the movement, then cross-check its direction with routing."""

        message = expense_message_from_state(state)
        try:
            extraction = parse_with_one_repair(
                model,
                schema=ExpenseExtraction,
                instructions=extraction_instructions,
                message=message.normalized_text,
            )
        except StructuredOutputError:
            return _clarify(ResolutionReason.MODEL_OUTPUT_INVALID)
        expected = _EXPECTED_POLARITY[WorkflowIntent(state["routed_intent"])]
        if extraction.polarity is not expected:
            return _clarify(ResolutionReason.POLARITY_DISAGREEMENT)
        return ExpenseWorkflowState(extraction_json=extraction.model_dump_json())

    def run_classify_category(
        state: ExpenseWorkflowState,
    ) -> ExpenseWorkflowState:
        """Classify the purpose against the owner's own active categories."""

        message = expense_message_from_state(state)
        taxonomy = configuration.get_taxonomy(UUID(state["profile_id"]))
        active = tuple(
            category.name
            for category in taxonomy.categories
            if category.status is CategoryStatus.ACTIVE
        )
        try:
            fit = parse_with_one_repair(
                model,
                schema=CategoryFit,
                instructions=(category_instructions + render_active_categories(active)),
                message=message.normalized_text,
            )
        except StructuredOutputError:
            return _clarify(ResolutionReason.MODEL_OUTPUT_INVALID)
        return ExpenseWorkflowState(category_fit_json=fit.model_dump_json())

    def run_persist(state: ExpenseWorkflowState) -> ExpenseWorkflowState:
        """Assemble the draft and commit it through the application services."""

        message = expense_message_from_state(state)
        profile_id = UUID(state["profile_id"])
        try:
            prepared = prepare_expense_draft(
                ExpenseExtraction.model_validate_json(
                    _required(state, "extraction_json")
                ),
                CategoryFit.model_validate_json(_required(state, "category_fit_json")),
                message=message,
                profile_id=profile_id,
                expense_id=ids.new(),
                proposal_id=ids.new(),
                taxonomy=configuration.get_taxonomy(profile_id),
                funding_sources=configuration.get_funding_sources(profile_id),
            )
        except UnresolvedExtractionError as error:
            return _clarify(error.reason)
        created = finance.create_expense(
            prepared.expense,
            request_hash=prepared.request_hash,
            actor="workflow",
            reason="draft prepared from a channel message",
        )
        if prepared.proposal is None:
            return ExpenseWorkflowState(
                outcome=ExpenseOutcome.DRAFTED.value,
                expense_id=str(created.id),
            )
        proposals.submit(
            prepared.proposal,
            actor="workflow",
            reason="candidate awaiting owner approval",
        )
        return ExpenseWorkflowState(
            outcome=ExpenseOutcome.AWAITING_CATEGORY_APPROVAL.value,
            expense_id=str(created.id),
            proposal_id=str(prepared.proposal.id),
        )

    def continue_to_category(state: ExpenseWorkflowState) -> str:
        """Classify the purpose only while no clarification is recorded."""

        return END if state.get("outcome") is not None else CLASSIFY_CATEGORY_NODE

    def continue_to_persist(state: ExpenseWorkflowState) -> str:
        """Write only while no clarification is recorded."""

        return END if state.get("outcome") is not None else PERSIST_NODE

    builder: StateGraph[
        ExpenseWorkflowState, None, ExpenseWorkflowState, ExpenseWorkflowState
    ] = StateGraph(ExpenseWorkflowState)
    builder.add_node(EXTRACT_NODE, run_extract)
    builder.add_node(CLASSIFY_CATEGORY_NODE, run_classify_category)
    builder.add_node(PERSIST_NODE, run_persist)
    builder.add_edge(START, EXTRACT_NODE)
    builder.add_conditional_edges(
        EXTRACT_NODE,
        continue_to_category,
        {CLASSIFY_CATEGORY_NODE: CLASSIFY_CATEGORY_NODE, END: END},
    )
    builder.add_conditional_edges(
        CLASSIFY_CATEGORY_NODE,
        continue_to_persist,
        {PERSIST_NODE: PERSIST_NODE, END: END},
    )
    builder.add_edge(PERSIST_NODE, END)
    return builder.compile(checkpointer=checkpointer)


def _clarify(reason: ResolutionReason) -> ExpenseWorkflowState:
    """Record the safe outcome that asks one question and writes nothing."""

    return ExpenseWorkflowState(
        outcome=ExpenseOutcome.CLARIFICATION.value,
        clarification_reason=reason.value,
    )


def _required(state: ExpenseWorkflowState, field: str) -> str:
    """Require a boundary answer an earlier node was supposed to record.

    Args:
        state: Checkpointed workflow state.
        field: State key naming the expected answer.

    Returns:
        The recorded answer.

    Raises:
        KeyError: If the node ran without its predecessor's answer, which
            would mean the graph topology no longer guarantees the order.
    """

    value = state.get(field)
    if not isinstance(value, str):
        raise KeyError(f"expense workflow reached persistence without {field}")
    return value
