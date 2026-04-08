"""v4.1.0 Company model -- CA multi-tenant sub-entity

Revision ID: v410_company
Revises: v400_apex
Create Date: 2026-04-07
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "v410_company"
down_revision = "v400_apex"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. companies table ───────────────────────────────────────────
    op.create_table(
        "companies",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=False),
        sa.Column("tan", sa.String(10), nullable=True),
        sa.Column("cin", sa.String(21), nullable=True),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("registered_address", sa.Text, nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("fy_start_month", sa.String(2), nullable=False, server_default="04"),
        sa.Column("fy_end_month", sa.String(2), nullable=False, server_default="03"),
        sa.Column("signatory_name", sa.String(255), nullable=True),
        sa.Column("signatory_designation", sa.String(100), nullable=True),
        sa.Column("signatory_email", sa.String(255), nullable=True),
        sa.Column("compliance_email", sa.String(255), nullable=True),
        sa.Column("dsc_serial", sa.String(100), nullable=True),
        sa.Column("dsc_expiry", sa.Date, nullable=True),
        sa.Column("pf_registration", sa.String(50), nullable=True),
        sa.Column("esi_registration", sa.String(50), nullable=True),
        sa.Column("pt_registration", sa.String(50), nullable=True),
        sa.Column("bank_name", sa.String(255), nullable=True),
        sa.Column("bank_account_number", sa.String(50), nullable=True),
        sa.Column("bank_ifsc", sa.String(11), nullable=True),
        sa.Column("bank_branch", sa.String(255), nullable=True),
        sa.Column("tally_config", JSONB, nullable=True),
        sa.Column(
            "gst_auto_file",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "user_roles",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "gstin", name="uq_company_tenant_gstin"),
    )
    op.create_index("ix_companies_tenant_id", "companies", ["tenant_id"])

    # ── 2. Add nullable company_id FK to operational tables ──────────
    _tables = [
        "agents",
        "workflow_definitions",
        "workflow_runs",
        "audit_log",
        "tool_calls",
        "connectors",
    ]
    for tbl in _tables:
        op.add_column(
            tbl,
            sa.Column(
                "company_id",
                UUID(as_uuid=True),
                sa.ForeignKey("companies.id"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{tbl}_company_id", tbl, ["company_id"])

    # ── 3. RLS on companies ──────────────────────────────────────────
    op.execute("ALTER TABLE companies ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON companies "
        "USING (tenant_id = current_setting('agenticorg.tenant_id')::UUID)"
    )

    # ── 4. Company-scoped RLS policies on operational tables ─────────
    # Table names are hardcoded — not user input.
    for tbl in ["agents", "workflow_definitions", "workflow_runs", "audit_log"]:
        policy_sql = (
            f"CREATE POLICY company_isolation ON {tbl} USING ("  # noqa: S608
            f"  company_id IS NULL"
            f"  OR company_id IN ("
            f"    SELECT id FROM companies"
            f"    WHERE tenant_id = current_setting('agenticorg.tenant_id')::UUID"
            f"  )"
            f")"
        )
        op.execute(policy_sql)


def downgrade():
    # Drop company-scoped RLS policies
    for tbl in ["audit_log", "workflow_runs", "workflow_definitions", "agents"]:
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {tbl}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON companies")
    op.execute("ALTER TABLE companies DISABLE ROW LEVEL SECURITY")

    # Drop company_id columns (reverse order for FK safety)
    _tables = [
        "connectors",
        "tool_calls",
        "audit_log",
        "workflow_runs",
        "workflow_definitions",
        "agents",
    ]
    for tbl in _tables:
        op.drop_index(f"ix_{tbl}_company_id", table_name=tbl)
        op.drop_column(tbl, "company_id")

    op.drop_index("ix_companies_tenant_id", table_name="companies")
    op.drop_table("companies")
