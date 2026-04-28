"""Idempotently re-create report_schedules on envs that missed v4.4.0.

Revision ID: v496_report_sched_recreate
Revises: v495_bge_m3_column
Create Date: 2026-04-28

The 27-Apr TC_001 reopen (Aishwarya, "report schedule create returns
500") surfaced a hidden gap: the v4.4.0 alembic migration was the
canonical creator of ``report_schedules``, but envs that were stamped
past v4.4.0 without ever running it (prod 2026-04-22 cutover) ended up
with the table missing. The v4.8.8 company_id migration documented the
gap in its preamble — "the table is created by the ORM at startup
(Base.metadata.create_all)" — but ``init_db`` never actually called
``metadata.create_all`` for this model.

The companion fix in ``core/database.py`` (commit c57126f) added
report_schedules to the init_db CREATE-TABLE-IF-NOT-EXISTS safety net.
This migration mirrors that DDL on the alembic side so:

1. Fresh envs that bootstrap purely via ``alembic upgrade head`` get
   the table without depending on init_db.
2. The CI gate ``scripts/check_migration_required.py`` is satisfied
   for the init_db change.
3. Envs missing the table from the v4.4.0 gap pick it up on next
   ``alembic upgrade head`` even if they don't restart the api pod.

Idempotent — every step guarded with IF NOT EXISTS / EXISTS checks.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v496_report_sched_recreate"
down_revision = "v495_bge_m3_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS report_schedules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            company_id UUID NULL,
            name VARCHAR(200) NOT NULL,
            report_type VARCHAR(50) NOT NULL,
            cron_expression VARCHAR(100) NOT NULL,
            recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
            delivery_channel VARCHAR(20) NOT NULL DEFAULT 'email',
            format VARCHAR(10) NOT NULL DEFAULT 'pdf',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        );
    """)

    # Pick up the v488 company_id column on envs that ran the v488
    # migration as a no-op (table didn't exist yet).
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'report_schedules'
                  AND column_name = 'company_id'
            ) THEN
                ALTER TABLE report_schedules ADD COLUMN company_id UUID NULL;
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_report_schedules_tenant
            ON report_schedules(tenant_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_report_schedules_tenant_company
            ON report_schedules(tenant_id, company_id);
    """)

    # RLS policy — must match init_db's enforcement so cross-tenant
    # reads are blocked the moment the table exists.
    op.execute("ALTER TABLE report_schedules ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE report_schedules FORCE ROW LEVEL SECURITY;")
    op.execute(
        "DROP POLICY IF EXISTS report_schedules_tenant_isolation "
        "ON report_schedules;"
    )
    op.execute("""
        CREATE POLICY report_schedules_tenant_isolation ON report_schedules
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    # Intentionally a no-op: this migration only re-creates a table
    # whose canonical owner is v4.4.0. Dropping here would orphan the
    # v488 column and policy that other migrations expect.
    pass
