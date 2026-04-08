-- migrations/015_ca_phase2.sql
-- v4.3.0: CA Phase 2 — credential vault, compliance calendar, partner KPIs.

-- ── 1. gstn_credentials table (encrypted vault) ───────────────────────
-- Stores encrypted GSTN portal credentials per company.
-- The password_encrypted column is Fernet-encrypted (AES-128-CBC).
CREATE TABLE IF NOT EXISTS gstn_credentials (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    gstin           VARCHAR(15) NOT NULL,
    username        VARCHAR(255) NOT NULL,
    password_encrypted TEXT NOT NULL,
    encryption_key_ref VARCHAR(100) NOT NULL DEFAULT 'default',
    portal_type     VARCHAR(20) NOT NULL DEFAULT 'gstn',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_verified_at TIMESTAMPTZ,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_gstn_cred_company UNIQUE (company_id, portal_type)
);

CREATE INDEX IF NOT EXISTS ix_gstn_credentials_tenant_id ON gstn_credentials(tenant_id);
CREATE INDEX IF NOT EXISTS ix_gstn_credentials_company_id ON gstn_credentials(company_id);

ALTER TABLE gstn_credentials ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'gstn_credentials' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON gstn_credentials USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 2. compliance_deadlines table ──────────────────────────────────────
-- Tracks statutory filing deadlines per company for cron-based alerts.
CREATE TABLE IF NOT EXISTS compliance_deadlines (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    deadline_type   VARCHAR(50) NOT NULL,
    filing_period   VARCHAR(20) NOT NULL,
    due_date        DATE NOT NULL,
    alert_7d_sent   BOOLEAN NOT NULL DEFAULT FALSE,
    alert_1d_sent   BOOLEAN NOT NULL DEFAULT FALSE,
    filed           BOOLEAN NOT NULL DEFAULT FALSE,
    filed_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_deadline_company_type_period UNIQUE (company_id, deadline_type, filing_period)
);

CREATE INDEX IF NOT EXISTS ix_compliance_deadlines_tenant_id ON compliance_deadlines(tenant_id);
CREATE INDEX IF NOT EXISTS ix_compliance_deadlines_company_id ON compliance_deadlines(company_id);
CREATE INDEX IF NOT EXISTS ix_compliance_deadlines_due_date ON compliance_deadlines(due_date);

ALTER TABLE compliance_deadlines ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'compliance_deadlines' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON compliance_deadlines USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 3. Add gstn_auto_upload flag to companies ─────────────────────────
-- Future flag-flip: when credentials are stored and verified, flip this
-- to auto-upload generated JSON to GSTN via API.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'gstn_auto_upload'
    ) THEN
        ALTER TABLE companies ADD COLUMN gstn_auto_upload BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;
