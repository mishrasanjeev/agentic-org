"""Add company_id column + index to report_schedules.

Revision ID: v488_report_schedule_comp
Revises: v487_shadow_acc_floor
Create Date: 2026-04-22

Codex 2026-04-22 review gap: the report-schedule list endpoint filtered
only by ``tenant_id``, and ``company_id`` was squirrelled away inside
the JSONB ``config`` column. Two users in the same tenant who managed
separate companies would see each other's schedules — a weak scoping
story that CLAUDE.md's non-negotiable tenancy rules do not accept for a
control-plane surface.

This migration:

1. Adds a nullable ``company_id UUID`` column (nullable so tenant-wide
   schedules are still valid).
2. Backfills from ``config->>'company_id'`` where present.
3. Adds an index on ``(tenant_id, company_id)`` so the list endpoint
   can filter cheaply.

Idempotent — every step guarded on existence.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v488_report_schedule_comp"
down_revision = "v487_shadow_acc_floor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'report_schedules'
                  AND column_name = 'company_id'
            ) THEN
                ALTER TABLE report_schedules
                    ADD COLUMN company_id UUID NULL;
            END IF;
        END
        $$;
    """)

    # Backfill from config JSON. Cast the text value to UUID; skip rows
    # whose config carries a non-UUID (legacy seeded data) so the
    # migration doesn't abort on one bad row.
    op.execute("""
        UPDATE report_schedules
        SET company_id = (config->>'company_id')::uuid
        WHERE company_id IS NULL
          AND config ? 'company_id'
          AND config->>'company_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'report_schedules'
                  AND indexname = 'ix_report_schedules_tenant_company'
            ) THEN
                CREATE INDEX ix_report_schedules_tenant_company
                    ON report_schedules (tenant_id, company_id);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS ix_report_schedules_tenant_company;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'report_schedules'
                  AND column_name = 'company_id'
            ) THEN
                ALTER TABLE report_schedules DROP COLUMN company_id;
            END IF;
        END
        $$;
    """)
