# SPDX-License-Identifier: Apache-2.0
"""Store a paused run's checkpoint thread on its approval row.

Revision ID: v6z23_hitl_checkpoint_thread
Revises: v6z22_langgraph_checkpoints
Create Date: 2026-09-15

``hitl_queue.checkpoint_thread_id`` holds the server-generated LangGraph
thread id (``tenant:<tenant uuid>:run:<hex>``) of the standalone agent run an
approval paused. The checkpoint tables carry no ``tenant_id`` and cannot be
covered by row-level security; this RLS-protected row is the only way to reach
a checkpoint, and ``ck_hitl_queue_checkpoint_thread_tenant`` guarantees the
stored thread belongs to the row's own tenant.

Additive and forward-only in intent: a nullable column (metadata-only on
PostgreSQL 11+) and a check constraint added ``NOT VALID`` and then validated,
so the scan does not hold an exclusive lock. Every existing row is NULL, which
the constraint accepts; no backfill. Guarded by name so a database bootstrapped
from the ORM (which declares the same column and constraint) is left alone.
Downgrade drops both, which strands paused runs: their approvals can no longer
resume them.
"""

from __future__ import annotations

from alembic import op

revision = "v6z23_hitl_checkpoint_thread"
down_revision = "v6z22_langgraph_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.hitl_queue') IS NOT NULL THEN
                ALTER TABLE hitl_queue ADD COLUMN IF NOT EXISTS checkpoint_thread_id VARCHAR(255);
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_hitl_queue_checkpoint_thread_tenant'
                      AND conrelid = 'public.hitl_queue'::regclass
                ) THEN
                    ALTER TABLE hitl_queue ADD CONSTRAINT ck_hitl_queue_checkpoint_thread_tenant
                        CHECK (
                            checkpoint_thread_id IS NULL
                            OR starts_with(checkpoint_thread_id, 'tenant:' || tenant_id::text || ':')
                        ) NOT VALID;
                    ALTER TABLE hitl_queue VALIDATE CONSTRAINT ck_hitl_queue_checkpoint_thread_tenant;
                END IF;
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
                ALTER TABLE hitl_queue DROP CONSTRAINT IF EXISTS ck_hitl_queue_checkpoint_thread_tenant;
                ALTER TABLE hitl_queue DROP COLUMN IF EXISTS checkpoint_thread_id;
            END IF;
        END $$;
        """
    )
