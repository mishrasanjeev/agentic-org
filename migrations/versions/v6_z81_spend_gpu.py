# SPDX-License-Identifier: Apache-2.0
"""Spend GPU: in-house pool node hours (a platform table) and each tenant's allocated share.

Revision ID: v6z81_spend_gpu
Revises: v6z80_spend_usage
Create Date: 2026-10-10

In-house model serving (ollama, vllm) is one deployment-wide endpoint that
serves every tenant, so a GPU node hour is a deployment cost, not a tenant's.
``spend_gpu_pool_hours`` holds the node hours of each pool and hour (from
configuration or an operator command) and the aggregate tokens it was spread
over; it holds no tenant data, so it has no ``tenant_id`` and no row-level
policy (the precedent of ``health_check_history``). ``spend_gpu_allocations``
holds each tenant's share of an hour, tenant scoped under a forced row-level
policy; the usage records of the share go to ``spend_usage_records``
(``allocated``). A pool hour is a whole UTC hour, checked in UTC so an IST
session cannot truncate it to a half hour. No existing table is altered.
"""

from alembic import op

revision = "v6z81_spend_gpu"
down_revision = "v6z80_spend_usage"
branch_labels = None
depends_on = None

TABLES = ("spend_gpu_pool_hours", "spend_gpu_allocations")
TENANT_TABLES = ("spend_gpu_allocations",)


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
    # A platform table: no tenant_id and no row-level policy (it holds node hours and aggregate tokens).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_gpu_pool_hours (
            id UUID PRIMARY KEY,
            provider VARCHAR(16) NOT NULL,
            node_pool VARCHAR(64) NOT NULL,
            models JSONB NOT NULL DEFAULT '[]'::jsonb,
            hour_start TIMESTAMPTZ NOT NULL,
            node_hours NUMERIC(12,4) NOT NULL,
            source VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            claimed_at TIMESTAMPTZ NULL,
            frozen_at TIMESTAMPTZ NULL,
            total_tokens NUMERIC(28,6) NULL,
            tenant_count INTEGER NOT NULL DEFAULT 0,
            idle BOOLEAN NOT NULL DEFAULT false,
            priced_calls_skipped BIGINT NOT NULL DEFAULT 0,
            allocated_at TIMESTAMPTZ NULL,
            recorded_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_gpu_pool_hours_provider CHECK (provider IN ('ollama','vllm')),
            CONSTRAINT ck_spend_gpu_pool_hours_hours CHECK (node_hours > 0 AND node_hours <= 10000),
            CONSTRAINT ck_spend_gpu_pool_hours_source CHECK (source IN ('config','metrics','manual')),
            CONSTRAINT ck_spend_gpu_pool_hours_status CHECK (status IN ('pending','allocating','allocated')),
            CONSTRAINT ck_spend_gpu_pool_hours_whole_hour CHECK (
                date_trunc('hour', hour_start AT TIME ZONE 'UTC') = (hour_start AT TIME ZONE 'UTC'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_gpu_pool_hours_key "
        "ON spend_gpu_pool_hours(provider, node_pool, hour_start);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_gpu_pool_hours_status ON spend_gpu_pool_hours(status, hour_start);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_gpu_allocations (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            pool_hour_id UUID NOT NULL REFERENCES spend_gpu_pool_hours(id) ON DELETE RESTRICT,
            provider VARCHAR(16) NOT NULL,
            node_pool VARCHAR(64) NOT NULL,
            hour_start TIMESTAMPTZ NOT NULL,
            tokens NUMERIC(28,6) NOT NULL,
            node_hours NUMERIC(18,6) NULL,
            amount NUMERIC(24,10) NULL,
            currency CHAR(3) NULL,
            records INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'frozen',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_gpu_allocations_status CHECK (status IN ('frozen','written')),
            CONSTRAINT ck_spend_gpu_allocations_tokens CHECK (tokens >= 0 AND (node_hours IS NULL OR node_hours >= 0))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_gpu_allocations_tenant_hour "
        "ON spend_gpu_allocations(tenant_id, pool_hour_id);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_gpu_allocations_pool_hour ON spend_gpu_allocations(pool_hour_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_gpu_allocations_tenant_hour_start "
        "ON spend_gpu_allocations(tenant_id, hour_start);"
    )
    _tenant_policy("spend_gpu_allocations")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS spend_gpu_allocations;")
    op.execute("DROP TABLE IF EXISTS spend_gpu_pool_hours;")
