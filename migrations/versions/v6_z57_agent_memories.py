# SPDX-License-Identifier: Apache-2.0
"""Agent memories.

Revision ID: v6z57_agent_memories
Revises: v6z56_finops_thresholds
Create Date: 2026-10-07

``agent_memories``: long-term memory entries about a subject with a kind,
importance, source and expiry (``core/memory/long_term.py``). An entry may
belong to one agent (and goes with it) or be shared by the tenant's agents.
Tenant scoped under a forced row-level policy (the application role owns the
table, so an enabled-only policy would not bind it); the foreign key carries a
leading index.

The same content about the same subject, for the same agent or shared, is one
entry: ``content_hash`` (SHA-256 of the stored content) backs two partial
unique indexes, one for shared entries and one for agent entries, which the
store's ``INSERT ... ON CONFLICT`` refreshes atomically. A database that ran an
earlier form of this revision gains the column, has it backfilled, keeps the
latest-expiring copy of any duplicates and is then forced under its policy.
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
            content_hash VARCHAR(64) NOT NULL,
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
    # A table created by an earlier form of this revision: add and backfill the
    # hash, drop duplicate copies (keep the latest-expiring) so the unique
    # indexes can be built. The policy is lifted for the backfill so a table
    # already forced under it is still reached by the owner.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('agent_memories') IS NOT NULL THEN
                ALTER TABLE agent_memories NO FORCE ROW LEVEL SECURITY;
                ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);
                UPDATE agent_memories
                    SET content_hash = encode(sha256(convert_to(content, 'UTF8')), 'hex')
                    WHERE content_hash IS NULL;
                DELETE FROM agent_memories older
                    USING agent_memories newer
                    WHERE older.tenant_id = newer.tenant_id
                      AND older.subject = newer.subject
                      AND older.agent_id IS NOT DISTINCT FROM newer.agent_id
                      AND older.content_hash = newer.content_hash
                      AND (older.expires_at, older.id) < (newer.expires_at, newer.id);
                ALTER TABLE agent_memories ALTER COLUMN content_hash SET NOT NULL;
            END IF;
        END
        $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_tenant_subject ON agent_memories(tenant_id, subject);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_tenant_expires ON agent_memories(tenant_id, expires_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memories_agent_id ON agent_memories(agent_id);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memories_shared_content "
        "ON agent_memories(tenant_id, subject, content_hash) WHERE agent_id IS NULL;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memories_agent_content "
        "ON agent_memories(tenant_id, subject, agent_id, content_hash) WHERE agent_id IS NOT NULL;"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('agent_memories') IS NOT NULL THEN
                ALTER TABLE agent_memories ENABLE ROW LEVEL SECURITY;
                ALTER TABLE agent_memories FORCE ROW LEVEL SECURITY;
            END IF;
        END
        $$;
        """
    )
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
