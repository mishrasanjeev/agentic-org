"""v4.2.0 CA paid add-on features -- approvals, subscriptions, GSTN uploads

Revision ID: v420_ca_features
Revises: v410_company
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "v420_ca_features"
down_revision = "v410_company"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. ca_subscriptions ─────────────────────────────────────────
    op.create_table(
        "ca_subscriptions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("plan", sa.String(50), nullable=False, server_default="ca_pro"),
        sa.Column("status", sa.String(20), nullable=False, server_default="trial"),
        sa.Column("max_clients", sa.Integer, nullable=False, server_default="7"),
        sa.Column("price_inr", sa.Integer, nullable=False, server_default="4999"),
        sa.Column("price_usd", sa.Integer, nullable=False, server_default="59"),
        sa.Column(
            "billing_cycle", sa.String(20), nullable=False, server_default="monthly"
        ),
        sa.Column("trial_ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "current_period_start", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("current_period_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_ca_sub_tenant"),
    )
    op.create_index(
        "ix_ca_subscriptions_tenant_id", "ca_subscriptions", ["tenant_id"]
    )

    # RLS
    op.execute("ALTER TABLE ca_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON ca_subscriptions "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 2. filing_approvals ─────────────────────────────────────────
    op.create_table(
        "filing_approvals",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column("filing_type", sa.String(50), nullable=False),
        sa.Column("filing_period", sa.String(20), nullable=False),
        sa.Column(
            "filing_data",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "auto_approved",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_filing_approvals_tenant_id", "filing_approvals", ["tenant_id"]
    )
    op.create_index(
        "ix_filing_approvals_company_id", "filing_approvals", ["company_id"]
    )
    op.create_index(
        "ix_filing_approvals_status", "filing_approvals", ["status"]
    )

    # RLS
    op.execute("ALTER TABLE filing_approvals ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON filing_approvals "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 3. gstn_uploads ─────────────────────────────────────────────
    op.create_table(
        "gstn_uploads",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column("upload_type", sa.String(50), nullable=False),
        sa.Column("filing_period", sa.String(20), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="generated"
        ),
        sa.Column("gstn_arn", sa.String(100), nullable=True),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_gstn_uploads_tenant_id", "gstn_uploads", ["tenant_id"])
    op.create_index("ix_gstn_uploads_company_id", "gstn_uploads", ["company_id"])

    # RLS
    op.execute("ALTER TABLE gstn_uploads ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON gstn_uploads "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 4. New columns on companies ─────────────────────────────────
    for col_name, col_type, default in [
        ("subscription_status", sa.String(20), "trial"),
        ("client_health_score", sa.Integer, None),
        ("document_vault_enabled", sa.Boolean, None),
        ("compliance_alerts_email", sa.String(255), None),
    ]:
        op.add_column(
            "companies",
            sa.Column(col_name, col_type, nullable=True, server_default=default),
        )


def downgrade():
    # Drop new columns
    for col in [
        "compliance_alerts_email",
        "document_vault_enabled",
        "client_health_score",
        "subscription_status",
    ]:
        op.drop_column("companies", col)

    # Drop tables (reverse order)
    for tbl in ["gstn_uploads", "filing_approvals", "ca_subscriptions"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
        op.drop_table(tbl)
