"""Separate scored shadow evidence from unscored completed samples.

Revision ID: v6z11_shadow_scored
Revises: v6z10_legacy_fk_indexes
Create Date: 2026-08-10
"""

from alembic import op

revision = "v6z11_shadow_scored"
down_revision = "v6z10_legacy_fk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS "
        "shadow_scored_sample_count INTEGER NOT NULL DEFAULT 0"
    )
    # Historical model averages were computed using shadow_sample_count. Treat
    # those samples as scored when a model average exists; rows without a model
    # signal remain at zero and cannot satisfy the strengthened promotion gate.
    op.execute(
        "UPDATE agents SET shadow_scored_sample_count = shadow_sample_count "
        "WHERE shadow_model_confidence_current IS NOT NULL "
        "AND shadow_scored_sample_count = 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE agents DROP COLUMN IF EXISTS shadow_scored_sample_count"
    )
