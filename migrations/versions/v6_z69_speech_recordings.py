# SPDX-License-Identifier: Apache-2.0
"""Speech recordings.

Revision ID: v6z69_speech_recordings
Revises: v6z68_force_rls_programme_tables
Create Date: 2026-10-07

``speech_recordings``: a kept recording with its audio, the segments and
speakers the diariser found, and the transcript encrypted under the
tenant's key (``core/speech/store.py``). Tenant scoped under a row-level
policy, forced.
"""

# ENCRYPTED_MIGRATION_HELPER_EXEMPT: additive schema only; upgrade never reads,
# rewrites or deletes ciphertext. Idempotent reruns preserve existing rows.
# PostgreSQL preservation coverage: test_speech_migration_security.py.
from alembic import op

revision = "v6z69_speech_recordings"
down_revision = "v6z68_force_rls_programme_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS speech_recordings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            filename VARCHAR(255) NOT NULL DEFAULT '',
            mime_type VARCHAR(100) NOT NULL DEFAULT 'audio/wav',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            content BYTEA NOT NULL,
            duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            sample_rate INTEGER NOT NULL DEFAULT 0,
            channels INTEGER NOT NULL DEFAULT 1,
            channel_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'received',
            engine VARCHAR(32) NULL,
            language VARCHAR(16) NOT NULL DEFAULT 'en',
            segments JSONB NOT NULL DEFAULT '[]'::jsonb,
            speakers JSONB NOT NULL DEFAULT '{}'::jsonb,
            transcript_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_error TEXT NULL,
            created_by VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_speech_recordings_tenant_created ON speech_recordings(tenant_id, created_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_speech_recordings_tenant_status ON speech_recordings(tenant_id, status);")
    op.execute("ALTER TABLE speech_recordings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE speech_recordings FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS speech_recordings_tenant_isolation ON speech_recordings;")
    op.execute(
        """
        CREATE POLICY speech_recordings_tenant_isolation
        ON speech_recordings
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS speech_recordings;")
