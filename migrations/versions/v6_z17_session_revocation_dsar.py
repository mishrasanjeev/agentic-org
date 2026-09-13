"""Session revocation watermark + durable DSAR request ledger.

Revision ID: v6z17_sessions_dsar
Revises: v6z16_rls_coverage
Create Date: 2026-09-13

Audit 2026-09-13 (enterprise bug sweep):

* ``users.sessions_invalid_before`` — deactivating a member, resetting a
  password, or "log out everywhere" previously left every outstanding JWT
  valid until natural expiry. The auth middleware now rejects legacy tokens
  whose ``iat`` is older than this watermark (and inactive users outright).
  Nullable, no backfill: NULL means "no revocation has happened".

* ``dsar_requests`` — ``POST /api/v1/dsar/{access,erase,export}`` used to
  answer ``status: processing`` while nothing was persisted or processed.
  Requests are now durable rows with an honest status that clients poll via
  ``GET /api/v1/dsar/{request_id}``. Tenant-scoped => RLS enforced with the
  same policy shape as v6z16.
"""

from alembic import op

revision = "v6z17_sessions_dsar"
down_revision = "v6z16_rls_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dsar_requests (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            request_type VARCHAR(20) NOT NULL,
            subject_email VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'received',
            requested_by VARCHAR(255) NOT NULL,
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT ck_dsar_requests_type
                CHECK (request_type IN ('access', 'erase', 'export')),
            CONSTRAINT ck_dsar_requests_status
                CHECK (status IN ('received', 'processing', 'completed', 'failed'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_dsar_requests_tenant_created "
        "ON dsar_requests (tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE dsar_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dsar_requests FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS dsar_requests_tenant_isolation ON dsar_requests")
    op.execute(
        """
        CREATE POLICY dsar_requests_tenant_isolation ON dsar_requests
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS dsar_requests_tenant_isolation ON dsar_requests")
    op.execute("DROP TABLE IF EXISTS dsar_requests")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS sessions_invalid_before")
