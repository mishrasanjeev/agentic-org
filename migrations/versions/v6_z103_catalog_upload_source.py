# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""C6Z onboarding packets and connector evidence accept the merchant's own catalog upload.

Revision ID: v6z103_catalog_upload_source
Revises: v6z102_mfa_challenge_generation
Create Date: 2026-10-29

A merchant without Shopify uploads its catalog (CSV or JSON,
``core/commerce/catalog_upload.py``); its onboarding packet and connector
evidence name the source ``catalog_upload``. Both source checks now admit
``shopify``, ``catalog_upload`` or ``synthetic_demo``; the mode stays
``read_only``. Additive: no row changes.

``downgrade`` restores the v6z92 checks; it fails while upload rows exist,
which is the intended guard.
"""

from alembic import op

revision = "v6z103_catalog_upload_source"
down_revision = "v6z102_mfa_challenge_generation"
branch_labels = None
depends_on = None

SOURCE_SYSTEMS = ("shopify", "catalog_upload", "synthetic_demo")
PREVIOUS_SOURCE_SYSTEMS = ("shopify", "synthetic_demo")

# (table, constraint, source column, mode column)
CHECKS = (
    ("commerce_c6z_seller_onboarding_packets", "ck_c6z_onboarding_connector", "connector_choice", "connector_mode"),
    ("commerce_c6z_connector_evidence_records", "ck_c6z_connector_source", "source_system", "source_mode"),
)


def _replace(sources: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{source}'" for source in sources)
    for table, constraint, source_column, mode_column in CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({source_column} IN ({allowed}) AND {mode_column} = 'read_only');"
        )


def upgrade() -> None:
    _replace(SOURCE_SYSTEMS)


def downgrade() -> None:
    _replace(PREVIOUS_SOURCE_SYSTEMS)
