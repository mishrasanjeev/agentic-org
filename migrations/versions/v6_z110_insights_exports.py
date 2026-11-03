# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights background exports: one row per export job and the file it made.

Revision ID: v6z110_insights_exports
Revises: v6z109_insights_value_alerts
Create Date: 2026-11-03

``insights_exports``: who asked (``user_id``) for what (``kind``: a metric, an
explorer table, a run bundle, a view PDF, the finance pack or a period evidence
pack; ``params`` holds the key, filters and period, and the caller's role,
domains and admin flag as the server read them), its state (queued, running,
ready, failed, expired), and for a ready file its object key in the export
bucket, size, SHA-256 and row count. Files expire seven days after the job was
made (``expires_at``); a daily sweep deletes the object and marks the row
expired. ``include_pii`` records that an administrator or auditor asked for
unmasked values. ``signed_by``/``signed_at``/``signature`` hold an auditor's
sign-off of an evidence pack. Tenant scoped under a forced row-level policy.

Downgrade drops the table (export files stay in the bucket until its lifecycle
rule removes them).
"""

from alembic import op

revision = "v6z110_insights_exports"
down_revision = "v6z109_insights_value_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_exports (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            user_id UUID NULL,
            kind VARCHAR(20) NOT NULL,
            format VARCHAR(10) NOT NULL,
            params JSONB NOT NULL DEFAULT '{}'::jsonb,
            include_pii BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(10) NOT NULL DEFAULT 'queued',
            error VARCHAR(500) NULL,
            row_count INTEGER NULL,
            size_bytes BIGINT NULL,
            sha256 VARCHAR(64) NULL,
            storage_key VARCHAR(500) NULL,
            filename VARCHAR(200) NULL,
            signed_by UUID NULL,
            signed_at TIMESTAMPTZ NULL,
            signature JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ NULL,
            finished_at TIMESTAMPTZ NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_insights_exports_kind CHECK (
                kind IN ('metric','explorer','run_bundle','view_pdf','finance_pack','evidence_pack')
            ),
            CONSTRAINT ck_insights_exports_status CHECK (status IN ('queued','running','ready','failed','expired')),
            CONSTRAINT ck_insights_exports_format CHECK (format IN ('csv','xlsx','json','pdf','zip'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_insights_exports_tenant_user_created "
        "ON insights_exports (tenant_id, user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_insights_exports_expiry ON insights_exports (expires_at) "
        "WHERE status IN ('ready','failed','queued','running');"
    )
    op.execute("ALTER TABLE insights_exports ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE insights_exports FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS insights_exports_tenant_isolation ON insights_exports;")
    op.execute(
        """
        CREATE POLICY insights_exports_tenant_isolation
        ON insights_exports
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS insights_exports;")
