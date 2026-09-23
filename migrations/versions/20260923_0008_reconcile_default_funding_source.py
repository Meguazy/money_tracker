"""Reconcile legacy profile pointers with their sole flagged funding source.

The original funding-source bootstrap marked a source as default without
updating the profile pointer. Current commands update both in one transaction,
but an old installation retains the mismatch and its backups cannot pass the
semantic restore gate. Only a unique active flagged source with a missing
pointer is inferred; every other mismatch refuses the migration.

Revision ID: 20260923_0008
Revises: 20260910_0007
Created: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260923_0008"
down_revision: str | None = "20260910_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Repair only unambiguous legacy pointers and reject all other drift."""

    op.execute(
        """
        UPDATE finance.profiles AS profile
           SET default_funding_source_id = source.id,
               updated_at = now()
          FROM finance.funding_sources AS source
         WHERE profile.default_funding_source_id IS NULL
           AND source.profile_id = profile.id
           AND source.status = 'active'
           AND source.is_default
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM finance.profiles AS profile
                WHERE
                    (profile.default_funding_source_id IS NULL AND EXISTS (
                        SELECT 1 FROM finance.funding_sources AS source
                        WHERE source.profile_id = profile.id AND source.is_default
                    ))
                    OR (profile.default_funding_source_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM finance.funding_sources AS source
                        WHERE source.id = profile.default_funding_source_id
                          AND source.profile_id = profile.id
                          AND source.status = 'active'
                          AND source.is_default
                    ))
            ) THEN
                RAISE EXCEPTION 'default funding source state is inconsistent';
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    """Refuse to erase the record of an authoritative metadata repair."""

    raise RuntimeError(
        "default funding source repair cannot be reversed safely; restore a "
        "reviewed backup or migrate forward"
    )
