"""Add agents.llm_provider so an agent's model cannot drift from its provider.

Revision ID: v6z20_agent_llm_provider
Revises: v6z19_repair_billing_cdc
Create Date: 2026-09-14

Bug sheet 2026-09-14 #31: ``agents`` carried only ``llm_model`` and the
runtime guessed the provider by substring of the model name, silently
mapping any unknown name to the Gemini default. The column is nullable —
NULL means "legacy row, infer from the model name" — so no backfill is
required and existing agents keep their current behaviour until an admin
pins a provider through the API/UI.

Idempotent and guarded: the ALTER only runs when the ``agents`` table
exists and uses ``ADD COLUMN IF NOT EXISTS`` so a re-run (or a database
that already got the column from ``metadata.create_all``) is a no-op.
"""

from __future__ import annotations

from alembic import op

revision = "v6z20_agent_llm_provider"
down_revision = "v6z19_repair_billing_cdc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.agents') IS NOT NULL THEN
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(50);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.agents') IS NOT NULL THEN
                ALTER TABLE agents DROP COLUMN IF EXISTS llm_provider;
            END IF;
        END $$;
        """
    )
