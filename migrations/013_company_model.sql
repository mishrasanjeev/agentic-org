-- migrations/013_company_model.sql
-- v4.1.0: Company sub-tenant model for CA multi-tenant use case.
-- A CA firm (tenant) manages N client companies; every operational
-- record can optionally be scoped to a specific company.

-- ── 1. companies table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    name            VARCHAR(255) NOT NULL,
    gstin           VARCHAR(15),
    pan             VARCHAR(10) NOT NULL,
    tan             VARCHAR(10),
    cin             VARCHAR(21),
    state_code      VARCHAR(2),
    registered_address TEXT,
    industry        VARCHAR(100),
    fy_start_month  VARCHAR(2) NOT NULL DEFAULT '04',
    fy_end_month    VARCHAR(2) NOT NULL DEFAULT '03',
    signatory_name  VARCHAR(255),
    signatory_designation VARCHAR(100),
    signatory_email VARCHAR(255),
    compliance_email VARCHAR(255),
    dsc_serial      VARCHAR(100),
    dsc_expiry      DATE,
    pf_registration VARCHAR(50),
    esi_registration VARCHAR(50),
    pt_registration VARCHAR(50),
    bank_name       VARCHAR(255),
    bank_account_number VARCHAR(50),
    bank_ifsc       VARCHAR(11),
    bank_branch     VARCHAR(255),
    tally_config    JSONB,
    gst_auto_file   BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    user_roles      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_company_tenant_gstin UNIQUE (tenant_id, gstin)
);

CREATE INDEX IF NOT EXISTS ix_companies_tenant_id ON companies(tenant_id);

-- ── 2. Add nullable company_id FK to operational tables ─────────────
-- Uses DO $$ blocks so the migration is safe to re-run (idempotent).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE agents ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workflow_definitions' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE workflow_definitions ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'workflow_runs' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE workflow_runs ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'audit_log' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE audit_log ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tool_calls' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE tool_calls ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'connectors' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE connectors ADD COLUMN company_id UUID REFERENCES companies(id);
    END IF;
END $$;

-- ── 3. Indexes on company_id ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_agents_company_id ON agents(company_id);
CREATE INDEX IF NOT EXISTS ix_workflow_definitions_company_id ON workflow_definitions(company_id);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_company_id ON workflow_runs(company_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_company_id ON audit_log(company_id);
CREATE INDEX IF NOT EXISTS ix_tool_calls_company_id ON tool_calls(company_id);
CREATE INDEX IF NOT EXISTS ix_connectors_company_id ON connectors(company_id);

-- ── 4. Row-Level Security on companies ──────────────────────────────
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'companies' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON companies USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;

-- ── 5. Company-scoped RLS policies on operational tables ────────────
-- These are additive: rows with company_id IS NULL still pass
-- the existing tenant_isolation policy. Rows WITH a company_id
-- require it to belong to the current tenant.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'agents' AND policyname = 'company_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY company_isolation ON agents USING (
            company_id IS NULL
            OR company_id IN (
                SELECT id FROM companies
                WHERE tenant_id = current_setting(''agenticorg.tenant_id'')::UUID
            )
        )';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'workflow_definitions' AND policyname = 'company_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY company_isolation ON workflow_definitions USING (
            company_id IS NULL
            OR company_id IN (
                SELECT id FROM companies
                WHERE tenant_id = current_setting(''agenticorg.tenant_id'')::UUID
            )
        )';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'workflow_runs' AND policyname = 'company_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY company_isolation ON workflow_runs USING (
            company_id IS NULL
            OR company_id IN (
                SELECT id FROM companies
                WHERE tenant_id = current_setting(''agenticorg.tenant_id'')::UUID
            )
        )';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'audit_log' AND policyname = 'company_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY company_isolation ON audit_log USING (
            company_id IS NULL
            OR company_id IN (
                SELECT id FROM companies
                WHERE tenant_id = current_setting(''agenticorg.tenant_id'')::UUID
            )
        )';
    END IF;
END $$;
