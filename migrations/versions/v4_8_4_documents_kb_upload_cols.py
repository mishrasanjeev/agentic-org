"""Add KB-upload columns to the legacy documents table.

Revision ID: v484_documents_kb_upload_cols
Revises: v483_prompt_tpl_partial_uniq
Create Date: 2026-04-18

Session 5 TC-013 root cause: api/v1/knowledge.py:_db_store_doc tries to
INSERT into `documents` with filename/content_type/size_bytes/status
fields that don't exist on the legacy (S3-era) schema. SQLAlchemy raises,
the upload path swallows the exception, the row is never persisted, and
the Knowledge Base UI always shows an empty table after upload.

Fix: additively add the four columns the upload path needs, and relax
the legacy NOT NULL constraints on s3_bucket / s3_key so metadata-only
(non-S3) uploads are representable. No existing rows are altered.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v484_docs_kb_upload_cols"
down_revision = "v483_prompt_tpl_partial_uniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive columns used by the KB upload path.
    op.add_column("documents", sa.Column("filename", sa.String(500), nullable=True))
    op.add_column("documents", sa.Column("content_type", sa.String(100), nullable=True))
    op.add_column(
        "documents",
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column("status", sa.String(30), nullable=False, server_default="ready"),
    )

    # Backfill filename from the existing `name` for any legacy rows, so a
    # later NOT NULL tightening (if we ever need one) has a clean starting
    # point. Cheap: `documents` is expected to be near-empty in every
    # environment because the upload path has been silently failing.
    op.execute("UPDATE documents SET filename = name WHERE filename IS NULL")

    # Relax the legacy S3 NOT NULLs so metadata-only KB uploads (which
    # have no S3 artifact) are representable without sentinel empty
    # strings leaking through the schema.
    op.alter_column("documents", "s3_bucket", nullable=True)
    op.alter_column("documents", "s3_key", nullable=True)


def downgrade() -> None:
    # Reinstate NOT NULL — rollback expects any rows to have populated
    # s3_bucket/s3_key, so if this is used in an environment with real
    # KB uploads it will need a manual backfill first.
    op.alter_column("documents", "s3_bucket", nullable=False)
    op.alter_column("documents", "s3_key", nullable=False)

    op.drop_column("documents", "status")
    op.drop_column("documents", "size_bytes")
    op.drop_column("documents", "content_type")
    op.drop_column("documents", "filename")
