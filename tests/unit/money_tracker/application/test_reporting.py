"""Tests for read-only execution of the scoped query and advice tools."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from money_tracker.application import ReportingService
from money_tracker.application.ports import ReportingRepository
from money_tracker.domain import (
    BalanceMode,
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClass,
    FundingSource,
    Money,
    MonthlyBudget,
    PaymentAllocation,
    Taxonomy,
    TaxonomyError,
    YearMonth,
    initial_taxonomy,
)

PROFILE = uuid4()
ROME = ZoneInfo("Europe/Rome")
PERIOD = YearMonth(2026, 9)
TAXONOMY = initial_taxonomy()
CARD = FundingSource(
    id=uuid4(),
    profile_id=PROFILE,
    name="Carta",
    funding_class=FundingClass.PERSONAL_FUNDS,
    balance_mode=BalanceMode.UNTRACKED,
    is_default=True,
)
VOUCHERS = FundingSource(
    id=uuid4(),
    profile_id=PROFILE,
    name="Buoni pasto",
    funding_class=FundingClass.BENEFIT,
    balance_mode=BalanceMode.UNTRACKED,
)


@dataclass
class InMemoryReporting:
    """A real read-only implementation of the reporting contract."""

    expenses: tuple[Expense, ...] = ()
    budgets: tuple[MonthlyBudget, ...] = ()
    taxonomy: Taxonomy = TAXONOMY
    reads: list[str] = field(default_factory=list)
    sources: tuple[FundingSource, ...] = (CARD, VOUCHERS)

    def get_taxonomy(self, profile_id: UUID) -> Taxonomy:
        """Return the seeded taxonomy."""

        self.reads.append("taxonomy")
        return self.taxonomy

    def get_funding_sources(self, profile_id: UUID) -> tuple[FundingSource, ...]:
        """Return both configured sources."""

        self.reads.append("funding_sources")
        return self.sources

    def get_expenses(
        self, profile_id: UUID, *, period: YearMonth
    ) -> tuple[Expense, ...]:
        """Return every movement state in reverse occurrence order."""

        self.reads.append("expenses")
        start, end = period.bounds()
        matching = (
            expense for expense in self.expenses if start <= expense.occurred_at < end
        )
        return tuple(
            sorted(
                matching,
                key=lambda expense: (expense.occurred_at, str(expense.id)),
                reverse=True,
            )
        )

    def get_posted_expenses(
        self, profile_id: UUID, *, period: YearMonth
    ) -> tuple[Expense, ...]:
        """Return posted movements occurring in the requested month."""

        self.reads.append("posted_expenses")
        start, end = period.bounds()
        return tuple(
            expense
            for expense in self.expenses
            if expense.status is ExpenseStatus.POSTED
            and start <= expense.occurred_at < end
        )

    def get_budget(self, profile_id: UUID, category_id: UUID) -> MonthlyBudget | None:
        """Return one configured budget or None."""

        self.reads.append("budget")
        return next(
            (item for item in self.budgets if item.category_id == category_id), None
        )

    def get_budgets(self, profile_id: UUID) -> tuple[MonthlyBudget, ...]:
        """Return every configured budget."""

        self.reads.append("budgets")
        return self.budgets


def _posted(
    category_name: str,
    minor_units: int,
    *,
    source: FundingSource = CARD,
    day: int = 10,
    month: int = 9,
) -> Expense:
    """Build one posted movement allocated to a named category."""

    category = TAXONOMY.resolve_active(category_name)
    amount = Money(minor_units)
    return Expense(
        id=uuid4(),
        profile_id=PROFILE,
        amount=amount,
        occurred_at=datetime(2026, month, day, 12, tzinfo=ROME),
        description=f"spesa {category_name}",
        merchant=None,
        status=ExpenseStatus.POSTED,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id, amount=amount, provenance="test"
            ),
        ),
        payment_allocations=(
            PaymentAllocation(funding_source_id=source.id, amount=amount),
        ),
        source_reference=f"test:{uuid4()}",
        idempotency_key=f"key:{uuid4()}",
    )


def _budget(category_name: str, minor_units: int) -> MonthlyBudget:
    """Build one recurring budget for a named category."""

    return MonthlyBudget(
        id=uuid4(),
        profile_id=PROFILE,
        category_id=TAXONOMY.resolve_active(category_name).id,
        recurring_target=Money(minor_units),
    )


def _service(repository: InMemoryReporting) -> ReportingService:
    """Bind the service to the in-memory read-only repository."""

    return ReportingService(repository)


def test_the_service_depends_only_on_a_read_only_port() -> None:
    """Give the bounded agent routes no reachable mutation at all."""

    assert not set(ReportingRepository.__protocol_attrs__) & {
        "create_expense",
        "replace_expense",
        "save_budget",
        "settle_category_review",
        "append_wallet_entry",
    }
    assert all(
        name.startswith("get_") for name in ReportingRepository.__protocol_attrs__
    )


def test_category_spending_rolls_up_descendants_for_the_month() -> None:
    """Answer J7's spending question from posted records only."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Abbonamenti", 599),
            _posted("Abbonamenti", 1200),
            _posted("Spesa alimentare", 5000),
        )
    )

    result = _service(repository).category_spending(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.category_name == "Abbonamenti"
    assert result.period == PERIOD
    assert result.posted_spend == Money(1799)


def test_spending_excludes_movements_outside_the_requested_month() -> None:
    """Report the period asked for rather than the whole history."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Abbonamenti", 599, month=9),
            _posted("Abbonamenti", 900, month=8),
        )
    )

    result = _service(repository).category_spending(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.posted_spend == Money(599)


def test_spending_excludes_drafts_and_discarded_movements() -> None:
    """Count only what actually posted."""

    draft = _posted("Abbonamenti", 900)
    repository = InMemoryReporting(
        expenses=(
            _posted("Abbonamenti", 599),
            Expense(
                id=draft.id,
                profile_id=draft.profile_id,
                amount=draft.amount,
                occurred_at=draft.occurred_at,
                description=draft.description,
                merchant=None,
                status=ExpenseStatus.DRAFT,
                category_allocations=draft.category_allocations,
                payment_allocations=draft.payment_allocations,
                source_reference=draft.source_reference,
                idempotency_key=draft.idempotency_key,
            ),
        )
    )

    result = _service(repository).category_spending(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.posted_spend == Money(599)


def test_spending_separates_personal_funds_from_benefits() -> None:
    """Report the split J7 says to show when useful."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Spesa alimentare", 2000, source=CARD),
            _posted("Spesa alimentare", 3000, source=VOUCHERS),
        )
    )

    result = _service(repository).category_spending(
        profile_id=PROFILE, category_name="Spesa alimentare", period=PERIOD
    )

    assert result.posted_spend == Money(5000)
    assert result.split.personal_funds == Money(2000)
    assert result.split.benefit == Money(3000)


@pytest.mark.parametrize("name", ["Abbonamenti", "abbonamenti", "ABBONAMENTI"])
def test_owner_words_resolve_through_the_active_taxonomy(name: str) -> None:
    """Resolve a name the way the taxonomy compares it, never an identifier."""

    repository = InMemoryReporting(expenses=(_posted("Abbonamenti", 599),))

    result = _service(repository).category_spending(
        profile_id=PROFILE, category_name=name, period=PERIOD
    )

    assert result.category_name == "Abbonamenti"


def test_an_unknown_category_is_stated_rather_than_guessed() -> None:
    """Say plainly that the category does not exist, as J7 requires."""

    repository = InMemoryReporting()

    with pytest.raises(TaxonomyError):
        _service(repository).category_spending(
            profile_id=PROFILE, category_name="Categoria inesistente", period=PERIOD
        )


def test_budget_status_reports_target_spend_and_remaining() -> None:
    """Answer J7's budget question with every figure it names."""

    repository = InMemoryReporting(
        expenses=(_posted("Abbonamenti", 1500),),
        budgets=(_budget("Abbonamenti", 5000),),
    )

    result = _service(repository).category_budget(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.status is not None
    assert result.status.target == Money(5000)
    assert result.status.posted_spend == Money(1500)
    assert result.status.remaining == Money(3500)
    assert result.status.period == PERIOD


def test_an_overspent_budget_reports_a_negative_remaining() -> None:
    """Report the overspend rather than clamping it to zero."""

    repository = InMemoryReporting(
        expenses=(_posted("Abbonamenti", 6000),),
        budgets=(_budget("Abbonamenti", 5000),),
    )

    result = _service(repository).category_budget(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.status is not None
    assert result.status.remaining == Money(-1000)


def test_a_missing_budget_is_reported_as_missing() -> None:
    """State the absent budget plainly instead of inventing a target."""

    repository = InMemoryReporting(expenses=(_posted("Abbonamenti", 1500),))

    result = _service(repository).category_budget(
        profile_id=PROFILE, category_name="Abbonamenti", period=PERIOD
    )

    assert result.status is None
    assert result.category_name == "Abbonamenti"


def test_the_period_split_reports_every_source_class() -> None:
    """Give the month's personal, benefit, and other totals."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Spesa alimentare", 2000, source=CARD),
            _posted("Abbonamenti", 3000, source=VOUCHERS),
        )
    )

    result = _service(repository).period_split(profile_id=PROFILE, period=PERIOD)

    assert result.split.personal_funds == Money(2000)
    assert result.split.benefit == Money(3000)
    assert result.split.other == Money(0)


def test_budgets_at_risk_orders_the_worst_position_first() -> None:
    """Put exceeded budgets ahead of comfortable ones, as J8 requires."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Abbonamenti", 6000),
            _posted("Spesa alimentare", 1000),
        ),
        budgets=(_budget("Abbonamenti", 5000), _budget("Spesa alimentare", 40000)),
    )

    result = _service(repository).budgets_at_risk(profile_id=PROFILE, period=PERIOD)

    assert [name for name, _ in result.statuses] == [
        "Abbonamenti",
        "Spesa alimentare",
    ]
    assert result.statuses[0][1].remaining == Money(-1000)
    assert result.statuses[1][1].remaining == Money(39000)


def test_budgets_at_risk_names_spend_it_cannot_compare() -> None:
    """Name what is missing rather than producing confident silence."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Abbonamenti", 6000),
            _posted("Cibo e accessori", 1200),
        ),
        budgets=(_budget("Abbonamenti", 5000),),
    )

    result = _service(repository).budgets_at_risk(profile_id=PROFILE, period=PERIOD)

    assert result.unbudgeted_category_names == ("Cibo e accessori",)


def test_spend_under_a_budgeted_ancestor_is_not_called_unbudgeted() -> None:
    """Respect the roll-up: a child is covered by its budgeted root."""

    repository = InMemoryReporting(
        expenses=(_posted("Abbonamenti", 1200),),
        budgets=(_budget("Tempo libero", 5000),),
    )

    result = _service(repository).budgets_at_risk(profile_id=PROFILE, period=PERIOD)

    assert result.unbudgeted_category_names == ()
    assert result.statuses[0][1].posted_spend == Money(1200)


def test_a_survey_without_budgets_reports_nothing_to_compare() -> None:
    """Return an empty survey instead of fabricating a baseline."""

    repository = InMemoryReporting(expenses=(_posted("Abbonamenti", 1200),))

    result = _service(repository).budgets_at_risk(profile_id=PROFILE, period=PERIOD)

    assert result.statuses == ()
    assert result.unbudgeted_category_names == ("Abbonamenti",)


def test_source_consumption_keeps_benefits_distinct_and_counts_only_portions() -> None:
    """Keep split payments, refunds and two benefit sources analytically distinct."""

    welfare = replace(VOUCHERS, id=uuid4(), name="Welfare")
    split_purchase = replace(
        _posted("Spesa alimentare", 5000),
        payment_allocations=(
            PaymentAllocation(funding_source_id=CARD.id, amount=Money(2000)),
            PaymentAllocation(funding_source_id=VOUCHERS.id, amount=Money(3000)),
        ),
    )
    repository = InMemoryReporting(
        expenses=(
            split_purchase,
            _posted("Abbonamenti", 1800, source=welfare),
            _posted("Spesa alimentare", -500, source=VOUCHERS),
            _posted("Spesa alimentare", 9000, source=VOUCHERS, month=8),
            replace(
                _posted("Abbonamenti", 10000, source=welfare),
                status=ExpenseStatus.DRAFT,
            ),
        ),
        sources=(CARD, VOUCHERS, welfare),
    )
    result = ReportingService(repository).period_split(
        profile_id=PROFILE, period=PERIOD
    )
    assert {source.name: amount.minor_units for source, amount in result.sources} == {
        "Carta": 2000,
        "Buoni pasto": 2500,
        "Welfare": 1800,
    }
    assert result.split.benefit == Money(4300)


def test_zero_source_consumption_remains_visible() -> None:
    """A configured unused source is distinct from an unknown source."""

    result = ReportingService(InMemoryReporting()).period_split(
        profile_id=PROFILE, period=PERIOD
    )
    assert {source.name: amount for source, amount in result.sources} == {
        "Carta": Money(0),
        "Buoni pasto": Money(0),
    }


def test_monthly_overview_reconciles_each_dimension_without_double_counting() -> None:
    """Expose one posted total while retaining its independent budget view."""

    repository = InMemoryReporting(
        expenses=(
            _posted("Spesa alimentare", 2000, source=CARD),
            _posted("Spesa alimentare", 3000, source=VOUCHERS),
        ),
        budgets=(_budget("Spesa alimentare", 8000),),
    )

    result = ReportingService(repository).monthly_overview(
        profile_id=PROFILE,
        period=PERIOD,
    )

    assert result.posted_total == Money(5000)
    assert result.split.split.personal_funds == Money(2000)
    assert result.split.split.benefit == Money(3000)
    assert result.budgets[0][0] == "Spesa alimentare"
    assert result.budgets[0][1].posted_spend == Money(5000)
    assert result.budgets[0][1].remaining == Money(3000)
    assert result.unbudgeted_category_names == ()
    assert repository.reads.count("posted_expenses") == 1


def test_transaction_register_keeps_every_state_and_display_reference() -> None:
    """Show the complete lifecycle while retaining trusted category/source names."""

    posted = _posted("Spesa alimentare", 2000, day=10)
    draft = replace(
        _posted("Abbonamenti", 1200, day=11),
        status=ExpenseStatus.DRAFT,
    )
    outside = _posted("Cibo e accessori", 900, month=8)
    repository = InMemoryReporting(expenses=(posted, draft, outside))

    result = ReportingService(repository).transaction_register(
        profile_id=PROFILE,
        period=PERIOD,
    )

    assert result.period == PERIOD
    assert result.expenses == (draft, posted)
    assert result.taxonomy is TAXONOMY
    assert result.funding_sources == (CARD, VOUCHERS)
    assert repository.reads == ["expenses", "taxonomy", "funding_sources"]
