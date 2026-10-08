# SPDX-License-Identifier: Apache-2.0
"""Processed documents for review.

Revision ID: v6z64_idp_documents
Revises: v6z63_content_clauses
Create Date: 2026-10-07

``idp_documents``: a file ``POST /idp/analyse`` kept with the pipeline's
result, the reviewer's corrections and the decision (``core/idp/store.py``).
The file is kept so the review overlay can render its pages. Tenant scoped
under a row-level policy.
"""

from alembic import op

revision = "v6z64_idp_documents"
down_revision = "v6z63_content_clauses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idp_documents (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            filename VARCHAR(255) NOT NULL DEFAULT '',
            mime_type VARCHAR(100) NOT NULL DEFAULT 'application/pdf',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            content BYTEA NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'processed',
            pages INTEGER NOT NULL DEFAULT 0,
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            corrections JSONB NOT NULL DEFAULT '{}'::jsonb,
            review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            review_notes TEXT NULL,
            created_by VARCHAR(128) NULL,
            reviewed_by VARCHAR(128) NULL,
            reviewed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_idp_documents_status CHECK (status IN ('processed', 'review', 'approved', 'rejected'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_idp_documents_tenant_status_created "
        "ON idp_documents(tenant_id, status, created_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_idp_documents_tenant_created ON idp_documents(tenant_id, created_at);")
    op.execute("ALTER TABLE idp_documents ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS idp_documents_tenant_isolation ON idp_documents;")
    op.execute(
        """
        CREATE POLICY idp_documents_tenant_isolation
        ON idp_documents
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS idp_documents;")
