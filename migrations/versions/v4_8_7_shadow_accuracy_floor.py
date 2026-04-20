"""Lower the default shadow_accuracy_floor from 0.950 to 0.800.

Revision ID: v487_shadow_accuracy_floor
Revises: v486_knowledge_embedding
Create Date: 2026-04-20

BUG-012 (Ramesh 2026-04-20): the default accuracy floor for shadow
agents was 0.950 — unreachable for LLM-driven agents whose per-task
confidence typically lands in the 0.70-0.85 band. Shadow agents
would never cross the promotion gate even when they were working.

This migration changes the column default for NEW rows to 0.800.
Existing rows keep their admin-set value; an operator can update the
floor via PATCH /agents/{id} if they want to realign an existing
agent.

Idempotent — ALTER COLUMN SET DEFAULT is safe to re-run.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v487_shadow_acc_floor"
down_revision = "v486_knowledge_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only adjusts the column default; does not touch existing values.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agents'
                  AND column_name = 'shadow_accuracy_floor'
            ) THEN
                ALTER TABLE agents
                    ALTER COLUMN shadow_accuracy_floor SET DEFAULT 0.800;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agents'
                  AND column_name = 'shadow_accuracy_floor'
            ) THEN
                ALTER TABLE agents
                    ALTER COLUMN shadow_accuracy_floor SET DEFAULT 0.950;
            END IF;
        END
        $$;
    """)
