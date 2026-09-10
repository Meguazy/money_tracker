"""Let the durable outbox revoke controls on a superseded summary.

Every movement summary carries inline controls, and a later summary for the
same movement leaves the earlier one holding controls that no longer apply.
The owner sees two messages offering the same buttons and reasonably presses
the older one, which then resolves to nothing useful.

Removing those controls is a Telegram call, and AD-018 keeps the Bot API
credential in the gateway, so the worker cannot make it. It has to become
durable outbound work like every response. That makes the outbox carry two
operations rather than one: sending a message, whose Telegram identifier is
only known afterwards, and editing an existing message, whose identifier is
known before. The existing bounded lease, retry, and dead-letter machinery
then covers both without a second delivery loop.

Revision ID: 20260910_0007
Revises: 20260910_0006
Created: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260910_0007"
down_revision: str | None = "20260910_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Admit a second outbound operation without loosening the first."""

    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        ADD COLUMN operation text NOT NULL DEFAULT 'send_message',
        ADD COLUMN target_message_id bigint
        """
    )
    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        ADD CONSTRAINT telegram_outbox_operation_check
            CHECK (operation IN ('send_message', 'revoke_controls'))
        """
    )
    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        ADD CONSTRAINT telegram_outbox_target_message_id_check
            CHECK (target_message_id > 0)
        """
    )
    # The original check demanded owner-facing text of every row. A revocation
    # carries none, so the requirement becomes conditional rather than absent:
    # a send still cannot be empty, and a revocation must name its target.
    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        DROP CONSTRAINT telegram_outbox_text_content_check
        """
    )
    op.execute(
        """
        ALTER TABLE messaging.telegram_outbox
        ADD CONSTRAINT telegram_outbox_operation_shape_check CHECK (
            CASE operation
                WHEN 'send_message' THEN
                    btrim(text_content) <> '' AND target_message_id IS NULL
                ELSE
                    target_message_id IS NOT NULL
            END
        )
        """
    )


def downgrade() -> None:
    """Refuse a downgrade that would strand revocation work."""

    raise RuntimeError(
        "destructive outbox operation downgrade is disabled; narrowing the "
        "outbox to sending alone would strand queued control revocations and "
        "restore stale buttons the owner can press. Restore a reviewed backup "
        "or migrate forward"
    )
