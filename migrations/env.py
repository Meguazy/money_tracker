"""Configure Alembic migrations for the application PostgreSQL database."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from money_tracker.persistence.database import database_url_from_env

config = context.config
target_metadata = None


def _configured_url() -> str:
    """Resolve the migration URL without storing a credential in source.

    Returns:
        The URL supplied programmatically or through the application environment.
    """

    configured = config.get_main_option("sqlalchemy.url")
    return configured or database_url_from_env()


def run_migrations_offline() -> None:
    """Render migrations without opening a database connection."""

    context.configure(
        url=_configured_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    """Execute configured migrations on one transactional connection.

    Args:
        connection: SQLAlchemy connection owned by the Alembic engine.
    """

    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Execute migrations against the configured PostgreSQL server."""

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _configured_url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run_with_connection(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
