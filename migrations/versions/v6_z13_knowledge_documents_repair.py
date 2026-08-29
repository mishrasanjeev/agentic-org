"""Repair the durable native knowledge-document index table.

Revision ID: v6z13_knowledge_docs
Revises: v6z12_voice_runtime
Create Date: 2026-08-29

Legacy-baseline installations can stamp past ``v400_apex`` after ORM
``create_all``. ``knowledge_documents`` is not an ORM table, so that path left
it absent. Older installations that did run ``v400_apex`` have the original
file-metadata shape, while native RAG ingestion now writes title/content and
embedding provenance. This migration reconciles both states idempotently.
"""

from alembic import op

revision = "v6z13_knowledge_docs"
down_revision = "v6z12_voice_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            title VARCHAR(500) NOT NULL,
            content TEXT NOT NULL,
            category VARCHAR(100),
            source VARCHAR(500),
            file_type VARCHAR(50) DEFAULT 'text',
            mime_type VARCHAR(128),
            embedding_model VARCHAR(128),
            embedding_dimensions INTEGER,
            token_count INTEGER DEFAULT 0,
            source_object_id VARCHAR(128),
            source_object_type VARCHAR(32),
            status VARCHAR(30) NOT NULL DEFAULT 'ready',
            embedding vector(384),
            embedding_bge_m3 vector(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge_documents
            ADD COLUMN IF NOT EXISTS title VARCHAR(500),
            ADD COLUMN IF NOT EXISTS content TEXT,
            ADD COLUMN IF NOT EXISTS category VARCHAR(100),
            ADD COLUMN IF NOT EXISTS source VARCHAR(500),
            ADD COLUMN IF NOT EXISTS file_type VARCHAR(50) DEFAULT 'text',
            ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128),
            ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128),
            ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER,
            ADD COLUMN IF NOT EXISTS token_count INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS source_object_id VARCHAR(128),
            ADD COLUMN IF NOT EXISTS source_object_type VARCHAR(32),
            ADD COLUMN IF NOT EXISTS embedding vector(384),
            ADD COLUMN IF NOT EXISTS embedding_bge_m3 vector(1024)
        """
    )
    op.execute(
        """
        UPDATE knowledge_documents
        SET title = COALESCE(
                NULLIF(title, ''),
                NULLIF(to_jsonb(knowledge_documents)->>'filename', ''),
                'Untitled'
            ),
            content = COALESCE(content, '')
        WHERE title IS NULL OR content IS NULL
        """
    )
    op.execute("ALTER TABLE knowledge_documents ALTER COLUMN title SET NOT NULL")
    op.execute("ALTER TABLE knowledge_documents ALTER COLUMN content SET NOT NULL")

    # The original v400 shape required file-metadata columns that native RAG
    # rows do not populate. Preserve the columns for compatibility but remove
    # only those obsolete NOT NULL constraints when present.
    op.execute(
        """
        DO $$
        DECLARE legacy_column TEXT;
        BEGIN
            FOREACH legacy_column IN ARRAY ARRAY['filename', 'content_type', 'file_size_bytes']
            LOOP
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'knowledge_documents'
                      AND column_name = legacy_column
                      AND is_nullable = 'NO'
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE knowledge_documents ALTER COLUMN %I DROP NOT NULL',
                        legacy_column
                    );
                END IF;
            END LOOP;
        END$$
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_id ON knowledge_documents (tenant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_source_type "
        "ON knowledge_documents (tenant_id, source_object_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_source_object "
        "ON knowledge_documents (tenant_id, source_object_id)"
    )
    op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = 'knowledge_documents'
                  AND policyname = 'knowledge_documents_tenant_isolation'
            ) THEN
                CREATE POLICY knowledge_documents_tenant_isolation
                    ON knowledge_documents
                    USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
                    WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
            END IF;
        END$$
        """
    )


def downgrade() -> None:
    # Preserve indexed customer knowledge. The repair is intentionally
    # forward-only because the pre-repair states are both data-loss hazards.
    pass
