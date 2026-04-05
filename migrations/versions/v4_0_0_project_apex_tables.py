"""v4.0.0 Project Apex — 10 new tables for knowledge base, feedback, CDC, billing, voice, RPA, packs

Revision ID: v400_apex
Revises: None (first Alembic migration; prior schema managed via raw SQL 001–011)
Create Date: 2026-04-04
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "v400_apex"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. knowledge_documents ────────────────────────────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="processing"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ragflow_dataset_id", sa.String(200), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])

    # ── 2. agent_feedback ─────────────────────────────────────────────
    op.create_table(
        "agent_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.String(200), nullable=False),
        sa.Column("feedback_type", sa.String(30), nullable=False),
        sa.Column("feedback_text", sa.Text, nullable=True),
        sa.Column("original_output", JSONB, nullable=True),
        sa.Column("corrected_output", JSONB, nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_agent_feedback_agent_id", "agent_feedback", ["agent_id"])
    op.create_index("ix_agent_feedback_tenant_id", "agent_feedback", ["tenant_id"])

    # ── 3. cdc_events ─────────────────────────────────────────────────
    op.create_table(
        "cdc_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("connector", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_cdc_events_tenant_id", "cdc_events", ["tenant_id"])
    op.create_index("ix_cdc_events_connector", "cdc_events", ["connector"])
    op.create_index("ix_cdc_events_event_hash", "cdc_events", ["event_hash"])

    # ── 4. cdc_triggers ───────────────────────────────────────────────
    op.create_table(
        "cdc_triggers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("connector", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflow_definitions.id"), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_cdc_triggers_tenant_id", "cdc_triggers", ["tenant_id"])

    # ── 5. billing_subscriptions ──────────────────────────────────────
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(30), nullable=False, server_default="free"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("current_period_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_billing_subscriptions_tenant_id", "billing_subscriptions", ["tenant_id"])

    # ── 6. billing_invoices ───────────────────────────────────────────
    op.create_table(
        "billing_invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subscription_id", UUID(as_uuid=True), sa.ForeignKey("billing_subscriptions.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("external_invoice_id", sa.String(200), nullable=False),
        sa.Column("invoice_url", sa.Text, nullable=True),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_billing_invoices_tenant_id", "billing_invoices", ["tenant_id"])

    # ── 7. password_history ───────────────────────────────────────────
    op.create_table(
        "password_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_password_history_user_id", "password_history", ["user_id"])

    # ── 8. rpa_scripts ────────────────────────────────────────────────
    op.create_table(
        "rpa_scripts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("target_url", sa.String(500), nullable=False),
        sa.Column("script_path", sa.String(500), nullable=False),
        sa.Column("parameters_schema", JSONB, nullable=True),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(30), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_rpa_scripts_tenant_id", "rpa_scripts", ["tenant_id"])

    # ── 9. voice_sessions ─────────────────────────────────────────────
    op.create_table(
        "voice_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("sip_provider", sa.String(30), nullable=False),
        sa.Column("phone_number", sa.String(30), nullable=False),
        sa.Column("call_direction", sa.String(10), nullable=False),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("recording_url", sa.Text, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="ringing"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_voice_sessions_tenant_id", "voice_sessions", ["tenant_id"])
    op.create_index("ix_voice_sessions_agent_id", "voice_sessions", ["agent_id"])

    # ── 10. industry_pack_installs ────────────────────────────────────
    op.create_table(
        "industry_pack_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("pack_name", sa.String(100), nullable=False),
        sa.Column("installed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("agent_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("workflow_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index("ix_industry_pack_installs_tenant_id", "industry_pack_installs", ["tenant_id"])
    op.create_index("ix_industry_pack_installs_pack_name", "industry_pack_installs", ["pack_name"])


def downgrade():
    # Drop in reverse order to respect foreign-key dependencies
    op.drop_table("industry_pack_installs")
    op.drop_table("voice_sessions")
    op.drop_table("rpa_scripts")
    op.drop_table("password_history")
    op.drop_table("billing_invoices")
    op.drop_table("billing_subscriptions")
    op.drop_table("cdc_triggers")
    op.drop_table("cdc_events")
    op.drop_table("agent_feedback")
    op.drop_table("knowledge_documents")
