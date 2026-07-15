"""Scope encrypted connector configuration to an exact company.

Revision ID: v6z6_connector_company_scope
Revises: v6z5_readiness_ledger
Create Date: 2026-07-14
"""

from alembic import op

revision = "v6z6_connector_company_scope"
down_revision = "v6z5_readiness_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE connector_configs "
        "ADD COLUMN IF NOT EXISTS company_id UUID NULL"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_connector_configs_company_id'
                  AND conrelid = 'connector_configs'::regclass
            ) THEN
                ALTER TABLE connector_configs
                ADD CONSTRAINT fk_connector_configs_company_id
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT;
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE connector_configs "
        "DROP CONSTRAINT IF EXISTS uq_connector_config_tenant"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_configs_tenant_global "
        "ON connector_configs (tenant_id, connector_name) "
        "WHERE company_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_configs_tenant_company "
        "ON connector_configs (tenant_id, company_id, connector_name) "
        "WHERE company_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant_company "
        "ON connector_configs (tenant_id, company_id)"
    )
    op.execute("ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector_configs FORCE ROW LEVEL SECURITY")
    # Raw migration 016 installed this permissive tenant-only policy.  RLS
    # policies are ORed, so leaving it in place would bypass company scope.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON connector_configs")
    op.execute(
        "DROP POLICY IF EXISTS connector_configs_tenant_isolation "
        "ON connector_configs"
    )
    op.execute(
        "DROP POLICY IF EXISTS connector_configs_scope_isolation "
        "ON connector_configs"
    )
    op.execute(
        """
        CREATE POLICY connector_configs_scope_isolation ON connector_configs
        USING (
            tenant_id::text = current_setting('agenticorg.tenant_id', true)
            AND company_id IS NOT DISTINCT FROM
                NULLIF(current_setting('agenticorg.company_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id::text = current_setting('agenticorg.tenant_id', true)
            AND company_id IS NOT DISTINCT FROM
                NULLIF(current_setting('agenticorg.company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION connector_config_company_scope_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.company_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.companies
                WHERE id = NEW.company_id AND tenant_id = NEW.tenant_id
            ) THEN
                RAISE EXCEPTION
                    'connector config company % is not owned by tenant %',
                    NEW.company_id, NEW.tenant_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION connector_config_company_scope_guard() FROM PUBLIC"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS connector_config_company_scope_guard_trigger "
        "ON connector_configs"
    )
    op.execute(
        """
        CREATE TRIGGER connector_config_company_scope_guard_trigger
        BEFORE INSERT OR UPDATE OF tenant_id, company_id ON connector_configs
        FOR EACH ROW EXECUTE FUNCTION connector_config_company_scope_guard()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS connector_configs_scope_isolation "
        "ON connector_configs"
    )
    op.execute("DROP POLICY IF EXISTS connector_configs_tenant_isolation ON connector_configs")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON connector_configs")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON connector_configs
        USING (
            tenant_id::text = current_setting('agenticorg.tenant_id', true)
        )
        WITH CHECK (
            tenant_id::text = current_setting('agenticorg.tenant_id', true)
        )
        """
    )
    op.execute("ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector_configs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP TRIGGER IF EXISTS connector_config_company_scope_guard_trigger "
        "ON connector_configs"
    )
    op.execute("DROP FUNCTION IF EXISTS connector_config_company_scope_guard()")
    op.execute("DROP INDEX IF EXISTS ix_connector_configs_tenant_company")
    op.execute("DROP INDEX IF EXISTS uq_connector_configs_tenant_company")
    op.execute("DROP INDEX IF EXISTS uq_connector_configs_tenant_global")
    op.execute(
        "ALTER TABLE connector_configs "
        "DROP CONSTRAINT IF EXISTS fk_connector_configs_company_id"
    )
    op.execute("ALTER TABLE connector_configs DROP COLUMN IF EXISTS company_id")
    op.execute(
        "ALTER TABLE connector_configs "
        "ADD CONSTRAINT uq_connector_config_tenant "
        "UNIQUE (tenant_id, connector_name)"
    )
