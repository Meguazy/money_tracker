"""Real PostgreSQL tests for correction and undo specialist routes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import connect
from pydantic import BaseModel

from money_tracker.application import FinanceService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    PaymentAllocation,
    Profile,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.runtime import build_movement_change_graph
from money_tracker.workflows import (
    CallbackAction,
    ChannelMessage,
    MovementChangeOutcome,
    MovementChangeReason,
    WorkflowIntent,
    canonical_request_hash,
    initial_movement_change_state,
    posting_request_hash,
)
from money_tracker.workflows.candidates import (
    DEFAULT_CATEGORY_FIT_CANDIDATE,
    DEFAULT_EXTRACTION_CANDIDATE,
)


@dataclass
class ScriptedModel:
    """A structured-output port replaying fixed boundary completions."""

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
        """Return the next completion and count actual model boundaries."""

        self.calls += 1
        return self.completions[self.calls - 1]


def _bootstrap(
    postgres_url: str,
) -> tuple[PostgresFinanceRepository, Profile, Expense]:
    """Create one owner, source, and posted movement in the real database."""

    upgrade_database(postgres_url)
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    now = datetime(2026, 9, 8, 9, tzinfo=UTC)
    taxonomy = initial_taxonomy()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    card = FundingSource(
        id=uuid4(),
        profile_id=profile.id,
        name="Carta",
        funding_class=FundingClass.PERSONAL_FUNDS,
        balance_mode=BalanceMode.UNTRACKED,
        is_default=True,
    )
    repository.create_funding_source(card, occurred_at=now)
    category = taxonomy.resolve_active("Cibo e accessori")
    draft = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(1200),
        occurred_at=now,
        description="croccantini",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(category.id, Money(1200), "integration-fixture"),
        ),
        payment_allocations=(PaymentAllocation(card.id, Money(1200)),),
        source_reference=f"integration:seed:{uuid4()}",
        idempotency_key=f"integration:seed-command:{uuid4()}",
    )
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    created = finance.create_expense(
        draft,
        request_hash=canonical_request_hash(draft),
        actor="integration",
        reason="seed correction target",
    )
    posted = finance.post_expense(
        profile_id=profile.id,
        expense_id=created.id,
        expected_version=created.version,
        idempotency_key=f"posting:{created.id}",
        request_hash=posting_request_hash(
            profile_id=created.profile_id,
            expense_id=created.id,
        ),
        actor="integration",
        reason="post correction target",
    )
    return repository, profile, posted


def _graph(
    repository: PostgresFinanceRepository,
    model: ScriptedModel,
) -> Any:
    """Compile both specialists over production services and real storage."""

    return build_movement_change_graph(
        model=model,
        configuration=repository,
        finance=FinanceService(
            repository,
            clock=SystemClock(),
            ids=UUID4Generator(),
        ),
        extraction_instructions=DEFAULT_EXTRACTION_CANDIDATE.instructions,
        category_instructions=DEFAULT_CATEGORY_FIT_CANDIDATE.instructions,
        extraction_candidate=DEFAULT_EXTRACTION_CANDIDATE,
        category_candidate=DEFAULT_CATEGORY_FIT_CANDIDATE,
    )


def _message(
    reference: str,
    text: str,
    *,
    callback_action: CallbackAction | None = None,
) -> ChannelMessage:
    """Build one normalized durable work unit."""

    return ChannelMessage(
        source_reference=reference,
        received_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        text=text,
        callback_action=callback_action,
    )


def _correction_extraction() -> str:
    """Return a schema-valid current extraction completion."""

    return json.dumps(
        {
            "polarity": "expense",
            "amount": {"kind": "amount_stated", "minor_units": 1500},
            "occurrence": {"kind": "time_not_stated"},
            "description": "croccantini e sabbia",
            "merchant": {"kind": "merchant_not_named"},
            "funding": {"kind": "funding_not_stated"},
            "evidence": "erano 15 euro",
        }
    )


def _category_fit() -> str:
    """Return a schema-valid current category-fit completion."""

    return json.dumps(
        {
            "judgment": {
                "kind": "existing_category",
                "category_name": "Cibo e accessori",
                "evidence": "croccantini",
            }
        }
    )


@pytest.mark.postgres
def test_correction_resolves_and_replaces_the_complete_movement(
    postgres_url: str,
) -> None:
    """Correct a posted movement through both boundaries and real PostgreSQL.

    The description is deliberately preserved even here, where the owner does
    restate the purpose. This path reuses the extraction contract written for
    creating a movement, which requires a description, so an instruction like
    "correggi l'importo a 35 euro" produced that sentence as the description
    and silently falsified the record.

    Suspending the rewrite costs the case below and keeps the record honest
    until a correction contract exists whose absent description means
    unchanged. That contract is a new model-visible boundary and AD-012 puts it
    behind an evaluation run, so this assertion is what should change when the
    boundary is promoted.
    """

    repository, profile, original = _bootstrap(postgres_url)
    model = ScriptedModel([_correction_extraction(), _category_fit()])
    message = _message(
        f"integration:correction:{uuid4()}",
        "Correggi la spesa dei croccantini: erano 15 euro per croccantini e sabbia",
    )

    result = _graph(repository, model).invoke(
        initial_movement_change_state(
            message,
            profile_id=profile.id,
            routed_intent=WorkflowIntent.CORRECTION,
            target_movement_id=original.id,
        )
    )

    corrected = repository.get_expense(profile.id, original.id)
    assert result["outcome"] == MovementChangeOutcome.CORRECTED.value
    assert corrected.amount == Money(1500)
    assert corrected.description == original.description
    assert corrected.occurred_at == original.occurred_at
    assert corrected.payment_allocations[0].amount == Money(1500)
    assert corrected.status is ExpenseStatus.POSTED
    assert corrected.version == original.version + 1
    assert model.calls == 2
    assert json.loads(json.dumps(result)) == result


@pytest.mark.postgres
def test_correction_without_a_resolved_movement_writes_nothing(
    postgres_url: str,
) -> None:
    """Clarify before model or repository work when runtime found no target."""

    repository, profile, _ = _bootstrap(postgres_url)
    model = ScriptedModel()
    message = _message(
        f"integration:correction-missing:{uuid4()}",
        "Correggi l'ultima spesa",
    )
    before = _audit_count(postgres_url, profile.id)

    result = _graph(repository, model).invoke(
        initial_movement_change_state(
            message,
            profile_id=profile.id,
            routed_intent=WorkflowIntent.CORRECTION,
            target_movement_id=None,
        )
    )

    assert result["outcome"] == MovementChangeOutcome.CLARIFICATION.value
    assert (
        result["clarification_reason"]
        == MovementChangeReason.NO_CORRECTABLE_MOVEMENT.value
    )
    assert _audit_count(postgres_url, profile.id) == before
    assert model.calls == 0


@pytest.mark.postgres
def test_undo_voids_a_posted_movement(postgres_url: str) -> None:
    """Void the exact runtime-resolved target through the application service."""

    repository, profile, posted = _bootstrap(postgres_url)
    message = _message(f"integration:undo:{uuid4()}", "Annulla l'ultima spesa")

    result = _graph(repository, ScriptedModel()).invoke(
        initial_movement_change_state(
            message,
            profile_id=profile.id,
            routed_intent=WorkflowIntent.UNDO,
            target_movement_id=posted.id,
        )
    )

    assert result["outcome"] == MovementChangeOutcome.VOIDED.value
    assert repository.get_expense(profile.id, posted.id).status is ExpenseStatus.VOIDED


@pytest.mark.postgres
def test_replayed_undo_produces_zero_duplicate_writes(postgres_url: str) -> None:
    """Replay a fresh graph invocation and retain one void and one audit."""

    repository, profile, posted = _bootstrap(postgres_url)
    reference = f"integration:undo-replay:{uuid4()}"
    message = _message(reference, "Annulla l'ultima spesa")
    state = initial_movement_change_state(
        message,
        profile_id=profile.id,
        routed_intent=WorkflowIntent.UNDO,
        target_movement_id=posted.id,
    )
    graph = _graph(repository, ScriptedModel())

    first = graph.invoke(state)
    second = graph.invoke(state)

    assert first["expense_id"] == second["expense_id"] == str(posted.id)
    assert json.loads(json.dumps(second)) == second
    assert repository.get_expense(profile.id, posted.id).version == posted.version + 1
    assert _action_count(postgres_url, posted.id, "void") == 1
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_recording_an_already_recorded_movement_writes_nothing(
    postgres_url: str,
) -> None:
    """Answer a stale Registra with a reason instead of a second post."""

    repository, profile, posted = _bootstrap(postgres_url)
    before = _action_count(postgres_url, posted.id, "post")
    state = initial_movement_change_state(
        _message(
            f"integration:posting-stale:{uuid4()}",
            "",
            callback_action=CallbackAction.POST_EXPENSE,
        ),
        profile_id=profile.id,
        routed_intent=WorkflowIntent.CORRECTION,
        target_movement_id=posted.id,
    )

    result = _graph(repository, ScriptedModel()).invoke(state)

    assert result["outcome"] == MovementChangeOutcome.CLARIFICATION.value
    assert result["clarification_reason"] == (
        MovementChangeReason.MOVEMENT_NOT_POSTABLE.value
    )
    assert _action_count(postgres_url, posted.id, "post") == before


@pytest.mark.postgres
def test_replayed_posting_records_the_movement_exactly_once(
    postgres_url: str,
) -> None:
    """Let a redelivered Registra return the first result, not a second write."""

    repository, profile, _ = _bootstrap(postgres_url)
    draft = _draft(repository, profile)
    reference = f"integration:posting-replay:{uuid4()}"
    state = initial_movement_change_state(
        _message(reference, "", callback_action=CallbackAction.POST_EXPENSE),
        profile_id=profile.id,
        routed_intent=WorkflowIntent.CORRECTION,
        target_movement_id=draft.id,
    )
    graph = _graph(repository, ScriptedModel())

    first = graph.invoke(state)
    second = graph.invoke(state)

    assert first["outcome"] == MovementChangeOutcome.POSTED.value
    assert first["expense_id"] == second["expense_id"] == str(draft.id)
    assert second["outcome"] == MovementChangeOutcome.POSTED.value
    assert repository.get_expense(profile.id, draft.id).status is ExpenseStatus.POSTED
    assert _action_count(postgres_url, draft.id, "post") == 1


def _draft(
    repository: PostgresFinanceRepository,
    profile: Profile,
) -> Expense:
    """Store one reconciled draft awaiting the owner's confirmation."""

    taxonomy = repository.get_taxonomy(profile.id)
    category = taxonomy.resolve_active("Cibo e accessori")
    source = repository.get_funding_sources(profile.id)[0]
    reference = f"integration:draft:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=Money(2600),
        occurred_at=datetime(2026, 9, 8, 9, tzinfo=UTC),
        description="benzina",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(category.id, Money(2600), "integration-fixture"),
        ),
        payment_allocations=(PaymentAllocation(source.id, Money(2600)),),
        source_reference=reference,
        idempotency_key=reference,
    )
    return FinanceService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).create_expense(
        expense,
        request_hash=canonical_request_hash(expense),
        actor="integration",
        reason="posting fixture",
    )


def _audit_count(postgres_url: str, profile_id: UUID) -> int:
    """Count audit rows owned by one isolated test profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.audit_events WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])


def _action_count(postgres_url: str, expense_id: UUID, action: str) -> int:
    """Count one action for a movement."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.audit_events "
            "WHERE aggregate_id = %s AND action = %s",
            (expense_id, action),
        ).fetchone()
    return 0 if row is None else int(row[0])


def _expense_count(postgres_url: str, profile_id: UUID) -> int:
    """Count movements owned by one isolated profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.expenses WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])
