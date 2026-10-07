# SPDX-License-Identifier: Apache-2.0
"""Agent memories.

Revision ID: v6z57_agent_memories
Revises: v6z56_finops_thresholds
Create Date: 2026-10-07

``agent_memories``: long-term memory entries about a subject with a kind,
importance, source and expiry (``core/memory/long_term.py``). An entry may
belong to one agent (and goes with it) or be shared by the tenant's agents.
Tenant scoped under a row-level policy; the foreign key carries a leading
index.
"""

from alembic import op

revision = "v6z57_agent_memories"
down_revision = "v6z56_finops_thresholds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_memories (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            agent_id UUID NULL REFERENCES agents(id) ON DELETE CASCADE,
            subject VARCHAR(128) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            importance SMALLINT NOT NULL DEFAULT 3,
            source VARCHAR(8) NOT NULL DEFAULT 'api',
            run_id VARCHAR(64) NULL,
            retention_days INTEGER NOT NULL DEFAULT 30,
            created_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            last_recalled_at TIMESTAMPTZ NULL,
            recall_count INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT ck_agent_memories_kind CHECK (kind IN ('fact', 'preference', 'summary', 'event')),
            CONSTRAINT ck_agent_memories_importance CHECK (importance >= 1 AND importance <= 5),
            CONSTRAINT ck_agent_memories_source CHECK (source IN ('api', 'run'))
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_tenant_subject ON agent_memories(tenant_id, subject);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_tenant_expires ON agent_memories(tenant_id, expires_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_agent_id ON agent_memories(agent_id);")
    op.execute("ALTER TABLE agent_memories ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS agent_memories_tenant_isolation ON agent_memories;")
    op.execute(
        """
        CREATE POLICY agent_memories_tenant_isolation
        ON agent_memories
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_memories;")
