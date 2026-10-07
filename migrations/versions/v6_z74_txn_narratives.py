# SPDX-License-Identifier: Apache-2.0
"""Transaction finding narratives.

Revision ID: v6z74_txn_narratives
Revises: v6z73_txn_intelligence
Create Date: 2026-10-07

``txn_findings.narrative``: the draft narrative a person reviews;
``narrative_at``: when it was drafted; ``evidence_digest``: the digest of
the evidence package last exported (``core/txn/narrative.py``).
"""

from alembic import op

revision = "v6z74_txn_narratives"
down_revision = "v6z73_txn_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE txn_findings ADD COLUMN IF NOT EXISTS narrative JSONB NOT NULL DEFAULT '{}'::jsonb;")
    op.execute("ALTER TABLE txn_findings ADD COLUMN IF NOT EXISTS narrative_at TIMESTAMPTZ NULL;")
    op.execute("ALTER TABLE txn_findings ADD COLUMN IF NOT EXISTS evidence_digest VARCHAR(64) NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE txn_findings DROP COLUMN IF EXISTS narrative;")
    op.execute("ALTER TABLE txn_findings DROP COLUMN IF EXISTS narrative_at;")
    op.execute("ALTER TABLE txn_findings DROP COLUMN IF EXISTS evidence_digest;")
