"""Real PostgreSQL tests for the read-only reporting behind query and advice."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from money_tracker.application import FinanceService, ReportingService
from money_tracker.application.administration import FinanceAdministrationService
from money_tracker.application.runtime import SystemClock, UUID4Generator
from money_tracker.domain import (
    AuditEvent,
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Profile,
    YearMonth,
    initial_taxonomy,
)
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository

ROME = ZoneInfo("Europe/Rome")
PERIOD = YearMonth(2026, 9)


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


def _post(
    repository: PostgresFinanceRepository,
    profile: Profile,
    source: FundingSource,
    category_name: str,
    minor_units: int,
    *,
    month: int = 9,
    day: int = 10,
) -> Expense:
    """Store one movement and post it, so a read model can count it."""

    taxonomy = repository.get_taxonomy(profile.id)
    category = taxonomy.resolve_active(category_name)
    amount = Money(minor_units)
    reference = f"integration:reporting:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=amount,
        occurred_at=datetime(2026, month, day, 12, tzinfo=ROME),
        description=f"spesa {category_name}",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id, amount=amount, provenance="test"
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=source.id, amount=amount),
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    created = finance.create_expense(
        expense,
        request_hash=sha256(reference.encode()).hexdigest(),
        actor="test",
        reason="reporting fixture",
    )
    return finance.post_expense(
        profile_id=profile.id,
        expense_id=created.id,
        expected_version=created.version,
        actor="test",
        reason="reporting fixture",
    )


def _draft(
    repository: PostgresFinanceRepository,
    profile: Profile,
    source: FundingSource,
    category_name: str,
    minor_units: int,
) -> None:
    """Store one movement without posting it."""

    taxonomy = repository.get_taxonomy(profile.id)
    category = taxonomy.resolve_active(category_name)
    amount = Money(minor_units)
    reference = f"integration:reporting:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=profile.id,
        amount=amount,
        occurred_at=datetime(2026, 9, 11, 12, tzinfo=ROME),
        description="bozza",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id, amount=amount, provenance="test"
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=source.id, amount=amount),
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    repository.create_expense(
        expense,
        request_hash=sha256(reference.encode()).hexdigest(),
        audit_event=AuditEvent(
            id=uuid4(),
            profile_id=profile.id,
            aggregate_type="expense",
            aggregate_id=expense.id,
            action="create",
            actor="test",
            source_reference=reference,
            occurred_at=SystemClock().now(),
            prior_version=None,
            current_version=1,
            reason="reporting fixture",
        ),
    )


@pytest.mark.postgres
def test_posted_spend_is_read_from_the_requested_month_only(
    postgres_url: str,
) -> None:
    """Bound the query by the real occurrence column, not by loading history."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    _post(repository, profile, card, "Abbonamenti", 599, month=9)
    _post(repository, profile, card, "Abbonamenti", 900, month=8)

    result = ReportingService(repository).category_spending(
        profile_id=profile.id, category_name="Abbonamenti", period=PERIOD
    )

    assert result.posted_spend == Money(599)
    assert result.period == PERIOD


@pytest.mark.postgres
def test_an_unposted_draft_is_not_counted(postgres_url: str) -> None:
    """Report what actually posted, so a pending review changes no figure."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    _post(repository, profile, card, "Abbonamenti", 599)
    _draft(repository, profile, card, "Abbonamenti", 5000)

    result = ReportingService(repository).category_spending(
        profile_id=profile.id, category_name="Abbonamenti", period=PERIOD
    )

    assert result.posted_spend == Money(599)


@pytest.mark.postgres
def test_the_split_separates_benefits_from_personal_funds(
    postgres_url: str,
) -> None:
    """Keep the benefit portion visible, as the product baseline requires."""

    repository, profile, card, vouchers = _bootstrap(postgres_url)
    _post(repository, profile, card, "Spesa alimentare", 2000)
    _post(repository, profile, vouchers, "Spesa alimentare", 3000)

    result = ReportingService(repository).period_split(
        profile_id=profile.id, period=PERIOD
    )

    assert result.split.personal_funds == Money(2000)
    assert result.split.benefit == Money(3000)


@pytest.mark.postgres
def test_budget_status_reads_a_real_stored_budget(postgres_url: str) -> None:
    """Answer J7's budget question against real configuration."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    category = repository.get_taxonomy(profile.id).resolve_active("Abbonamenti")
    FinanceAdministrationService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).create_budget(
        MonthlyBudget(
            id=uuid4(),
            profile_id=profile.id,
            category_id=category.id,
            recurring_target=Money(5000),
        ),
        actor="test",
        reason="reporting fixture",
    )
    _post(repository, profile, card, "Abbonamenti", 1500)

    result = ReportingService(repository).category_budget(
        profile_id=profile.id, category_name="Abbonamenti", period=PERIOD
    )

    assert result.status is not None
    assert result.status.target == Money(5000)
    assert result.status.remaining == Money(3500)


@pytest.mark.postgres
def test_the_survey_loads_every_stored_budget_worst_first(
    postgres_url: str,
) -> None:
    """Order the real survey deterministically rather than by model judgment."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    taxonomy = repository.get_taxonomy(profile.id)
    administration = FinanceAdministrationService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    )
    for name, target in (("Abbonamenti", 5000), ("Spesa alimentare", 40000)):
        administration.create_budget(
            MonthlyBudget(
                id=uuid4(),
                profile_id=profile.id,
                category_id=taxonomy.resolve_active(name).id,
                recurring_target=Money(target),
            ),
            actor="test",
            reason="reporting fixture",
        )
    _post(repository, profile, card, "Abbonamenti", 6000)
    _post(repository, profile, card, "Spesa alimentare", 1000)

    result = ReportingService(repository).budgets_at_risk(
        profile_id=profile.id, period=PERIOD
    )

    assert [name for name, _ in result.statuses] == [
        "Abbonamenti",
        "Spesa alimentare",
    ]
    assert result.statuses[0][1].remaining == Money(-1000)


@pytest.mark.postgres
def test_the_survey_names_posted_spend_with_no_budget(postgres_url: str) -> None:
    """Name the gap instead of implying every category is under control."""

    repository, profile, card, _ = _bootstrap(postgres_url)
    _post(repository, profile, card, "Cibo e accessori", 1200)

    result = ReportingService(repository).budgets_at_risk(
        profile_id=profile.id, period=PERIOD
    )

    assert result.statuses == ()
    assert result.unbudgeted_category_names == ("Cibo e accessori",)
