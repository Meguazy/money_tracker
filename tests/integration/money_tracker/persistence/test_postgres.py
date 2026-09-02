"""Integration tests against the real dedicated application PostgreSQL server."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TypedDict
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from psycopg import connect, sql

from money_tracker.persistence.database import (
    bootstrap_checkpointer,
    downgrade_database,
    open_postgres_checkpointer,
    upgrade_database,
)


class _CounterState(TypedDict):
    """Small serializable graph state used to verify checkpoint persistence."""

    value: int


def _increment(state: _CounterState) -> _CounterState:
    """Increment deterministic test state without any external boundary."""

    return {"value": state["value"] + 1}


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


@pytest.mark.postgres
def test_forward_migration_safe_rollback_and_checkpoint_bootstrap(
    postgres_url: str,
) -> None:
    """Exercise forward, empty-foundation rollback, and fresh checkpoint setup."""

    upgrade_database(postgres_url)
    with connect(postgres_url) as connection:
        schemas = {
            row[0]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN ('finance', 'messaging', 'workflow')"
            )
        }
        privileges = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert schemas == {"finance", "messaging", "workflow"}
    assert privileges == (False, False, False, False)

    downgrade_database(postgres_url, "base")
    with connect(postgres_url) as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name IN ('finance', 'messaging', 'workflow')"
        ).fetchone()
    assert remaining == (0,)

    upgrade_database(postgres_url)
    bootstrap_checkpointer(postgres_url)
    with connect(postgres_url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'workflow'"
            )
        }
    assert {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }.issubset(tables)


@pytest.mark.postgres
def test_checkpoint_survives_connection_recreation(postgres_url: str) -> None:
    """Recover graph state through a new real PostgreSQL connection."""

    upgrade_database(postgres_url)
    builder = StateGraph(_CounterState)
    builder.add_node("increment", _increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    thread_id = f"integration-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    with open_postgres_checkpointer(postgres_url, setup=True) as saver:
        graph = builder.compile(checkpointer=saver)
        result = graph.invoke({"value": 1}, config)
    assert result == {"value": 2}

    with open_postgres_checkpointer(postgres_url) as saver:
        restarted_graph = builder.compile(checkpointer=saver)
        snapshot = restarted_graph.get_state(config)
    assert snapshot.values == {"value": 2}
