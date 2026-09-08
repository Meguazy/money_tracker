"""Real PostgreSQL tests for the compiled expense and refund workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import connect
from pydantic import BaseModel

from money_tracker.application import CategoryProposalService, FinanceService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    open_postgres_checkpointer,
    upgrade_database,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import build_expense_graph
from money_tracker.workflows import (
    CATEGORY_FIT,
    EXTRACTION,
    ChannelMessage,
    ExpenseOutcome,
    ResolutionReason,
    WorkflowIntent,
    default_prompt,
    initial_expense_state,
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


def _extraction(**overrides: Any) -> str:
    """Render one schema-valid extraction completion."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 5000,
        "description": "spesa al supermercato",
        "evidence": "ho speso 50 euro",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _fit(**overrides: Any) -> str:
    """Render one schema-valid existing-category completion."""

    payload: dict[str, Any] = {
        "outcome": "existing",
        "category_name": "Spesa alimentare",
        "evidence": "supermercato",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _bootstrap(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, Profile, FundingSource, FundingSource]:
    """Migrate and seed one profile, its taxonomy, and two funding sources."""

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
    vouchers = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Buoni pasto",
        funding_class=FundingClass.BENEFIT,
        balance_mode=BalanceMode.UNTRACKED,
    )
    repository.create_funding_source(card, occurred_at=now)
    repository.create_funding_source(vouchers, occurred_at=now)
    return repository, profile, card, vouchers


def _record(reference: str = "integration:workflow:1") -> ChannelMessage:
    """Build one durable inbound record."""

    return ChannelMessage(
        source_reference=reference,
        received_at=SystemClock().now().astimezone(PRODUCT_TIMEZONE),
        text="ho speso 50 euro al supermercato",
    )


def _graph(
    repository: PostgresFinanceRepository,
    completions: list[str],
    checkpointer: Any = None,
) -> Any:
    """Compile the workflow over the real repository and application services."""

    return build_expense_graph(
        model=ScriptedModel(completions),
        configuration=repository,
        finance=FinanceService(repository, clock=SystemClock(), ids=UUID4Generator()),
        proposals=CategoryProposalService(
            repository, clock=SystemClock(), ids=UUID4Generator()
        ),
        ids=UUID4Generator(),
        extraction_instructions=default_prompt(EXTRACTION).text,
        category_instructions=default_prompt(CATEGORY_FIT).text,
        checkpointer=checkpointer,
    )


def _expense_count(postgres_url: str, profile_id: UUID) -> int:
    """Count stored movements owned by one profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.expenses WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])


@pytest.mark.postgres
def test_workflow_stores_a_reconciled_draft(postgres_url: str) -> None:
    """Complete the clear-expense journey against real profile configuration."""

    repository, profile, card, _ = _bootstrap(postgres_url)

    state = _graph(repository, [_extraction(), _fit()]).invoke(
        initial_expense_state(
            _record(),
            profile_id=profile.id,
            routed_intent=WorkflowIntent.EXPENSE,
        )
    )

    assert state["outcome"] == ExpenseOutcome.DRAFTED.value
    stored = repository.get_expense(profile.id, UUID(state["expense_id"]))
    assert stored.status is ExpenseStatus.DRAFT
    assert stored.amount == Money(5000)
    assert stored.payment_allocations[0].funding_source_id == card.id
    stored.require_reconciled()
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_workflow_stores_the_split_funding_journey(postgres_url: str) -> None:
    """Resolve two named sources against the profile and reconcile them."""

    repository, profile, card, vouchers = _bootstrap(postgres_url)

    state = _graph(
        repository,
        [
            _extraction(
                payment_parts=[
                    {
                        "funding_source_text": "buoni pasto",
                        "amount_minor_units": 3000,
                    },
                    {"funding_source_text": "carta", "amount_minor_units": 2000},
                ]
            ),
            _fit(),
        ],
    ).invoke(
        initial_expense_state(
            _record(),
            profile_id=profile.id,
            routed_intent=WorkflowIntent.EXPENSE,
        )
    )

    stored = repository.get_expense(profile.id, UUID(state["expense_id"]))
    by_source = {
        item.funding_source_id: item.amount for item in stored.payment_allocations
    }

    assert by_source == {vouchers.id: Money(3000), card.id: Money(2000)}
    stored.require_reconciled()


@pytest.mark.postgres
def test_workflow_stores_a_draft_and_its_pending_candidate(
    postgres_url: str,
) -> None:
    """Submit a candidate through the real port without extending the taxonomy."""

    repository, profile, _, _ = _bootstrap(postgres_url)

    state = _graph(
        repository,
        [
            _extraction(),
            json.dumps(
                {
                    "outcome": "proposal",
                    "proposed_name": "Assicurazione animali",
                    "description": "Polizze sanitarie per animali domestici.",
                    "rationale": "Nessuna categoria attiva copre le polizze.",
                    "parent_name": "Animali",
                    "evidence": "polizza",
                }
            ),
        ],
    ).invoke(
        initial_expense_state(
            _record(),
            profile_id=profile.id,
            routed_intent=WorkflowIntent.EXPENSE,
        )
    )

    assert state["outcome"] == ExpenseOutcome.AWAITING_CATEGORY_APPROVAL.value
    stored = repository.get_expense(profile.id, UUID(state["expense_id"]))
    proposal = repository.get_category_proposal(profile.id, UUID(state["proposal_id"]))

    assert stored.category_allocations == ()
    assert stored.payment_allocations != ()
    assert proposal.proposed_name == "Assicurazione animali"
    assert {item.id for item in repository.get_taxonomy(profile.id).categories} == {
        item.id for item in initial_taxonomy().categories
    }


@pytest.mark.postgres
def test_clarification_writes_nothing_to_the_database(
    postgres_url: str,
) -> None:
    """Leave the database untouched when a question is owed instead."""

    repository, profile, _, _ = _bootstrap(postgres_url)

    # Funding words are resolved during assembly, after the purpose boundary,
    # so both boundaries are asked before this failure is reachable.
    state = _graph(
        repository,
        [
            _extraction(payment_parts=[{"funding_source_text": "bancomat"}]),
            _fit(),
        ],
    ).invoke(
        initial_expense_state(
            _record(),
            profile_id=profile.id,
            routed_intent=WorkflowIntent.EXPENSE,
        )
    )

    assert state["outcome"] == ExpenseOutcome.CLARIFICATION.value
    assert state["clarification_reason"] == (
        ResolutionReason.FUNDING_SOURCE_UNRESOLVED.value
    )
    assert _expense_count(postgres_url, profile.id) == 0


@pytest.mark.postgres
def test_redelivered_record_stores_exactly_one_movement(
    postgres_url: str,
) -> None:
    """Hold the zero-duplicate-write gate through the whole workflow."""

    repository, profile, _, _ = _bootstrap(postgres_url)
    record = _record()

    first = _graph(repository, [_extraction(), _fit()]).invoke(
        initial_expense_state(
            record, profile_id=profile.id, routed_intent=WorkflowIntent.EXPENSE
        )
    )
    second = _graph(repository, [_extraction(), _fit()]).invoke(
        initial_expense_state(
            record, profile_id=profile.id, routed_intent=WorkflowIntent.EXPENSE
        )
    )

    assert first["expense_id"] == second["expense_id"]
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_workflow_thread_resumes_from_a_real_checkpoint(
    postgres_url: str,
) -> None:
    """Reconstruct a finished run from PostgreSQL after a fresh connection."""

    repository, profile, _, _ = _bootstrap(postgres_url)
    bootstrap_checkpointer(postgres_url)
    configuration = {"configurable": {"thread_id": str(uuid4())}}

    with open_postgres_checkpointer(postgres_url) as checkpointer:
        written = _graph(repository, [_extraction(), _fit()], checkpointer).invoke(
            initial_expense_state(
                _record(),
                profile_id=profile.id,
                routed_intent=WorkflowIntent.EXPENSE,
            ),
            configuration,
        )

    with open_postgres_checkpointer(postgres_url) as reopened:
        resumed = _graph(repository, [], reopened).get_state(configuration).values

    assert resumed == dict(written)
    assert json.loads(json.dumps(resumed)) == resumed
    assert resumed["outcome"] == ExpenseOutcome.DRAFTED.value
