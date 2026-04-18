"""Add pgvector embedding column + ANN index to knowledge_documents.

Revision ID: v486_knowledge_embedding
Revises: v485_governance_config
Create Date: 2026-04-18

Enterprise Readiness Plan PR-B4 — native embeddings for the knowledge base.
Prior state: /knowledge/search fell back to returning an empty list whenever
RAGFlow was unavailable (see api/v1/knowledge.py:400). With this column the
server computes 384-dim BGE vectors at seed + upload time and serves cosine
similarity queries without any external embedding API.

Idempotent — ADD COLUMN / CREATE INDEX IF NOT EXISTS — because existing
environments were bootstrapped via ORM create_all + alembic stamp and the
column may already exist in some installations via the legacy raw SQL path.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v486_knowledge_embedding"
down_revision = "v485_governance_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector is a hard requirement for this migration. The extension is
    # already created by migrations/001_extensions.sql in fresh envs and
    # can be enabled on hosted Postgres (e.g. Cloud SQL flag).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE knowledge_documents "
        "ADD COLUMN IF NOT EXISTS embedding vector(384)"
    )
    # IVFFlat cosine index. `lists = 100` is a reasonable default for a
    # catalogue of hundreds to a few thousand documents. Rebuild (REINDEX)
    # is only needed when the row count grows by >10x.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_embedding "
        "ON knowledge_documents USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_documents_embedding")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS embedding")
