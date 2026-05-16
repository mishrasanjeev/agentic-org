"""Durable workflow event-wait listeners.

Revision ID: v4912_workflow_event_waits
Revises: v4911_workflow_state_pg
Create Date: 2026-05-16

wait_for_event listener discovery was previously Redis-only. This migration
adds PostgreSQL-backed listener rows so Redis loss cannot make a waiting
workflow miss future matching events.
"""

from __future__ import annotations

from alembic import op

revision = "v4912_workflow_event_waits"
down_revision = "v4911_workflow_state_pg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_event_waits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NULL REFERENCES tenants(id) ON DELETE SET NULL,
            engine_run_id VARCHAR(100) NOT NULL,
            workflow_run_id UUID NULL REFERENCES workflow_runs(id) ON DELETE SET NULL,
            step_id VARCHAR(100) NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            connector VARCHAR(100),
            provider VARCHAR(100),
            match_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'waiting',
            timeout_at TIMESTAMPTZ,
            matched_at TIMESTAMPTZ,
            matched_event_id VARCHAR(255),
            matched_event JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_event_waits_tenant_event_status "
        "ON workflow_event_waits (tenant_id, event_type, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_event_waits_run_step "
        "ON workflow_event_waits (engine_run_id, step_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_event_waits_waiting_timeout "
        "ON workflow_event_waits (timeout_at) WHERE status = 'waiting'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_event_waits_active_run_step "
        "ON workflow_event_waits (engine_run_id, step_id) WHERE status = 'waiting'"
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workflow_event_waits_active_run_step",
        table_name="workflow_event_waits",
    )
    op.drop_index(
        "ix_workflow_event_waits_waiting_timeout",
        table_name="workflow_event_waits",
    )
    op.drop_index("ix_workflow_event_waits_run_step", table_name="workflow_event_waits")
    op.drop_index(
        "ix_workflow_event_waits_tenant_event_status",
        table_name="workflow_event_waits",
    )
    op.drop_table("workflow_event_waits")
