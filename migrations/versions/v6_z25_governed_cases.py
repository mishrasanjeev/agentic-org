# SPDX-License-Identifier: Apache-2.0
"""Governed business cases and their state transitions.

Revision ID: v6z25_governed_cases
Revises: v6z24_case_pseudonym_maps
Create Date: 2026-09-15

PRD A-8: a business onboarding case moves through ``submitted``,
``in_progress``, ``awaiting_decision``, ``decided``, ``withdrawn`` and
``failed`` (``core.cases.states``). ``governed_cases`` holds the application
and every document the reference agents produce (memo, policy result,
screening results and dispositions, ownership graph, agent case records);
``governed_case_transitions`` records every state change.

Additive and forward-only: two new tables with row-level security in the same
shape as their neighbours. Nothing reads them unless the ``governed_cases``
flag is on for a tenant. ``downgrade`` keeps the tables: dropping them would
discard case records an evidence package depends on. ``upgrade`` is
idempotent.
"""

from alembic import op

revision = "v6z25_governed_cases"
down_revision = "v6z24_case_pseudonym_maps"
branch_labels = None
depends_on = None

_TABLES = ("governed_cases", "governed_case_transitions")


def _enable_row_level_security(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS governed_cases (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_ref VARCHAR(128) NOT NULL,
            purpose VARCHAR(128) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'submitted',
            provider VARCHAR(64) NOT NULL,
            policy_id VARCHAR(128) NOT NULL,
            application JSONB NOT NULL,
            subject JSONB,
            memo JSONB,
            policy_result JSONB,
            ownership_graph JSONB,
            screening_results JSONB NOT NULL DEFAULT '[]'::jsonb,
            screening_dispositions JSONB NOT NULL DEFAULT '[]'::jsonb,
            parties JSONB NOT NULL DEFAULT '[]'::jsonb,
            agent_records JSONB NOT NULL DEFAULT '[]'::jsonb,
            information_requests JSONB NOT NULL DEFAULT '[]'::jsonb,
            decision JSONB,
            failure_reason VARCHAR(128),
            version INTEGER NOT NULL DEFAULT 1,
            created_by VARCHAR(256),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT uq_governed_cases_tenant_ref UNIQUE (tenant_id, case_ref),
            CONSTRAINT ck_governed_cases_state CHECK (
                state IN ('submitted', 'in_progress', 'awaiting_decision', 'decided', 'withdrawn', 'failed')
            ),
            CONSTRAINT ck_governed_cases_version CHECK (version >= 1)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_governed_cases_tenant_state_updated "
        "ON governed_cases (tenant_id, state, updated_at);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS governed_case_transitions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id UUID NOT NULL REFERENCES governed_cases(id) ON DELETE CASCADE,
            case_version INTEGER NOT NULL,
            from_state VARCHAR(32),
            to_state VARCHAR(32) NOT NULL,
            actor VARCHAR(256) NOT NULL,
            reason VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_governed_case_transitions_tenant_case "
        "ON governed_case_transitions (tenant_id, case_id, created_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_governed_case_transitions_case ON governed_case_transitions (case_id);")
    for table in _TABLES:
        _enable_row_level_security(table)


def downgrade() -> None:
    # Forward-only: keep case records that evidence packages depend on.
    pass
