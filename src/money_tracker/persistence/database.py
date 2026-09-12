"""PostgreSQL configuration, migrations, and LangGraph checkpoint bootstrap."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection, connect, sql
from psycopg.rows import DictRow, dict_row

from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.persistence.database")

DATABASE_URL_ENV = "MONEY_TRACKER_DATABASE_URL"
TEST_DATABASE_URL_ENV = "MONEY_TRACKER_TEST_DATABASE_URL"
WORKFLOW_SCHEMA = "workflow"
STRICT_MSGPACK_ENV = "LANGGRAPH_STRICT_MSGPACK"


CHECKPOINT_ALLOWED_TYPES: tuple[tuple[str, str], ...] = (
    ("money_tracker.workflows.review", "ReviewQuestion"),
    ("money_tracker.workflows.review", "ReviewDecision"),
    ("money_tracker.workflows.messages", "CallbackAction"),
)
"""Workflow contracts a checkpoint may reconstruct, beyond the library's own.

A paused review outlives the process that opened it, so the question the owner
is shown and the decision that resumes it must survive a restart as themselves
rather than as anonymous mappings. Only contracts this repository owns and that
actually cross a pause are listed; the list is additive over the library's safe
types and never enables arbitrary reconstruction.
"""


def _checkpoint_serializer_from_env() -> JsonPlusSerializer:
    """Build the checkpointer serializer while prohibiting pickle fallback.

    The allowlist is passed explicitly rather than left to the library's own
    environment lookup. That lookup happens at import time and defaults to
    permissive, while this repository's invariant is that strictness is the
    default; passing the list closes that gap, because an unset or late-set
    variable can no longer decide whether unregistered types are reconstructed.
    The local check is kept, so an operator who tries to relax the invariant is
    still refused rather than silently ignored.

    Returns:
        A JSON/msgpack serializer that cannot deserialize pickle payloads and
        reconstructs only the library's safe types plus this repository's
        pause contracts.

    Raises:
        RuntimeError: If local configuration attempts to relax the invariant.
    """

    enabled = os.getenv(STRICT_MSGPACK_ENV, "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        # Relaxing this would let a checkpoint reconstruct arbitrary pickled
        # objects, so the attempt is recorded at the level it deserves.
        _LOGGER.critical(
            "checkpointer.strict_serialization.refused", setting=STRICT_MSGPACK_ENV
        )
        raise RuntimeError(
            f"{STRICT_MSGPACK_ENV} must remain true; pickle fallback is prohibited"
        )
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=CHECKPOINT_ALLOWED_TYPES,
    )


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Connection settings for the dedicated application database.

    The URL is omitted from representations because it contains a password.

    Attributes:
        database_url: PostgreSQL URL used by application services and migrations.
    """

    database_url: str = field(repr=False)

    @classmethod
    def from_env(cls, *, test_database: bool = False) -> DatabaseSettings:
        """Load a database URL from the process environment.

        Args:
            test_database: Read the isolated integration-test URL when true.

        Returns:
            Validated settings for a PostgreSQL database.

        Raises:
            RuntimeError: If the selected variable is absent or not PostgreSQL.
        """

        variable = TEST_DATABASE_URL_ENV if test_database else DATABASE_URL_ENV
        value = os.getenv(variable, "").strip()
        if not value:
            # The URL carries a password and never reaches a record; naming
            # the variable is what an operator actually needs to fix it.
            _LOGGER.error("database.url.missing", setting=variable)
            raise RuntimeError(f"{variable} is required")
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            _LOGGER.error("database.url.unsupported_scheme", setting=variable)
            raise RuntimeError(f"{variable} must use PostgreSQL")
        return cls(database_url=value)


def database_url_from_env(*, test_database: bool = False) -> str:
    """Return the configured application or isolated test database URL.

    Args:
        test_database: Select the integration-test database URL when true.

    Returns:
        A PostgreSQL connection URL.
    """

    return DatabaseSettings.from_env(test_database=test_database).database_url


def _alembic_config(database_url: str) -> Config:
    """Create Alembic configuration without persisting a credential.

    Args:
        database_url: Runtime-only PostgreSQL URL.

    Returns:
        Alembic configuration rooted at this repository.
    """

    repository_root = Path(__file__).resolve().parents[3]
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "migrations"))
    sqlalchemy_url = database_url
    if sqlalchemy_url.startswith("postgresql://"):
        sqlalchemy_url = sqlalchemy_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    config.set_main_option("sqlalchemy.url", sqlalchemy_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str, revision: str = "head") -> None:
    """Apply forward application migrations.

    Args:
        database_url: Runtime-only PostgreSQL URL.
        revision: Alembic revision target.
    """

    with _LOGGER.timed("database.upgrade", revision=revision):
        command.upgrade(_alembic_config(database_url), revision)


def downgrade_database(database_url: str, revision: str) -> None:
    """Apply an explicitly selected rollback revision.

    Args:
        database_url: Runtime-only PostgreSQL URL.
        revision: Alembic revision target, such as ``base``.
    """

    # A downgrade removes schema objects, so it is worth a record even when
    # it succeeds and even though the CLI already refuses an unconfirmed one.
    _LOGGER.warning("database.downgrade.started", revision=revision)
    with _LOGGER.timed("database.downgrade", revision=revision):
        command.downgrade(_alembic_config(database_url), revision)


@contextmanager
def open_postgres_checkpointer(
    database_url: str,
    *,
    setup: bool = False,
) -> Generator[PostgresSaver]:
    """Open a LangGraph saver scoped to the workflow schema.

    Args:
        database_url: Runtime-only PostgreSQL URL.
        setup: Create or upgrade checkpoint tables before yielding when true.

    Yields:
        A saver whose connection uses autocommit and dictionary rows as required
        by the LangGraph PostgreSQL implementation.
    """

    connection: Connection[DictRow]
    with connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(WORKFLOW_SCHEMA))
        )
        saver = PostgresSaver(
            connection,
            serde=_checkpoint_serializer_from_env(),
        )
        if setup:
            _LOGGER.info("checkpointer.setup.started", schema=WORKFLOW_SCHEMA)
            saver.setup()
        _LOGGER.debug("checkpointer.opened", schema=WORKFLOW_SCHEMA)
        yield saver


def bootstrap_checkpointer(database_url: str) -> None:
    """Create or upgrade persistent LangGraph checkpoint tables.

    Args:
        database_url: Runtime-only PostgreSQL URL whose workflow schema already
            exists through Alembic.
    """

    with open_postgres_checkpointer(database_url, setup=True):
        pass
    _LOGGER.info("checkpointer.bootstrapped", schema=WORKFLOW_SCHEMA)
