"""Add rpa_schedules table for tenant-scheduled RPA runs.

Revision ID: v490_rpa_schedules
Revises: v489_tpl_edit_history
Create Date: 2026-04-23

Backs the RPA-framework feature (generic RPA script registry +
tenant scheduling + vector-embedded quality gate). A schedule row
identifies:

- ``tenant_id`` / optional ``company_id`` — isolation boundary.
- ``script_key`` — resolves to a script module discovered by
  ``rpa.scripts._registry.discover_scripts()``.
- ``cron_expression`` — preset keyword or 5-field cron.
- ``params`` + ``config`` JSONB — script- and framework-level inputs.
- last_run telemetry (status, chunks published/rejected, quality avg)
  so operators can see 4.8/5 compliance at a glance.

Idempotent: uses ``CREATE TABLE IF NOT EXISTS`` so multiple deploys or
retried upgrades don't fail. The index guards also use IF NOT EXISTS.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars per preflight gate.
revision = "v490_rpa_schedules"
down_revision = "v489_tpl_edit_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rpa_schedules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            company_id UUID,
            name VARCHAR(255) NOT NULL,
            script_key VARCHAR(100) NOT NULL,
            cron_expression VARCHAR(100) NOT NULL DEFAULT 'daily',
            enabled BOOLEAN NOT NULL DEFAULT true,
            params JSONB NOT NULL DEFAULT '{}'::jsonb,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            last_run_status VARCHAR(30),
            last_run_chunks_published INTEGER,
            last_run_chunks_rejected INTEGER,
            last_quality_avg NUMERIC(4, 3),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tenants') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'rpa_schedules'
                      AND constraint_name = 'rpa_schedules_tenant_id_fkey'
                ) THEN
                    ALTER TABLE rpa_schedules
                        ADD CONSTRAINT rpa_schedules_tenant_id_fkey
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
                END IF;
            END IF;
        END$$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'companies') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'rpa_schedules'
                      AND constraint_name = 'rpa_schedules_company_id_fkey'
                ) THEN
                    ALTER TABLE rpa_schedules
                        ADD CONSTRAINT rpa_schedules_company_id_fkey
                        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL;
                END IF;
            END IF;
        END$$;
        """
    )

    # Unique schedule name per (tenant, script_key) so ops can't create
    # two conflicting schedules with the same label.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_rpa_schedules_tenant_name "
        "ON rpa_schedules (tenant_id, name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rpa_schedules_tenant_id "
        "ON rpa_schedules (tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rpa_schedules_next_run_at "
        "ON rpa_schedules (enabled, next_run_at) WHERE enabled = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rpa_schedules_next_run_at")
    op.execute("DROP INDEX IF EXISTS ix_rpa_schedules_tenant_id")
    op.execute("DROP INDEX IF EXISTS ix_rpa_schedules_tenant_name")
    op.execute("DROP TABLE IF EXISTS rpa_schedules")
