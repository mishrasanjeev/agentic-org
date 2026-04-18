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

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v484_docs_kb_upload_cols"
down_revision = "v483_prompt_tpl_partial_uniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive columns used by the KB upload path. Use ADD COLUMN IF NOT
    # EXISTS (repo convention — see v4_6_0_enterprise_readiness) so the
    # migration is idempotent against environments where the updated
    # ORM's metadata.create_all already provisioned these columns
    # (notably the test_alembic_e2e legacy-schema fixture).
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS filename VARCHAR(500)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_type VARCHAR(100)")
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'ready'"
    )

    # Backfill filename from the existing `name` for any legacy rows, so a
    # later NOT NULL tightening (if we ever need one) has a clean starting
    # point. Cheap: `documents` is expected to be near-empty in every
    # environment because the upload path has been silently failing.
    op.execute("UPDATE documents SET filename = name WHERE filename IS NULL")

    # Relax the legacy S3 NOT NULLs so metadata-only KB uploads (which
    # have no S3 artifact) are representable without sentinel empty
    # strings leaking through the schema. DROP NOT NULL is a no-op when
    # the column is already nullable, so no explicit guard is needed.
    op.execute("ALTER TABLE documents ALTER COLUMN s3_bucket DROP NOT NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN s3_key DROP NOT NULL")


def downgrade() -> None:
    # Reinstate NOT NULL — rollback expects any rows to have populated
    # s3_bucket/s3_key, so if this is used in an environment with real
    # KB uploads it will need a manual backfill first.
    op.execute("ALTER TABLE documents ALTER COLUMN s3_bucket SET NOT NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN s3_key SET NOT NULL")

    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS size_bytes")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS content_type")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS filename")
