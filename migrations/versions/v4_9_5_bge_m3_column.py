"""Add ``embedding_bge_m3 vector(1024)`` column + IVFFlat index.

Revision ID: v495_bge_m3_column
Revises: v494_multimodal_rag
Create Date: 2026-04-26

PR-A of 2 for the BGE-M3 embedding upgrade. Adds the new column +
index alongside the existing ``embedding vector(384)`` column. PR-A
does NOT touch the existing column or flip the default model — it
only makes the destination column available so the backfill job can
populate it without taking a schema-rename outage.

The cutover (drop ``embedding``, rename ``embedding_bge_m3`` to
``embedding``, flip ``DEFAULT_EMBEDDING_MODEL``) is in PR-B and is
gated on the backfill job reporting zero rows where
``embedding IS NOT NULL AND embedding_bge_m3 IS NULL``.

Idempotent — guarded on information_schema.
"""

from __future__ import annotations

from alembic import op

revision = "v495_bge_m3_column"
down_revision = "v494_multimodal_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'knowledge_documents'
            ) THEN
                ALTER TABLE knowledge_documents
                    ADD COLUMN IF NOT EXISTS embedding_bge_m3 vector(1024);
                -- IVFFlat with lists=100 mirrors the existing index. The
                -- backfill job will populate the column before any
                -- production query targets it.
                CREATE INDEX IF NOT EXISTS ix_knowledge_documents_embedding_bge_m3
                    ON knowledge_documents
                    USING ivfflat (embedding_bge_m3 vector_cosine_ops)
                    WITH (lists = 100);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_knowledge_documents_embedding_bge_m3"
    )
    op.execute(
        "ALTER TABLE knowledge_documents "
        "DROP COLUMN IF EXISTS embedding_bge_m3"
    )
