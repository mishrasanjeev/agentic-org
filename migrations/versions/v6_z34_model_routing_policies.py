# SPDX-License-Identifier: Apache-2.0
"""Model routing policies for the model gateway.

Revision ID: v6z34_model_routing_policies
Revises: v6z33_provider_attestations
Create Date: 2026-10-02

One row per routing policy a tenant administrator writes: what requests it
matches (use case, data sensitivity, agent, business unit, language) and what a
match gets (provider, model or cost tier, the providers allowed, in-region only).
``core.governance.model_gateway`` evaluates the enabled rows in priority order.
Tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z34_model_routing_policies"
down_revision = "v6z33_provider_attestations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_routing_policies (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            enabled BOOLEAN NOT NULL DEFAULT true,
            use_case VARCHAR(64) NULL,
            sensitivity VARCHAR(16) NULL,
            agent_id VARCHAR(64) NULL,
            business_unit VARCHAR(64) NULL,
            language VARCHAR(16) NULL,
            provider VARCHAR(64) NULL,
            model VARCHAR(128) NULL,
            tier VARCHAR(8) NULL,
            allowed_providers JSONB NULL,
            in_region_only BOOLEAN NOT NULL DEFAULT false,
            reason TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by VARCHAR(255) NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_model_routing_policies_sensitivity
                CHECK (sensitivity IS NULL OR sensitivity IN ('public','internal','confidential','restricted')),
            CONSTRAINT ck_model_routing_policies_tier CHECK (tier IS NULL OR tier IN ('tier1','tier2','tier3')),
            CONSTRAINT ck_model_routing_policies_priority CHECK (priority >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_routing_policies_tenant_enabled "
        "ON model_routing_policies(tenant_id, enabled, priority);"
    )
    op.execute("ALTER TABLE model_routing_policies ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE model_routing_policies FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS model_routing_policies_tenant_isolation ON model_routing_policies;")
    op.execute("""
        CREATE POLICY model_routing_policies_tenant_isolation
        ON model_routing_policies
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_routing_policies;")
