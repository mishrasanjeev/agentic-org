# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Report schedule run history and the schedule's owner.

Revision ID: v6z111_report_schedule_runs
Revises: v6z110_insights_exports
Create Date: 2026-11-03

``report_schedule_runs``: one row per run of a schedule (scheduled or run now):
when it started and finished, its status (``delivered``, ``generated``,
``failed``, ``blocked``), why it failed or was held back, the Insights export it
made (Insights report types) and what each delivery channel answered. Tenant
scoped under a forced row-level policy.

``report_schedules.created_by`` (nullable): the user who made the schedule; an
Insights report runs with that user's access (older schedules have none and run
as an administrator of the tenant).

Downgrade drops the table and the column.
"""

from alembic import op

revision = "v6z111_report_schedule_runs"
down_revision = "v6z110_insights_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS created_by UUID NULL;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS report_schedule_runs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            schedule_id UUID NOT NULL,
            trigger VARCHAR(12) NOT NULL DEFAULT 'schedule',
            status VARCHAR(12) NOT NULL,
            reason VARCHAR(500) NULL,
            export_id UUID NULL,
            channels JSONB NOT NULL DEFAULT '[]'::jsonb,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_report_schedule_runs_status CHECK (status IN ('delivered','generated','failed','blocked')),
            CONSTRAINT ck_report_schedule_runs_trigger CHECK (trigger IN ('schedule','manual'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_schedule_runs_schedule "
        "ON report_schedule_runs (tenant_id, schedule_id, started_at DESC);"
    )
    op.execute("ALTER TABLE report_schedule_runs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE report_schedule_runs FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS report_schedule_runs_tenant_isolation ON report_schedule_runs;")
    op.execute(
        """
        CREATE POLICY report_schedule_runs_tenant_isolation
        ON report_schedule_runs
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS report_schedule_runs;")
    op.execute("ALTER TABLE report_schedules DROP COLUMN IF EXISTS created_by;")
