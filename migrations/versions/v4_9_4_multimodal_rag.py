"""Add multimodal RAG columns + knowledge_chunk_sources table.

Revision ID: v494_multimodal_rag
Revises: v493_tenant_ai_settings
Create Date: 2026-04-24

Closes S0-06 ingestion persistence requirements (PR-3 of
docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md):

- Adds provenance + provider metadata columns to ``knowledge_documents``
  so the ingestion service can stamp every chunk with the model it was
  embedded by and the source object it came from.
- Creates ``knowledge_chunk_sources`` — a per-chunk provenance table so
  retrieval can answer "page 42 of invoice.pdf" instead of "somewhere
  in that doc".

Idempotent: all statements guard on information_schema.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars.
revision = "v494_multimodal_rag"
down_revision = "v493_tenant_ai_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── knowledge_documents new columns ────────────────────────────
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'knowledge_documents') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'mime_type'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN mime_type VARCHAR(128);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'embedding_model'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN embedding_model VARCHAR(128);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'embedding_dimensions'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN embedding_dimensions INTEGER;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'token_count'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN token_count INTEGER;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'source_object_id'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN source_object_id VARCHAR(128);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_documents' AND column_name = 'source_object_type'
                ) THEN
                    ALTER TABLE knowledge_documents ADD COLUMN source_object_type VARCHAR(32);
                END IF;
            END IF;
        END$$;
        """
    )

    # Index for filtering by tenant + source_object_type (used by
    # search UIs that show a per-source sidebar). Guarded on
    # information_schema so ephemeral CI databases that haven't yet
    # created knowledge_documents (and therefore have no
    # source_object_type column) don't fail with a relation-not-found
    # error.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_documents'
                  AND column_name = 'source_object_type'
            ) THEN
                CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_source_type
                    ON knowledge_documents (tenant_id, source_object_type);
            END IF;
        END$$;
        """
    )

    # ── knowledge_chunk_sources ────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunk_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            chunk_source VARCHAR(500) NOT NULL,
            page INTEGER,
            sheet VARCHAR(64),
            cell_range VARCHAR(128),
            frame_timestamp_s DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tenants') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'knowledge_chunk_sources'
                      AND constraint_name = 'knowledge_chunk_sources_tenant_id_fkey'
                ) THEN
                    ALTER TABLE knowledge_chunk_sources
                        ADD CONSTRAINT knowledge_chunk_sources_tenant_id_fkey
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
                END IF;
            END IF;
        END$$;
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_sources_tenant_source "
        "ON knowledge_chunk_sources (tenant_id, chunk_source)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunk_sources_tenant_source")
    op.execute("DROP TABLE IF EXISTS knowledge_chunk_sources")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_documents_tenant_source_type")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'knowledge_documents') THEN
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS source_object_type;
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS source_object_id;
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS token_count;
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS embedding_dimensions;
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS embedding_model;
                ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS mime_type;
            END IF;
        END$$;
        """
    )
