# SPDX-License-Identifier: Apache-2.0
"""Workbench assignments.

Revision ID: v6z65_workbench_assignments
Revises: v6z64_idp_documents
Create Date: 2026-10-07

``workbench_assignments``: the workbenches an administrator assigned to a
user (``core/workbench/assignments.py``), one row per user and workbench.
Tenant scoped under a row-level policy.
"""

from alembic import op

revision = "v6z65_workbench_assignments"
down_revision = "v6z64_idp_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workbench_assignments (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            workbench VARCHAR(32) NOT NULL,
            assigned_by VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_workbench_assignments_tenant_user_bench "
        "ON workbench_assignments(tenant_id, user_id, workbench);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workbench_assignments_tenant_user ON workbench_assignments(tenant_id, user_id);"
    )
    op.execute("ALTER TABLE workbench_assignments ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS workbench_assignments_tenant_isolation ON workbench_assignments;")
    op.execute(
        """
        CREATE POLICY workbench_assignments_tenant_isolation
        ON workbench_assignments
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workbench_assignments;")
