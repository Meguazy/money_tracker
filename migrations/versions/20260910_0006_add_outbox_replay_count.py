"""Track manual replay cycles for durable Telegram outbox messages.

Revision ID: 20260910_0006
Revises: 20260909_0005
Created: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260910_0006"
down_revision: str | None = "20260909_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a non-negative manual replay counter to the durable outbox."""

    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        ADD COLUMN replay_count integer NOT NULL DEFAULT 0
            CHECK (replay_count >= 0)
        """
    )


def downgrade() -> None:
    """Refuse removal of durable outbox recovery history."""

    raise RuntimeError(
        "destructive outbox recovery downgrade is disabled; restore a reviewed "
        "backup or migrate forward"
    )
