"""Durable workflow engine state.

Revision ID: v4911_workflow_state_pg
Revises: v4910_pack_agent_idempotency
Create Date: 2026-05-16

Workflow engine state was previously Redis-only. This migration adds a
PostgreSQL source-of-truth row per engine run plus append-only transition
audit rows. Redis may still cache these records, but correctness now depends
on these tables.
"""

from __future__ import annotations

from alembic import op

revision = "v4911_workflow_state_pg"
down_revision = "v4910_pack_agent_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_run_states (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id VARCHAR(100) NOT NULL,
            tenant_id UUID NULL REFERENCES tenants(id) ON DELETE SET NULL,
            workflow_run_id UUID NULL REFERENCES workflow_runs(id) ON DELETE SET NULL,
            status VARCHAR(30) NOT NULL,
            waiting_step_id VARCHAR(100),
            state JSONB NOT NULL,
            state_hash VARCHAR(64) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_workflow_run_states_run_id UNIQUE (run_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_run_states_tenant_status "
        "ON workflow_run_states (tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_run_states_workflow_run_id "
        "ON workflow_run_states (workflow_run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_run_states_updated_at "
        "ON workflow_run_states (updated_at)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_state_transitions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id VARCHAR(100) NOT NULL
                REFERENCES workflow_run_states(run_id) ON DELETE CASCADE,
            step_id VARCHAR(100),
            previous_state_hash VARCHAR(64),
            new_state_hash VARCHAR(64) NOT NULL,
            actor VARCHAR(100) NOT NULL DEFAULT 'workflow_engine',
            idempotency_key VARCHAR(255),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_state_transitions_run_created "
        "ON workflow_state_transitions (run_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_state_transitions_step "
        "ON workflow_state_transitions (run_id, step_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_state_transitions_idempotency "
        "ON workflow_state_transitions (run_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workflow_state_transitions_idempotency",
        table_name="workflow_state_transitions",
    )
    op.drop_index(
        "ix_workflow_state_transitions_step",
        table_name="workflow_state_transitions",
    )
    op.drop_index(
        "ix_workflow_state_transitions_run_created",
        table_name="workflow_state_transitions",
    )
    op.drop_table("workflow_state_transitions")

    op.drop_index("ix_workflow_run_states_updated_at", table_name="workflow_run_states")
    op.drop_index(
        "ix_workflow_run_states_workflow_run_id",
        table_name="workflow_run_states",
    )
    op.drop_index("ix_workflow_run_states_tenant_status", table_name="workflow_run_states")
    op.drop_table("workflow_run_states")
