# SPDX-License-Identifier: Apache-2.0
"""Speech live sessions.

Revision ID: v6z71_speech_live_sessions
Revises: v6z70_speech_summaries
Create Date: 2026-10-07

``speech_live_sessions``: a call as it happens for agent assist, with its
turns encrypted under the tenant's key, the flags raised and the closing
compliance report (``core/speech/assist.py``). Tenant scoped under a
forced row-level policy.
"""

# ENCRYPTED_MIGRATION_HELPER_EXEMPT: additive schema only; upgrade never reads,
# rewrites or deletes ciphertext. Idempotent reruns preserve existing rows.
# PostgreSQL preservation coverage: test_speech_migration_security.py.
from alembic import op

revision = "v6z71_speech_live_sessions"
down_revision = "v6z70_speech_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS speech_live_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            call_ref VARCHAR(128) NOT NULL DEFAULT '',
            agent_id VARCHAR(128) NULL,
            call_type VARCHAR(32) NOT NULL DEFAULT 'service',
            required JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            turn_count INTEGER NOT NULL DEFAULT 0,
            turns_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            flags JSONB NOT NULL DEFAULT '[]'::jsonb,
            report JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_speech_live_sessions_tenant_started ON speech_live_sessions(tenant_id, started_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_speech_live_sessions_tenant_status ON speech_live_sessions(tenant_id, status);"
    )
    op.execute("ALTER TABLE speech_live_sessions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE speech_live_sessions FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS speech_live_sessions_tenant_isolation ON speech_live_sessions;")
    op.execute(
        """
        CREATE POLICY speech_live_sessions_tenant_isolation
        ON speech_live_sessions
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS speech_live_sessions;")
