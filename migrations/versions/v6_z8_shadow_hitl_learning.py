"""Capture shadow HITL learning and calibrate confidence.

Revision ID: v6z8_shadow_hitl
Revises: v6z7_readiness_security
Create Date: 2026-08-09
"""

from alembic import op

revision = "v6z8_shadow_hitl"
down_revision = "v6z7_readiness_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The migration wrapper bootstraps legacy databases with current ORM
    # metadata before stamping the baseline. Keep every schema operation
    # idempotent so that path and normal version-to-version upgrades both work.
    # Some long-lived production databases were stamped past the original
    # v4 migration without receiving this table. Repair that legacy drift
    # before adding the shadow-learning columns.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_feedback (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            agent_id UUID NOT NULL REFERENCES agents(id),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            run_id VARCHAR(200) NOT NULL,
            feedback_type VARCHAR(30) NOT NULL,
            feedback_text TEXT,
            original_output JSONB,
            corrected_output JSONB,
            applied_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_feedback_agent_id "
        "ON agent_feedback (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_feedback_tenant_id "
        "ON agent_feedback (tenant_id)"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS "
        "shadow_model_confidence_current NUMERIC(4, 3)"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS "
        "shadow_human_confidence_current NUMERIC(4, 3)"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS "
        "shadow_feedback_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "UPDATE agents SET shadow_model_confidence_current = shadow_accuracy_current "
        "WHERE shadow_accuracy_current IS NOT NULL"
    )

    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS "
        "source VARCHAR(30) NOT NULL DEFAULT 'manual'"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(200)"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS actor_id VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS decision VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS "
        "context JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS confidence_before NUMERIC(4, 3)"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS confidence_after NUMERIC(4, 3)"
    )
    op.execute(
        "ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS "
        "learning_weight NUMERIC(5, 2) NOT NULL DEFAULT 1.00"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_feedback_tenant_source_event "
        "ON agent_feedback (tenant_id, source_event_id)"
    )
    op.execute("ALTER TABLE agent_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_feedback FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS agent_feedback_tenant_isolation ON agent_feedback")
    op.execute("""
        CREATE POLICY agent_feedback_tenant_isolation ON agent_feedback
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_feedback_tenant_isolation ON agent_feedback")
    op.execute("ALTER TABLE agent_feedback NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_feedback DISABLE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE agent_feedback DROP CONSTRAINT IF EXISTS "
        "uq_agent_feedback_tenant_source_event"
    )
    op.execute("DROP INDEX IF EXISTS uq_agent_feedback_tenant_source_event")
    for column in (
        "learning_weight",
        "confidence_after",
        "confidence_before",
        "context",
        "decision",
        "actor_id",
        "source_event_id",
        "source",
    ):
        op.execute(f'ALTER TABLE agent_feedback DROP COLUMN IF EXISTS "{column}"')
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS shadow_feedback_count")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS shadow_human_confidence_current")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS shadow_model_confidence_current")
