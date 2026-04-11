"""v4.6.0 — Enterprise readiness gap closures

Batch migration that closes several gaps flagged in the April 2026 enterprise
readiness review:

  - Audit log immutability (Postgres trigger prevents UPDATE/DELETE)
  - User.timezone, User.locale  (i18n + per-user localization)
  - Company.currency           (multi-currency billing)
  - Department + cost_center   (multi-tenancy org structure)
  - UserDelegation              (approval delegation)
  - FeatureFlag                 (internal feature-flag system)
  - BudgetAlert                 (budget threshold notifications)
  - agent.maturity              (GA / BETA / ALPHA labeling)

All tables get tenant_id and are protected by RLS using the existing
agenticorg.tenant_id session setting.

Revision ID: v460_enterprise
Revises: v450_company_rls
Create Date: 2026-04-11
"""

from alembic import op

revision = "v460_enterprise"
down_revision = "v450_company_rls"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------
    # 1. Audit log immutability — trigger blocks UPDATE/DELETE.
    #    The signature column already exists (HMAC chain).
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
              'audit_log is append-only — UPDATE/DELETE rejected (row id=%)',
              COALESCE(OLD.id::text, 'unknown')
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

    # ------------------------------------------------------------------
    # 2. User.timezone, User.locale — i18n per user
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) NOT NULL DEFAULT 'UTC';"
    )
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS locale VARCHAR(10) NOT NULL DEFAULT 'en';"
    )

    # ------------------------------------------------------------------
    # 3. Company.currency — multi-currency support
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE companies "
        "ADD COLUMN IF NOT EXISTS currency CHAR(3) NOT NULL DEFAULT 'INR';"
    )

    # ------------------------------------------------------------------
    # 4. Department + cost_center tables — org hierarchy
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            code VARCHAR(50),
            parent_id UUID REFERENCES departments(id) ON DELETE SET NULL,
            manager_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, company_id, name)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_departments_tenant_company "
        "ON departments(tenant_id, company_id);"
    )
    op.execute("ALTER TABLE departments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE departments FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS departments_tenant_isolation ON departments;")
    op.execute("""
        CREATE POLICY departments_tenant_isolation ON departments
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS cost_centers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
            code VARCHAR(50) NOT NULL,
            name VARCHAR(255) NOT NULL,
            budget_limit NUMERIC(14, 2),
            fiscal_year INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, company_id, code)
        );
    """)
    op.execute("ALTER TABLE cost_centers ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE cost_centers FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS cost_centers_tenant_isolation ON cost_centers;")
    op.execute("""
        CREATE POLICY cost_centers_tenant_isolation ON cost_centers
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)

    # Users can be assigned to a department
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS department_id UUID "
        "REFERENCES departments(id) ON DELETE SET NULL;"
    )

    # Agents (and therefore their task results) can carry a cost center
    op.execute(
        "ALTER TABLE agents "
        "ADD COLUMN IF NOT EXISTS cost_center_id UUID "
        "REFERENCES cost_centers(id) ON DELETE SET NULL;"
    )

    # ------------------------------------------------------------------
    # 5. UserDelegation — approval delegation
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_delegations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            delegator_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delegate_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reason VARCHAR(255),
            starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ends_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (delegator_id <> delegate_id)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_delegations_active "
        "ON user_delegations(tenant_id, delegator_id) "
        "WHERE revoked_at IS NULL;"
    )
    op.execute("ALTER TABLE user_delegations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_delegations FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS user_delegations_tenant_isolation ON user_delegations;")
    op.execute("""
        CREATE POLICY user_delegations_tenant_isolation ON user_delegations
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)

    # ------------------------------------------------------------------
    # 6. FeatureFlag — internal feature-flag system
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,              -- NULL = global default
            flag_key VARCHAR(100) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            rollout_percentage INTEGER NOT NULL DEFAULT 0
              CHECK (rollout_percentage BETWEEN 0 AND 100),
            description VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, flag_key)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feature_flags_key "
        "ON feature_flags(flag_key);"
    )

    # ------------------------------------------------------------------
    # 7. BudgetAlert — billing thresholds
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS budget_alerts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
            cost_center_id UUID REFERENCES cost_centers(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            period VARCHAR(20) NOT NULL,       -- daily|weekly|monthly
            threshold_usd NUMERIC(14, 2) NOT NULL,
            warn_at_percent INTEGER NOT NULL DEFAULT 80
              CHECK (warn_at_percent BETWEEN 1 AND 100),
            notify_channels VARCHAR(255) NOT NULL DEFAULT 'email',
            last_triggered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("ALTER TABLE budget_alerts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE budget_alerts FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS budget_alerts_tenant_isolation ON budget_alerts;")
    op.execute("""
        CREATE POLICY budget_alerts_tenant_isolation ON budget_alerts
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)

    # ------------------------------------------------------------------
    # 8. Agent.maturity — GA/BETA/ALPHA labeling
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE agents "
        "ADD COLUMN IF NOT EXISTS maturity VARCHAR(20) NOT NULL DEFAULT 'beta' "
        "CHECK (maturity IN ('ga', 'beta', 'alpha', 'deprecated'));"
    )


def downgrade():
    # Reverse order so FK dependencies are clean.
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS maturity;")

    op.execute("DROP POLICY IF EXISTS budget_alerts_tenant_isolation ON budget_alerts;")
    op.execute("DROP TABLE IF EXISTS budget_alerts;")

    op.execute("DROP TABLE IF EXISTS feature_flags;")

    op.execute("DROP POLICY IF EXISTS user_delegations_tenant_isolation ON user_delegations;")
    op.execute("DROP TABLE IF EXISTS user_delegations;")

    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS cost_center_id;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS department_id;")

    op.execute("DROP POLICY IF EXISTS cost_centers_tenant_isolation ON cost_centers;")
    op.execute("DROP TABLE IF EXISTS cost_centers;")

    op.execute("DROP POLICY IF EXISTS departments_tenant_isolation ON departments;")
    op.execute("DROP TABLE IF EXISTS departments;")

    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS currency;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locale;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS timezone;")

    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_mutation();")
