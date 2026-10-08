# SPDX-License-Identifier: Apache-2.0
"""Business settings.

Revision ID: v6z67_business_settings
Revises: v6z66_content_draft_edits
Create Date: 2026-10-07

``business_settings``: a tenant's values for the business console's
rules, thresholds and routing (``core/workbench/console.py``), one row
per setting with the previous value and who changed it. Tenant scoped
under a row-level policy.
"""

from alembic import op

revision = "v6z67_business_settings"
down_revision = "v6z66_content_draft_edits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS business_settings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            key VARCHAR(64) NOT NULL,
            value JSONB NOT NULL,
            previous JSONB NULL,
            updated_by VARCHAR(128) NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_settings_tenant_key ON business_settings(tenant_id, key);"
    )
    op.execute("ALTER TABLE business_settings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE business_settings FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS business_settings_tenant_isolation ON business_settings;")
    op.execute(
        """
        CREATE POLICY business_settings_tenant_isolation
        ON business_settings
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS business_settings;")
