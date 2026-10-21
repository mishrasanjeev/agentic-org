# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""C6Z onboarding packets and connector evidence accept the synthetic demo source.

Revision ID: v6z92_c6z_demo_source
Revises: v6z91_merchant_custom_domains
Create Date: 2026-10-21

``v6z_runtime_vertical_demo`` limited ``connector_choice`` and ``source_system``
to ``shopify``. The OACP demo merchant writes ``synthetic_demo`` (behind
``AGENTICORG_OACP_DEMO_MERCHANT_ENABLED``, for the marked demo tenant only), so
its seed failed on ``ck_c6z_onboarding_connector``. Both checks now admit
``shopify`` or ``synthetic_demo``; the mode stays ``read_only``. Who may use the
synthetic source is still decided in the application, not here.

``downgrade`` restores the shopify-only checks; it fails while synthetic rows
exist, which is the intended guard (tear the demo down first).
"""

from alembic import op

revision = "v6z92_c6z_demo_source"
down_revision = "v6z91_merchant_custom_domains"
branch_labels = None
depends_on = None

SOURCE_SYSTEMS = ("shopify", "synthetic_demo")

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
    _replace(("shopify",))
