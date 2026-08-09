"""Capture shadow HITL learning and calibrate confidence.

Revision ID: v6z8_shadow_hitl
Revises: v6z7_readiness_security
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "v6z8_shadow_hitl"
down_revision = "v6z7_readiness_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("shadow_model_confidence_current", sa.Numeric(4, 3)))
    op.add_column("agents", sa.Column("shadow_human_confidence_current", sa.Numeric(4, 3)))
    op.add_column(
        "agents",
        sa.Column("shadow_feedback_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE agents SET shadow_model_confidence_current = shadow_accuracy_current "
        "WHERE shadow_accuracy_current IS NOT NULL"
    )

    op.add_column(
        "agent_feedback",
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
    )
    op.add_column("agent_feedback", sa.Column("source_event_id", sa.String(200)))
    op.add_column("agent_feedback", sa.Column("actor_id", sa.String(255)))
    op.add_column("agent_feedback", sa.Column("decision", sa.String(100)))
    op.add_column(
        "agent_feedback",
        sa.Column("context", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("agent_feedback", sa.Column("confidence_before", sa.Numeric(4, 3)))
    op.add_column("agent_feedback", sa.Column("confidence_after", sa.Numeric(4, 3)))
    op.add_column(
        "agent_feedback",
        sa.Column("learning_weight", sa.Numeric(5, 2), nullable=False, server_default="1.00"),
    )
    op.create_unique_constraint(
        "uq_agent_feedback_tenant_source_event",
        "agent_feedback",
        ["tenant_id", "source_event_id"],
    )
    op.execute("ALTER TABLE agent_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS agent_feedback_tenant_isolation ON agent_feedback")
    op.execute("""
        CREATE POLICY agent_feedback_tenant_isolation ON agent_feedback
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_feedback_tenant_isolation ON agent_feedback")
    op.execute("ALTER TABLE agent_feedback DISABLE ROW LEVEL SECURITY")
    op.drop_constraint(
        "uq_agent_feedback_tenant_source_event", "agent_feedback", type_="unique"
    )
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
        op.drop_column("agent_feedback", column)
    op.drop_column("agents", "shadow_feedback_count")
    op.drop_column("agents", "shadow_human_confidence_current")
    op.drop_column("agents", "shadow_model_confidence_current")
