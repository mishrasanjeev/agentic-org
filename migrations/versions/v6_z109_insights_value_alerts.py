# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights money saved and alerts: value baselines and rates, alerts, and when an approval was first opened.

Revision ID: v6z109_insights_value_alerts
Revises: v6z108_insights_bi
Create Date: 2026-11-02

``value_baselines``: minutes of human work one completed task of an agent
type replaces, and the role that would do it, per tenant (``source`` seeded
or tenant; savings count only once a tenant administrator confirms it).
``value_rates``: a role's loaded hourly cost per tenant, with the citation of a
seeded default. ``insights_alerts``: one alert per (rule, entity) while it is
not resolved (a partial unique index), acknowledged in place. Seeds are written
per tenant on first use, not here. Each table is tenant scoped under forced
row-level policies and holds no subject identifier beyond the user ids of who
confirmed, edited or acknowledged.

``hitl_queue.first_viewed_at`` (nullable): when a person first opened the
approval; review time for money saved runs from it.

Downgrade drops the three tables and the column.
"""

from alembic import op

revision = "v6z109_insights_value_alerts"
down_revision = "v6z108_insights_bi"
branch_labels = None
depends_on = None

TABLES = ("value_baselines", "value_rates", "insights_alerts")


def _tenant_policy(table: str) -> None:
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


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS value_baselines (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            agent_type VARCHAR(100) NOT NULL,
            task_type VARCHAR(100) NOT NULL DEFAULT '*',
            baseline_minutes NUMERIC(8, 2) NOT NULL,
            role VARCHAR(50) NOT NULL,
            setup_cost NUMERIC(14, 2) NULL,
            source VARCHAR(10) NOT NULL,
            confirmed_by UUID NULL,
            confirmed_at TIMESTAMPTZ NULL,
            updated_by UUID NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_value_baselines_source CHECK (source IN ('seeded','tenant')),
            CONSTRAINT ck_value_baselines_minutes CHECK (baseline_minutes > 0 AND baseline_minutes <= 1440),
            CONSTRAINT ck_value_baselines_setup_cost CHECK (setup_cost IS NULL OR setup_cost >= 0),
            CONSTRAINT uq_value_baselines_tenant_type_task UNIQUE (tenant_id, agent_type, task_type)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS value_rates (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            role VARCHAR(50) NOT NULL,
            hourly_cost NUMERIC(12, 2) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            source VARCHAR(10) NOT NULL,
            citation VARCHAR(500) NULL,
            confirmed_by UUID NULL,
            confirmed_at TIMESTAMPTZ NULL,
            updated_by UUID NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_value_rates_source CHECK (source IN ('seeded','tenant')),
            CONSTRAINT ck_value_rates_currency CHECK (currency IN ('INR','USD')),
            CONSTRAINT ck_value_rates_hourly_cost CHECK (hourly_cost >= 0),
            CONSTRAINT uq_value_rates_tenant_role UNIQUE (tenant_id, role)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_alerts (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            rule_key VARCHAR(64) NOT NULL,
            metric_key VARCHAR(64) NULL,
            entity_ref VARCHAR(200) NOT NULL DEFAULT '',
            severity VARCHAR(8) NOT NULL,
            owner_role VARCHAR(32) NOT NULL,
            domain VARCHAR(50) NULL,
            title VARCHAR(300) NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            deep_link VARCHAR(500) NULL,
            state VARCHAR(10) NOT NULL DEFAULT 'open',
            fired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            acked_by UUID NULL,
            acked_at TIMESTAMPTZ NULL,
            resolved_at TIMESTAMPTZ NULL,
            notified_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_insights_alerts_severity CHECK (severity IN ('watch','act')),
            CONSTRAINT ck_insights_alerts_state CHECK (state IN ('open','acked','resolved'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_insights_alerts_live "
        "ON insights_alerts (tenant_id, rule_key, entity_ref) WHERE state <> 'resolved';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_insights_alerts_tenant_state ON insights_alerts (tenant_id, state, fired_at);"
    )
    for table in TABLES:
        _tenant_policy(table)
    op.execute("ALTER TABLE hitl_queue ADD COLUMN IF NOT EXISTS first_viewed_at TIMESTAMPTZ NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE hitl_queue DROP COLUMN IF EXISTS first_viewed_at;")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
