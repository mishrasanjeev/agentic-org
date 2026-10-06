# SPDX-License-Identifier: Apache-2.0
"""Evaluation datasets.

Revision ID: v6z44_eval_datasets
Revises: v6z43_prompt_change_requests
Create Date: 2026-10-05

Tenant evaluation datasets and their versions (``core/evals/datasets.py``),
both tenant-scoped under row-level security. A version is written once: a
trigger refuses any later change to its cases, hash, number or dataset.
"""

from alembic import op

revision = "v6z44_eval_datasets"
down_revision = "v6z43_prompt_change_requests"
branch_labels = None
depends_on = None

_TABLES = ("eval_datasets", "eval_dataset_versions")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_datasets (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            description VARCHAR(500) NULL,
            latest_version INTEGER NOT NULL DEFAULT 0,
            case_count INTEGER NOT NULL DEFAULT 0,
            created_by_user UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ NULL
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_datasets_tenant_name "
        "ON eval_datasets(tenant_id, lower(name)) WHERE archived_at IS NULL;"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_dataset_versions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            dataset_id UUID NOT NULL REFERENCES eval_datasets(id),
            version INTEGER NOT NULL,
            cases JSONB NOT NULL DEFAULT '[]'::jsonb,
            case_count INTEGER NOT NULL DEFAULT 0,
            content_hash VARCHAR(64) NOT NULL,
            note VARCHAR(500) NULL,
            created_by_user UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_eval_dataset_versions_version CHECK (version >= 1)
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_dataset_versions_dataset_version "
        "ON eval_dataset_versions(dataset_id, version);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_dataset_versions_tenant ON eval_dataset_versions(tenant_id, created_at);"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION eval_dataset_versions_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evaluation dataset versions are immutable';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS eval_dataset_versions_no_update ON eval_dataset_versions;")
    op.execute("""
        CREATE TRIGGER eval_dataset_versions_no_update
        BEFORE UPDATE ON eval_dataset_versions
        FOR EACH ROW EXECUTE FUNCTION eval_dataset_versions_immutable();
    """)
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
    op.execute("DROP TABLE IF EXISTS eval_dataset_versions;")
    op.execute("DROP FUNCTION IF EXISTS eval_dataset_versions_immutable();")
    op.execute("DROP TABLE IF EXISTS eval_datasets;")
