# SPDX-License-Identifier: Apache-2.0
"""Routing records for the model gateway.

Revision ID: v6z36_model_gateway_records
Revises: v6z35_model_access_limits
Create Date: 2026-10-02

One signed row per model call made while the gateway was on for the tenant:
the correlation id, use case, agent, policies evaluated, what was requested
and chosen, fallback, outcome, latency, admission wait, tokens and cost.
Tenant-scoped under row-level security; pruned by the retention task.
"""

from alembic import op

revision = "v6z36_model_gateway_records"
down_revision = "v6z35_model_access_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_gateway_records (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            correlation_id VARCHAR(128) NOT NULL,
            use_case VARCHAR(64) NOT NULL DEFAULT '',
            agent_id VARCHAR(64) NULL,
            policy_id VARCHAR(64) NULL,
            access_policy_id VARCHAR(64) NULL,
            requested_provider VARCHAR(64) NULL,
            requested_model VARCHAR(128) NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL,
            fallback_from VARCHAR(128) NULL,
            restricted BOOLEAN NOT NULL DEFAULT false,
            outcome VARCHAR(16) NOT NULL,
            error_type VARCHAR(128) NULL,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            admission_wait_ms INTEGER NULL,
            tokens INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NULL,
            output_tokens INTEGER NULL,
            cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            signature VARCHAR(512) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_gateway_records_tenant_created "
        "ON model_gateway_records(tenant_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_gateway_records_tenant_correlation "
        "ON model_gateway_records(tenant_id, correlation_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_model_gateway_records_tenant_agent "
        "ON model_gateway_records(tenant_id, agent_id);"
    )
    op.execute("ALTER TABLE model_gateway_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE model_gateway_records FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS model_gateway_records_tenant_isolation ON model_gateway_records;")
    op.execute("""
        CREATE POLICY model_gateway_records_tenant_isolation
        ON model_gateway_records
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_gateway_records;")
