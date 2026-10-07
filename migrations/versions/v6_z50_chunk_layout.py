# SPDX-License-Identifier: Apache-2.0
"""Chunk layout: the chunking strategy per tenant and paragraph and heading provenance per chunk.

Revision ID: v6z50_chunk_layout
Revises: v6z49_agent_ratings
Create Date: 2026-10-07

``tenant_ai_settings.chunk_strategy`` (``core/rag/chunking.py``) and
``knowledge_chunk_sources.paragraph`` and ``.heading``, written by ingestion
from layout-preserving extraction (``core/rag/extractors.py``). Columns are
added if missing; nothing is backfilled, so existing chunks keep their page,
sheet, cell range and frame provenance only.
"""

from alembic import op

revision = "v6z50_chunk_layout"
down_revision = "v6z49_agent_ratings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_ai_settings ADD COLUMN IF NOT EXISTS chunk_strategy VARCHAR(16) NULL;")
    op.execute("ALTER TABLE knowledge_chunk_sources ADD COLUMN IF NOT EXISTS paragraph INTEGER NULL;")
    op.execute("ALTER TABLE knowledge_chunk_sources ADD COLUMN IF NOT EXISTS heading VARCHAR(200) NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunk_sources DROP COLUMN IF EXISTS heading;")
    op.execute("ALTER TABLE knowledge_chunk_sources DROP COLUMN IF EXISTS paragraph;")
    op.execute("ALTER TABLE tenant_ai_settings DROP COLUMN IF EXISTS chunk_strategy;")
