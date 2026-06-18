"""CA firm client operations: PT, client portal, and client billing.

Revision ID: v4917_ca_firm_client_ops
Revises: v4916_merge_p0_heads
Create Date: 2026-06-12
"""

from __future__ import annotations

# ruff: noqa: S608

from alembic import op

revision = "v4917_ca_firm_client_ops"
down_revision = "v4916_merge_p0_heads"
branch_labels = None
depends_on = None


TENANT_RLS_TABLES = (
    "professional_tax_registrations",
    "professional_tax_returns",
    "client_portal_invites",
    "client_portal_documents",
    "ca_service_plans",
    "ca_client_invoices",
    "ca_client_payments",
)


def _enable_tenant_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
    op.execute(f"""
        CREATE POLICY {table_name}_tenant_isolation ON {table_name}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS professional_tax_registrations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            state_code VARCHAR(2) NOT NULL,
            registration_number VARCHAR(100) NOT NULL,
            employer_name VARCHAR(255),
            portal_username VARCHAR(255),
            credential_ref VARCHAR(500),
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            last_verified_at TIMESTAMPTZ,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_pt_registration_tenant_company_state
                UNIQUE (tenant_id, company_id, state_code)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pt_registrations_tenant_company "
        "ON professional_tax_registrations (tenant_id, company_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS professional_tax_returns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            registration_id UUID REFERENCES professional_tax_registrations(id) ON DELETE SET NULL,
            state_code VARCHAR(2) NOT NULL,
            filing_period VARCHAR(20) NOT NULL,
            employer_name VARCHAR(255),
            employee_count INTEGER NOT NULL DEFAULT 0,
            gross_wages NUMERIC(14, 2) NOT NULL,
            pt_amount NUMERIC(14, 2) NOT NULL,
            interest NUMERIC(14, 2) NOT NULL DEFAULT 0,
            penalty NUMERIC(14, 2) NOT NULL DEFAULT 0,
            total_payable NUMERIC(14, 2) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'draft',
            challan_number VARCHAR(100),
            acknowledgement_number VARCHAR(100),
            prepared_by VARCHAR(255),
            submitted_at TIMESTAMPTZ,
            line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            portal_response JSONB NOT NULL DEFAULT '{}'::jsonb,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_pt_return_tenant_company_state_period
                UNIQUE (tenant_id, company_id, state_code, filing_period)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pt_returns_tenant_company_period "
        "ON professional_tax_returns (tenant_id, company_id, filing_period)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS client_portal_invites (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            client_email VARCHAR(255) NOT NULL,
            client_name VARCHAR(255),
            role VARCHAR(30) NOT NULL DEFAULT 'client_admin',
            token_hash VARCHAR(64) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            last_sent_at TIMESTAMPTZ,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_client_portal_invite_token_hash UNIQUE (token_hash)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_portal_invites_tenant_company "
        "ON client_portal_invites (tenant_id, company_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_portal_invites_email "
        "ON client_portal_invites (tenant_id, lower(client_email))"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS client_portal_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            document_type VARCHAR(80) NOT NULL,
            filing_period VARCHAR(20),
            status VARCHAR(30) NOT NULL DEFAULT 'published',
            source_type VARCHAR(80),
            source_id UUID,
            file_url VARCHAR(1000),
            visible_to_client BOOLEAN NOT NULL DEFAULT TRUE,
            summary TEXT,
            uploaded_by VARCHAR(255),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_client_portal_documents_tenant_company "
        "ON client_portal_documents (tenant_id, company_id)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS ca_service_plans (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
            currency VARCHAR(3) NOT NULL DEFAULT 'INR',
            default_fee NUMERIC(14, 2) NOT NULL,
            tax_rate_percent NUMERIC(6, 3) NOT NULL DEFAULT 18,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_ca_service_plan_tenant_name UNIQUE (tenant_id, name)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ca_client_invoices (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            service_plan_id UUID REFERENCES ca_service_plans(id) ON DELETE SET NULL,
            invoice_number VARCHAR(60) NOT NULL,
            issue_date DATE NOT NULL,
            due_date DATE NOT NULL,
            period_start DATE,
            period_end DATE,
            currency VARCHAR(3) NOT NULL DEFAULT 'INR',
            subtotal NUMERIC(14, 2) NOT NULL,
            tax NUMERIC(14, 2) NOT NULL DEFAULT 0,
            total NUMERIC(14, 2) NOT NULL,
            amount_paid NUMERIC(14, 2) NOT NULL DEFAULT 0,
            balance_due NUMERIC(14, 2) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'draft',
            line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            notes TEXT,
            sent_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            created_by VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_ca_client_invoice_number UNIQUE (tenant_id, invoice_number)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ca_client_invoices_tenant_company "
        "ON ca_client_invoices (tenant_id, company_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ca_client_invoices_status_due "
        "ON ca_client_invoices (tenant_id, status, due_date)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS ca_client_payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            invoice_id UUID NOT NULL REFERENCES ca_client_invoices(id) ON DELETE CASCADE,
            amount NUMERIC(14, 2) NOT NULL,
            payment_date DATE NOT NULL,
            method VARCHAR(40) NOT NULL,
            reference VARCHAR(120),
            notes TEXT,
            recorded_by VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ca_client_payments_invoice "
        "ON ca_client_payments (tenant_id, invoice_id)"
    )

    for table_name in TENANT_RLS_TABLES:
        _enable_tenant_rls(table_name)


def downgrade() -> None:
    for table_name in TENANT_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")

    op.drop_index("ix_ca_client_payments_invoice", table_name="ca_client_payments")
    op.drop_table("ca_client_payments")

    op.drop_index("ix_ca_client_invoices_status_due", table_name="ca_client_invoices")
    op.drop_index("ix_ca_client_invoices_tenant_company", table_name="ca_client_invoices")
    op.drop_table("ca_client_invoices")

    op.drop_table("ca_service_plans")

    op.drop_index("ix_client_portal_documents_tenant_company", table_name="client_portal_documents")
    op.drop_table("client_portal_documents")

    op.drop_index("ix_client_portal_invites_email", table_name="client_portal_invites")
    op.drop_index("ix_client_portal_invites_tenant_company", table_name="client_portal_invites")
    op.drop_table("client_portal_invites")

    op.drop_index("ix_pt_returns_tenant_company_period", table_name="professional_tax_returns")
    op.drop_table("professional_tax_returns")

    op.drop_index("ix_pt_registrations_tenant_company", table_name="professional_tax_registrations")
    op.drop_table("professional_tax_registrations")
