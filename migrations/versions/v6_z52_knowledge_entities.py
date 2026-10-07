# SPDX-License-Identifier: Apache-2.0
"""Knowledge entities.

Revision ID: v6z52_knowledge_entities
Revises: v6z51_knowledge_document_domain
Create Date: 2026-10-07

``knowledge_entities``: the entities each ingested chunk mentions
(``core/rag/entities.py``), one row per chunk and entity, the nodes of the
graph that graph retrieval walks. A row belongs to its chunk
(``knowledge_documents``) and goes with it; the table is tenant scoped
under the same row-level policy as the chunks. Nothing is backfilled:
chunks ingested before this revision have no entities until re-indexed.
"""

from alembic import op

revision = "v6z52_knowledge_entities"
down_revision = "v6z51_knowledge_document_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entities (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            entity VARCHAR(200) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            mentions INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_knowledge_entities_kind CHECK (kind IN ('name', 'code', 'amount', 'date')),
            CONSTRAINT ck_knowledge_entities_mentions CHECK (mentions >= 1)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_entities_document_entity "
        "ON knowledge_entities(document_id, entity);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_entities_tenant_entity ON knowledge_entities(tenant_id, entity);"
    )
    op.execute("ALTER TABLE knowledge_entities ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS knowledge_entities_tenant_isolation ON knowledge_entities;")
    op.execute(
        """
        CREATE POLICY knowledge_entities_tenant_isolation
        ON knowledge_entities
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_entities;")
