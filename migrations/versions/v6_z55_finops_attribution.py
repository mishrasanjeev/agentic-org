# SPDX-License-Identifier: Apache-2.0
"""FinOps attribution.

Revision ID: v6z55_finops_attribution
Revises: v6z54_model_cards
Create Date: 2026-10-07

``finops_cost_ledger``: one row per day, agent and attribution (use case,
application, business unit, department, cost centre) with tokens, cost and
calls (``core/finops/attribution.py``), tenant scoped under a row-level
policy that is forced, so the table-owning role is bound by it too. The unique
key covers every attribution dimension, the nullable ones (agent, department,
cost centre) coalesced, so a run attributed to a different department or cost
centre on the same day gets its own row. A database that ran an earlier draft
of this revision has its narrower ``ux_finops_cost_ledger_key`` dropped.

The model call records gain the business unit and application of the run, and
the tool call rows the use case and application; all nullable, nothing
backfilled. Those legacy-table changes are guarded on the table existing, so a
drifted database missing either table still migrates.
"""

from alembic import op

revision = "v6z55_finops_attribution"
down_revision = "v6z54_model_cards"
branch_labels = None
depends_on = None

# The legacy tables gaining attribution columns, with their (column, type) pairs.
_LEGACY_COLUMNS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("model_gateway_records", (("business_unit", "VARCHAR(64)"), ("application", "VARCHAR(64)"))),
    ("tool_calls", (("use_case", "VARCHAR(64)"), ("application", "VARCHAR(64)"))),
)


def _if_table(table: str, body: str) -> str:
    """Run ``body`` only when ``table`` exists (table names are fixed above, not input)."""
    return f"DO $$ BEGIN IF to_regclass('public.{table}') IS NOT NULL THEN {body} END IF; END $$;"


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
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_finops_cost_ledger_attribution_key ON finops_cost_ledger "
        "(tenant_id, period_date, COALESCE(agent_id::text, ''), use_case, application, business_unit, "
        "COALESCE(department_id::text, ''), COALESCE(cost_center_id::text, ''));"
    )
    # An earlier draft of this revision keyed rows without the department and cost centre.
    op.execute("DROP INDEX IF EXISTS ux_finops_cost_ledger_key;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finops_cost_ledger_tenant_day ON finops_cost_ledger(tenant_id, period_date);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finops_cost_ledger_tenant_use_case ON finops_cost_ledger(tenant_id, use_case);"
    )
    op.execute("ALTER TABLE finops_cost_ledger ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE finops_cost_ledger FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS finops_cost_ledger_tenant_isolation ON finops_cost_ledger;")
    op.execute(
        """
        CREATE POLICY finops_cost_ledger_tenant_isolation
        ON finops_cost_ledger
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )
    for table, columns in _LEGACY_COLUMNS:
        for column, sql_type in columns:
            op.execute(_if_table(table, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type} NULL;"))


def downgrade() -> None:
    for table, columns in reversed(_LEGACY_COLUMNS):
        for column, _sql_type in reversed(columns):
            op.execute(_if_table(table, f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column};"))
    op.execute("DROP TABLE IF EXISTS finops_cost_ledger;")
