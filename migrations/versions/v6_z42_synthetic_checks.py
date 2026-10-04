# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks.

Revision ID: v6z42_synthetic_checks
Revises: v6z41_tamper_evident_audit
Create Date: 2026-10-04

A tenant's scheduled probes (``synthetic_checks``) and the result of every
run (``synthetic_check_results``): the status, the latency and the reasons,
never an answer or retrieved text. Both tenant-scoped under row-level
security; results are pruned after the retention period and go with their
check.
"""

from alembic import op

revision = "v6z42_synthetic_checks"
down_revision = "v6z41_tamper_evident_audit"
branch_labels = None
depends_on = None

_TABLES = ("synthetic_checks", "synthetic_check_results")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS synthetic_checks (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            interval_minutes INTEGER NOT NULL DEFAULT 60,
            enabled BOOLEAN NOT NULL DEFAULT true,
            last_run_at TIMESTAMPTZ NULL,
            last_status VARCHAR(8) NULL,
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by VARCHAR(255) NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_synthetic_checks_kind CHECK (kind IN ('model','knowledge','audit_chain','guardrail')),
            CONSTRAINT ck_synthetic_checks_interval CHECK (interval_minutes >= 5 AND interval_minutes <= 1440)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_synthetic_checks_tenant_name ON synthetic_checks(tenant_id, name);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_synthetic_checks_tenant_enabled "
        "ON synthetic_checks(tenant_id, enabled, last_run_at);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS synthetic_check_results (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            check_id UUID NOT NULL REFERENCES synthetic_checks(id) ON DELETE CASCADE,
            status VARCHAR(8) NOT NULL,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            trigger VARCHAR(16) NOT NULL DEFAULT 'schedule',
            started_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_synthetic_check_results_status CHECK (status IN ('ok','failed','error')),
            CONSTRAINT ck_synthetic_check_results_latency CHECK (latency_ms >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_synthetic_check_results_check_started "
        "ON synthetic_check_results(tenant_id, check_id, started_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_synthetic_check_results_tenant_started "
        "ON synthetic_check_results(tenant_id, started_at DESC);"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation
            ON {table}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS synthetic_check_results;")
    op.execute("DROP TABLE IF EXISTS synthetic_checks;")
