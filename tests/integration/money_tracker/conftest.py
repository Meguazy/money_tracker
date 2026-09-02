"""Shared real-service fixtures for Money Tracker integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from psycopg import connect, sql


def _isolated_test_url() -> str:
    """Return a URL only when it names the exact local isolated test database."""

    if os.getenv("RUN_POSTGRES_INTEGRATION_TESTS") != "1":
        pytest.skip("set RUN_POSTGRES_INTEGRATION_TESTS=1 for real PostgreSQL tests")
    database_url = os.getenv("MONEY_TRACKER_TEST_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("MONEY_TRACKER_TEST_DATABASE_URL is required")
    parsed = urlparse(database_url)
    if (
        parsed.scheme != "postgresql"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5433
        or parsed.path != "/money_tracker_test"
    ):
        raise RuntimeError(
            "integration cleanup is restricted to local money_tracker_test on port 5433"
        )
    return database_url


def _reset_test_database(database_url: str) -> None:
    """Remove only schemas owned by the isolated integration-test database."""

    with connect(database_url, autocommit=True) as connection:
        current_database = connection.execute("SELECT current_database()").fetchone()
        if current_database != ("money_tracker_test",):
            raise RuntimeError("refusing cleanup outside money_tracker_test")
        for schema_name in ("workflow", "messaging", "finance"):
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
        connection.execute("DROP TABLE IF EXISTS public.alembic_version")


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Yield a clean real test database and remove only its owned data afterward."""

    database_url = _isolated_test_url()
    _reset_test_database(database_url)
    try:
        yield database_url
    finally:
        _reset_test_database(database_url)
