"""v4.3.0 CA Phase 2 -- credential vault, compliance calendar, partner KPIs

Revision ID: v430_ca_phase2
Revises: v420_ca_features
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "v430_ca_phase2"
down_revision = "v420_ca_features"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. gstn_credentials (encrypted vault) ──────────────────────
    op.create_table(
        "gstn_credentials",
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
        sa.Column("gstin", sa.String(15), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_encrypted", sa.Text, nullable=False),
        sa.Column(
            "encryption_key_ref",
            sa.String(100),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "portal_type",
            sa.String(20),
            nullable=False,
            server_default="gstn",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "company_id", "portal_type", name="uq_gstn_cred_company"
        ),
    )
    op.create_index(
        "ix_gstn_credentials_tenant_id", "gstn_credentials", ["tenant_id"]
    )
    op.create_index(
        "ix_gstn_credentials_company_id", "gstn_credentials", ["company_id"]
    )
    op.execute("ALTER TABLE gstn_credentials ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON gstn_credentials "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 2. compliance_deadlines ─────────────────────────────────────
    op.create_table(
        "compliance_deadlines",
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
        sa.Column("deadline_type", sa.String(50), nullable=False),
        sa.Column("filing_period", sa.String(20), nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column(
            "alert_7d_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "alert_1d_sent",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "filed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("filed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "company_id",
            "deadline_type",
            "filing_period",
            name="uq_deadline_company_type_period",
        ),
    )
    op.create_index(
        "ix_compliance_deadlines_tenant_id",
        "compliance_deadlines",
        ["tenant_id"],
    )
    op.create_index(
        "ix_compliance_deadlines_company_id",
        "compliance_deadlines",
        ["company_id"],
    )
    op.create_index(
        "ix_compliance_deadlines_due_date",
        "compliance_deadlines",
        ["due_date"],
    )
    op.execute("ALTER TABLE compliance_deadlines ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON compliance_deadlines "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 3. gstn_auto_upload flag on companies ──────────────────────
    op.add_column(
        "companies",
        sa.Column(
            "gstn_auto_upload",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )


def downgrade():
    op.drop_column("companies", "gstn_auto_upload")

    for tbl in ["compliance_deadlines", "gstn_credentials"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
        op.drop_table(tbl)
