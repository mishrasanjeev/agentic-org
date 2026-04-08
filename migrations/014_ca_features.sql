-- migrations/014_ca_features.sql
-- v4.2.0: CA paid add-on features — filing approvals, subscriptions, GSTN uploads.

-- ── 1. ca_subscriptions table ──────────────────────────────────────────
-- Tracks the paid CA add-on subscription per tenant.
CREATE TABLE IF NOT EXISTS ca_subscriptions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    plan            VARCHAR(50) NOT NULL DEFAULT 'ca_pro',
    status          VARCHAR(20) NOT NULL DEFAULT 'trial',
    max_clients     INT NOT NULL DEFAULT 7,
    price_inr       INT NOT NULL DEFAULT 4999,
    price_usd       INT NOT NULL DEFAULT 59,
    billing_cycle   VARCHAR(20) NOT NULL DEFAULT 'monthly',
    trial_ends_at   TIMESTAMPTZ,
    current_period_start TIMESTAMPTZ,
    current_period_end   TIMESTAMPTZ,
    cancelled_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_ca_sub_tenant UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_ca_subscriptions_tenant_id ON ca_subscriptions(tenant_id);

-- RLS on ca_subscriptions
ALTER TABLE ca_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'ca_subscriptions' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON ca_subscriptions USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 2. filing_approvals table ──────────────────────────────────────────
-- Partner self-approval workflow for GST/TDS filings.
CREATE TABLE IF NOT EXISTS filing_approvals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    filing_type     VARCHAR(50) NOT NULL,
    filing_period   VARCHAR(20) NOT NULL,
    filing_data     JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    requested_by    VARCHAR(255) NOT NULL,
    approved_by     VARCHAR(255),
    approved_at     TIMESTAMPTZ,
    rejection_reason TEXT,
    auto_approved   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_filing_approvals_tenant_id ON filing_approvals(tenant_id);
CREATE INDEX IF NOT EXISTS ix_filing_approvals_company_id ON filing_approvals(company_id);
CREATE INDEX IF NOT EXISTS ix_filing_approvals_status ON filing_approvals(status);

-- RLS on filing_approvals
ALTER TABLE filing_approvals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'filing_approvals' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON filing_approvals USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 3. gstn_uploads table ──────────────────────────────────────────────
-- Track manual JSON uploads to GSTN portal (for clients who prefer manual).
CREATE TABLE IF NOT EXISTS gstn_uploads (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    upload_type     VARCHAR(50) NOT NULL,
    filing_period   VARCHAR(20) NOT NULL,
    file_name       VARCHAR(500) NOT NULL,
    file_path       VARCHAR(1000),
    file_size_bytes BIGINT,
    status          VARCHAR(20) NOT NULL DEFAULT 'generated',
    gstn_arn        VARCHAR(100),
    uploaded_at     TIMESTAMPTZ,
    uploaded_by     VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_gstn_uploads_tenant_id ON gstn_uploads(tenant_id);
CREATE INDEX IF NOT EXISTS ix_gstn_uploads_company_id ON gstn_uploads(company_id);

-- RLS on gstn_uploads
ALTER TABLE gstn_uploads ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'gstn_uploads' AND policyname = 'tenant_isolation'
    ) THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON gstn_uploads USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 4. Add subscription_status to companies ────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'subscription_status'
    ) THEN
        ALTER TABLE companies ADD COLUMN subscription_status VARCHAR(20) NOT NULL DEFAULT 'trial';
    END IF;
END $$;

-- ── 5. Add client_health_score to companies ────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'client_health_score'
    ) THEN
        ALTER TABLE companies ADD COLUMN client_health_score INT DEFAULT 100;
    END IF;
END $$;

-- ── 6. Add document_vault_enabled to companies ─────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'document_vault_enabled'
    ) THEN
        ALTER TABLE companies ADD COLUMN document_vault_enabled BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;
END $$;

-- ── 7. Add compliance_alerts_email to companies ────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'compliance_alerts_email'
    ) THEN
        ALTER TABLE companies ADD COLUMN compliance_alerts_email VARCHAR(255);
    END IF;
END $$;
