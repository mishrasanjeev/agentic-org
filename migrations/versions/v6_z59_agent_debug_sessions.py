# SPDX-License-Identifier: Apache-2.0
"""Agent debug sessions.

Revision ID: v6z59_agent_debug_sessions
Revises: v6z58_tool_registrations
Create Date: 2026-10-07

``agent_debug_sessions``: a run paused at a breakpoint, stepped from the
debugging console (``core/langgraph/debugger.py``): the thread, where it
stopped, the nodes it pauses before and the graph parameters of the next
step. One session per thread and tenant. Tenant scoped under a row-level
policy; the foreign key carries a leading index.
"""

from alembic import op

revision = "v6z59_agent_debug_sessions"
down_revision = "v6z58_tool_registrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_debug_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            thread_id VARCHAR(160) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'paused',
            paused_before JSONB NOT NULL DEFAULT '[]'::jsonb,
            breakpoints JSONB NOT NULL DEFAULT '[]'::jsonb,
            spec JSONB NOT NULL DEFAULT '{}'::jsonb,
            steps_taken INTEGER NOT NULL DEFAULT 0,
            last_status VARCHAR(32) NULL,
            created_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_debug_sessions_status
                CHECK (status IN ('paused', 'running', 'completed', 'failed'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_debug_sessions_tenant_thread "
        "ON agent_debug_sessions(tenant_id, thread_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_debug_sessions_tenant_created "
        "ON agent_debug_sessions(tenant_id, created_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_debug_sessions_agent_id ON agent_debug_sessions(agent_id);")
    op.execute("ALTER TABLE agent_debug_sessions ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS agent_debug_sessions_tenant_isolation ON agent_debug_sessions;")
    op.execute(
        """
        CREATE POLICY agent_debug_sessions_tenant_isolation
        ON agent_debug_sessions
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_debug_sessions;")
