# SPDX-License-Identifier: Apache-2.0
"""Prompt change requests.

Revision ID: v6z43_prompt_change_requests
Revises: v6z42_synthetic_checks
Create Date: 2026-10-04

Maker-checker for prompt templates: a proposed change waits in
``prompt_change_requests`` for a second person's decision (tenant-scoped under
row-level security), and the template's edit history records who approved a
change and the request it came from.
"""

from alembic import op

revision = "v6z43_prompt_change_requests"
down_revision = "v6z42_synthetic_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prompt_change_requests (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            template_id UUID NULL REFERENCES prompt_templates(id),
            kind VARCHAR(16) NOT NULL,
            domain VARCHAR(50) NOT NULL,
            proposed JSONB NOT NULL DEFAULT '{}'::jsonb,
            base_updated_at TIMESTAMPTZ NULL,
            reason VARCHAR(500) NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            requested_by VARCHAR(255) NOT NULL,
            requested_by_user UUID NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_by VARCHAR(255) NULL,
            decided_at TIMESTAMPTZ NULL,
            decision_note VARCHAR(500) NULL,
            CONSTRAINT ck_prompt_change_requests_kind CHECK (kind IN ('create','update','rollback','delete')),
            CONSTRAINT ck_prompt_change_requests_status
                CHECK (status IN ('pending','approved','rejected','withdrawn','stale')),
            CONSTRAINT ck_prompt_change_requests_checker CHECK (decided_by IS NULL OR decided_by <> requested_by
                OR status = 'withdrawn')
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_change_requests_template ON prompt_change_requests(template_id, status);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_prompt_change_requests_one_pending "
        "ON prompt_change_requests(template_id) WHERE status = 'pending' AND template_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_change_requests_tenant_status "
        "ON prompt_change_requests(tenant_id, status, requested_at);"
    )
    op.execute("ALTER TABLE prompt_change_requests ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE prompt_change_requests FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS prompt_change_requests_tenant_isolation ON prompt_change_requests;")
    op.execute("""
        CREATE POLICY prompt_change_requests_tenant_isolation
        ON prompt_change_requests
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)
    op.execute("ALTER TABLE prompt_template_edit_history ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255) NULL;")
    op.execute("ALTER TABLE prompt_template_edit_history ADD COLUMN IF NOT EXISTS change_request_id UUID NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE prompt_template_edit_history DROP COLUMN IF EXISTS change_request_id;")
    op.execute("ALTER TABLE prompt_template_edit_history DROP COLUMN IF EXISTS approved_by;")
    op.execute("DROP TABLE IF EXISTS prompt_change_requests;")
