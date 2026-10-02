# SPDX-License-Identifier: Apache-2.0
"""Guardrail rules.

Revision ID: v6z38_guardrail_rules
Revises: v6z37_cost_aware_routing
Create Date: 2026-10-02

One row per guardrail rule a tenant administrator writes: the stage, the
detector, the action, the threshold and the agent, use case or risk tier it
is narrowed to. Tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z38_guardrail_rules"
down_revision = "v6z37_cost_aware_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_rules (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            stage VARCHAR(16) NOT NULL,
            detector VARCHAR(32) NOT NULL,
            action VARCHAR(16) NOT NULL DEFAULT 'flag',
            priority INTEGER NOT NULL DEFAULT 100,
            enabled BOOLEAN NOT NULL DEFAULT true,
            threshold DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            agent_id VARCHAR(64) NULL,
            use_case VARCHAR(64) NULL,
            risk_tier VARCHAR(16) NULL,
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            reason TEXT NOT NULL DEFAULT '',
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by VARCHAR(255) NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_guardrail_rules_stage CHECK (stage IN ('input','retrieval','output','action')),
            CONSTRAINT ck_guardrail_rules_action CHECK (action IN ('flag','mask','redact','tokenise','block')),
            CONSTRAINT ck_guardrail_rules_threshold CHECK (threshold >= 0 AND threshold <= 1),
            CONSTRAINT ck_guardrail_rules_priority CHECK (priority >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_rules_tenant_enabled "
        "ON guardrail_rules(tenant_id, enabled, stage, priority);"
    )
    op.execute("ALTER TABLE guardrail_rules ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE guardrail_rules FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS guardrail_rules_tenant_isolation ON guardrail_rules;")
    op.execute("""
        CREATE POLICY guardrail_rules_tenant_isolation
        ON guardrail_rules
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guardrail_rules;")
