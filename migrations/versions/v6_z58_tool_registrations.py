# SPDX-License-Identifier: Apache-2.0
"""Tool registrations.

Revision ID: v6z58_tool_registrations
Revises: v6z57_agent_memories
Create Date: 2026-10-07

``tool_registrations``: a tenant's registered tools with their input and
output JSON Schemas, risk class and execution envelope
(``core/tool_gateway/registry.py``). One row per tenant and name; tenant
scoped under a row-level policy, enabled and forced so the table owner is
bound by it too.
"""

from alembic import op

revision = "v6z58_tool_registrations"
down_revision = "v6z57_agent_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_registrations (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(160) NOT NULL,
            description VARCHAR(500) NOT NULL DEFAULT '',
            input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_schema JSONB NULL,
            risk VARCHAR(16) NOT NULL DEFAULT 'read',
            timeout_seconds INTEGER NOT NULL DEFAULT 30,
            max_output_bytes INTEGER NOT NULL DEFAULT 256000,
            untrusted_output BOOLEAN NOT NULL DEFAULT TRUE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by UUID NULL,
            updated_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_tool_registrations_risk
                CHECK (risk IN ('read', 'draft', 'internal-write', 'customer-write', 'money', 'destructive')),
            CONSTRAINT ck_tool_registrations_timeout CHECK (timeout_seconds >= 1 AND timeout_seconds <= 300)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_registrations_tenant_name ON tool_registrations(tenant_id, name);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_registrations_tenant_enabled ON tool_registrations(tenant_id, enabled);"
    )
    op.execute("ALTER TABLE tool_registrations ENABLE ROW LEVEL SECURITY;")
    # FORCE: the policy binds the table owner too, so a deployment where the
    # migration role and the application role are the same is still isolated.
    op.execute("ALTER TABLE tool_registrations FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tool_registrations_tenant_isolation ON tool_registrations;")
    op.execute(
        """
        CREATE POLICY tool_registrations_tenant_isolation
        ON tool_registrations
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tool_registrations;")
