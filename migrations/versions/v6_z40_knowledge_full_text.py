# SPDX-License-Identifier: Apache-2.0
"""Index tenant-ready knowledge text for native hybrid retrieval.

Revision ID: v6z40_knowledge_full_text
Revises: v6z39_run_spans
"""

from alembic import op

revision = "v6z40_knowledge_full_text"
down_revision = "v6z39_run_spans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_knowledge_documents_ready_fts "
            "ON knowledge_documents USING gin "
            "(to_tsvector('english', title || ' ' || content)) "
            "WHERE status = 'ready'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_knowledge_documents_ready_fts")
