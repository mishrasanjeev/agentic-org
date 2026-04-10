"""v4.4.0 Persist in-memory stores to PostgreSQL

Migrates ABM accounts/campaigns, report schedules, bridge registry,
and A2A tasks from in-memory dicts to real database tables.

Revision ID: v440_persist_stores
Revises: v430_ca_phase2
Create Date: 2026-04-10
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "v440_persist_stores"
down_revision = "v430_ca_phase2"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. ABM Accounts ─────────────────────────────────────────────
    op.create_table(
        "abm_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(200)),
        sa.Column("tier", sa.String(10), server_default="3"),
        sa.Column("industry", sa.String(100)),
        sa.Column("revenue", sa.String(50)),
        sa.Column("employee_count", sa.String(50)),
        sa.Column("intent_score", sa.Numeric(5, 2), server_default="0"),
        sa.Column("engagement_score", sa.Numeric(5, 2), server_default="0"),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("contacts", JSONB, server_default="[]"),
        sa.Column("metadata_", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_abm_accounts_tenant", "abm_accounts", ["tenant_id"])

    # ── 2. ABM Campaigns ────────────────────────────────────────────
    op.create_table(
        "abm_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("abm_accounts.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(50)),
        sa.Column("budget", sa.Numeric(12, 2)),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("config", JSONB, server_default="{}"),
        sa.Column("results", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_abm_campaigns_account", "abm_campaigns", ["account_id"])

    # ── 3. Report Schedules ─────────────────────────────────────────
    op.create_table(
        "report_schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("recipients", JSONB, server_default="[]"),
        sa.Column("delivery_channel", sa.String(20), server_default="email"),
        sa.Column("format", sa.String(10), server_default="pdf"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("config", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_report_schedules_tenant", "report_schedules", ["tenant_id"])

    # ── 4. Bridge Registry ──────────────────────────────────────────
    op.create_table(
        "bridge_registry",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("bridge_id", sa.String(100), nullable=False, unique=True),
        sa.Column("bridge_type", sa.String(50), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("metadata_", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_bridge_registry_tenant", "bridge_registry", ["tenant_id"])

    # ── 5. A2A Tasks ────────────────────────────────────────────────
    op.create_table(
        "a2a_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("task_id", sa.String(100), nullable=False, unique=True),
        sa.Column("agent_type", sa.String(100)),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("input_data", JSONB, server_default="{}"),
        sa.Column("output_data", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index("ix_a2a_tasks_tenant", "a2a_tasks", ["tenant_id"])
    op.create_index("ix_a2a_tasks_task_id", "a2a_tasks", ["task_id"])


def downgrade():
    op.drop_table("a2a_tasks")
    op.drop_table("bridge_registry")
    op.drop_table("report_schedules")
    op.drop_table("abm_campaigns")
    op.drop_table("abm_accounts")
