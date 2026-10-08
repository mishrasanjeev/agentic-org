# SPDX-License-Identifier: Apache-2.0
"""Conversation sessions.

Revision ID: v6z60_conversation_sessions
Revises: v6z59_agent_debug_sessions
Create Date: 2026-10-07

``conversation_sessions``: the dialogue state of a banking conversation per
channel, company, agent and user (``core/conversation/runtime.py``). One row
per session key and tenant. Tenant scoped under a row-level policy; the
foreign key carries a leading index.
"""

from alembic import op

revision = "v6z60_conversation_sessions"
down_revision = "v6z59_agent_debug_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            session_key VARCHAR(200) NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            agent_id UUID NULL REFERENCES agents(id) ON DELETE SET NULL,
            channel VARCHAR(16) NOT NULL DEFAULT 'web',
            status VARCHAR(16) NOT NULL DEFAULT 'idle',
            intent VARCHAR(40) NULL,
            state JSONB NOT NULL DEFAULT '{}'::jsonb,
            turns INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_conversation_sessions_status CHECK (status IN ('active', 'idle', 'escalated'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_sessions_tenant_key "
        "ON conversation_sessions(tenant_id, session_key);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_sessions_tenant_updated "
        "ON conversation_sessions(tenant_id, updated_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_sessions_agent_id ON conversation_sessions(agent_id);")
    op.execute("ALTER TABLE conversation_sessions ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS conversation_sessions_tenant_isolation ON conversation_sessions;")
    op.execute(
        """
        CREATE POLICY conversation_sessions_tenant_isolation
        ON conversation_sessions
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_sessions;")
