"""Align industry pack install UUID server default.

Revision ID: v6y3_industry_pack_uuid_default
Revises: v6y2_oacp_review_manifests
Create Date: 2026-06-14
"""

from __future__ import annotations

from alembic import op

revision = "v6y3_industry_pack_uuid_default"
down_revision = "v6y2_oacp_review_manifests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE industry_pack_installs "
        "ALTER COLUMN id SET DEFAULT gen_random_uuid();"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE industry_pack_installs "
        "ALTER COLUMN id SET DEFAULT uuid_generate_v4();"
    )
