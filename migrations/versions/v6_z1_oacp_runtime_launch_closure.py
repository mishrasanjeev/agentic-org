"""Extend C6Z runtime packet statuses for launch closure.

Revision ID: v6z1_oacp_runtime_launch_closure
Revises: v6z_runtime_vertical_demo
Create Date: 2026-06-18

The original C6Z table is already deployed in some environments, so this
forward migration updates only the packet lifecycle CHECK constraint. It does
not add execution, public discovery, payment, mandate, order, or provider calls.
"""

from __future__ import annotations

from alembic import op

revision = "v6z1_oacp_runtime_launch_closure"
down_revision = "v6z_runtime_vertical_demo"
branch_labels = None
depends_on = None

STATUSES = (
    "draft",
    "received",
    "sync_ready",
    "synced",
    "authority_requested",
    "artifacts_cached",
    "cache_refresh_needed",
    "blocked_missing_credentials",
    "blocked_grantex_unavailable",
    "rejected",
)


def _status_sql() -> str:
    return ", ".join(f"'{status}'" for status in STATUSES)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE commerce_c6z_seller_onboarding_packets "
        "DROP CONSTRAINT IF EXISTS ck_c6z_onboarding_status;"
    )
    op.execute(
        "ALTER TABLE commerce_c6z_seller_onboarding_packets "
        "ADD CONSTRAINT ck_c6z_onboarding_status "
        f"CHECK (status IN ({_status_sql()}));"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE commerce_c6z_seller_onboarding_packets "
        "DROP CONSTRAINT IF EXISTS ck_c6z_onboarding_status;"
    )
    op.execute(
        "ALTER TABLE commerce_c6z_seller_onboarding_packets "
        "ADD CONSTRAINT ck_c6z_onboarding_status "
        "CHECK (status IN ('received', 'pending_sandbox_review', 'rejected', 'artifact_issuance_ready'));"
    )
