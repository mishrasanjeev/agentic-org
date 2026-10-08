# SPDX-License-Identifier: Apache-2.0
"""Agent ratings.

Revision ID: v6z49_agent_ratings
Revises: v6z48_agent_registry
Create Date: 2026-10-07

One rating per user and agent (``core/agent_registry/reliability.py``),
tenant-scoped under row-level security and removed with the agent.
"""

from alembic import op

revision = "v6z49_agent_ratings"
down_revision = "v6z48_agent_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_ratings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            user_id UUID NOT NULL,
            score SMALLINT NOT NULL,
            comment VARCHAR(500) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_ratings_score CHECK (score >= 1 AND score <= 5)
        );
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_ratings_agent_user ON agent_ratings(agent_id, user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_ratings_tenant_created ON agent_ratings(tenant_id, created_at);")
    op.execute("ALTER TABLE agent_ratings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE agent_ratings FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS agent_ratings_tenant_isolation ON agent_ratings;")
    op.execute("""
        CREATE POLICY agent_ratings_tenant_isolation
        ON agent_ratings
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_ratings;")
