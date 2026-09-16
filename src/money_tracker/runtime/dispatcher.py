"""Explicit composition of the root router and Step 3 specialists.

The root router and specialist graphs intentionally remain independently
compiled units.  This dispatcher is the single application-facing entry point:
it invokes routing once, passes only the accepted route and trusted references
to one specialist, and converts graph state into a compact typed outcome.

Tracing enters through an injected port. Each dispatch opens one span holding
the complete work unit, trusted context, version set, and typed outcome, and
each graph invocation receives only the callbacks the tracer returns. Callbacks
are not persisted by LangGraph, so a traced invocation checkpoints exactly
what an untraced one does.

The same span carries the deterministic decisions taken around the model as
flat, filterable metadata: the routed intent, its reason code and confidence,
whether policy vetoed the model, how allocations were resolved, which read tools
the route admitted and what grounded result executed, and the graph threads the
work unit touched. They are read from the typed decision and the graphs' final
states after the fact, so recording them cannot change a decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
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
from money_tracker.structured_logging import get_logger, log_context
from money_tracker.tracing import DISABLED_TRACER, Tracer
from money_tracker.workflows import (
    DEFAULT_READ_SELECTION_POLICY,
    POLICY_VETO_REASONS,
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
    outcome_facts,
    registry_for,
    review_decision_from_message,
    routing_decision_from_state,
)
from money_tracker.workflows.candidates import CategoryFitCandidate, ExtractionCandidate

Graph = Any


_LOGGER = get_logger("money_tracker.runtime.dispatcher")


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
        tracer: Tracing port injected by the entry point.
        trace_metadata: Contract, prompt, and policy versions in force, recorded
            on every dispatch span. They describe the assembly, so the entry
            point that assembled it supplies them.
    """

    router: Graph
    expense: Graph
    movement_change: Graph
    read: Graph
    review: Graph
    tracer: Tracer = DISABLED_TRACER
    trace_metadata: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def routing_only(
        cls,
        router: Graph,
        *,
        tracer: Tracer = DISABLED_TRACER,
        trace_metadata: Mapping[str, object] | None = None,
    ) -> WorkflowDispatcher:
        """Build the same entry point for diagnostics without finance access.

        Args:
            router: Compiled root router.
            tracer: Tracing port injected by the entry point.
            trace_metadata: Versions in force for the router assembly.

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
            tracer=tracer,
            trace_metadata=dict(trace_metadata or {}),
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

        # The source reference is the work unit's own identity, not owner
        # content, so it is what correlates every record a dispatch produces.
        record = _DispatchRecord()
        with (
            log_context(source_reference=message.source_reference),
            self.tracer.span(
                "dispatch",
                input=trace_work_unit(message, context),
                metadata=self.trace_metadata,
            ) as span,
        ):
            try:
                outcome = self._dispatch(message, context, record)
                record.facts.update(_outcome_metadata(outcome))
                span.record_output(outcome_facts(outcome))
                return outcome
            finally:
                # Recorded on failure too: what routing decided before a
                # specialist failed is exactly what explains the failure.
                if self.tracer.enabled:
                    span.record_metadata(record.metadata())

    def _dispatch(
        self,
        message: ChannelMessage,
        context: DispatchContext,
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Route and dispatch one message inside an established log context.

        Args:
            message: Channel-neutral work unit.
            context: Trusted owner scope and correlation facts.
            record: Decision facts and threads collected for the trace.

        Returns:
            One compact result suitable for a durable gateway boundary.

        Raises:
            RuntimeError: If a graph violates its state contract.
        """

        with _LOGGER.timed("routing") as routing_outcome:
            routed_state = cast(
                RouterState,
                self.router.invoke(
                    initial_router_state(message, context=context.routing),
                    self._graph_config("router", message.source_reference, record),
                ),
            )
            decision = routing_decision_from_state(routed_state)
            if decision is None:
                _LOGGER.error("routing.no_decision")
                raise RuntimeError("router reached no decision")
            # Intent, confidence, and a stable reason code are routing
            # telemetry by design; the message they were derived from is not.
            routing_outcome["intent"] = decision.intent.value
            routing_outcome["reason_code"] = decision.reason.value
            routing_outcome["confidence"] = decision.confidence
        record.facts.update(_routing_metadata(decision))
        with log_context(intent=decision.intent.value):
            if decision.intent is WorkflowIntent.CLARIFICATION:
                _LOGGER.info(
                    "dispatch.clarification", reason_code=decision.reason.value
                )
                return DispatchOutcome(
                    routing=decision,
                    status=DispatchStatus.CLARIFICATION,
                    clarification_reason=decision.reason.value,
                )
            if context.profile_id is None:
                _LOGGER.debug("dispatch.routing_only")
                return DispatchOutcome(routing=decision, status=DispatchStatus.ROUTED)
            return self._specialist(message, context, decision, record)

    def _specialist(
        self,
        message: ChannelMessage,
        context: DispatchContext,
        decision: RoutingDecision,
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Send an accepted route to the one specialist that serves it.

        Args:
            message: Channel-neutral work unit.
            context: Trusted owner scope, with a profile already established.
            decision: Accepted routing decision.
            record: Decision facts and threads collected for the trace.

        Returns:
            The specialist's typed outcome.
        """

        assert context.profile_id is not None
        with log_context(profile_id=context.profile_id):
            if decision.intent in {WorkflowIntent.EXPENSE, WorkflowIntent.REFUND}:
                return self._dispatch_expense(
                    message, context.profile_id, decision, record
                )
            if decision.intent in {WorkflowIntent.QUERY, WorkflowIntent.ADVICE}:
                return self._dispatch_read(
                    message, context.profile_id, decision, record
                )
            if decision.intent in {
                WorkflowIntent.CORRECTION,
                WorkflowIntent.UNDO,
            }:
                return self._dispatch_movement_change(
                    message, context, decision, record
                )
            if decision.intent is WorkflowIntent.CATEGORY_FOLLOW_UP:
                return self._dispatch_review(message, context, decision, record)
            _LOGGER.warning("dispatch.no_specialist")
            return DispatchOutcome(routing=decision, status=DispatchStatus.ROUTED)

    def _dispatch_expense(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Run a movement creation specialist and open any resulting review."""

        record.facts["specialist"] = "expense"
        with _LOGGER.timed("workflow.expense") as timing:
            final = self._invoke_expense(message, profile_id, decision, record)
            timing["outcome"] = final.get("outcome")
        record.facts.update(_movement_resolution_metadata(final))
        return self._expense_outcome(message, profile_id, decision, final, record)

    def _invoke_expense(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
        record: _DispatchRecord,
    ) -> ExpenseWorkflowState:
        """Run the movement creation graph for one routed message.

        Args:
            message: Channel-neutral work unit.
            profile_id: Trusted owner scope.
            decision: Accepted routing decision.
            record: Decision facts and threads collected for the trace.

        Returns:
            The graph's final state.
        """

        return cast(
            ExpenseWorkflowState,
            self.expense.invoke(
                initial_expense_state(
                    message,
                    profile_id=profile_id,
                    routed_intent=decision.intent,
                ),
                self._graph_config("expense", message.source_reference, record),
            ),
        )

    def _expense_outcome(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
        final: ExpenseWorkflowState,
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Open any review the movement opened and build the typed outcome.

        Args:
            message: Channel-neutral work unit.
            profile_id: Trusted owner scope.
            decision: Accepted routing decision.
            final: Final expense graph state.
            record: Decision facts and threads collected for the trace.

        Returns:
            The dispatch outcome for this movement.
        """

        status = DispatchStatus(_required(final.get("outcome"), "outcome"))
        expense_id = _optional_uuid(final.get("expense_id"))
        proposal_id = _optional_uuid(final.get("proposal_id"))
        if (
            status is DispatchStatus.AWAITING_CATEGORY_APPROVAL
            and expense_id is not None
            and proposal_id is not None
        ):
            _LOGGER.info(
                "workflow.review.opened",
                expense_id=expense_id,
                proposal_id=proposal_id,
            )
            record.facts["review_opened"] = True
            self.review.invoke(
                initial_review_state(
                    profile_id=profile_id,
                    proposal_id=proposal_id,
                    expense_id=expense_id,
                    source_reference=message.source_reference,
                ),
                self._graph_config("category-review", str(proposal_id), record),
            )
        _LOGGER.info(
            "workflow.expense.settled",
            outcome=status.value,
            expense_id=expense_id,
            proposal_id=proposal_id,
            clarification_reason=final.get("clarification_reason"),
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
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Run one scoped read-only specialist."""

        record.facts["specialist"] = "read"
        with _LOGGER.timed("workflow.read") as timing:
            final = self._invoke_read(message, profile_id, decision, record)
            timing["outcome"] = final.get("outcome")
            timing["executed_tool"] = final.get("executed_tool")
        record.facts.update(read_decision_metadata(decision.intent, final))
        return self._read_outcome(decision, final)

    def _invoke_read(
        self,
        message: ChannelMessage,
        profile_id: UUID,
        decision: RoutingDecision,
        record: _DispatchRecord,
    ) -> ReadWorkflowState:
        """Run the read-only graph for one routed question.

        Args:
            message: Channel-neutral work unit.
            profile_id: Trusted owner scope.
            decision: Accepted routing decision.
            record: Decision facts and threads collected for the trace.

        Returns:
            The graph's final state.
        """

        return cast(
            ReadWorkflowState,
            self.read.invoke(
                initial_read_state(
                    message,
                    profile_id=profile_id,
                    routed_intent=decision.intent,
                ),
                self._graph_config("read", message.source_reference, record),
            ),
        )

    def _read_outcome(
        self,
        decision: RoutingDecision,
        final: ReadWorkflowState,
    ) -> DispatchOutcome:
        """Build the typed outcome for one completed read workflow.

        Args:
            decision: Accepted routing decision.
            final: Final read graph state.

        Returns:
            The dispatch outcome, carrying grounded facts when there are any.
        """

        facts_json = final.get("answer_json")
        facts = (
            None if facts_json is None else cast(dict[str, Any], json.loads(facts_json))
        )
        tool_name = final.get("executed_tool")
        # The answer itself is owner-facing content. Whether the workflow
        # grounded it in a tool result is the part a journal needs.
        _LOGGER.debug(
            "workflow.read.settled",
            executed_tool=tool_name,
            grounded=facts is not None,
        )
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
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Run correction or undo against a trusted movement reference."""

        record.facts["specialist"] = "movement-change"
        target = decision.target_movement_id
        record.facts["movement_target_basis"] = "routing"
        if target is None and decision.pending_action_id is not None:
            target = context.movement_for_action(decision.pending_action_id)
            record.facts["movement_target_basis"] = "pending_action"
        if target is None:
            record.facts["movement_target_basis"] = "unresolved"
            _LOGGER.info(
                "workflow.movement_change.unresolved",
                reason_code=DispatchReason.MOVEMENT_TARGET_REQUIRED.value,
            )
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
                self._graph_config("movement-change", message.source_reference, record),
            ),
        )
        record.facts["movement_target_id"] = str(target)
        record.facts.update(_movement_resolution_metadata(final))
        status = DispatchStatus(_required(final.get("outcome"), "outcome"))
        _LOGGER.info(
            "workflow.movement_change.settled",
            outcome=status.value,
            target_movement_id=target,
            clarification_reason=final.get("clarification_reason"),
        )
        return DispatchOutcome(
            routing=decision,
            status=status,
            expense_id=_optional_uuid(final.get("expense_id")),
            clarification_reason=final.get("clarification_reason"),
        )

    def _dispatch_review(
        self,
        message: ChannelMessage,
        context: DispatchContext,
        decision: RoutingDecision,
        record: _DispatchRecord,
    ) -> DispatchOutcome:
        """Resume the exact persistent review named by routing acceptance."""

        record.facts["specialist"] = "category-review"
        proposal_id = decision.pending_action_id
        record.facts["review_proposal_id"] = _optional_text(proposal_id)
        if (
            proposal_id is None
            or proposal_id not in context.routing.pending_category_review_ids
        ):
            # A stale button is the ordinary cause here: the review the owner
            # tapped has already settled, so the decision has nowhere to land.
            _LOGGER.info(
                "workflow.review.not_pending",
                proposal_id=proposal_id,
                reason_code=DispatchReason.REVIEW_NOT_PENDING.value,
            )
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.CLARIFICATION,
                clarification_reason=DispatchReason.REVIEW_NOT_PENDING.value,
            )
        review_decision = review_decision_from_message(message)
        record.facts["review_decision_action"] = (
            None if review_decision is None else review_decision.action.value
        )
        if review_decision is None:
            _LOGGER.info(
                "workflow.review.decision_required",
                proposal_id=proposal_id,
                reason_code=DispatchReason.REVIEW_DECISION_REQUIRED.value,
            )
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
                self._graph_config("category-review", str(proposal_id), record),
            ),
        )
        record.facts["review_resumed"] = True
        outcome = final.get("outcome")
        current_proposal = UUID(_required(final.get("proposal_id"), "proposal_id"))
        if outcome is None:
            _LOGGER.info("workflow.review.still_open", proposal_id=current_proposal)
            return DispatchOutcome(
                routing=decision,
                status=DispatchStatus.AWAITING_CATEGORY_APPROVAL,
                proposal_id=current_proposal,
                clarification_reason=final.get("clarification_reason"),
            )
        _LOGGER.info(
            "workflow.review.settled",
            outcome=outcome,
            proposal_id=current_proposal,
            settled_category_id=final.get("settled_category_id"),
        )
        return DispatchOutcome(
            routing=decision,
            status=DispatchStatus(outcome),
            proposal_id=current_proposal,
            # A settled review posts the movement, and the owner is shown that
            # movement rather than the decision. Dropping the reference here
            # left the renderer nothing to summarize, so every approval and
            # every reuse committed its finance transaction and then failed
            # while rendering: the category existed, the movement was posted,
            # and the owner was told nothing.
            expense_id=_optional_uuid(final.get("expense_id")),
            settled_category_id=_optional_uuid(final.get("settled_category_id")),
            clarification_reason=final.get("clarification_reason"),
        )

    def _graph_config(
        self, namespace: str, reference: str, record: _DispatchRecord
    ) -> dict[str, Any]:
        """Build one graph invocation's configuration.

        Args:
            namespace: Stable graph namespace.
            reference: Durable reference the thread is keyed by.
            record: Collector that remembers the thread for the trace.

        Returns:
            The stable thread identifier plus whatever non-persisted keys the
            tracer contributes, which is nothing when tracing is disabled.
        """

        config = _thread_config(namespace, reference)
        record.thread_ids.append(config["configurable"]["thread_id"])
        return {**config, **self.tracer.graph_config(graph=namespace)}


@dataclass(slots=True)
class _DispatchRecord:
    """Decision facts and graph threads one dispatch collects for its trace.

    Attributes:
        facts: Flat JSON-compatible decision metadata keyed by stable names.
        thread_ids: LangGraph thread identifiers invoked, in order.
    """

    facts: dict[str, object] = field(default_factory=dict)
    thread_ids: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, object]:
        """Return the facts worth recording, with the threads that were used."""

        recorded = {
            key: value for key, value in self.facts.items() if value is not None
        }
        recorded["thread_ids"] = list(self.thread_ids)
        return recorded


class _UnavailableSpecialist:
    """Fail closed if routing-only assembly reaches finance unexpectedly."""

    def invoke(self, value: object, config: object) -> object:
        """Reject an invocation that profile gating should make unreachable."""

        _LOGGER.critical("dispatch.specialist.unavailable")
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
    tracer: Tracer = DISABLED_TRACER,
    trace_metadata: Mapping[str, object] | None = None,
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
        tracer: Tracing port injected by the entry point.
        trace_metadata: Versions in force for this assembly, recorded on every
            dispatch span.

    Returns:
        A dispatcher whose ``dispatch`` method is the sole channel-facing call.
    """

    return WorkflowDispatcher(
        tracer=tracer,
        trace_metadata=dict(trace_metadata or {}),
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


def trace_work_unit(
    message: ChannelMessage, context: DispatchContext
) -> dict[str, Any]:
    """Render a work unit and its trusted context as JSON primitives.

    The trace store holds the complete message by decision, so nothing here is
    summarized. Primitives are produced up front because the store's redaction
    walks JSON structures and would pass an opaque object through untouched.

    Args:
        message: Channel-neutral work unit.
        context: Trusted owner scope and correlations.

    Returns:
        The message and context as nested JSON-compatible mappings.
    """

    return {
        "message": {
            "source_reference": message.source_reference,
            "received_at": message.received_at.isoformat(),
            "text": message.text,
            "callback_action": (
                None
                if message.callback_action is None
                else message.callback_action.value
            ),
            "pending_action_id": _optional_text(message.pending_action_id),
            "reply_to_action_id": _optional_text(message.reply_to_action_id),
            "has_image": message.has_image,
        },
        "context": {
            "profile_id": _optional_text(context.profile_id),
            "pending_category_review_ids": [
                str(item) for item in context.routing.pending_category_review_ids
            ],
            "latest_correctable_movement_id": _optional_text(
                context.routing.latest_correctable_movement_id
            ),
            "movement_actions": [
                {"action_id": str(item.action_id), "movement_id": str(item.movement_id)}
                for item in context.movement_actions
            ],
        },
    }


def _routing_metadata(decision: RoutingDecision) -> dict[str, object]:
    """Describe a routing decision as flat, filterable trace metadata.

    Args:
        decision: Accepted or clarified routing decision.

    Returns:
        Intent, source, stable reason code, confidence, and whether
        deterministic policy overruled a valid model classification.
    """

    return {
        "routing_intent": decision.intent.value,
        "routing_source": decision.source.value,
        "routing_reason_code": decision.reason.value,
        "routing_confidence": decision.confidence,
        "routing_policy_veto": decision.reason in POLICY_VETO_REASONS,
        "routing_command": None if decision.command is None else decision.command.value,
        "routing_callback_action": (
            None if decision.callback_action is None else decision.callback_action.value
        ),
    }


def _outcome_metadata(outcome: DispatchOutcome) -> dict[str, object]:
    """Describe how a dispatch ended as flat, filterable trace metadata."""

    return {
        "outcome_status": outcome.status.value,
        "outcome_clarification_reason": outcome.clarification_reason,
    }


def _movement_resolution_metadata(final: Mapping[str, Any]) -> dict[str, object]:
    """Describe how a movement specialist resolved its model answers.

    The extraction and purpose judgment are the converted domain contracts the
    deterministic side acted on. The funding basis says whether the owner named
    the payment or the profile default applied, and the category basis whether
    an existing node fit or a proposal was opened.

    Args:
        final: Final expense or movement-change graph state.

    Returns:
        Resolution facts; keys whose value is unknown are omitted.
    """

    extraction = _json_object(final.get("extraction_json"))
    fit = _json_object(final.get("category_fit_json"))
    facts: dict[str, object] = {
        "resolution_reason": final.get("clarification_reason"),
        "extraction": extraction,
        "category_fit": fit,
    }
    if extraction is not None:
        parts = extraction.get("payment_parts")
        count = len(parts) if isinstance(parts, list) else 0
        facts["allocation_funding_basis"] = (
            "profile_default"
            if count == 0
            else "stated_single"
            if count == 1
            else "stated_split"
        )
    if fit is not None:
        facts["allocation_category_basis"] = fit.get("outcome")
    return facts


def read_decision_metadata(
    intent: WorkflowIntent, final: Mapping[str, Any]
) -> dict[str, object]:
    """Describe a read specialist's admitted tools and grounded result.

    Shared by every entry point that runs the read graph, so a question asked
    through Telegram and one asked in the browser are filterable by the same
    keys.

    Args:
        intent: Trusted routed intent whose registry bounded the model.
        final: Final read graph state.

    Returns:
        The route's admitted tools, the request that passed the selection
        policy, the executed tool, and whether its answer is grounded in tool
        output.
    """

    return {
        "read_admitted_tools": sorted(tool.value for tool in registry_for(intent)),
        "read_tool_request": _json_object(final.get("tool_request_json")),
        "read_executed_tool": final.get("executed_tool"),
        "read_grounded": final.get("answer_json") is not None,
        "read_clarification_reason": final.get("clarification_reason"),
    }


def _json_object(value: object) -> dict[str, Any] | None:
    """Decode a JSON object carried in graph state, or None when absent."""

    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _optional_text(value: UUID | None) -> str | None:
    """Render an optional identifier as text."""

    return None if value is None else str(value)


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


__all__ = [
    "WorkflowDispatcher",
    "build_workflow_dispatcher",
    "read_decision_metadata",
    "trace_work_unit",
]
