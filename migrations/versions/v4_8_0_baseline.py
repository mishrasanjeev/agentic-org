"""v4.8.0 — Alembic cutover baseline

Consolidates schema additions applied by ``init_db()`` after v4.7.0 so that
an Alembic-managed environment converges with production.

Tables added:
  - kpi_cache            (per-tenant role-scoped KPI cache)
  - agent_task_results   (agent execution results / telemetry)
  - connector_configs    (tenant-scoped encrypted connector credentials)

Other changes:
  - Enforce RLS on the v4.7 tenant-scoped tables
    (sso_configs, approval_policies, approval_steps, invoices,
     tenant_branding, workflow_variants)
  - audit_log immutability trigger

Every block is idempotent (``IF NOT EXISTS`` / ``CREATE OR REPLACE``) so
``alembic upgrade head`` is safe on environments that already went
through ``init_db()``.

Revision ID: v480_baseline
Revises: v470_sso_invoices
Create Date: 2026-04-14
"""

from alembic import op

revision = "v480_baseline"
down_revision = "v470_sso_invoices"
branch_labels = None
depends_on = None


_RLS_TABLES = [
    "sso_configs",
    "approval_policies",
    "invoices",
    "tenant_branding",
    "workflow_variants",
]


def upgrade() -> None:
    # ── 1. kpi_cache ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS kpi_cache (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            company_id UUID REFERENCES companies(id),
            role VARCHAR(20) NOT NULL,
            metric_name VARCHAR(100) NOT NULL,
            metric_value JSONB NOT NULL,
            source VARCHAR(50) NOT NULL DEFAULT 'agent',
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ttl_seconds INT NOT NULL DEFAULT 3600,
            stale BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kpi_cache_tenant_role ON kpi_cache(tenant_id, role);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kpi_cache_metric "
        "ON kpi_cache(tenant_id, role, metric_name);"
    )

    # ── 2. agent_task_results ─────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_task_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            agent_id UUID NOT NULL,
            agent_type VARCHAR(100) NOT NULL,
            domain VARCHAR(50) NOT NULL,
            task_type VARCHAR(100) NOT NULL,
            task_input JSONB NOT NULL DEFAULT '{}'::jsonb,
            task_output JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence FLOAT,
            tool_calls JSONB DEFAULT '[]'::jsonb,
            llm_model VARCHAR(100),
            tokens_used INT DEFAULT 0,
            cost_usd FLOAT DEFAULT 0.0,
            duration_ms INT DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            error_message TEXT,
            hitl_required BOOLEAN NOT NULL DEFAULT FALSE,
            hitl_decision VARCHAR(20),
            company_id UUID REFERENCES companies(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_results_tenant ON agent_task_results(tenant_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_results_domain "
        "ON agent_task_results(tenant_id, domain);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_results_created ON agent_task_results(created_at);"
    )

    # ── 3. connector_configs (encrypted credentials) ─────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS connector_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            connector_name VARCHAR(100) NOT NULL,
            display_name VARCHAR(255),
            auth_type VARCHAR(50) NOT NULL DEFAULT 'api_key',
            credentials_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'configured',
            last_health_check TIMESTAMPTZ,
            health_status VARCHAR(20) DEFAULT 'unknown',
            last_sync_at TIMESTAMPTZ,
            sync_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_connector_config_tenant
                UNIQUE (tenant_id, connector_name)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant ON connector_configs(tenant_id);"
    )

    # ── 4. Enforce RLS on v4.7 tenant-scoped tables ──────────────
    for tbl in _RLS_TABLES:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON {tbl};")
        op.execute(
            f"CREATE POLICY {tbl}_tenant_isolation ON {tbl} "
            "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));"
        )

    # approval_steps inherits isolation from its parent policy.
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

    # ── 5. Audit log immutability trigger ─────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
              'audit_log is append-only — UPDATE/DELETE rejected'
              USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;")
    op.execute("""
        CREATE TRIGGER audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_mutation();")

    for tbl in _RLS_TABLES + ["approval_steps"]:
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON {tbl};")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP TABLE IF EXISTS connector_configs CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_task_results CASCADE;")
    op.execute("DROP TABLE IF EXISTS kpi_cache CASCADE;")
