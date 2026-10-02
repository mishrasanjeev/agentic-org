# SPDX-License-Identifier: Apache-2.0
"""Run spans.

Revision ID: v6z39_run_spans
Revises: v6z38_guardrail_rules
Create Date: 2026-10-02

One row per finished span of an agent run stored for the console's
waterfall (the run, its model calls, tool calls and knowledge searches),
with timings, outcomes and the governance events. Tenant-scoped under
row-level security; pruned after the retention period.
"""

from alembic import op

revision = "v6z39_run_spans"
down_revision = "v6z38_guardrail_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS run_spans (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            trace_id VARCHAR(32) NOT NULL,
            span_id VARCHAR(16) NOT NULL,
            parent_span_id VARCHAR(16) NULL,
            name VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'internal',
            status VARCHAR(8) NOT NULL DEFAULT 'unset',
            agent_id VARCHAR(64) NULL,
            correlation_id VARCHAR(128) NULL,
            started_at TIMESTAMPTZ NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            events JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_run_spans_status CHECK (status IN ('unset','ok','error')),
            CONSTRAINT ck_run_spans_duration CHECK (duration_ms >= 0)
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_spans_tenant_trace ON run_spans(tenant_id, trace_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_run_spans_tenant_name_started ON run_spans(tenant_id, name, started_at DESC);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_spans_tenant_created ON run_spans(tenant_id, created_at DESC);")
    op.execute("ALTER TABLE run_spans ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE run_spans FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS run_spans_tenant_isolation ON run_spans;")
    op.execute("""
        CREATE POLICY run_spans_tenant_isolation
        ON run_spans
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_spans;")
