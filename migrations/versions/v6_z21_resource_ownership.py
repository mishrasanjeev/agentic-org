"""Per-user ownership for agents, connectors, and HITL attribution.

Revision ID: v6z21_resource_ownership
Revises: v6z20_agent_llm_provider
Create Date: 2026-09-14

Bug sheet 2026-09-14 rows 17/18/19/22/29/30/52:

* ``agents.owner_user_id`` + ``agents.visibility`` ('tenant' | 'personal').
  Every existing row is a tenant agent (the column default), so nothing a
  user can see today disappears and no backfill is needed.
* ``connectors.owner_user_id``. NULL is a tenant-shared, admin-managed
  connector, which is what every existing row is.
* ``hitl_queue.requested_by_user_id`` records who triggered the approval.
  Visibility is derived from the agent's owner, not from this column, so
  legacy rows with NULL keep today's domain-based behaviour.

Owners reference ``users(id) ON DELETE SET NULL``. A personal agent whose
owner is deleted keeps ``visibility='personal'`` with no owner, which makes
it visible to tenant admins only (fail closed), never to other users.

Idempotent and guarded: each statement runs only when its table exists and
uses ``IF NOT EXISTS`` checks. Foreign keys are guarded by *column* (any FK
already on the column), not by name: ``scripts/alembic_migrate.py`` bootstraps
an empty database with ``metadata.create_all`` before upgrading, and a
name-only guard would add a structurally duplicate FK that the index gate
rejects. The ORM declares the same constraint names for consistency. Adding nullable
columns and a column with a constant default is metadata-only on
PostgreSQL 11+, so the migration takes no long table rewrite.
"""

from __future__ import annotations

from alembic import op

revision = "v6z21_resource_ownership"
down_revision = "v6z20_agent_llm_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.agents') IS NOT NULL THEN
                ALTER TABLE agents ADD COLUMN IF NOT EXISTS owner_user_id UUID;
                ALTER TABLE agents
                    ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'tenant';
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_agents_visibility'
                ) THEN
                    ALTER TABLE agents ADD CONSTRAINT ck_agents_visibility
                        CHECK (visibility IN ('tenant', 'personal'));
                END IF;
                IF to_regclass('public.users') IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                    WHERE c.conrelid = 'public.agents'::regclass AND c.contype = 'f'
                      AND a.attname = 'owner_user_id'
                ) THEN
                    ALTER TABLE agents ADD CONSTRAINT fk_agents_owner_user_id
                        FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
                END IF;
                -- FK column leads (scripts/check_database_indexes.py) so a
                -- users DELETE ... SET NULL never scans agents; tenant_id
                -- second serves the owner-scoped list filter.
                CREATE INDEX IF NOT EXISTS ix_agents_owner_user_id
                    ON agents (owner_user_id, tenant_id)
                    WHERE owner_user_id IS NOT NULL;
            END IF;

            IF to_regclass('public.connectors') IS NOT NULL THEN
                ALTER TABLE connectors ADD COLUMN IF NOT EXISTS owner_user_id UUID;
                IF to_regclass('public.users') IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                    WHERE c.conrelid = 'public.connectors'::regclass AND c.contype = 'f'
                      AND a.attname = 'owner_user_id'
                ) THEN
                    ALTER TABLE connectors ADD CONSTRAINT fk_connectors_owner_user_id
                        FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;
                END IF;
                CREATE INDEX IF NOT EXISTS ix_connectors_owner_user_id
                    ON connectors (owner_user_id, tenant_id)
                    WHERE owner_user_id IS NOT NULL;
            END IF;

            IF to_regclass('public.hitl_queue') IS NOT NULL THEN
                ALTER TABLE hitl_queue ADD COLUMN IF NOT EXISTS requested_by_user_id UUID;
                IF to_regclass('public.users') IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                    WHERE c.conrelid = 'public.hitl_queue'::regclass AND c.contype = 'f'
                      AND a.attname = 'requested_by_user_id'
                ) THEN
                    ALTER TABLE hitl_queue ADD CONSTRAINT fk_hitl_queue_requested_by_user_id
                        FOREIGN KEY (requested_by_user_id) REFERENCES users(id) ON DELETE SET NULL;
                END IF;
                CREATE INDEX IF NOT EXISTS ix_hitl_queue_requested_by_user_id
                    ON hitl_queue (requested_by_user_id)
                    WHERE requested_by_user_id IS NOT NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.hitl_queue') IS NOT NULL THEN
                DROP INDEX IF EXISTS ix_hitl_queue_requested_by_user_id;
                ALTER TABLE hitl_queue DROP CONSTRAINT IF EXISTS fk_hitl_queue_requested_by_user_id;
                ALTER TABLE hitl_queue DROP COLUMN IF EXISTS requested_by_user_id;
            END IF;
            IF to_regclass('public.connectors') IS NOT NULL THEN
                DROP INDEX IF EXISTS ix_connectors_owner_user_id;
                ALTER TABLE connectors DROP CONSTRAINT IF EXISTS fk_connectors_owner_user_id;
                ALTER TABLE connectors DROP COLUMN IF EXISTS owner_user_id;
            END IF;
            IF to_regclass('public.agents') IS NOT NULL THEN
                DROP INDEX IF EXISTS ix_agents_owner_user_id;
                ALTER TABLE agents DROP CONSTRAINT IF EXISTS fk_agents_owner_user_id;
                ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_visibility;
                ALTER TABLE agents DROP COLUMN IF EXISTS visibility;
                ALTER TABLE agents DROP COLUMN IF EXISTS owner_user_id;
            END IF;
        END $$;
        """
    )
