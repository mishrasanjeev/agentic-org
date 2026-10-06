# SPDX-License-Identifier: Apache-2.0
"""Synthetic check kinds: adversarial and eval_dataset.

Revision ID: v6z46_synthetic_check_kinds
Revises: v6z45_eval_runs
Create Date: 2026-10-06

Two scheduled probe kinds (``observability.synthetic``): the adversarial
evaluation set over the tenant's guardrail rules, and a dataset version scored
with a prompt and a model. No new table.
"""

from alembic import op

revision = "v6z46_synthetic_check_kinds"
down_revision = "v6z45_eval_runs"
branch_labels = None
depends_on = None

_OLD = "kind IN ('model','knowledge','audit_chain','guardrail')"
_NEW = "kind IN ('model','knowledge','audit_chain','guardrail','adversarial','eval_dataset')"


def upgrade() -> None:
    op.execute("ALTER TABLE synthetic_checks DROP CONSTRAINT IF EXISTS ck_synthetic_checks_kind;")
    op.execute(f"ALTER TABLE synthetic_checks ADD CONSTRAINT ck_synthetic_checks_kind CHECK ({_NEW});")


def downgrade() -> None:
    op.execute("DELETE FROM synthetic_checks WHERE kind IN ('adversarial','eval_dataset');")
    op.execute("ALTER TABLE synthetic_checks DROP CONSTRAINT IF EXISTS ck_synthetic_checks_kind;")
    op.execute(f"ALTER TABLE synthetic_checks ADD CONSTRAINT ck_synthetic_checks_kind CHECK ({_OLD});")
