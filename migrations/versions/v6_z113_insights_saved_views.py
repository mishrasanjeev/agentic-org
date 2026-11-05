# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights saved views, each person's last visit, and the partner client pack export kind.

Revision ID: v6z113_insights_saved_views
Revises: v6z112_insights_reconcile_streak
Create Date: 2026-11-04

- ``saved_views``: a named filter set (the URL query the dashboard and
  explorers already use) on one landing view or explorer, owned by a person and
  optionally shared with the whole tenant.
- ``insights_user_state``: when each person last opened Insights, for the
  "what changed since you last looked" strip.
- ``insights_exports.kind`` also accepts ``partner_pack`` (one PDF per client
  company and an XLSX rollup).

Both tables are tenant-scoped with forced row-level security on
``agenticorg.tenant_id``, as every Insights table. Downgrade drops the two
tables and restores the export kind check (it refuses while partner packs
exist, so no row is left breaking the restored check).
"""

from alembic import op

revision = "v6z113_insights_saved_views"
down_revision = "v6z112_insights_reconcile_streak"
branch_labels = None
depends_on = None

_KINDS_V6Z110 = "'metric','explorer','run_bundle','view_pdf','finance_pack','evidence_pack'"
_KINDS = _KINDS_V6Z110 + ",'partner_pack'"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def _kind_check(kinds: str) -> None:
    op.execute("ALTER TABLE insights_exports DROP CONSTRAINT IF EXISTS ck_insights_exports_kind;")
    op.execute(f"ALTER TABLE insights_exports ADD CONSTRAINT ck_insights_exports_kind CHECK (kind IN ({kinds}));")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_views (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            user_id UUID NOT NULL,
            view_key VARCHAR(64) NOT NULL,
            name VARCHAR(120) NOT NULL,
            filters JSONB NOT NULL DEFAULT '{}'::jsonb,
            shared BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_saved_views_owner_name UNIQUE (tenant_id, user_id, view_key, name)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_saved_views_tenant_view ON saved_views (tenant_id, view_key);")
    _rls("saved_views")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_user_state (
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            user_id UUID NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        );
        """
    )
    _rls("insights_user_state")
    _kind_check(_KINDS)


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM insights_exports WHERE kind = 'partner_pack') THEN
                RAISE EXCEPTION 'partner_pack exports exist; delete them before downgrading v6z113';
            END IF;
        END $$
        """
    )
    _kind_check(_KINDS_V6Z110)
    op.execute("DROP TABLE IF EXISTS insights_user_state;")
    op.execute("DROP TABLE IF EXISTS saved_views;")
