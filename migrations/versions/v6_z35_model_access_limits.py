# SPDX-License-Identifier: Apache-2.0
"""Model access policies, per-model limits and weighted routing targets for the model gateway.

Revision ID: v6z35_model_access_limits
Revises: v6z34_model_routing_policies
Create Date: 2026-10-02

``model_routing_policies.targets`` lets a routing policy split its matches
across several provider and model pairs by weight. ``model_access_policies``
says which application, principal, agent or business unit may use which
provider or model (first match in priority order; allow with fences, or deny).
``model_limits`` caps the calls in flight and the calls per minute per provider
or per model. All tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z35_model_access_limits"
down_revision = "v6z34_model_routing_policies"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def upgrade() -> None:
    op.execute("ALTER TABLE model_routing_policies ADD COLUMN IF NOT EXISTS targets JSONB NULL;")
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_access_policies (
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
            application VARCHAR(128) NULL,
            principal VARCHAR(255) NULL,
            provider VARCHAR(64) NULL,
            model VARCHAR(128) NULL,
            effect VARCHAR(8) NOT NULL DEFAULT 'allow',
            allowed_providers JSONB NULL,
            allowed_models JSONB NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by VARCHAR(255) NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_model_access_policies_effect CHECK (effect IN ('allow','deny')),
            CONSTRAINT ck_model_access_policies_sensitivity
                CHECK (sensitivity IS NULL OR sensitivity IN ('public','internal','confidential','restricted')),
            CONSTRAINT ck_model_access_policies_priority CHECK (priority >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_access_policies_tenant_enabled "
        "ON model_access_policies(tenant_id, enabled, priority);"
    )
    _rls("model_access_policies")
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_limits (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(128) NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            max_concurrency INTEGER NULL,
            requests_per_minute INTEGER NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by VARCHAR(255) NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_model_limits_one_limit
                CHECK (max_concurrency IS NOT NULL OR requests_per_minute IS NOT NULL),
            CONSTRAINT ck_model_limits_concurrency CHECK (max_concurrency IS NULL OR max_concurrency >= 1),
            CONSTRAINT ck_model_limits_rate CHECK (requests_per_minute IS NULL OR requests_per_minute >= 1)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_limits_tenant_provider_model "
        "ON model_limits(tenant_id, provider, COALESCE(model, ''));"
    )
    _rls("model_limits")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_limits;")
    op.execute("DROP TABLE IF EXISTS model_access_policies;")
    op.execute("ALTER TABLE model_routing_policies DROP COLUMN IF EXISTS targets;")
