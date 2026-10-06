# SPDX-License-Identifier: Apache-2.0
"""Evaluation runs: tokens.

Revision ID: v6z47_eval_run_tokens
Revises: v6z46_synthetic_check_kinds
Create Date: 2026-10-07

A stored evaluation run keeps the tokens its answers used, so the model
comparison can rank by token cost.
"""

from alembic import op

revision = "v6z47_eval_run_tokens"
down_revision = "v6z46_synthetic_check_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS tokens INTEGER NOT NULL DEFAULT 0;")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS tokens;")
