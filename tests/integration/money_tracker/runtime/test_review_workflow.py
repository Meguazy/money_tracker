"""Real PostgreSQL tests for the paused owner category review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.types import Command
from psycopg import connect
from pydantic import BaseModel

from money_tracker.application import (
    CategoryProposalService,
    CategoryReviewService,
)
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryProposal,
    CategoryProposalStatus,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import ASK_NODE, build_review_graph
from money_tracker.workflows import (
    CATEGORY_FIT,
    CallbackAction,
    ReviewDecision,
    ReviewOutcome,
    default_prompt,
    initial_review_state,
)
from money_tracker.workflows.resolution import PRODUCT_TIMEZONE


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


def _bootstrap(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, Profile, FundingSource]:
    """Migrate and seed one profile, its taxonomy, and one default source."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    return repository, profile, card


def _audit(expense: Expense) -> AuditEvent:
    """Build the creation audit the repository requires."""

    return AuditEvent(
        id=uuid4(),
        profile_id=expense.profile_id,
        aggregate_type="expense",
        aggregate_id=expense.id,
        action="create",
        actor="test",
        source_reference=expense.source_reference,
        occurred_at=SystemClock().now(),
        prior_version=None,
        current_version=1,
        reason="integration fixture",
    )


def _draft(
    repository: PostgresFinanceRepository,
    profile: Profile,
    card: FundingSource,
) -> Expense:
    """Store one draft whose purpose dimension is deliberately empty."""

    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=SystemClock().now().astimezone(PRODUCT_TIMEZONE),
        description="croccantini per i gatti",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(),
        payment_allocations=(
            PaymentAllocation(funding_source_id=card.id, amount=Money(1200)),
        ),
        source_reference="integration:review:1",
        idempotency_key="integration:review:1",
    )
    return repository.create_expense(
        expense,
        audit_event=_audit(expense),
        request_hash=sha256(b"integration:review:1").hexdigest(),
    )


def _propose(
    repository: PostgresFinanceRepository,
    profile: Profile,
    expense: Expense,
) -> CategoryProposal:
    """Submit one pending candidate against the stored draft."""

    return CategoryProposalService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).submit(
        CategoryProposal(
            id=uuid4(),
            profile_id=profile.id,
            proposed_name="Giardinaggio",
            description="Attrezzi e materiali per il giardino.",
            parent_id=None,
            rationale="Nessuna categoria attiva descrive lo scopo.",
            alternative_category_ids=(),
            excluded_names=(),
            source_reference=expense.source_reference,
        ),
        actor="workflow",
        reason="no active category is adequate",
    )


def _graph(
    repository: PostgresFinanceRepository,
    checkpointer: Any,
    completions: list[str] | None = None,
) -> Any:
    """Compile the review over the real repository and checkpointer."""

    return build_review_graph(
        model=ScriptedModel(completions or []),
        configuration=repository,
        review=CategoryReviewService(
            repository, repository, clock=SystemClock(), ids=UUID4Generator()
        ),
        category_instructions=default_prompt(CATEGORY_FIT).text,
        checkpointer=checkpointer,
    )


def _category_count(postgres_url: str, profile_id: UUID) -> int:
    """Count taxonomy nodes owned by one profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.categories WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])


@pytest.mark.postgres
def test_a_paused_review_survives_separate_connections(postgres_url: str) -> None:
    """Let a review wait across process restarts, which is why it persists."""

    repository, profile, card = _bootstrap(postgres_url)
    bootstrap_checkpointer(postgres_url)
    draft = _draft(repository, profile, card)
    proposal = _propose(repository, profile, draft)
    configuration = {"configurable": {"thread_id": str(uuid4())}}

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        paused = _graph(repository, checkpointer).invoke(
            initial_review_state(
                profile_id=profile.id,
                proposal_id=proposal.id,
                expense_id=draft.id,
                source_reference=draft.source_reference,
            ),
            configuration,
        )
        assert paused["__interrupt__"][0].value.candidate_name == "Giardinaggio"

    with open_postgres_checkpointer(postgres_url) as reopened:
        snapshot = _graph(repository, reopened).get_state(configuration)

    assert snapshot.next == (ASK_NODE,)
    assert json.loads(json.dumps(snapshot.values)) == snapshot.values
    assert repository.get_expense(profile.id, draft.id).status is ExpenseStatus.DRAFT


@pytest.mark.postgres
def test_the_owner_decision_resumes_a_thread_from_its_checkpoint(
    postgres_url: str,
) -> None:
    """Settle a review opened by a connection that no longer exists."""

    repository, profile, card = _bootstrap(postgres_url)
    bootstrap_checkpointer(postgres_url)
    draft = _draft(repository, profile, card)
    proposal = _propose(repository, profile, draft)
    configuration = {"configurable": {"thread_id": str(uuid4())}}
    before = _category_count(postgres_url, profile.id)

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        _graph(repository, checkpointer).invoke(
            initial_review_state(
                profile_id=profile.id,
                proposal_id=proposal.id,
                expense_id=draft.id,
                source_reference=draft.source_reference,
            ),
            configuration,
        )

    with open_postgres_checkpointer(postgres_url) as reopened:
        final = _graph(repository, reopened).invoke(
            Command(resume=ReviewDecision(CallbackAction.APPROVE_CATEGORY)),
            configuration,
        )

    assert final["outcome"] == ReviewOutcome.APPROVED.value
    stored = repository.get_expense(profile.id, draft.id)
    stored.require_reconciled()
    assert stored.status is ExpenseStatus.POSTED
    created = repository.get_taxonomy(profile.id).resolve_active("Giardinaggio")
    assert final["settled_category_id"] == str(created.id)
    assert _category_count(postgres_url, profile.id) == before + 1
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.APPROVED
    )


@pytest.mark.postgres
def test_cancelling_a_resumed_review_discards_the_stored_draft(
    postgres_url: str,
) -> None:
    """Reach the new terminal state through the real graph and schema."""

    repository, profile, card = _bootstrap(postgres_url)
    bootstrap_checkpointer(postgres_url)
    draft = _draft(repository, profile, card)
    proposal = _propose(repository, profile, draft)
    configuration = {"configurable": {"thread_id": str(uuid4())}}
    before = _category_count(postgres_url, profile.id)

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        compiled = _graph(repository, checkpointer)
        compiled.invoke(
            initial_review_state(
                profile_id=profile.id,
                proposal_id=proposal.id,
                expense_id=draft.id,
                source_reference=draft.source_reference,
            ),
            configuration,
        )
        final = compiled.invoke(
            Command(resume=ReviewDecision(CallbackAction.CANCEL_REVIEW)),
            configuration,
        )

    assert final["outcome"] == ReviewOutcome.CANCELLED.value
    assert (
        repository.get_expense(profile.id, draft.id).status is ExpenseStatus.DISCARDED
    )
    assert _category_count(postgres_url, profile.id) == before
    assert repository.get_pending_category_proposals(profile.id) == ()


@pytest.mark.postgres
def test_a_reproposal_pauses_again_on_a_persisted_successor(
    postgres_url: str,
) -> None:
    """Keep the loop durable: the successor is stored, not held in memory."""

    repository, profile, card = _bootstrap(postgres_url)
    bootstrap_checkpointer(postgres_url)
    draft = _draft(repository, profile, card)
    proposal = _propose(repository, profile, draft)
    configuration = {"configurable": {"thread_id": str(uuid4())}}
    completion = json.dumps(
        {
            "outcome": "proposal",
            "proposed_name": "Piante e fiori",
            "parent_name": "Casa e utenze",
            "description": "Piante, fiori e terriccio.",
            "rationale": "Nessuna categoria attiva descrive lo scopo.",
            "considered_category_names": [],
            "evidence": "piante",
        }
    )

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        compiled = _graph(repository, checkpointer, [completion])
        compiled.invoke(
            initial_review_state(
                profile_id=profile.id,
                proposal_id=proposal.id,
                expense_id=draft.id,
                source_reference=draft.source_reference,
            ),
            configuration,
        )
        paused = compiled.invoke(
            Command(resume=ReviewDecision(CallbackAction.REPROPOSE_CATEGORY)),
            configuration,
        )

    successor_id = UUID(paused["__interrupt__"][0].value.proposal_id)
    pending = repository.get_pending_category_proposals(profile.id)
    assert tuple(item.id for item in pending) == (successor_id,)
    assert pending[0].excluded_names == ("Giardinaggio",)
    assert (
        repository.get_category_proposal(profile.id, proposal.id).status
        is CategoryProposalStatus.REJECTED
    )
    assert repository.get_expense(profile.id, draft.id).status is ExpenseStatus.DRAFT
