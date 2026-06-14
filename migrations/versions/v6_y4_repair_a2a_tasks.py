"""Repair missing persisted A2A task table.

Revision ID: v6y4_repair_a2a_tasks
Revises: v6y3_industry_pack_uuid_default
Create Date: 2026-06-14

Production E2E on 2026-06-14 found that ``/api/v1/a2a/tasks`` could
return 500 even after the database was stamped at the current Alembic
head: the original v4.4.0 migration was marked as applied, but the
``a2a_tasks`` table was absent. This mirrors the older
``report_schedules`` drift class and must be repaired at the current
head so already-stamped environments pick it up on the next deploy.
"""

from __future__ import annotations

from alembic import op

revision = "v6y4_repair_a2a_tasks"
down_revision = "v6y3_industry_pack_uuid_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS a2a_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            task_id VARCHAR(100) NOT NULL UNIQUE,
            agent_type VARCHAR(100),
            status VARCHAR(20) DEFAULT 'pending',
            input_data JSONB DEFAULT '{}'::jsonb,
            output_data JSONB,
            error TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_tasks_tenant
            ON a2a_tasks(tenant_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_a2a_tasks_task_id
            ON a2a_tasks(task_id);
    """)
    op.execute("ALTER TABLE a2a_tasks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE a2a_tasks FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS a2a_tasks_tenant_isolation ON a2a_tasks;")
    op.execute("""
        CREATE POLICY a2a_tasks_tenant_isolation ON a2a_tasks
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    # No-op by design. v4.4.0 remains the canonical owner of this table;
    # this revision only repairs environments that were stamped past it.
    pass
