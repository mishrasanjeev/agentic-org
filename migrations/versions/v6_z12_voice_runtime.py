"""Add durable voice call runtime records.

Revision ID: v6z12_voice_runtime
Revises: v6z11_shadow_scored
Create Date: 2026-08-26
"""

from alembic import op

revision = "v6z12_voice_runtime"
down_revision = "v6z11_shadow_scored"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS voice_calls (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            provider VARCHAR(32) NOT NULL,
            provider_call_id VARCHAR(160) NOT NULL,
            direction VARCHAR(16) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'queued',
            from_number VARCHAR(32),
            to_number VARCHAR(32),
            transcript_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            turn_count INTEGER NOT NULL DEFAULT 0,
            duration_seconds INTEGER,
            last_error TEXT,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_voice_calls_direction CHECK (direction IN ('inbound', 'outbound')),
            CONSTRAINT ck_voice_calls_status CHECK (
                status IN (
                    'queued', 'ringing', 'in_progress', 'completed', 'busy',
                    'failed', 'no_answer', 'cancelled'
                )
            ),
            CONSTRAINT ck_voice_calls_turn_count CHECK (turn_count >= 0),
            CONSTRAINT ck_voice_calls_duration CHECK (
                duration_seconds IS NULL OR duration_seconds >= 0
            )
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_voice_calls_tenant_provider_ref
        ON voice_calls (tenant_id, provider, provider_call_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_voice_calls_agent_id
        ON voice_calls (agent_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_voice_calls_tenant_agent_created
        ON voice_calls (tenant_id, agent_id, created_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_voice_calls_tenant_status
        ON voice_calls (tenant_id, status);
    """)
    op.execute("ALTER TABLE voice_calls ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE voice_calls FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS voice_calls_tenant_isolation ON voice_calls;")
    op.execute("""
        CREATE POLICY voice_calls_tenant_isolation ON voice_calls
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS voice_calls_tenant_isolation ON voice_calls;")
    op.execute("DROP INDEX IF EXISTS ix_voice_calls_tenant_status;")
    op.execute("DROP INDEX IF EXISTS ix_voice_calls_tenant_agent_created;")
    op.execute("DROP INDEX IF EXISTS ix_voice_calls_agent_id;")
    op.execute("DROP INDEX IF EXISTS uq_voice_calls_tenant_provider_ref;")
    op.execute("DROP TABLE IF EXISTS voice_calls;")
