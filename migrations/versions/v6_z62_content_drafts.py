# SPDX-License-Identifier: Apache-2.0
"""Content drafts.

Revision ID: v6z62_content_drafts
Revises: v6z61_conversation_supervision
Create Date: 2026-10-07

``content_drafts``: what a content service produced (``core/content/``), the
sources it used, the guardrail outcomes, and the approval a second person
gives a notice or circular before it is final. Tenant scoped under a
row-level policy.
"""

from alembic import op

revision = "v6z62_content_drafts"
down_revision = "v6z61_conversation_supervision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_drafts (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            service VARCHAR(32) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'memo',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            title VARCHAR(300) NOT NULL DEFAULT '',
            input JSONB NOT NULL DEFAULT '{}'::jsonb,
            output JSONB NOT NULL DEFAULT '{}'::jsonb,
            sources JSONB NOT NULL DEFAULT '[]'::jsonb,
            guardrails JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by VARCHAR(128) NULL,
            decided_by VARCHAR(128) NULL,
            decision_notes TEXT NULL,
            decided_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_content_drafts_status
                CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_content_drafts_tenant_status_created "
        "ON content_drafts(tenant_id, status, created_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_content_drafts_tenant_created ON content_drafts(tenant_id, created_at);")
    op.execute("ALTER TABLE content_drafts ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS content_drafts_tenant_isolation ON content_drafts;")
    op.execute(
        """
        CREATE POLICY content_drafts_tenant_isolation
        ON content_drafts
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content_drafts;")
