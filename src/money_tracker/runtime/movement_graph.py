"""LangGraph specialists for correction and undo mutations.

Correction reuses the evaluated extraction and category-fit boundaries, then
hands their answers to framework-independent replacement preparation. Undo has
no model boundary. Both mutations go through FinanceService with a stable
command key and request digest, so a fresh replay returns the first result and
writes no second audit event.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from money_tracker.application import AggregateNotFoundError, FinanceService
from money_tracker.domain import CategoryStatus, InvalidTransitionError
from money_tracker.runtime.expense_graph import ProfileConfigurationReader
from money_tracker.workflows import (
    CallbackAction,
    CategoryFit,
    ExpenseExtraction,
    ExpensePolarity,
    MovementChangeOutcome,
    MovementChangeReason,
    MovementChangeState,
    ResolutionReason,
    StructuredOutputError,
    StructuredOutputModel,
    UnresolvedExtractionError,
    WorkflowIntent,
    movement_change_message_from_state,
    movement_command_key,
    parse_with_one_repair,
    posting_command_key,
    posting_request_hash,
    prepare_expense_correction,
    render_active_categories,
    undo_request_hash,
)
from money_tracker.workflows.candidates import CategoryFitCandidate, ExtractionCandidate

EXTRACT_CORRECTION_NODE = "extract_correction"
CLASSIFY_CORRECTION_CATEGORY_NODE = "classify_correction_category"
APPLY_CORRECTION_NODE = "apply_correction"
APPLY_UNDO_NODE = "apply_undo"
APPLY_POSTING_NODE = "apply_posting"


def build_movement_change_graph(
    *,
    model: StructuredOutputModel,
    configuration: ProfileConfigurationReader,
    finance: FinanceService,
    extraction_instructions: str,
    category_instructions: str,
    extraction_candidate: ExtractionCandidate | None = None,
    category_candidate: CategoryFitCandidate | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[
    MovementChangeState, None, MovementChangeState, MovementChangeState
]:
    """Compile the correction and undo specialist routes.

    Args:
        model: Narrow structured-output port used only by correction.
        configuration: Trusted aggregate, taxonomy, and funding reader.
        finance: Application service owning replay-safe mutations.
        extraction_instructions: Evaluated extraction candidate text.
        category_instructions: Evaluated category-fit candidate text.
        extraction_candidate: Paired extraction schema and conversion.
        category_candidate: Paired purpose schema, conversion, and rendering.
        checkpointer: Optional persistent workflow checkpoint adapter.

    Returns:
        A compiled graph producing a corrected reference, a voided reference,
        or a typed clarification.
    """

    if (
        extraction_candidate
        and extraction_instructions != extraction_candidate.instructions
    ):
        raise ValueError("extraction instructions do not match their candidate")
    if category_candidate and category_instructions != category_candidate.instructions:
        raise ValueError("category instructions do not match their candidate")

    def run_extract(state: MovementChangeState) -> MovementChangeState:
        """Extract replacement facts and preserve the target polarity."""

        target = _target(state)
        if target is None:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        message = movement_change_message_from_state(state)
        if not message.normalized_text:
            return _clarify(MovementChangeReason.CORRECTION_DETAILS_REQUIRED)
        try:
            current = configuration.get_expense(UUID(state["profile_id"]), target)
        except AggregateNotFoundError:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        try:
            parsed = parse_with_one_repair(
                model,
                schema=(
                    extraction_candidate.schema
                    if extraction_candidate
                    else ExpenseExtraction
                ),
                instructions=extraction_instructions,
                message=message.normalized_text,
            )
            extraction = (
                extraction_candidate.convert(parsed)
                if extraction_candidate
                else cast(ExpenseExtraction, parsed)
            )
        except StructuredOutputError:
            return _clarify(ResolutionReason.MODEL_OUTPUT_INVALID)
        expected = (
            ExpensePolarity.REFUND if current.is_refund else ExpensePolarity.EXPENSE
        )
        if extraction.polarity is not expected:
            return _clarify(ResolutionReason.POLARITY_DISAGREEMENT)
        return MovementChangeState(extraction_json=extraction.model_dump_json())

    def run_classify_category(
        state: MovementChangeState,
    ) -> MovementChangeState:
        """Classify the replacement purpose against the active taxonomy."""

        message = movement_change_message_from_state(state)
        taxonomy = configuration.get_taxonomy(UUID(state["profile_id"]))
        active = tuple(
            category.name
            for category in taxonomy.categories
            if category.status is CategoryStatus.ACTIVE
        )
        try:
            parsed = parse_with_one_repair(
                model,
                schema=(
                    category_candidate.build_schema(taxonomy)
                    if category_candidate
                    else CategoryFit
                ),
                instructions=(
                    category_instructions
                    + (
                        category_candidate.render_taxonomy(taxonomy)
                        if category_candidate
                        else render_active_categories(active)
                    )
                ),
                message=message.normalized_text,
            )
            fit = (
                category_candidate.convert(parsed)
                if category_candidate
                else cast(CategoryFit, parsed)
            )
        except StructuredOutputError:
            return _clarify(ResolutionReason.MODEL_OUTPUT_INVALID)
        return MovementChangeState(category_fit_json=fit.model_dump_json())

    def run_correct(state: MovementChangeState) -> MovementChangeState:
        """Prepare and persist one full replay-safe replacement."""

        target = _target(state)
        if target is None:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        profile_id = UUID(state["profile_id"])
        try:
            current = configuration.get_expense(profile_id, target)
            prepared = prepare_expense_correction(
                current,
                ExpenseExtraction.model_validate_json(
                    _required(state, "extraction_json")
                ),
                CategoryFit.model_validate_json(_required(state, "category_fit_json")),
                message=movement_change_message_from_state(state),
                taxonomy=configuration.get_taxonomy(profile_id),
                funding_sources=configuration.get_funding_sources(profile_id),
            )
        except AggregateNotFoundError:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        except InvalidTransitionError:
            return _clarify(MovementChangeReason.MOVEMENT_NOT_CORRECTABLE)
        except UnresolvedExtractionError as error:
            return _clarify(error.reason)
        corrected = finance.correct_expense(
            profile_id=profile_id,
            expense_id=target,
            expected_version=current.version,
            amount=prepared.expense.amount,
            occurred_at=prepared.expense.occurred_at,
            description=prepared.expense.description,
            merchant=prepared.expense.merchant,
            category_allocations=prepared.expense.category_allocations,
            payment_allocations=prepared.expense.payment_allocations,
            idempotency_key=movement_command_key(
                WorkflowIntent.CORRECTION,
                state["source_reference"],
            ),
            request_hash=prepared.request_hash,
            actor="workflow",
            reason="owner corrected a resolved movement",
        )
        return MovementChangeState(
            outcome=MovementChangeOutcome.CORRECTED.value,
            expense_id=str(corrected.id),
        )

    def run_undo(state: MovementChangeState) -> MovementChangeState:
        """End the trusted target exactly once without model involvement.

        The owner-facing outcome stays `VOIDED` for a discarded draft too,
        because `Annulla` means one thing to them and the rendered
        confirmation is already the same sentence. Which transition ran is
        recorded in the audit, where the distinction is load-bearing.
        """

        target = _target(state)
        if target is None:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        profile_id = UUID(state["profile_id"])
        try:
            current = configuration.get_expense(profile_id, target)
            voided = finance.cancel_expense(
                profile_id=profile_id,
                expense_id=target,
                expected_version=current.version,
                idempotency_key=movement_command_key(
                    WorkflowIntent.UNDO,
                    state["source_reference"],
                ),
                request_hash=undo_request_hash(
                    profile_id=profile_id,
                    expense_id=target,
                ),
                actor="workflow",
                reason="owner undid a resolved movement",
            )
        except AggregateNotFoundError:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        except InvalidTransitionError:
            return _clarify(MovementChangeReason.MOVEMENT_NOT_CORRECTABLE)
        return MovementChangeState(
            outcome=MovementChangeOutcome.VOIDED.value,
            expense_id=str(voided.id),
        )

    def run_post(state: MovementChangeState) -> MovementChangeState:
        """Record the trusted draft exactly once without model involvement.

        Posting is the act that makes a movement count, so it stays as
        deterministic as undo: no extraction, no classification, and no
        interpretation of owner text. The owner already said which movement by
        pressing its control.
        """

        target = _target(state)
        if target is None:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        profile_id = UUID(state["profile_id"])
        try:
            current = configuration.get_expense(profile_id, target)
            posted = finance.post_expense(
                profile_id=profile_id,
                expense_id=target,
                expected_version=current.version,
                idempotency_key=posting_command_key(state["source_reference"]),
                request_hash=posting_request_hash(
                    profile_id=profile_id,
                    expense_id=target,
                ),
                actor="owner",
                reason="owner recorded a confirmed draft",
            )
        except AggregateNotFoundError:
            return _clarify(MovementChangeReason.NO_CORRECTABLE_MOVEMENT)
        except InvalidTransitionError:
            return _clarify(MovementChangeReason.MOVEMENT_NOT_POSTABLE)
        return MovementChangeState(
            outcome=MovementChangeOutcome.POSTED.value,
            expense_id=str(posted.id),
        )

    def start_route(state: MovementChangeState) -> str:
        """Select correction, posting, or undo from the trusted routed intent."""

        if state.get("callback_action") == CallbackAction.POST_EXPENSE.value:
            return APPLY_POSTING_NODE
        intent = WorkflowIntent(state["routed_intent"])
        if intent is WorkflowIntent.CORRECTION:
            return EXTRACT_CORRECTION_NODE
        return APPLY_UNDO_NODE

    def continue_or_end(state: MovementChangeState, next_node: str) -> str:
        """Stop after a clarification; otherwise continue in order."""

        return END if state.get("outcome") is not None else next_node

    builder: StateGraph[
        MovementChangeState, None, MovementChangeState, MovementChangeState
    ] = StateGraph(MovementChangeState)
    builder.add_node(EXTRACT_CORRECTION_NODE, run_extract)
    builder.add_node(CLASSIFY_CORRECTION_CATEGORY_NODE, run_classify_category)
    builder.add_node(APPLY_CORRECTION_NODE, run_correct)
    builder.add_node(APPLY_UNDO_NODE, run_undo)
    builder.add_node(APPLY_POSTING_NODE, run_post)
    builder.add_conditional_edges(
        START,
        start_route,
        {
            EXTRACT_CORRECTION_NODE: EXTRACT_CORRECTION_NODE,
            APPLY_POSTING_NODE: APPLY_POSTING_NODE,
            APPLY_UNDO_NODE: APPLY_UNDO_NODE,
        },
    )
    builder.add_conditional_edges(
        EXTRACT_CORRECTION_NODE,
        lambda state: continue_or_end(state, CLASSIFY_CORRECTION_CATEGORY_NODE),
        {
            CLASSIFY_CORRECTION_CATEGORY_NODE: CLASSIFY_CORRECTION_CATEGORY_NODE,
            END: END,
        },
    )
    builder.add_conditional_edges(
        CLASSIFY_CORRECTION_CATEGORY_NODE,
        lambda state: continue_or_end(state, APPLY_CORRECTION_NODE),
        {APPLY_CORRECTION_NODE: APPLY_CORRECTION_NODE, END: END},
    )
    builder.add_edge(APPLY_CORRECTION_NODE, END)
    builder.add_edge(APPLY_UNDO_NODE, END)
    builder.add_edge(APPLY_POSTING_NODE, END)
    return builder.compile(checkpointer=checkpointer)


def _target(state: MovementChangeState) -> UUID | None:
    """Return the trusted target reference when runtime resolved one."""

    value = state.get("target_movement_id")
    return None if value is None else UUID(value)


def _clarify(
    reason: MovementChangeReason | ResolutionReason,
) -> MovementChangeState:
    """Return the safe terminal outcome carrying no owner text."""

    return MovementChangeState(
        outcome=MovementChangeOutcome.CLARIFICATION.value,
        clarification_reason=reason.value,
    )


def _required(state: MovementChangeState, field: str) -> str:
    """Require an answer guaranteed by the preceding graph node."""

    value = state.get(field)
    if not isinstance(value, str):
        raise KeyError(f"movement change workflow reached mutation without {field}")
    return value
