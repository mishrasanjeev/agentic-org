# SPDX-License-Identifier: Apache-2.0
"""Operator overrides: halt or throttle a model, agent, workflow or the tool pipeline.

Revision ID: v6z32_operator_overrides
Revises: v6z31_a2a_buyers
Create Date: 2026-10-02

One row per override an administrator placed. Active rows (``released_at`` NULL and
not expired) are read by ``core.governance.operator_override`` at the model router,
the agent runner, the workflow engine and the tool dispatch boundary. Tenant-scoped
under row-level security like every other tenant table.
"""

from alembic import op

revision = "v6z32_operator_overrides"
down_revision = "v6z31_a2a_buyers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS operator_overrides (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            target_kind VARCHAR(32) NOT NULL,
            target_id VARCHAR(255) NOT NULL DEFAULT '',
            mode VARCHAR(16) NOT NULL,
            limit_per_minute INTEGER NULL,
            reason TEXT NOT NULL,
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NULL,
            released_at TIMESTAMPTZ NULL,
            released_by VARCHAR(255) NULL,
            CONSTRAINT ck_operator_overrides_target_kind CHECK (
                target_kind IN ('provider','model','agent','all_agents','workflow','connector','tool','tool_pipeline')
            ),
            CONSTRAINT ck_operator_overrides_mode CHECK (mode IN ('halt','throttle')),
            CONSTRAINT ck_operator_overrides_limit CHECK (
                (mode = 'halt' AND limit_per_minute IS NULL) OR (mode = 'throttle' AND limit_per_minute >= 0)
            )
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_operator_overrides_tenant_active ON operator_overrides(tenant_id, released_at);"
    )
    op.execute("ALTER TABLE operator_overrides ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE operator_overrides FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS operator_overrides_tenant_isolation ON operator_overrides;")
    op.execute("""
        CREATE POLICY operator_overrides_tenant_isolation
        ON operator_overrides
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_overrides;")
