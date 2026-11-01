# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights foundation: run facts (partitioned by month), the daily rollup and its state.

Revision ID: v6z106_insights_foundation
Revises: v6z105_llm_daily_spend
Create Date: 2026-11-01

``run_facts``: one row per agent run (outcome, usage, cost and the run's
dimensions), written once with ``ON CONFLICT DO NOTHING`` on
``ux_run_facts_tenant_run_key``. It is range-partitioned by ``started_at``,
one partition per month from October 2026 to December 2028 plus a default
partition, so facts past the horizon are kept, never lost; a later migration
adds months. ``insights_daily``: run facts summed per tenant-local day and
dimension combination, rebuildable from the facts. ``insights_rollup_state``:
per tenant-day rollup and reconciliation status. Every table, and every
partition, is tenant scoped under forced row-level policies. No table holds a
subject identifier. No existing table is altered.

Downgrade drops the three tables (the partitions go with their parent). The
facts are derived from runs that keep their own records.
"""

from alembic import op

revision = "v6z106_insights_foundation"
down_revision = "v6z105_llm_daily_spend"
branch_labels = None
depends_on = None

TABLES = ("run_facts", "insights_daily", "insights_rollup_state")

# October 2026 to December 2028 (27 months); core/models/insights.py keeps the same list.
FIRST_MONTH = (2026, 10)
LAST_MONTH = (2028, 12)
DEFAULT_PARTITION = "run_facts_default"


def _months() -> list[tuple[int, int]]:
    year, month = FIRST_MONTH
    out = []
    while (year, month) <= LAST_MONTH:
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _partition_name(year: int, month: int) -> str:
    return f"run_facts_y{year:04d}m{month:02d}"


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
        CREATE TABLE IF NOT EXISTS run_facts (
            id UUID NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            company_id UUID NULL,
            run_key VARCHAR(160) NOT NULL,
            entrypoint VARCHAR(24) NOT NULL,
            agent_id UUID NULL,
            agent_ref VARCHAR(128) NULL,
            agent_type VARCHAR(100) NULL,
            agent_version VARCHAR(64) NULL,
            domain VARCHAR(50) NULL,
            correlation_id VARCHAR(160) NULL,
            trace_id VARCHAR(64) NULL,
            workflow_run_id UUID NULL,
            step_id VARCHAR(128) NULL,
            task_type VARCHAR(100) NULL,
            use_case VARCHAR(100) NULL,
            environment VARCHAR(16) NOT NULL DEFAULT 'production',
            status VARCHAR(24) NOT NULL,
            confidence DOUBLE PRECISION NULL,
            confidence_floor DOUBLE PRECISION NULL,
            duration_ms INTEGER NULL,
            hitl_required BOOLEAN NOT NULL DEFAULT false,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            tokens_cached INTEGER NOT NULL DEFAULT 0,
            tokens_total INTEGER NOT NULL DEFAULT 0,
            cost_usd NUMERIC(14,6) NOT NULL DEFAULT 0,
            provider VARCHAR(32) NULL,
            model VARCHAR(128) NULL,
            error_class VARCHAR(100) NULL,
            outcome_validated BOOLEAN NOT NULL DEFAULT false,
            data_quality VARCHAR(12) NOT NULL DEFAULT 'measured',
            started_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_run_facts PRIMARY KEY (id, started_at),
            CONSTRAINT ck_run_facts_status CHECK (status IN ('completed','failed','hitl_triggered',
                'limit_exceeded','refused','paused','rejected')),
            CONSTRAINT ck_run_facts_entrypoint CHECK (entrypoint IN ('agents','channel','chat','a2a','mcp',
                'voice','hitl_resume','debug','workflow','sales','customer_agent','rpa')),
            CONSTRAINT ck_run_facts_data_quality CHECK (data_quality IN ('measured','seeded')),
            CONSTRAINT ck_run_facts_environment CHECK (environment IN ('production','shadow','sandbox'))
        ) PARTITION BY RANGE (started_at);
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_run_facts_tenant_run_key ON run_facts(tenant_id, run_key, started_at);"
    )
    # Also covers the tenant_id foreign key.
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_facts_tenant_started ON run_facts(tenant_id, started_at);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_facts_tenant_agent_started ON run_facts(tenant_id, agent_id, started_at);"
    )
    partitions = []
    for year, month in _months():
        following = (year + 1, 1) if month == 12 else (year, month + 1)
        name = _partition_name(year, month)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF run_facts "
            f"FOR VALUES FROM ('{year:04d}-{month:02d}-01 00:00:00+00') "
            f"TO ('{following[0]:04d}-{following[1]:02d}-01 00:00:00+00');"
        )
        partitions.append(name)
    op.execute(f"CREATE TABLE IF NOT EXISTS {DEFAULT_PARTITION} PARTITION OF run_facts DEFAULT;")
    partitions.append(DEFAULT_PARTITION)
    _tenant_policy("run_facts")
    for name in partitions:
        _tenant_policy(name)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_daily (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            day DATE NOT NULL,
            dims_hash VARCHAR(64) NOT NULL,
            company_id UUID NULL,
            agent_id UUID NULL,
            agent_ref VARCHAR(128) NULL,
            agent_type VARCHAR(100) NULL,
            domain VARCHAR(50) NULL,
            entrypoint VARCHAR(24) NOT NULL,
            task_type VARCHAR(100) NULL,
            status VARCHAR(24) NOT NULL,
            environment VARCHAR(16) NOT NULL,
            data_quality VARCHAR(12) NOT NULL,
            runs INTEGER NOT NULL DEFAULT 0,
            hitl_required INTEGER NOT NULL DEFAULT 0,
            outcome_validated INTEGER NOT NULL DEFAULT 0,
            tokens_in BIGINT NOT NULL DEFAULT 0,
            tokens_out BIGINT NOT NULL DEFAULT 0,
            tokens_cached BIGINT NOT NULL DEFAULT 0,
            tokens_total BIGINT NOT NULL DEFAULT 0,
            cost_usd NUMERIC(16,6) NOT NULL DEFAULT 0,
            duration_ms_sum BIGINT NOT NULL DEFAULT 0,
            duration_n INTEGER NOT NULL DEFAULT 0,
            latency_buckets INTEGER[] NOT NULL,
            confidence_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
            confidence_n INTEGER NOT NULL DEFAULT 0,
            below_floor INTEGER NOT NULL DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_insights_daily_latency_buckets CHECK (cardinality(latency_buckets) = 12)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_insights_daily_tenant_day_dims "
        "ON insights_daily(tenant_id, day, dims_hash);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_insights_daily_tenant_day ON insights_daily(tenant_id, day);")
    _tenant_policy("insights_daily")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS insights_rollup_state (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            day DATE NOT NULL,
            status VARCHAR(16) NOT NULL,
            rolled_at TIMESTAMPTZ NULL,
            reconciled_at TIMESTAMPTZ NULL,
            source_runs INTEGER NOT NULL DEFAULT 0,
            rolled_runs INTEGER NOT NULL DEFAULT 0,
            source_tokens BIGINT NOT NULL DEFAULT 0,
            rolled_tokens BIGINT NOT NULL DEFAULT 0,
            source_cost_usd NUMERIC(16,6) NOT NULL DEFAULT 0,
            rolled_cost_usd NUMERIC(16,6) NOT NULL DEFAULT 0,
            detail VARCHAR(200) NULL,
            CONSTRAINT ck_insights_rollup_state_status CHECK (status IN ('rolled','reconciled','mismatch'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_insights_rollup_state_tenant_day "
        "ON insights_rollup_state(tenant_id, day);"
    )
    _tenant_policy("insights_rollup_state")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS insights_rollup_state;")
    op.execute("DROP TABLE IF EXISTS insights_daily;")
    op.execute("DROP TABLE IF EXISTS run_facts;")
