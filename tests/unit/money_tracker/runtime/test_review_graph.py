"""Tests for the compiled owner category review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from money_tracker.application import CategoryReviewService
from money_tracker.application.runtime import SystemClock
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    Category,
    CategoryProposal,
    CategoryProposalStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Taxonomy,
    initial_taxonomy,
)
from money_tracker.runtime import ASK_NODE, build_review_graph
from money_tracker.workflows import (
    CATEGORY_FIT,
    CallbackAction,
    ReviewDecision,
    ReviewOutcome,
    ReviewReason,
    default_prompt,
    initial_review_state,
)

PROFILE_ID = uuid4()
OCCURRED_AT = datetime(2026, 9, 3, 12, tzinfo=UTC)
CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE_ID,
    name="Carta",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    is_default=True,
)


@dataclass
class ScriptedModel:
    """A real port implementation replaying a fixed script of completions."""

    completions: list[str] = field(default_factory=list)
    calls: int = 0

    def complete_json(
        self,
        *,
        instructions: str,
        message: str,
        schema: type[BaseModel],
        repair: Any = None,
    ) -> str:
        """Return the next scripted completion and count the request."""

        self.calls += 1
        return self.completions[self.calls - 1]


@dataclass
class InMemoryStore:
    """A lightweight real implementation of every port the review uses."""

    taxonomy: Taxonomy = field(default_factory=initial_taxonomy)
    expenses: dict[UUID, Expense] = field(default_factory=dict)
    proposals: dict[UUID, CategoryProposal] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    settlements: int = 0

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the current taxonomy view."""

        return self.taxonomy

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return the one configured source these tests use."""

        return (CARD,)

    def get_expense(self, profile_id: UUID, expense_id: UUID) -> Expense:
        """Return the movement the review is holding."""

        return self.expenses[expense_id]

    def get_category_proposal(
        self, profile_id: UUID, proposal_id: UUID
    ) -> CategoryProposal:
        """Return one proposal in its profile scope."""

        return self.proposals[proposal_id]

    def get_pending_category_proposals(
        self, profile_id: UUID
    ) -> tuple[CategoryProposal, ...]:
        """Return every open review."""

        return tuple(
            item
            for item in self.proposals.values()
            if item.status is CategoryProposalStatus.PENDING
        )

    def settle_category_review(
        self,
        proposal: CategoryProposal,
        *,
        expense: Expense,
        expected_version: int,
        category: Category | None,
        proposal_audit: AuditEvent,
        expense_audit: AuditEvent,
    ) -> None:
        """Apply the whole settlement or none of it."""

        if self.proposals[proposal.id].status is not CategoryProposalStatus.PENDING:
            raise ValueError("category review is no longer pending")
        if category is not None:
            self.taxonomy = self.taxonomy.with_category(category)
        self.proposals[proposal.id] = proposal
        self.expenses[expense.id] = expense
        self.audits.extend((proposal_audit, expense_audit))
        self.settlements += 1

    def repropose_category(
        self,
        rejected: CategoryProposal,
        successor: CategoryProposal,
        *,
        rejected_audit: AuditEvent,
        successor_audit: AuditEvent,
    ) -> None:
        """Refuse one candidate and open its replacement together."""

        if self.proposals[rejected.id].status is not CategoryProposalStatus.PENDING:
            raise ValueError("category review is no longer pending")
        self.proposals[rejected.id] = rejected
        self.proposals[successor.id] = successor
        self.audits.extend((rejected_audit, successor_audit))
        self.settlements += 1


class SequentialIds:
    """Deterministic identifier provider."""

    def new(self) -> UUID:
        """Return a fresh identifier."""

        return uuid4()


def _fit(**overrides: Any) -> str:
    """Render one schema-valid proposal completion."""

    payload: dict[str, Any] = {
        "outcome": "proposal",
        "proposed_name": "Piante e fiori",
        "parent_name": "Casa e utenze",
        "description": "Piante, fiori e terriccio.",
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "considered_category_names": [],
        "evidence": "piante",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _draft() -> Expense:
    """Build one stored draft whose purpose dimension is empty."""

    return Expense(
        id=uuid4(),
        profile_id=PROFILE_ID,
        amount=Money(1200),
        occurred_at=OCCURRED_AT,
        description="croccantini per i gatti",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=CARD.id, amount=Money(1200)),
        ),
        source_reference="channel:1",
        idempotency_key="key:1",
    )


def _proposal(**overrides: Any) -> CategoryProposal:
    """Build one pending proposal with explicit overrides."""

    fields: dict[str, Any] = {
        "id": uuid4(),
        "profile_id": PROFILE_ID,
        "proposed_name": "Giardinaggio",
        "description": "Attrezzi e materiali per il giardino.",
        "parent_id": None,
        "rationale": "Nessuna categoria attiva descrive lo scopo.",
        "alternative_category_ids": (),
        "excluded_names": (),
        "source_reference": "channel:1",
    }
    fields.update(overrides)
    return CategoryProposal(**fields)


def _graph(
    store: InMemoryStore, completions: list[str] | None = None
) -> tuple[Any, dict[str, Any]]:
    """Compile the review over a real checkpointer and a fresh thread."""

    compiled = build_review_graph(
        model=ScriptedModel(completions or []),
        configuration=store,
        review=CategoryReviewService(
            store, store, clock=SystemClock(), ids=SequentialIds()
        ),
        category_instructions=default_prompt(CATEGORY_FIT).text,
        checkpointer=InMemorySaver(),
    )
    return compiled, {"configurable": {"thread_id": str(uuid4())}}


def _start(
    store: InMemoryStore,
    draft: Expense,
    proposal: CategoryProposal,
    completions: list[str] | None = None,
) -> tuple[Any, dict[str, Any], Any]:
    """Run the review until it pauses on its first question."""

    compiled, config = _graph(store, completions)
    paused = compiled.invoke(
        initial_review_state(
            profile_id=PROFILE_ID,
            proposal_id=proposal.id,
            expense_id=draft.id,
            source_reference=draft.source_reference,
        ),
        config,
    )
    return compiled, config, paused


def _fixture(
    completions: list[str] | None = None, **proposal_overrides: Any
) -> tuple[InMemoryStore, Any, dict[str, Any], Expense, CategoryProposal, Any]:
    """Seed one draft, one pending review, and pause the graph on it."""

    draft = _draft()
    proposal = _proposal(**proposal_overrides)
    store = InMemoryStore(expenses={draft.id: draft}, proposals={proposal.id: proposal})
    compiled, config, paused = _start(store, draft, proposal, completions)
    return store, compiled, config, draft, proposal, paused


def _interrupt(paused: Any) -> Any:
    """Return the single question the paused graph is asking."""

    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    return interrupts[0].value


def test_review_pauses_on_its_candidate_before_writing_anything() -> None:
    """Stop and wait for the owner rather than deciding for them."""

    store, _, _, _, proposal, paused = _fixture()

    question = _interrupt(paused)

    assert question.proposal_id == str(proposal.id)
    assert question.candidate_name == "Giardinaggio"
    assert question.can_repropose is True
    assert store.settlements == 0
    assert store.proposals[proposal.id].status is CategoryProposalStatus.PENDING


def test_question_names_the_parent_and_the_reuse_shortlist() -> None:
    """Name categories and never identify them, as the boundaries do."""

    taxonomy = initial_taxonomy()
    root = taxonomy.resolve_active("Casa e utenze")
    alternative = taxonomy.resolve_active("Cibo e accessori")
    _, _, _, _, _, paused = _fixture(
        parent_id=root.id, alternative_category_ids=(alternative.id,)
    )

    question = _interrupt(paused)

    assert question.parent_name == "Casa e utenze"
    assert question.alternative_names == ("Cibo e accessori",)
    assert CallbackAction.USE_BEST_EXISTING in question.offered_actions()


def test_a_question_without_alternatives_offers_no_shortlist() -> None:
    """Offer the shortlist only when the review examined something to reuse."""

    _, _, _, _, _, paused = _fixture()

    offered = _interrupt(paused).offered_actions()

    assert CallbackAction.USE_BEST_EXISTING not in offered
    assert CallbackAction.USE_NAMED_EXISTING in offered
    assert CallbackAction.APPROVE_CATEGORY in offered
    assert CallbackAction.CANCEL_REVIEW in offered


def test_approval_creates_the_node_and_ends_the_review() -> None:
    """Complete J3's approve branch through the graph."""

    store, compiled, config, draft, proposal, _ = _fixture()

    final = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.APPROVE_CATEGORY)), config
    )

    assert final["outcome"] == ReviewOutcome.APPROVED.value
    created = store.taxonomy.resolve_active("Giardinaggio")
    assert final["settled_category_id"] == str(created.id)
    assert store.expenses[draft.id].status is ExpenseStatus.POSTED
    assert store.proposals[proposal.id].status is CategoryProposalStatus.APPROVED
    assert "__interrupt__" not in final


def test_named_reuse_posts_against_the_owner_typed_category() -> None:
    """Resolve the owner's own words against their active taxonomy."""

    store, compiled, config, draft, proposal, _ = _fixture()

    final = compiled.invoke(
        Command(
            resume=ReviewDecision(CallbackAction.USE_NAMED_EXISTING, "cibo e accessori")
        ),
        config,
    )

    assert final["outcome"] == ReviewOutcome.REUSED.value
    stored = store.expenses[draft.id]
    assert stored.status is ExpenseStatus.POSTED
    assert stored.category_allocations[0].owner_override is True
    assert store.proposals[proposal.id].status is CategoryProposalStatus.REJECTED


def test_best_existing_uses_the_first_examined_alternative() -> None:
    """Reuse what the review already examined rather than asking again."""

    taxonomy = initial_taxonomy()
    alternative = taxonomy.resolve_active("Cibo e accessori")
    store, compiled, config, draft, _, _ = _fixture(
        alternative_category_ids=(alternative.id,)
    )

    final = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.USE_BEST_EXISTING)), config
    )

    assert final["outcome"] == ReviewOutcome.REUSED.value
    assert final["settled_category_id"] == str(alternative.id)
    assert store.expenses[draft.id].category_allocations[0].owner_override is True


def test_cancel_discards_the_draft_and_ends_the_review() -> None:
    """Complete J3's cancel branch with no node and no posted movement."""

    store, compiled, config, draft, proposal, _ = _fixture()
    before = len(store.taxonomy.categories)

    final = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.CANCEL_REVIEW)), config
    )

    assert final["outcome"] == ReviewOutcome.CANCELLED.value
    assert final["settled_category_id"] is None
    assert store.expenses[draft.id].status is ExpenseStatus.DISCARDED
    assert store.proposals[proposal.id].status is CategoryProposalStatus.CANCELLED
    assert len(store.taxonomy.categories) == before


def test_repropose_pauses_again_on_a_distinct_candidate() -> None:
    """Offer another name and keep waiting instead of ending the review."""

    store, compiled, config, draft, proposal, _ = _fixture(completions=[_fit()])

    paused = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    question = _interrupt(paused)
    assert question.candidate_name == "Piante e fiori"
    assert question.proposal_id != str(proposal.id)
    assert store.proposals[proposal.id].status is CategoryProposalStatus.REJECTED
    assert store.expenses[draft.id].status is ExpenseStatus.DRAFT
    successor = store.proposals[UUID(question.proposal_id)]
    assert successor.excluded_names == ("Giardinaggio",)


def test_a_reproposed_candidate_can_then_be_approved() -> None:
    """Carry the loop through to a settlement on the second candidate."""

    store, compiled, config, draft, _, _ = _fixture(completions=[_fit()])
    compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    final = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.APPROVE_CATEGORY)), config
    )

    assert final["outcome"] == ReviewOutcome.APPROVED.value
    assert store.taxonomy.resolve_active("Piante e fiori")
    assert store.expenses[draft.id].status is ExpenseStatus.POSTED
    # The first settlement must not have replayed when the loop paused again.
    assert store.settlements == 2


def test_the_proposal_loop_stops_at_its_cap() -> None:
    """Offer existing categories and manual entry instead of a fourth name."""

    store, compiled, config, _, _, paused = _fixture(
        proposed_name="Terriccio", excluded_names=("Giardinaggio", "Piante e fiori")
    )
    assert _interrupt(paused).can_repropose is False

    again = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    assert again["clarification_reason"] == ReviewReason.PROPOSALS_EXHAUSTED.value
    assert again["outcome"] is None
    assert _interrupt(again).can_repropose is False
    assert store.settlements == 0


def test_an_unknown_named_category_keeps_the_review_open() -> None:
    """Ask again rather than inventing a node from a name that resolves to nothing."""

    store, compiled, config, draft, proposal, _ = _fixture()

    again = compiled.invoke(
        Command(
            resume=ReviewDecision(
                CallbackAction.USE_NAMED_EXISTING, "Categoria inesistente"
            )
        ),
        config,
    )

    assert again["clarification_reason"] == ReviewReason.UNKNOWN_CATEGORY.value
    assert again["outcome"] is None
    assert store.settlements == 0
    assert store.expenses[draft.id].status is ExpenseStatus.DRAFT
    assert store.proposals[proposal.id].status is CategoryProposalStatus.PENDING
    assert _interrupt(again).proposal_id == str(proposal.id)


def test_a_shortlist_choice_without_alternatives_keeps_the_review_open() -> None:
    """Report the missing shortlist instead of picking an arbitrary category."""

    store, compiled, config, _, _, _ = _fixture()

    again = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.USE_BEST_EXISTING)), config
    )

    assert again["clarification_reason"] == ReviewReason.NO_BEST_EXISTING.value
    assert again["outcome"] is None
    assert store.settlements == 0


def test_a_reproposal_that_names_an_existing_category_is_refused() -> None:
    """Require a candidate when the owner asked for one."""

    store, compiled, config, _, _, _ = _fixture(
        completions=[
            json.dumps(
                {
                    "outcome": "existing",
                    "category_name": "Cibo e accessori",
                    "evidence": "croccantini",
                }
            )
        ]
    )

    again = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    assert again["clarification_reason"] == ReviewReason.INVALID_CANDIDATE.value
    assert again["outcome"] is None
    assert store.settlements == 0


def test_an_unusable_model_answer_keeps_the_review_open() -> None:
    """Treat an exhausted retry budget as a question, not a failed review."""

    store, compiled, config, _, _, _ = _fixture(completions=["not json", "still not"])

    again = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    assert again["clarification_reason"] == ReviewReason.INVALID_CANDIDATE.value
    assert store.settlements == 0


def test_a_duplicate_reproposal_keeps_the_review_open() -> None:
    """Refuse a replacement the taxonomy already covers, as an unusable one.

    A candidate duplicating an active node was refused by taxonomy governance,
    which is what `invalid_candidate` means. It is not the owner naming a
    category that does not exist, so it must not report `unknown_category`: the
    reason code picks the question the owner is asked, and the two questions
    are different. This expectation was corrected once the pinned
    `workflow/category-review-it` suite measured the branch against the
    documented reason vocabulary.
    """

    store, compiled, config, _, _, _ = _fixture(
        completions=[_fit(proposed_name="Abbonamenti", parent_name=None)]
    )

    again = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)), config
    )

    assert again["clarification_reason"] == ReviewReason.INVALID_CANDIDATE.value
    assert again["outcome"] is None
    assert store.settlements == 0


def test_a_reused_name_the_owner_typed_still_reports_an_unknown_category() -> None:
    """Keep the reuse branch reporting the refusal that actually happened.

    The two branches raise the same error type, so separating the re-proposal
    case must not silently reclassify this one.
    """

    store, compiled, config, _, _, _ = _fixture()

    again = compiled.invoke(
        Command(
            resume=ReviewDecision(
                CallbackAction.USE_NAMED_EXISTING, "Categoria inesistente"
            )
        ),
        config,
    )

    assert again["clarification_reason"] == ReviewReason.UNKNOWN_CATEGORY.value
    assert again["outcome"] is None
    assert store.settlements == 0


def test_a_review_settled_elsewhere_ends_without_deciding_twice() -> None:
    """Recognize a settled review instead of applying a second decision."""

    store, compiled, config, draft, proposal, _ = _fixture()
    store.proposals[proposal.id] = proposal.cancel()

    final = compiled.invoke(
        Command(resume=ReviewDecision(CallbackAction.APPROVE_CATEGORY)), config
    )

    assert final["outcome"] == ReviewOutcome.CLARIFICATION.value
    assert final["clarification_reason"] == ReviewReason.ALREADY_SETTLED.value
    assert store.settlements == 0
    assert store.expenses[draft.id].status is ExpenseStatus.DRAFT


def test_a_decision_may_arrive_as_its_primitive_parts() -> None:
    """Accept the wire shape a channel actually resumes with."""

    store, compiled, config, draft, _, _ = _fixture()

    final = compiled.invoke(
        Command(
            resume={
                "action": "use_named_existing",
                "category_name": "Cibo e accessori",
            }
        ),
        config,
    )

    assert final["outcome"] == ReviewOutcome.REUSED.value
    assert store.expenses[draft.id].status is ExpenseStatus.POSTED


@pytest.mark.parametrize(
    "payload",
    ["approve_category", {"action": "not_an_action"}, {"category_name": "X"}, 7],
)
def test_an_unrecognizable_resume_payload_is_refused(payload: object) -> None:
    """Refuse a resume value rather than guessing which branch it means.

    A bare action string, an action outside the taxonomy, a payload naming only
    a category, and a non-payload are all refused. `None` is absent because
    LangGraph reads it as "no resume value" rather than as a payload, so it
    never reaches the graph.
    """

    _, compiled, config, _, _, _ = _fixture()

    with pytest.raises(ValueError):
        compiled.invoke(Command(resume=payload), config)


def test_a_paused_review_holds_only_small_serializable_values() -> None:
    """Keep a review that may wait indefinitely inside the state contract."""

    _, compiled, config, _, _, _ = _fixture()

    values = compiled.get_state(config).values

    assert set(values) <= {
        "state_version",
        "profile_id",
        "source_reference",
        "proposal_id",
        "expense_id",
        "decision_action",
        "decision_category_name",
        "outcome",
        "settled_category_id",
        "clarification_reason",
    }
    for value in values.values():
        assert value is None or isinstance(value, str)


def test_the_paused_thread_resumes_at_the_asking_node() -> None:
    """Keep the pause exactly where the owner was asked."""

    _, compiled, config, _, _, _ = _fixture()

    snapshot = compiled.get_state(config)

    assert snapshot.next == (ASK_NODE,)


def test_a_node_reached_without_its_decision_fails_loudly() -> None:
    """Fail loudly if the topology stops guaranteeing the node order.

    The graph's edges make this unreachable today. The guard exists so a future
    change that settles before asking is caught instead of applying a branch
    the owner never chose.
    """

    from money_tracker.runtime.review_graph import _required

    with pytest.raises(KeyError, match="decision_action"):
        _required(
            initial_review_state(
                profile_id=PROFILE_ID,
                proposal_id=uuid4(),
                expense_id=uuid4(),
                source_reference="channel:1",
            ),
            "decision_action",
        )
