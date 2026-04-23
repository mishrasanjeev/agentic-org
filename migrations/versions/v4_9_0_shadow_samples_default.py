"""Update shadow_min_samples default from 20 to 10 on new agents.

Revision ID: v490_shadow_samples_def
Revises: v489_tpl_edit_history
Create Date: 2026-04-23

Uday 2026-04-23 Bug 1: the shadow tab "Generate Test Samples" target
defaulted to 20, producing a long bulk batch that testers read as a
single parallel run. The UI now defaults to 10 and the ORM default
on ``core/models/agent.py`` is 10. For the DB to match ORM intent
when callers don't pass the column explicitly, also change the
server-side default here. Existing rows keep their admin-set value
(we only touch DEFAULT, not the stored data), so it's a non-
destructive change.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars per preflight gate.
revision = "v490_shadow_samples_def"
down_revision = "v489_tpl_edit_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only touch the DEFAULT expression. Use DO-block so the migration
    # survives a rerun on databases where the column doesn't exist yet
    # (e.g. ephemeral CI / fresh minions).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agents'
                  AND column_name = 'shadow_min_samples'
            ) THEN
                ALTER TABLE agents
                    ALTER COLUMN shadow_min_samples SET DEFAULT 10;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agents'
                  AND column_name = 'shadow_min_samples'
            ) THEN
                ALTER TABLE agents
                    ALTER COLUMN shadow_min_samples SET DEFAULT 20;
            END IF;
        END$$;
        """
    )
