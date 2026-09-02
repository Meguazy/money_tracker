"""Create logical ownership schemas for application persistence.

Revision ID: 20260901_0001
Revises: None
Created: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create empty finance, messaging, and workflow schemas.

    Product tables remain excluded until their owning roadmap step. LangGraph
    checkpoint bootstrap owns the tables created later inside ``workflow``.
    """

    op.execute("CREATE SCHEMA finance")
    op.execute("CREATE SCHEMA messaging")
    op.execute("CREATE SCHEMA workflow")


def downgrade() -> None:
    """Remove only empty foundation schemas.

    PostgreSQL intentionally rejects this downgrade if a later component has
    created objects in a schema. Operators must use a forward migration or an
    explicitly reviewed recovery procedure once persistent data exists.
    """

    op.execute("DROP SCHEMA workflow")
    op.execute("DROP SCHEMA messaging")
    op.execute("DROP SCHEMA finance")
