"""Real PostgreSQL regression for the legacy default-source pointer repair."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import connect
from sqlalchemy.exc import DBAPIError

from money_tracker.domain import Profile, initial_taxonomy
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository
from money_tracker.persistence.schema import APPLICATION_SCHEMA_REVISION


@pytest.mark.postgres
def test_upgrade_reconciles_legacy_default_source_pointer(postgres_url: str) -> None:
    """Carry the unique old default flag into the profile without guessing."""

    upgrade_database(postgres_url, revision="20260910_0007")
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    when = datetime.now(UTC)
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=when)
    source_id = uuid4()
    with connect(postgres_url) as database:
        database.execute(
            "INSERT INTO finance.funding_sources "
            "(id, profile_id, name, normalized_name, funding_class, "
            "balance_mode, status, is_default, version, created_at, updated_at) "
            "VALUES (%s, %s, 'Legacy card', 'legacy card', 'personal_funds', "
            "'untracked', 'active', true, 1, %s, %s)",
            (source_id, profile.id, when, when),
        )
        assert database.execute(
            "SELECT default_funding_source_id FROM finance.profiles WHERE id = %s",
            (profile.id,),
        ).fetchone() == (None,)

    upgrade_database(postgres_url)
    with connect(postgres_url) as database:
        assert database.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == (APPLICATION_SCHEMA_REVISION,)
        assert database.execute(
            "SELECT default_funding_source_id FROM finance.profiles WHERE id = %s",
            (profile.id,),
        ).fetchone() == (source_id,)


@pytest.mark.postgres
def test_upgrade_refuses_ambiguous_default_source_drift(postgres_url: str) -> None:
    """Leave unsupported mismatches untouched and keep the old revision."""

    upgrade_database(postgres_url, revision="20260910_0007")
    repository = PostgresFinanceRepository(postgres_url)
    profile = Profile(uuid4())
    when = datetime.now(UTC)
    repository.bootstrap_profile(profile, taxonomy=initial_taxonomy(), occurred_at=when)
    with connect(postgres_url) as database:
        database.execute(
            "INSERT INTO finance.funding_sources "
            "(id, profile_id, name, normalized_name, funding_class, "
            "balance_mode, status, is_default, version, created_at, updated_at) "
            "VALUES (%s, %s, 'Archived card', 'archived card', 'personal_funds', "
            "'untracked', 'archived', true, 1, %s, %s)",
            (uuid4(), profile.id, when, when),
        )

    with pytest.raises(
        DBAPIError, match="default funding source state is inconsistent"
    ):
        upgrade_database(postgres_url)
    with connect(postgres_url) as database:
        assert database.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone() == ("20260910_0007",)
        assert database.execute(
            "SELECT default_funding_source_id FROM finance.profiles WHERE id = %s",
            (profile.id,),
        ).fetchone() == (None,)
