"""Version receipt metadata for auditable owner-review transitions.

Revision ID: 20260902_0003
Revises: 20260902_0002
Created: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0003"
down_revision: str | None = "20260902_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an optimistic version to existing and future receipt metadata."""

    op.execute(
        "ALTER TABLE finance.receipts "
        "ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )


def downgrade() -> None:
    """Refuse destructive removal of receipt concurrency history."""

    raise RuntimeError(
        "destructive receipt-version downgrade is disabled; restore a reviewed backup"
    )
