"""LangGraph assembly of the paused owner category review.

This is the first workflow that deliberately stops and waits for a human. The
topology is a two-node loop: one node asks and pauses, the other applies what
the owner answered and decides whether the review is over.

The split is the whole point. LangGraph replays an interrupted node from its
start when the thread resumes, so anything before `interrupt` runs again. Only
reads live in the asking node; every write lives in the settling node, which
runs exactly once per resume. Putting both in one node would look simpler and
would replay a settlement the second time around the loop, because a re-proposal
pauses again and the cached first interrupt would return immediately.

Replay safety does not rest on that alone. The application service checks the
durable command record of the proposal before any mutable review state, so an
identical resumed decision returns its first result instead of deciding twice.
A second branch on the same proposal is refused, and the settling handler
answers it with the same already-settled question a review settled elsewhere
receives: from the owner's side the proposal already has its decision either
way.

The re-proposal branch re-invokes the purpose boundary with every candidate the
owner has already refused. It classifies the stored movement's own description
rather than the original owner message: the description is what the movement
actually records, and a review may wait indefinitely, so the paused checkpoint
holds no raw owner text it does not need.

That branch handles its own taxonomy refusals rather than leaving them to the
settling handler. Both branches raise `TaxonomyError`, but it means different
things in each: from reuse the owner named a category that does not exist, and
from re-proposal the replacement candidate was refused by governance. The reason
code selects which question the owner is asked, so the two cannot share one.

State stays small and serializable. The proposal, the movement, and the taxonomy
live in repositories and are referenced here by identifier, because a review may
outlive many application restarts.
"""

from __future__ import annotations

from typing import Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from money_tracker.application import (
    CategoryReviewService,
    IdempotencyConflictError,
)
from money_tracker.domain import (
    CategoryProposal,
    CategoryStatus,
    Expense,
    Taxonomy,
    TaxonomyError,
)
from money_tracker.workflows import (
    CallbackAction,
    CategoryFit,
    CategoryFitOutcome,
    ReviewDecision,
    ReviewOutcome,
    ReviewQuestion,
    ReviewReason,
    ReviewWorkflowState,
    StructuredOutputError,
    StructuredOutputModel,
    parse_with_one_repair,
    render_active_categories,
)
from money_tracker.workflows.candidates import CategoryFitCandidate

ASK_NODE = "ask_owner"
SETTLE_NODE = "settle_review"


class ReviewConfigurationReader(Protocol):
    """Trusted reads the review needs from the owner's stored state."""

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the profile's complete validated taxonomy."""

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Return the movement the review is holding."""

    def get_category_proposal(
        self, profile_id: UUID, proposal_id: UUID
    ) -> CategoryProposal:
        """Return the candidate currently awaiting the owner."""


def build_review_graph(
    *,
    model: StructuredOutputModel,
    configuration: ReviewConfigurationReader,
    review: CategoryReviewService,
    category_instructions: str,
    category_candidate: CategoryFitCandidate | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[
    ReviewWorkflowState, None, ReviewWorkflowState, ReviewWorkflowState
]:
    """Compile the owner category review.

    The instruction text is a required argument with no default, for the same
    reason the other assemblies require theirs: no prompt is promoted, so the
    assembly must not imply a production configuration.

    Args:
        model: Narrow structured-output port for the purpose boundary.
        configuration: Trusted taxonomy, movement, and proposal reader.
        review: Application service owning every owner decision.
        category_instructions: Candidate text for the purpose boundary.
        category_candidate: Schema, conversion and taxonomy rendering paired
            with that text; None retains the explicit legacy flat boundary.
        checkpointer: Persistent checkpointer, required in practice because a
            paused review outlives the process that opened it.

    Returns:
        A compiled graph that pauses on a candidate and ends when the owner
        approves, reuses, or cancels.
    """

    if category_candidate and category_instructions != category_candidate.instructions:
        raise ValueError("category instructions do not match their candidate")

    def _question(state: ReviewWorkflowState) -> ReviewQuestion:
        """Render what the owner is shown, from the authoritative proposal."""

        profile_id = UUID(state["profile_id"])
        proposal_id = UUID(state["proposal_id"])
        proposal = configuration.get_category_proposal(profile_id, proposal_id)
        taxonomy = configuration.get_taxonomy(profile_id)
        parent = (
            None
            if proposal.parent_id is None
            else taxonomy.category(proposal.parent_id).name
        )
        return ReviewQuestion(
            proposal_id=str(proposal.id),
            candidate_name=proposal.proposed_name,
            parent_name=parent,
            rationale=proposal.rationale,
            alternative_names=tuple(
                taxonomy.category(identifier).name
                for identifier in proposal.alternative_category_ids
            ),
            can_repropose=not review.exhausted(
                profile_id=profile_id, proposal_id=proposal_id
            ),
        )

    def run_ask(state: ReviewWorkflowState) -> ReviewWorkflowState:
        """Present the candidate and pause until the owner decides.

        Only reads happen before `interrupt`, so a resumed thread repeating
        them changes nothing. The decision is carried into state as its two
        primitive parts rather than as an object, keeping the checkpoint within
        the state contract.
        """

        answer = interrupt(_question(state))
        decision = _decision_from(answer)
        return ReviewWorkflowState(
            decision_action=decision.action.value,
            decision_category_name=decision.category_name,
            clarification_reason=None,
        )

    def run_settle(state: ReviewWorkflowState) -> ReviewWorkflowState:
        """Apply the owner's decision, or record why it could not be applied."""

        profile_id = UUID(state["profile_id"])
        proposal_id = UUID(state["proposal_id"])
        expense_id = UUID(state["expense_id"])
        action = CallbackAction(_required(state, "decision_action"))
        try:
            if action is CallbackAction.APPROVE_CATEGORY:
                posted = review.approve(
                    profile_id=profile_id,
                    proposal_id=proposal_id,
                    expense_id=expense_id,
                    actor="owner",
                    reason="owner approved the proposed category",
                )
                return _settled(ReviewOutcome.APPROVED, posted)
            if action is CallbackAction.CANCEL_REVIEW:
                review.cancel(
                    profile_id=profile_id,
                    proposal_id=proposal_id,
                    expense_id=expense_id,
                    actor="owner",
                    reason="owner cancelled the category review",
                )
                return ReviewWorkflowState(
                    outcome=ReviewOutcome.CANCELLED.value,
                    settled_category_id=None,
                    clarification_reason=None,
                )
            if action is CallbackAction.REPROPOSE_CATEGORY:
                return _repropose(state, profile_id, proposal_id, expense_id)
            name = _reuse_name(state, action, profile_id, proposal_id)
            if name is None:
                return _pending(ReviewReason.NO_BEST_EXISTING)
            posted = review.use_existing(
                profile_id=profile_id,
                proposal_id=proposal_id,
                expense_id=expense_id,
                category_name=name,
                actor="owner",
                reason="owner chose an existing category",
            )
            return _settled(ReviewOutcome.REUSED, posted)
        except TaxonomyError:
            return _pending(ReviewReason.UNKNOWN_CATEGORY)
        except (IdempotencyConflictError, ValueError):
            # A conflicting command key and a review that is no longer pending
            # are the same event seen from two sides: the proposal already has
            # its decision. The owner is owed one answer, not two, and neither
            # case may reach a second finance write.
            return ReviewWorkflowState(
                outcome=ReviewOutcome.CLARIFICATION.value,
                clarification_reason=ReviewReason.ALREADY_SETTLED.value,
            )

    def _reuse_name(
        state: ReviewWorkflowState,
        action: CallbackAction,
        profile_id: UUID,
        proposal_id: UUID,
    ) -> str | None:
        """Return the category name the chosen reuse branch resolves against."""

        if action is CallbackAction.USE_NAMED_EXISTING:
            return _required(state, "decision_category_name")
        proposal = configuration.get_category_proposal(profile_id, proposal_id)
        if not proposal.alternative_category_ids:
            return None
        taxonomy = configuration.get_taxonomy(profile_id)
        return taxonomy.category(proposal.alternative_category_ids[0]).name

    def _repropose(
        state: ReviewWorkflowState,
        profile_id: UUID,
        proposal_id: UUID,
        expense_id: UUID,
    ) -> ReviewWorkflowState:
        """Ask the purpose boundary for a candidate the owner has not refused."""

        if review.exhausted(profile_id=profile_id, proposal_id=proposal_id):
            return _pending(ReviewReason.PROPOSALS_EXHAUSTED)
        taxonomy = configuration.get_taxonomy(profile_id)
        active = tuple(
            category.name
            for category in taxonomy.categories
            if category.status is CategoryStatus.ACTIVE
        )
        movement = configuration.get_expense(profile_id, expense_id)
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
                message=movement.description,
            )
            fit = (
                category_candidate.convert(parsed)
                if category_candidate
                else cast(CategoryFit, parsed)
            )
        except StructuredOutputError:
            return _pending(ReviewReason.INVALID_CANDIDATE)
        if (
            fit.outcome is not CategoryFitOutcome.PROPOSAL
            or fit.proposed_name is None
            or fit.description is None
            or fit.rationale is None
        ):
            return _pending(ReviewReason.INVALID_CANDIDATE)
        try:
            successor = review.repropose(
                profile_id=profile_id,
                proposal_id=proposal_id,
                proposed_name=fit.proposed_name,
                description=fit.description,
                parent_name=fit.parent_name,
                rationale=fit.rationale,
                actor="owner",
                reason="owner asked for a different candidate",
            )
        except TaxonomyError:
            # Taxonomy governance refusing a replacement is not the owner
            # naming a category that does not exist. It means the candidate
            # duplicates an active node, repeats one already rejected in this
            # review, or hangs under something that is not an active root. The
            # owner is owed "that name will not work", not "which category did
            # you mean", and the settling handler below cannot tell the two
            # apart because both branches raise the same error type.
            return _pending(ReviewReason.INVALID_CANDIDATE)
        return ReviewWorkflowState(
            proposal_id=str(successor.id),
            outcome=None,
            clarification_reason=None,
        )

    def continue_after_settlement(state: ReviewWorkflowState) -> str:
        """Pause again while the review is still open, otherwise end."""

        return END if state.get("outcome") is not None else ASK_NODE

    builder: StateGraph[
        ReviewWorkflowState, None, ReviewWorkflowState, ReviewWorkflowState
    ] = StateGraph(ReviewWorkflowState)
    builder.add_node(ASK_NODE, run_ask)
    builder.add_node(SETTLE_NODE, run_settle)
    builder.add_edge(START, ASK_NODE)
    builder.add_edge(ASK_NODE, SETTLE_NODE)
    builder.add_conditional_edges(
        SETTLE_NODE,
        continue_after_settlement,
        {ASK_NODE: ASK_NODE, END: END},
    )
    return builder.compile(checkpointer=checkpointer)


def _decision_from(answer: object) -> ReviewDecision:
    """Interpret a resume payload as one owner decision.

    A channel resumes with either the contract itself or its two primitive
    parts. Both are accepted, and anything else is refused rather than guessed,
    because a resume payload arrives from outside the graph.

    Args:
        answer: Value the thread was resumed with.

    Returns:
        The validated decision.

    Raises:
        ValueError: If the payload is not a recognizable decision.
    """

    if isinstance(answer, ReviewDecision):
        return answer
    if isinstance(answer, dict):
        action = answer.get("action")
        name = answer.get("category_name")
        if isinstance(action, str) and (name is None or isinstance(name, str)):
            return ReviewDecision(CallbackAction(action), name)
    raise ValueError("review resumed without a recognizable owner decision")


def _settled(outcome: ReviewOutcome, posted: Expense) -> ReviewWorkflowState:
    """Record a review that ended by assigning the movement a purpose."""

    return ReviewWorkflowState(
        outcome=outcome.value,
        settled_category_id=str(posted.category_allocations[0].category_id),
        clarification_reason=None,
    )


def _pending(reason: ReviewReason) -> ReviewWorkflowState:
    """Record a decision that could not be applied, keeping the review open."""

    return ReviewWorkflowState(outcome=None, clarification_reason=reason.value)


def _required(state: ReviewWorkflowState, field: str) -> str:
    """Read one state value a node cannot proceed without.

    Args:
        state: Checkpointed review state.
        field: Key to read.

    Returns:
        The stored value.

    Raises:
        KeyError: If the value is absent, which would mean the graph reached a
            node its own edges should have made unreachable.
    """

    value = state.get(field)
    if not isinstance(value, str):
        raise KeyError(f"review state is missing {field}")
    return value


__all__ = ["ASK_NODE", "SETTLE_NODE", "ReviewConfigurationReader", "build_review_graph"]
