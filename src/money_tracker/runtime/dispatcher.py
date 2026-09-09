"""Explicit composition of the root router and Step 3 specialists.

The root router and specialist graphs intentionally remain independently
compiled units.  This dispatcher is the single application-facing entry point:
it invokes routing once, passes only the accepted route and trusted references
to one specialist, and converts graph state into a compact typed outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
    FinanceService,
    IdGenerator,
    ReportingService,
)
from money_tracker.runtime.expense_graph import (
    ProfileConfigurationReader,
    build_expense_graph,
)
from money_tracker.runtime.movement_graph import build_movement_change_graph
from money_tracker.runtime.read_graph import SelectionBoundary, build_read_graph
from money_tracker.runtime.review_graph import (
    ReviewConfigurationReader,
    build_review_graph,
)
from money_tracker.runtime.router_graph import build_router_graph
from money_tracker.workflows import (
    DEFAULT_READ_SELECTION_POLICY,
    ChannelMessage,
    DispatchContext,
    DispatchOutcome,
    DispatchReason,
    DispatchStatus,
    ExpenseWorkflowState,
    MovementChangeState,
    ReadSelectionPolicyVersion,
    ReadTool,
    ReadWorkflowState,
    ReviewWorkflowState,
    RouterState,
    RoutingDecision,
    StructuredOutputModel,
    WorkflowIntent,
    initial_expense_state,
    initial_movement_change_state,
    initial_read_state,
    initial_review_state,
    initial_router_state,
    review_decision_from_message,
    routing_decision_from_state,
)
from money_tracker.workflows.candidates import CategoryFitCandidate, ExtractionCandidate

Graph = Any


class DispatchConfigurationReader(
    ProfileConfigurationReader, ReviewConfigurationReader, Protocol
):
    """Combined trusted reads required by every dispatched specialist."""


@dataclass(slots=True)
class WorkflowDispatcher:
    """Route one normalized work unit and invoke exactly one specialist.

    Attributes:
        router: Compiled root routing graph.
        expense: Compiled expense/refund specialist.
        movement_change: Compiled correction/undo specialist.
        read: Compiled query/advice specialist.
        review: Compiled persistent category-review specialist.
    """

    router: Graph
    expense: Graph
    movement_change: Graph
    read: Graph
    review: Graph

    @classmethod
    def routing_only(cls, router: Graph) -> WorkflowDispatcher:
        """Build the same entry point for diagnostics without finance access.

        Args:
            router: Compiled root router.

        Returns:
            A dispatcher that can return routing outcomes while a missing
            profile prevents every specialist invocation.
        """

        unavailable = _UnavailableSpecialist()
        return cls(
            router=router,
            expense=unavailable,
            movement_change=unavailable,
            read=unavailable,
            review=unavailable,
        )

    def dispatch(
        self,
        message: ChannelMessage,
        *,
        context: DispatchContext,
    ) -> DispatchOutcome:
        """Route a normalized message and return its typed specialist outcome.

        Args:
            message: Channel-neutral ``ChannelMessage`` work unit.
            context: Trusted owner scope and correlation facts.

        Returns:
            One compact result suitable for a durable gateway boundary.

        Raises:
            RuntimeError: If a graph violates its state contract or a category
                follow-up names a checkpoint that does not exist.
        """

        routed_state = cast(
            RouterState,
            self.router.invoke(
                initial_router_state(message, context=context.routing),
                _thread_config("router", message.source_reference),
            ),
        )
        decision = routing_decision_from_state(routed_state)
        if decision is None:
            raise RuntimeError("router reached no decision")
        if decision.intent is WorkflowIntent.CLARIFICATION:
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.CLARIFICATION,
                clarification_reason=decision.reason.value,
            )
        if context.profile_id is None:
            return DispatchOutcome(routing=decision, status=DispatchStatus.ROUTED)

        if decision.intent in {WorkflowIntent.EXPENSE, WorkflowIntent.REFUND}:
            return self._dispatch_expense(message, context.profile_id, decision)
        if decision.intent in {WorkflowIntent.QUERY, WorkflowIntent.ADVICE}:
            return self._dispatch_read(message, context.profile_id, decision)
        if decision.intent in {WorkflowIntent.CORRECTION, WorkflowIntent.UNDO}:
            return self._dispatch_movement_change(message, context, decision)
        if decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP:
            return self._dispatch_review(message, context, decision)
        return DispatchOutcome(routing=decision, status=DispatchStatus.ROUTED)

    def _dispatch_expense(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
    ) -> DispatchOutcome:
        """Run a movement creation specialist and open any resulting review."""

        final = cast(
            ExpenseWorkflowState,
            self.expense.invoke(
                initial_expense_state(
                    message,
                    profile_id=profile_id,
                    routed_intent=decision.intent,
                ),
                _thread_config("expense", message.source_reference),
            ),
        )
        status = DispatchStatus(_required(final.get("outcome"), "outcome"))
        expense_id = _optional_uuid(final.get("expense_id"))
        proposal_id = _optional_uuid(final.get("proposal_id"))
        if (
            status is DispatchStatus.AWAITING_CATEGORY_APPROVAL
            and expense_id is not None
            and proposal_id is not None
        ):
            self.review.invoke(
                initial_review_state(
                    profile_id=profile_id,
                    proposal_id=proposal_id,
                    expense_id=expense_id,
                    source_reference=message.source_reference,
                ),
                _thread_config("category-review", str(proposal_id)),
            )
        return DispatchOutcome(
            routing=decision,
            status=status,
            expense_id=expense_id,
            proposal_id=proposal_id,
            clarification_reason=final.get("clarification_reason"),
        )

    def _dispatch_read(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
    ) -> DispatchOutcome:
        """Run one scoped read-only specialist."""

        final = cast(
            ReadWorkflowState,
            self.read.invoke(
                initial_read_state(
                    message,
                    profile_id=profile_id,
                    routed_intent=decision.intent,
                ),
                _thread_config("read", message.source_reference),
            ),
        )
        facts_json = final.get("answer_json")
        facts = (
            None if facts_json is None else cast(dict[str, Any], json.loads(facts_json))
        )
        tool_name = final.get("executed_tool")
        return DispatchOutcome(
            routing=decision,
            status=DispatchStatus(_required(final.get("outcome"), "outcome")),
            executed_tool=None if tool_name is None else ReadTool(tool_name),
            facts=facts,
            clarification_reason=final.get("clarification_reason"),
        )

    def _dispatch_movement_change(
        self,
        message: ChannelMessage,
        context: DispatchContext,
        decision: RoutingDecision,
    ) -> DispatchOutcome:
        """Run correction or undo against a trusted movement reference."""

        target = decision.target_movement_id
        if target is None and decision.pending_action_id is not None:
            target = context.movement_for_action(decision.pending_action_id)
        if target is None:
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.CLARIFICATION,
                clarification_reason=DispatchReason.MOVEMENT_TARGET_REQUIRED.value,
            )
        final = cast(
            MovementChangeState,
            self.movement_change.invoke(
                initial_movement_change_state(
                    message,
                    profile_id=cast(UUID, context.profile_id),
                    routed_intent=decision.intent,
                    target_movement_id=target,
                ),
                _thread_config("movement-change", message.source_reference),
            ),
        )
        return DispatchOutcome(
            routing=decision,
            status=DispatchStatus(_required(final.get("outcome"), "outcome")),
            expense_id=_optional_uuid(final.get("expense_id")),
            clarification_reason=final.get("clarification_reason"),
        )

    def _dispatch_review(
        self,
        message: ChannelMessage,
        context: DispatchContext,
        decision: RoutingDecision,
    ) -> DispatchOutcome:
        """Resume the exact persistent review named by routing acceptance."""

        proposal_id = decision.pending_action_id
        if (
            proposal_id is None
            or proposal_id not in context.routing.pending_category_review_ids
        ):
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.CLARIFICATION,
                clarification_reason=DispatchReason.REVIEW_NOT_PENDING.value,
            )
        review_decision = review_decision_from_message(message)
        if review_decision is None:
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.CLARIFICATION,
                proposal_id=proposal_id,
                clarification_reason=DispatchReason.REVIEW_DECISION_REQUIRED.value,
            )
        final = cast(
            ReviewWorkflowState,
            self.review.invoke(
                Command(resume=review_decision),
                _thread_config("category-review", str(proposal_id)),
            ),
        )
        outcome = final.get("outcome")
        current_proposal = UUID(_required(final.get("proposal_id"), "proposal_id"))
        if outcome is None:
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
                proposal_id=current_proposal,
                clarification_reason=final.get("clarification_reason"),
            )
        return DispatchOutcome(
            routing=decision,
            status=DispatchStatus(outcome),
            proposal_id=current_proposal,
            settled_category_id=_optional_uuid(final.get("settled_category_id")),
            clarification_reason=final.get("clarification_reason"),
        )


class _UnavailableSpecialist:
    """Fail closed if routing-only assembly reaches finance unexpectedly."""

    def invoke(self, value: object, config: object) -> object:
        """Reject an invocation that profile gating should make unreachable."""

        raise RuntimeError("routing-only dispatcher cannot invoke a specialist")


def build_workflow_dispatcher(
    *,
    model: StructuredOutputModel,
    configuration: DispatchConfigurationReader,
    finance: FinanceService,
    proposals: CategoryProposalService,
    review: CategoryReviewService,
    reporting: ReportingService,
    ids: IdGenerator,
    router_instructions: str,
    minimum_confidence: float,
    extraction_instructions: str,
    category_instructions: str,
    selection_instructions: str,
    extraction_candidate: ExtractionCandidate | None = None,
    category_candidate: CategoryFitCandidate | None = None,
    selection_boundary: SelectionBoundary,
    category_names: tuple[str, ...] = (),
    funding_source_names: tuple[str, ...] = (),
    selection_policy: ReadSelectionPolicyVersion = DEFAULT_READ_SELECTION_POLICY,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> WorkflowDispatcher:
    """Assemble the single dispatcher from independently bounded graphs.

    Args:
        model: Shared narrow local structured-output model port.
        configuration: Trusted profile, taxonomy, funding, and movement reads.
        finance: Replay-safe finance mutation service.
        proposals: Pending category-proposal service.
        review: Owner category decision service.
        reporting: Read-only reporting service.
        ids: Stable movement identifier provider.
        router_instructions: Evaluated router instructions.
        minimum_confidence: Explicit routing confidence threshold.
        extraction_instructions: Evaluated extraction instructions.
        category_instructions: Evaluated purpose instructions.
        selection_instructions: Evaluated tool-selection instructions.
        extraction_candidate: Extraction schema/conversion paired with its text.
        category_candidate: Purpose schema/conversion paired with its text.
        selection_boundary: Tool schema/conversion paired with its text.
        category_names: Active names visible to read selection.
        funding_source_names: Active source display names visible to the
            deterministic read-selection policy.
        selection_policy: Versioned post-model policy; defaults to the V1
            policy promoted by EVD-004.
        checkpointer: Persistent serializer-safe graph checkpoint adapter.

    Returns:
        A dispatcher whose ``dispatch`` method is the sole channel-facing call.
    """

    return WorkflowDispatcher(
        router=cast(
            Graph,
            build_router_graph(
                model=model,
                instructions=router_instructions,
                minimum_confidence=minimum_confidence,
                checkpointer=checkpointer,
            ),
        ),
        expense=cast(
            Graph,
            build_expense_graph(
                model=model,
                configuration=configuration,
                finance=finance,
                proposals=proposals,
                ids=ids,
                extraction_instructions=extraction_instructions,
                category_instructions=category_instructions,
                extraction_candidate=extraction_candidate,
                category_candidate=category_candidate,
                checkpointer=checkpointer,
            ),
        ),
        movement_change=cast(
            Graph,
            build_movement_change_graph(
                model=model,
                configuration=configuration,
                finance=finance,
                extraction_instructions=extraction_instructions,
                category_instructions=category_instructions,
                extraction_candidate=extraction_candidate,
                category_candidate=category_candidate,
                checkpointer=checkpointer,
            ),
        ),
        read=cast(
            Graph,
            build_read_graph(
                model=model,
                reporting=reporting,
                selection_instructions=selection_instructions,
                checkpointer=checkpointer,
                selection_boundary=selection_boundary,
                category_names=category_names,
                funding_source_names=funding_source_names,
                selection_policy=selection_policy,
            ),
        ),
        review=cast(
            Graph,
            build_review_graph(
                model=model,
                configuration=configuration,
                review=review,
                category_instructions=category_instructions,
                category_candidate=category_candidate,
                checkpointer=checkpointer,
            ),
        ),
    )


def _thread_config(namespace: str, reference: str) -> dict[str, dict[str, str]]:
    """Build a collision-free stable LangGraph thread identifier."""

    return {"configurable": {"thread_id": f"{namespace}:{reference}"}}


def _required(value: str | None, field: str) -> str:
    """Require a terminal state field guaranteed by a specialist contract."""

    if value is None:
        raise RuntimeError(f"specialist reached no {field}")
    return value


def _optional_uuid(value: str | None) -> UUID | None:
    """Convert an optional stable state reference to UUID."""

    return None if value is None else UUID(value)


__all__ = ["WorkflowDispatcher", "build_workflow_dispatcher"]
