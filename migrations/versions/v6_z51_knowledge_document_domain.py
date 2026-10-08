# SPDX-License-Identifier: Apache-2.0
"""Knowledge document domain.

Revision ID: v6z51_knowledge_document_domain
Revises: v6z50_chunk_layout
Create Date: 2026-10-07

``knowledge_documents.domain``: the domain a document belongs to, or NULL for
a document shared with the tenant (``core/rag/access.py``). Existing rows keep
NULL, so nothing a caller could see before is withheld by this revision.
"""

from alembic import op

revision = "v6z51_knowledge_document_domain"
down_revision = "v6z50_chunk_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS domain VARCHAR(50) NULL;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tenant_domain ON knowledge_documents(tenant_id, domain);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_documents_tenant_domain;")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS domain;")
