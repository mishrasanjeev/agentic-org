"""Merge P0 stability migration heads.

Revision ID: v4916_merge_p0_heads
Revises: v4913_cdc_webhook_durability, v4913_feed_events, v4915_bridge_durability
Create Date: 2026-05-16

PRs #553, #554, and #556 intentionally added independent durability
migrations from v4912_workflow_event_waits. This merge revision records the
single Alembic head once those P0 branches are integrated together.
"""

from __future__ import annotations

revision = "v4916_merge_p0_heads"
down_revision = (
    "v4913_cdc_webhook_durability",
    "v4913_feed_events",
    "v4915_bridge_durability",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op merge point for parallel P0 durability heads."""


def downgrade() -> None:
    """No-op; downgrade resumes at the three merged P0 durability heads."""
