# SPDX-License-Identifier: Apache-2.0
"""FinOps attribution.

Revision ID: v6z55_finops_attribution
Revises: v6z54_model_cards
Create Date: 2026-10-07

``finops_cost_ledger``: one row per day, agent and attribution (use case,
application, business unit, department, cost centre) with tokens, cost and
calls (``core/finops/attribution.py``), tenant scoped under a row-level
policy. The model call records gain the business unit and application of the
run, and the tool call rows the use case and application; all nullable,
nothing backfilled.
"""

from alembic import op

revision = "v6z55_finops_attribution"
down_revision = "v6z54_model_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finops_cost_ledger (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            period_date DATE NOT NULL,
            agent_id UUID NULL,
            use_case VARCHAR(64) NOT NULL,
            application VARCHAR(64) NOT NULL,
            business_unit VARCHAR(64) NOT NULL DEFAULT '',
            department_id UUID NULL,
            cost_center_id UUID NULL,
            tokens BIGINT NOT NULL DEFAULT 0,
            cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            calls INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_finops_cost_ledger_counts CHECK (tokens >= 0 AND cost_usd >= 0 AND calls >= 0)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_finops_cost_ledger_key ON finops_cost_ledger "
        "(tenant_id, period_date, COALESCE(agent_id::text, ''), use_case, application, business_unit);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finops_cost_ledger_tenant_day ON finops_cost_ledger(tenant_id, period_date);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finops_cost_ledger_tenant_use_case ON finops_cost_ledger(tenant_id, use_case);"
    )
    op.execute("ALTER TABLE finops_cost_ledger ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS finops_cost_ledger_tenant_isolation ON finops_cost_ledger;")
    op.execute(
        """
        CREATE POLICY finops_cost_ledger_tenant_isolation
        ON finops_cost_ledger
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS business_unit VARCHAR(64) NULL;")
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS application VARCHAR(64) NULL;")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS use_case VARCHAR(64) NULL;")
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS application VARCHAR(64) NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS application;")
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS use_case;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS application;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS business_unit;")
    op.execute("DROP TABLE IF EXISTS finops_cost_ledger;")
