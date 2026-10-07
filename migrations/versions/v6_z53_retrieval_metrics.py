# SPDX-License-Identifier: Apache-2.0
"""Knowledge retrieval metrics.

Revision ID: v6z53_retrieval_metrics
Revises: v6z52_knowledge_entities
Create Date: 2026-10-07

``knowledge_retrieval_metrics``: one row per knowledge search while
``AGENTICORG_KNOWLEDGE_METRICS_ENABLED`` is on (``core/rag/metrics.py``):
the retrieval path, result and withheld counts, the best score, context
relevance, latency and whether the query was expanded or the graph
consulted. Figures only: no query text, no chunk, no user. Tenant scoped
under a row-level policy; pruning is the operator's retention task.
"""

from alembic import op

revision = "v6z53_retrieval_metrics"
down_revision = "v6z52_knowledge_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_retrieval_metrics (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            path VARCHAR(16) NOT NULL,
            results INTEGER NOT NULL,
            withheld INTEGER NOT NULL DEFAULT 0,
            top_score REAL NOT NULL DEFAULT 0,
            relevance REAL NOT NULL DEFAULT 0,
            covered_share REAL NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            expanded BOOLEAN NOT NULL DEFAULT FALSE,
            graph BOOLEAN NOT NULL DEFAULT FALSE,
            query_terms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_knowledge_retrieval_metrics_path CHECK (path IN ('ragflow', 'hybrid', 'vector_keyword')),
            CONSTRAINT ck_knowledge_retrieval_metrics_counts CHECK (results >= 0 AND withheld >= 0 AND latency_ms >= 0)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_retrieval_metrics_tenant_created "
        "ON knowledge_retrieval_metrics(tenant_id, created_at);"
    )
    op.execute("ALTER TABLE knowledge_retrieval_metrics ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS knowledge_retrieval_metrics_tenant_isolation ON knowledge_retrieval_metrics;")
    op.execute(
        """
        CREATE POLICY knowledge_retrieval_metrics_tenant_isolation
        ON knowledge_retrieval_metrics
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_retrieval_metrics;")
