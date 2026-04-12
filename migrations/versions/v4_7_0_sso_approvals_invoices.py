"""v4.7.0 — SSO, approval policies, invoices, branding, A/B variants

Batch migration matching the v4.7.0 enterprise readiness work.
Mirrors the idempotent DDL applied at runtime by ``init_db()`` in
``core/database.py`` so that any future Alembic-driven environment
ends up with the same schema.

Tables added:
  - sso_configs            (per-tenant OIDC providers)
  - approval_policies      (multi-step approval chains)
  - approval_steps         (steps within a policy, with quorum)
  - invoices               (monthly invoice records + line items)
  - tenant_branding        (white-label config + custom domain)
  - workflow_variants      (A/B variants per workflow)

Columns added:
  - tenants.byok_kek_resource (BYOK / CMEK key resource name)

Revision ID: v470_sso_invoices
Revises: v460_enterprise
Create Date: 2026-04-11
"""

from alembic import op

revision = "v470_sso_invoices"
down_revision = "v460_enterprise"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. Tenants — BYOK KEK resource ────────────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'byok_kek_resource'
            ) THEN
                ALTER TABLE tenants ADD COLUMN byok_kek_resource VARCHAR(500) NOT NULL DEFAULT '';
            END IF;
        END $$;
    """)

    # ── 2. SSO configs ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS sso_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            provider_key VARCHAR(50) NOT NULL,
            provider_type VARCHAR(20) NOT NULL DEFAULT 'oidc',
            display_name VARCHAR(100) NOT NULL,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            jit_provisioning BOOLEAN NOT NULL DEFAULT TRUE,
            default_role VARCHAR(50) NOT NULL DEFAULT 'analyst',
            allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, provider_key)
        );
    """)

    # ── 3. Approval policies + steps ──────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_policies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            description VARCHAR(500),
            workflow_id UUID,
            agent_id UUID,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, name)
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS approval_steps (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_id UUID NOT NULL REFERENCES approval_policies(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            approver_role VARCHAR(50) NOT NULL,
            quorum_required INTEGER NOT NULL DEFAULT 1,
            quorum_total INTEGER NOT NULL DEFAULT 1,
            mode VARCHAR(20) NOT NULL DEFAULT 'sequential',
            condition VARCHAR(500),
            step_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (policy_id, sequence),
            CHECK (quorum_required >= 1),
            CHECK (quorum_required <= quorum_total)
        );
    """)
    # v4.7.0 hotfix — convert any legacy varchar is_active to boolean
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'approval_policies'
                  AND column_name = 'is_active'
                  AND data_type = 'character varying'
            ) THEN
                ALTER TABLE approval_policies
                    ALTER COLUMN is_active DROP DEFAULT,
                    ALTER COLUMN is_active TYPE BOOLEAN
                    USING (is_active::text IN ('true', 't', '1')),
                    ALTER COLUMN is_active SET DEFAULT TRUE;
            END IF;
        END $$;
    """)

    # ── 4. Invoices ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            invoice_number VARCHAR(50) NOT NULL,
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            issue_date DATE NOT NULL,
            due_date DATE NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'USD',
            subtotal NUMERIC(14, 2) NOT NULL,
            tax NUMERIC(14, 2) NOT NULL DEFAULT 0,
            total NUMERIC(14, 2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            pdf_url VARCHAR(500),
            payment_provider VARCHAR(20),
            payment_ref VARCHAR(100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, invoice_number)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invoices_tenant_period "
        "ON invoices(tenant_id, period_start);"
    )

    # ── 5. Tenant branding ────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_branding (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL UNIQUE,
            product_name VARCHAR(100) NOT NULL DEFAULT 'AgenticOrg',
            logo_url VARCHAR(500),
            favicon_url VARCHAR(500),
            primary_color VARCHAR(7) NOT NULL DEFAULT '#7c3aed',
            accent_color VARCHAR(7) NOT NULL DEFAULT '#1e293b',
            custom_domain VARCHAR(255),
            support_email VARCHAR(255),
            footer_text VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    # ── 6. Workflow A/B variants ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_variants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            workflow_id UUID NOT NULL,
            variant_name VARCHAR(100) NOT NULL,
            weight INTEGER NOT NULL DEFAULT 50 CHECK (weight BETWEEN 0 AND 100),
            definition JSONB NOT NULL DEFAULT '{}'::jsonb,
            run_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workflow_id, variant_name)
        );
    """)


    # ── RLS for all v4.7 tenant-scoped tables ───────────────────────
    # (Originally missing — added per gap analysis recheck)
    _rls_tables = [
        "sso_configs",
        "approval_policies",
        "invoices",
        "tenant_branding",
        "workflow_variants",
    ]
    for tbl in _rls_tables:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON {tbl};")
        op.execute(
            f"CREATE POLICY {tbl}_tenant_isolation ON {tbl} "
            "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));"
        )
    # approval_steps: RLS via FK subquery
    op.execute("ALTER TABLE approval_steps ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE approval_steps FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS approval_steps_tenant_isolation ON approval_steps;")
    op.execute("""
        CREATE POLICY approval_steps_tenant_isolation ON approval_steps
        USING (policy_id IN (
            SELECT id FROM approval_policies
            WHERE tenant_id::text = current_setting('agenticorg.tenant_id', true)
        ));
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS workflow_variants;")
    op.execute("DROP TABLE IF EXISTS tenant_branding;")
    op.execute("DROP INDEX IF EXISTS ix_invoices_tenant_period;")
    op.execute("DROP TABLE IF EXISTS invoices;")
    op.execute("DROP TABLE IF EXISTS approval_steps;")
    op.execute("DROP TABLE IF EXISTS approval_policies;")
    op.execute("DROP TABLE IF EXISTS sso_configs;")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS byok_kek_resource;")
