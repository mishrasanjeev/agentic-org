# SPDX-License-Identifier: Apache-2.0
"""Cost-aware routing on model routing policies.

Revision ID: v6z37_cost_aware_routing
Revises: v6z36_model_gateway_records
Create Date: 2026-10-02

A routing policy with ``cost_aware`` set picks the cheapest of its ``targets``
whose observed failure rate over the quality window stays at or under
``max_failure_rate`` (the deployment's default when NULL).
"""

from alembic import op

revision = "v6z37_cost_aware_routing"
down_revision = "v6z36_model_gateway_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_routing_policies ADD COLUMN IF NOT EXISTS cost_aware BOOLEAN NOT NULL DEFAULT false;")
    op.execute("ALTER TABLE model_routing_policies ADD COLUMN IF NOT EXISTS max_failure_rate DOUBLE PRECISION NULL;")
    op.execute(
        "ALTER TABLE model_routing_policies DROP CONSTRAINT IF EXISTS ck_model_routing_policies_max_failure_rate;"
    )
    op.execute(
        "ALTER TABLE model_routing_policies ADD CONSTRAINT ck_model_routing_policies_max_failure_rate "
        "CHECK (max_failure_rate IS NULL OR (max_failure_rate >= 0 AND max_failure_rate <= 1));"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE model_routing_policies DROP CONSTRAINT IF EXISTS ck_model_routing_policies_max_failure_rate;"
    )
    op.execute("ALTER TABLE model_routing_policies DROP COLUMN IF EXISTS max_failure_rate;")
    op.execute("ALTER TABLE model_routing_policies DROP COLUMN IF EXISTS cost_aware;")
