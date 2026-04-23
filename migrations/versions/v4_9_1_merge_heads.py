"""Merge two v4.9.0 migration heads.

Revision ID: v491_merge_v490_heads
Revises: v490_rpa_schedules, v490_shadow_samples_def
Create Date: 2026-04-23

PR #296 (fix/qa-23apr-sweep) added ``v490_shadow_samples_def`` and
PR #297 (feat/rpa-framework-rbi) added ``v490_rpa_schedules`` — both
branched off ``v489_tpl_edit_history`` and landed on main in quick
succession, producing a multi-head Alembic state that
``scripts/alembic_migrate.py`` refuses to advance.

This empty merge revision re-joins the two heads so ``alembic
upgrade head`` picks one deterministic ancestry without either
migration needing to change ``down_revision``.

No DDL is emitted; the two head migrations stay independent and
both are still applied before this merge runs.
"""

from __future__ import annotations

# Alembic identifiers — kept <=32 chars per preflight gate.
revision = "v491_merge_v490_heads"
# Tuple signals a merge node to Alembic.
down_revision = ("v490_rpa_schedules", "v490_shadow_samples_def")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge-only migration; no DDL.
    pass


def downgrade() -> None:
    # Merge-only migration; no DDL.
    pass
