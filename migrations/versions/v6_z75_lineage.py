# SPDX-License-Identifier: Apache-2.0
"""Provenance and lineage: nodes and steps.

Revision ID: v6z75_lineage
Revises: v6z74_txn_narratives
Create Date: 2026-10-07

``lineage_nodes``: one thing kept or used, under its kind, reference and
version, with its origin and when it was observed. ``lineage_steps``:
what was done between two nodes, by which tool, with which parameters
(``core/lineage/provenance.py``). Both tenant scoped under forced
row-level policies; the foreign keys of a step carry leading indexes.
"""

from alembic import op

revision = "v6z75_lineage"
down_revision = "v6z74_txn_narratives"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lineage_nodes (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            kind VARCHAR(32) NOT NULL,
            ref VARCHAR(500) NOT NULL,
            source VARCHAR(500) NOT NULL DEFAULT '',
            version VARCHAR(80) NOT NULL DEFAULT '',
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lineage_nodes_tenant_key ON lineage_nodes(tenant_id, kind, ref, version);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_nodes_tenant_kind_ref ON lineage_nodes(tenant_id, kind, ref);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_nodes_tenant_source ON lineage_nodes(tenant_id, source);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_nodes_tenant_observed ON lineage_nodes(tenant_id, observed_at);")
    op.execute("ALTER TABLE lineage_nodes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE lineage_nodes FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS lineage_nodes_tenant_isolation ON lineage_nodes;")
    op.execute(
        """
        CREATE POLICY lineage_nodes_tenant_isolation
        ON lineage_nodes
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lineage_steps (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            from_node UUID NOT NULL REFERENCES lineage_nodes(id) ON DELETE CASCADE,
            to_node UUID NOT NULL REFERENCES lineage_nodes(id) ON DELETE CASCADE,
            step VARCHAR(32) NOT NULL,
            tool VARCHAR(128) NOT NULL DEFAULT '',
            params_hash VARCHAR(64) NOT NULL DEFAULT '',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_lineage_steps_tenant_edge "
        "ON lineage_steps(tenant_id, from_node, to_node, step);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_steps_from_node ON lineage_steps(from_node);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_steps_to_node ON lineage_steps(to_node);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lineage_steps_tenant_at ON lineage_steps(tenant_id, at);")
    op.execute("ALTER TABLE lineage_steps ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE lineage_steps FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS lineage_steps_tenant_isolation ON lineage_steps;")
    op.execute(
        """
        CREATE POLICY lineage_steps_tenant_isolation
        ON lineage_steps
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lineage_steps;")
    op.execute("DROP TABLE IF EXISTS lineage_nodes;")
