"""Give an abandoned draft its own terminal state.

The owner may cancel a category review, which ends a movement that was never
posted. `voided` cannot express that: the domain and the reconciliation trigger
both require a voided movement to reconcile, because voiding reverses something
that already counted. A cancelled draft never counted and has no purpose
dimension, so it needs a terminal state of its own rather than a weakened
reconciliation rule.

The reconciliation trigger already applies only to `posted` and `voided`, so
`discarded` is exempt without changing it.

Revision ID: 20260903_0004
Revises: 20260902_0003
Created: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0004"
down_revision: str | None = "20260902_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Admit the discarded terminal state for abandoned drafts."""

    op.execute("ALTER TABLE finance.expenses DROP CONSTRAINT expenses_status_check")
    op.execute(
        "ALTER TABLE finance.expenses ADD CONSTRAINT expenses_status_check "
        "CHECK (status IN ('draft', 'needs_review', 'posted', 'voided', "
        "'discarded'))"
    )


def downgrade() -> None:
    """Refuse a downgrade that would orphan discarded movements."""

    raise RuntimeError(
        "destructive expense-status downgrade is disabled; narrowing the status "
        "domain would strand discarded drafts. Restore a reviewed backup"
    )
