# SPDX-License-Identifier: Apache-2.0
"""Incremental synchronisation: sync sources and their runs.

Revision ID: v6z76_lineage_sync
Revises: v6z75_lineage
Create Date: 2026-10-07

``lineage_sync_sources``: a feed polled on a schedule for what changed
since its cursor, with its token kept encrypted for the tenant.
``lineage_sync_runs``: one run of a source with what it received,
processed, skipped and failed (``core/lineage/sync.py``). Both tenant
scoped under forced row-level policies; the run's foreign key carries a
leading index.
"""

from alembic import op

revision = "v6z76_lineage_sync"
down_revision = "v6z75_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lineage_sync_sources (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'feed',
            url VARCHAR(500) NOT NULL,
            item_kind VARCHAR(16) NOT NULL DEFAULT 'document',
            interval_minutes INTEGER NOT NULL DEFAULT 60,
            enabled BOOLEAN NOT NULL DEFAULT true,
            token TEXT NOT NULL DEFAULT '',
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            cursor VARCHAR(500) NOT NULL DEFAULT '',
            next_run_at TIMESTAMPTZ NULL,
            last_run_at TIMESTAMPTZ NULL,
            last_status VARCHAR(16) NOT NULL DEFAULT '',
            created_by VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lineage_sync_sources_tenant_name "
        "ON lineage_sync_sources(tenant_id, name);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_sync_sources_tenant_due "
        "ON lineage_sync_sources(tenant_id, enabled, next_run_at);"
    )
    op.execute("ALTER TABLE lineage_sync_sources ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE lineage_sync_sources FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS lineage_sync_sources_tenant_isolation ON lineage_sync_sources;")
    op.execute(
        """
        CREATE POLICY lineage_sync_sources_tenant_isolation
        ON lineage_sync_sources
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lineage_sync_runs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL REFERENCES lineage_sync_sources(id) ON DELETE CASCADE,
            trigger VARCHAR(16) NOT NULL DEFAULT 'manual',
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ NULL,
            cursor_before VARCHAR(500) NOT NULL DEFAULT '',
            cursor_after VARCHAR(500) NOT NULL DEFAULT '',
            received INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            errors JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_sync_runs_source ON lineage_sync_runs(source_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_sync_runs_tenant_started ON lineage_sync_runs(tenant_id, started_at);"
    )
    op.execute("ALTER TABLE lineage_sync_runs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE lineage_sync_runs FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS lineage_sync_runs_tenant_isolation ON lineage_sync_runs;")
    op.execute(
        """
        CREATE POLICY lineage_sync_runs_tenant_isolation
        ON lineage_sync_runs
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lineage_sync_runs;")
    op.execute("DROP TABLE IF EXISTS lineage_sync_sources;")
