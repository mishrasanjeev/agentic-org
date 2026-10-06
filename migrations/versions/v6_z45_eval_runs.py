# SPDX-License-Identifier: Apache-2.0
"""Evaluation runs.

Revision ID: v6z45_eval_runs
Revises: v6z44_eval_datasets
Create Date: 2026-10-06

Stored evaluation runs (``core/evals/runs.py``): what a run measured and what
came out, tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z45_eval_runs"
down_revision = "v6z44_eval_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            dataset_id UUID NOT NULL REFERENCES eval_datasets(id),
            version INTEGER NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL,
            judge_model VARCHAR(128) NULL,
            judges JSONB NOT NULL DEFAULT '[]'::jsonb,
            prompt_hash VARCHAR(64) NOT NULL,
            prompt_label VARCHAR(120) NULL,
            max_tokens INTEGER NOT NULL DEFAULT 512,
            cases_total INTEGER NOT NULL DEFAULT 0,
            "offset" INTEGER NOT NULL DEFAULT 0,
            cases_run INTEGER NOT NULL DEFAULT 0,
            passed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            pass_rate DOUBLE PRECISION NULL,
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            scores JSONB NOT NULL DEFAULT '{}'::jsonb,
            results JSONB NOT NULL DEFAULT '[]'::jsonb,
            avg_latency_ms INTEGER NOT NULL DEFAULT 0,
            cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_by_user UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_eval_runs_dataset_created ON eval_runs(dataset_id, created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_eval_runs_tenant_created ON eval_runs(tenant_id, created_at);")
    op.execute("ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS eval_runs_tenant_isolation ON eval_runs;")
    op.execute("""
        CREATE POLICY eval_runs_tenant_isolation
        ON eval_runs
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_runs;")
