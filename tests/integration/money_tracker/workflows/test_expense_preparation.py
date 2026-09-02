"""Real PostgreSQL tests for persisting a prepared draft movement."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from psycopg import connect

from money_tracker.application import (
    CategoryProposalService,
    FinanceService,
    IdempotencyConflictError,
)
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
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.workflows import (
    CategoryFit,
    ChannelMessage,
    ExpenseExtraction,
    PreparedExpense,
    prepare_expense_draft,
)
from money_tracker.workflows.resolution import PRODUCT_TIMEZONE


def _extraction(**overrides: Any) -> ExpenseExtraction:
    """Build one schema-valid extraction."""

    payload: dict[str, Any] = {
        "polarity": "expense",
        "amount_minor_units": 5000,
        "description": "spesa al supermercato",
        "evidence": "ho speso 50 euro al supermercato",
    }
    payload.update(overrides)
    return ExpenseExtraction.model_validate_json(json.dumps(payload))


def _fit(**overrides: Any) -> CategoryFit:
    """Build one existing-category judgment."""

    payload: dict[str, Any] = {
        "outcome": "existing",
        "category_name": "Spesa alimentare",
        "evidence": "spesa al supermercato",
    }
    payload.update(overrides)
    return CategoryFit.model_validate_json(json.dumps(payload))


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


def _inbound_record() -> ChannelMessage:
    """Build one durable inbound record.

    A redelivery replays the stored record unchanged, timestamp included, so a
    test of idempotent replay must reuse this exact object rather than build a
    second one from a fresh clock reading.
    """

    return ChannelMessage(
        source_reference="integration:inbox:1",
        received_at=SystemClock().now().astimezone(PRODUCT_TIMEZONE),
        text="ho speso 50 euro al supermercato",
    )


def _prepare(
    repository: PostgresFinanceRepository,
    profile: Profile,
    *,
    extraction: ExpenseExtraction | None = None,
    fit: CategoryFit | None = None,
    message: ChannelMessage | None = None,
) -> PreparedExpense:
    """Prepare one draft using taxonomy and sources read from PostgreSQL."""

    return prepare_expense_draft(
        extraction or _extraction(),
        fit or _fit(),
        message=message or _inbound_record(),
        profile_id=profile.id,
        expense_id=uuid4(),
        proposal_id=uuid4(),
        taxonomy=repository.get_taxonomy(profile.id),
        funding_sources=repository.get_funding_sources(profile.id),
    )


def _expense_count(postgres_url: str, profile_id: uuid4) -> int:  # type: ignore[valid-type]
    """Count stored movements owned by one profile."""

    with connect(postgres_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM finance.expenses WHERE profile_id = %s",
            (profile_id,),
        ).fetchone()
    return 0 if row is None else int(row[0])


@pytest.mark.postgres
def test_prepared_draft_persists_against_the_real_taxonomy_and_sources(
    postgres_url: str,
) -> None:
    """Resolve against real profile configuration and store the draft."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    service = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())

    prepared = _prepare(repository, profile)
    created = service.create_expense(
        prepared.expense,
        request_hash=prepared.request_hash,
        actor="integration",
        reason="prepared from a channel message",
    )

    stored = repository.get_expense(profile.id, created.id)

    assert stored.status is ExpenseStatus.DRAFT
    assert stored.amount == Money(5000)
    assert stored.payment_allocations[0].funding_source_id == card.id
    assert stored.category_allocations[0].category_id == (
        repository.get_taxonomy(profile.id).resolve_active("Spesa alimentare").id
    )
    stored.require_reconciled()
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_redelivered_message_creates_exactly_one_movement(
    postgres_url: str,
) -> None:
    """Hold the zero-duplicate-write gate for a repeated inbound record."""

    repository, profile, _, _ = _bootstrap(postgres_url)
    service = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())

    record = _inbound_record()
    first = _prepare(repository, profile, message=record)
    created = service.create_expense(
        first.expense,
        request_hash=first.request_hash,
        actor="integration",
        reason="first delivery",
    )
    second = _prepare(repository, profile, message=record)
    replayed = service.create_expense(
        second.expense,
        request_hash=second.request_hash,
        actor="integration",
        reason="redelivery of the same record",
    )

    assert second.expense.id != first.expense.id
    assert replayed.id == created.id
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_same_record_with_changed_content_is_a_conflict(
    postgres_url: str,
) -> None:
    """Refuse to reuse one inbound record's key for a different request."""

    repository, profile, _, _ = _bootstrap(postgres_url)
    service = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())

    record = _inbound_record()
    first = _prepare(repository, profile, message=record)
    service.create_expense(
        first.expense,
        request_hash=first.request_hash,
        actor="integration",
        reason="first delivery",
    )
    altered = _prepare(
        repository,
        profile,
        extraction=_extraction(amount_minor_units=6000),
        message=record,
    )

    with pytest.raises(IdempotencyConflictError):
        service.create_expense(
            altered.expense,
            request_hash=altered.request_hash,
            actor="integration",
            reason="same record, different content",
        )
    assert _expense_count(postgres_url, profile.id) == 1


@pytest.mark.postgres
def test_split_funding_draft_persists_both_dimensions(
    postgres_url: str,
) -> None:
    """Store the documented benefit and personal-funds split."""

    repository, profile, card, vouchers = _bootstrap(postgres_url)
    service = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())

    prepared = _prepare(
        repository,
        profile,
        extraction=_extraction(
            payment_parts=[
                {"funding_source_text": "buoni pasto", "amount_minor_units": 3000},
                {"funding_source_text": "carta", "amount_minor_units": 2000},
            ]
        ),
    )
    created = service.create_expense(
        prepared.expense,
        request_hash=prepared.request_hash,
        actor="integration",
        reason="split funding draft",
    )

    stored = repository.get_expense(profile.id, created.id)
    by_source = {
        item.funding_source_id: item.amount for item in stored.payment_allocations
    }

    assert by_source == {vouchers.id: Money(3000), card.id: Money(2000)}
    stored.require_reconciled()


@pytest.mark.postgres
def test_pending_proposal_draft_persists_with_its_proposal(
    postgres_url: str,
) -> None:
    """Keep the draft and its candidate while approval is still pending."""

    repository, profile, _, _ = _bootstrap(postgres_url)
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    proposals = CategoryProposalService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    )

    prepared = _prepare(
        repository,
        profile,
        fit=CategoryFit.model_validate_json(
            json.dumps(
                {
                    "outcome": "proposal",
                    "proposed_name": "Assicurazione animali",
                    "description": "Polizze sanitarie per animali domestici.",
                    "rationale": "Nessuna categoria attiva copre le polizze.",
                    "parent_name": "Animali",
                    "evidence": "polizza per il gatto",
                }
            )
        ),
    )
    assert prepared.proposal is not None
    created = finance.create_expense(
        prepared.expense,
        request_hash=prepared.request_hash,
        actor="integration",
        reason="draft awaiting category approval",
    )
    proposals.submit(
        prepared.proposal,
        actor="integration",
        reason="candidate awaiting owner approval",
    )

    stored = repository.get_expense(profile.id, created.id)
    stored_proposal = repository.get_category_proposal(profile.id, prepared.proposal.id)

    assert stored.status is ExpenseStatus.DRAFT
    assert stored.category_allocations == ()
    assert stored.payment_allocations != ()
    assert stored_proposal.proposed_name == "Assicurazione animali"
    assert stored_proposal.source_reference == "integration:inbox:1"
    assert {item.id for item in repository.get_taxonomy(profile.id).categories} == {
        item.id for item in initial_taxonomy().categories
    }
