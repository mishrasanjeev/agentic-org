# SPDX-License-Identifier: Apache-2.0
"""Guard registry event source states on databases already at v6z48.

Revision ID: v6z49_registry_from_state
Revises: v6z48_agent_registry

Never rewrite historical audit rows. Legacy invalid rows leave the check NOT
VALID for operator review, while all new writes are constrained immediately.
"""

from alembic import op

revision = "v6z49_registry_from_state"
down_revision = "v6z48_agent_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('agent_registry_events') IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_agent_registry_events_from'
                      AND conrelid = 'agent_registry_events'::regclass
                ) THEN
                    ALTER TABLE agent_registry_events
                        ADD CONSTRAINT ck_agent_registry_events_from CHECK (
                            from_state IN ('draft','review','approved','published','deprecated','retired')
                        ) NOT VALID;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM agent_registry_events
                    WHERE from_state NOT IN ('draft','review','approved','published','deprecated','retired')
                ) THEN
                    ALTER TABLE agent_registry_events VALIDATE CONSTRAINT ck_agent_registry_events_from;
                ELSE
                    RAISE NOTICE 'Registry source-state check protects new writes; historical rows require review';
                END IF;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE IF EXISTS agent_registry_events
            DROP CONSTRAINT IF EXISTS ck_agent_registry_events_from;
    """)
