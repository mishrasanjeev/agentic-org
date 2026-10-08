# SPDX-License-Identifier: Apache-2.0
"""Content clauses.

Revision ID: v6z63_content_clauses
Revises: v6z62_content_drafts
Create Date: 2026-10-07

``content_clauses``: the clause library for rule-driven assembly
(``core/content/clauses.py``): a clause's document types, category, order,
conditions, text with placeholders, version and approval. One name per
tenant. Tenant scoped under a row-level policy.
"""

from alembic import op

revision = "v6z63_content_clauses"
down_revision = "v6z62_content_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_clauses (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(80) NOT NULL,
            title VARCHAR(200) NOT NULL DEFAULT '',
            category VARCHAR(24) NOT NULL DEFAULT 'terms',
            document_types JSONB NOT NULL DEFAULT '[]'::jsonb,
            order_index INTEGER NOT NULL DEFAULT 0,
            required BOOLEAN NOT NULL DEFAULT false,
            conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
            text TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(16) NOT NULL DEFAULT 'draft',
            created_by VARCHAR(128) NULL,
            approved_by VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_content_clauses_status CHECK (status IN ('draft', 'approved', 'retired'))
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_content_clauses_tenant_name ON content_clauses(tenant_id, name);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_content_clauses_tenant_status ON content_clauses(tenant_id, status);")
    op.execute("ALTER TABLE content_clauses ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS content_clauses_tenant_isolation ON content_clauses;")
    op.execute(
        """
        CREATE POLICY content_clauses_tenant_isolation
        ON content_clauses
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content_clauses;")
