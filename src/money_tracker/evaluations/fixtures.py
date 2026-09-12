"""Shared real-PostgreSQL fixtures for suites that gate postconditions.

Most suites score a model against labelled text and need no database. Three do
not: the category review and the two read-only suites gate what the system
actually stored or actually read back, and a state-only check would pass for a
workflow that reported success while touching nothing.

Everything here writes real rows, so the guard is the first thing in the module
and every entry point goes through it. A suite that bootstrapped a profile or
posted a movement into the owner's real database would be a defect far worse
than any it measures, and a name check is the only protection that cannot be
undone by a misconfigured environment file.

V1 permits exactly one active profile, enforced by a unique index, so a run
seeds one owner and every case shares it. Suites therefore read per-case deltas
rather than absolute counts, and address their own records by identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from psycopg import connect, sql

from money_tracker.application import FinanceService
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
    Taxonomy,
)
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.workflows import posting_request_hash

TEST_DATABASE_NAME = "money_tracker_test"
"""The only database these suites may open."""

ROME = ZoneInfo("Europe/Rome")
"""Calendar every fixture timestamp is expressed in."""

DEFAULT_SOURCES: tuple[tuple[str, FundingClass, bool], ...] = (
    ("Carta", FundingClass.PERSONAL_FUNDS, True),
    ("Contanti", FundingClass.PERSONAL_FUNDS, False),
    ("Buoni pasto", FundingClass.BENEFIT, False),
)
"""Funding sources every fixture owner configures, default first."""


def require_isolated_database(database_url: str) -> str:
    """Refuse any database but the local isolated integration-test one.

    Args:
        database_url: Candidate PostgreSQL URL.

    Returns:
        The same URL when it names the expected local test database.

    Raises:
        ValueError: If the URL names anything else.
    """

    parsed = urlparse(database_url)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5433
        or parsed.path != f"/{TEST_DATABASE_NAME}"
    ):
        raise ValueError(
            "this suite writes real rows and runs only against the local "
            f"{TEST_DATABASE_NAME} database on port 5433"
        )
    return database_url


def reset_database(database_url: str) -> None:
    """Drop only the schemas this repository owns in the test database.

    A run starts from a known empty database so that counts, statuses, and
    checkpoint threads mean the same thing on every execution. The
    current-database check repeats the URL guard from inside the connection,
    because that is the last place a mistake can still be caught.

    Args:
        database_url: Already-guarded isolated test database URL.

    Raises:
        ValueError: If the open connection is not the test database.
    """

    with connect(database_url, autocommit=True) as connection:
        current = connection.execute("SELECT current_database()").fetchone()
        if current != (TEST_DATABASE_NAME,):
            raise ValueError("refusing to reset a database other than the test one")
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


@dataclass(frozen=True, slots=True)
class OwnerFixture:
    """The single seeded owner every case in a run shares.

    Attributes:
        profile: The one active profile V1 permits.
        sources: Configured funding sources by owner-facing name.
    """

    profile: Profile
    sources: dict[str, FundingSource]

    def source(self, name: str) -> FundingSource:
        """Return one configured source by the name the fixture gave it."""

        return self.sources[name]


def bootstrap_owner(
    repository: PostgresFinanceRepository,
    *,
    taxonomy: Taxonomy,
    sources: tuple[tuple[str, FundingClass, bool], ...] = DEFAULT_SOURCES,
) -> OwnerFixture:
    """Seed the profile, its taxonomy, and its configured funding sources.

    Args:
        repository: Repository already pointed at the guarded test database.
        taxonomy: Taxonomy to bootstrap, which a suite may extend for branches
            the accepted seed taxonomy cannot exercise.
        sources: Name, economic class, and default flag for each source.

    Returns:
        The seeded owner.
    """

    profile = Profile(uuid4())
    now = SystemClock().now()
    repository.bootstrap_profile(profile, taxonomy=taxonomy, occurred_at=now)
    configured: dict[str, FundingSource] = {}
    for name, funding_class, is_default in sources:
        source = FundingSource(
            id=uuid4(),
            profile_id=profile.id,
            name=name,
            funding_class=funding_class,
            balance_mode=BalanceMode.UNTRACKED,
            is_default=is_default,
        )
        repository.create_funding_source(source, occurred_at=now)
        configured[name] = source
    return OwnerFixture(profile=profile, sources=configured)


def post_expense(
    repository: PostgresFinanceRepository,
    owner: OwnerFixture,
    *,
    category_name: str,
    portions: tuple[tuple[str, int], ...],
    year: int,
    month: int,
    day: int = 12,
) -> Expense:
    """Store one movement and post it, so a read model counts it.

    Args:
        repository: Repository pointed at the guarded test database.
        owner: Seeded owner.
        category_name: Active category receiving the whole purpose allocation.
        portions: Funding source name and minor units for each portion.
        year: Rome calendar year of the occurrence.
        month: Rome calendar month of the occurrence.
        day: Rome calendar day of the occurrence.

    Returns:
        The posted movement.
    """

    taxonomy = repository.get_taxonomy(owner.profile.id)
    category = taxonomy.resolve_active(category_name)
    total = Money(sum(minor_units for _, minor_units in portions))
    reference = f"eval:fixture:{uuid4()}"
    expense = Expense(
        id=uuid4(),
        profile_id=owner.profile.id,
        amount=total,
        occurred_at=datetime(year, month, day, 12, tzinfo=ROME),
        description=f"spesa {category_name}",
        merchant=None,
        status=ExpenseStatus.DRAFT,
        category_allocations=(
            CategoryAllocation(
                category_id=category.id, amount=total, provenance="eval-fixture"
            ),
        ),
        payment_allocations=tuple(
            PaymentAllocation(
                funding_source_id=owner.source(name).id, amount=Money(minor_units)
            )
            for name, minor_units in portions
        ),
        source_reference=reference,
        idempotency_key=reference,
    )
    finance = FinanceService(repository, clock=SystemClock(), ids=UUID4Generator())
    created = finance.create_expense(
        expense,
        request_hash=sha256(reference.encode()).hexdigest(),
        actor="eval",
        reason="evaluation fixture",
    )
    return finance.post_expense(
        profile_id=owner.profile.id,
        expense_id=created.id,
        expected_version=created.version,
        idempotency_key=f"posting:{reference}",
        request_hash=posting_request_hash(
            profile_id=owner.profile.id,
            expense_id=created.id,
        ),
        actor="eval",
        reason="evaluation fixture",
    )


def create_budget(
    repository: PostgresFinanceRepository,
    owner: OwnerFixture,
    *,
    category_name: str,
    recurring_target: int,
) -> UUID:
    """Configure one recurring monthly budget against an active category.

    Args:
        repository: Repository pointed at the guarded test database.
        owner: Seeded owner.
        category_name: Active category the budget belongs to.
        recurring_target: Monthly target in minor units.

    Returns:
        The category the budget was configured against.
    """

    category = repository.get_taxonomy(owner.profile.id).resolve_active(category_name)
    FinanceAdministrationService(
        repository, clock=SystemClock(), ids=UUID4Generator()
    ).create_budget(
        MonthlyBudget(
            id=uuid4(),
            profile_id=owner.profile.id,
            category_id=category.id,
            recurring_target=Money(recurring_target),
        ),
        idempotency_key=f"eval-budget:{owner.profile.id}:{category.id}",
        actor="eval",
        reason="evaluation fixture",
    )
    return category.id


def audit_event(expense: Expense, *, action: str) -> AuditEvent:
    """Build the audit record a direct repository write requires."""

    return AuditEvent(
        id=uuid4(),
        profile_id=expense.profile_id,
        aggregate_type="expense",
        aggregate_id=expense.id,
        action=action,
        actor="eval",
        source_reference=expense.source_reference,
        occurred_at=SystemClock().now(),
        prior_version=None,
        current_version=1,
        reason="evaluation fixture",
    )


__all__ = [
    "DEFAULT_SOURCES",
    "ROME",
    "TEST_DATABASE_NAME",
    "OwnerFixture",
    "audit_event",
    "bootstrap_owner",
    "create_budget",
    "post_expense",
    "require_isolated_database",
    "reset_database",
]
