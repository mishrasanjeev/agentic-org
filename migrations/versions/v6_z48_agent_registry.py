# SPDX-License-Identifier: Apache-2.0
"""Agent registry.

Revision ID: v6z48_agent_registry
Revises: v6z47_eval_run_tokens
Create Date: 2026-10-07

An agent's card fields and governance lifecycle state (``agent_registry``,
one row per agent) and its lifecycle transitions (``agent_registry_events``),
both tenant-scoped under row-level security (``core/agent_registry``).
"""

from alembic import op

revision = "v6z48_agent_registry"
down_revision = "v6z47_eval_run_tokens"
branch_labels = None
depends_on = None

_TABLES = ("agent_registry", "agent_registry_events")
_STATES = "'draft','review','approved','published','deprecated','retired'"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS agent_registry (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            purpose TEXT NULL,
            risk_tier VARCHAR(16) NULL,
            use_case VARCHAR(120) NULL,
            channels JSONB NOT NULL DEFAULT '[]'::jsonb,
            state VARCHAR(16) NOT NULL DEFAULT 'draft',
            state_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            state_changed_by UUID NULL,
            submitted_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_registry_state CHECK (state IN ({_STATES})),
            CONSTRAINT ck_agent_registry_risk_tier
                CHECK (risk_tier IS NULL OR risk_tier IN ('low','medium','high','critical'))
        );
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_registry_agent ON agent_registry(agent_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_registry_tenant_state ON agent_registry(tenant_id, state, risk_tier);"
    )
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS agent_registry_events (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            from_state VARCHAR(16) NOT NULL,
            to_state VARCHAR(16) NOT NULL,
            actor_user_id UUID NULL,
            note VARCHAR(500) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_registry_events_to CHECK (to_state IN ({_STATES}))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_registry_events_agent_created "
        "ON agent_registry_events(agent_id, created_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_registry_events_tenant_created "
        "ON agent_registry_events(tenant_id, created_at);"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation
            ON {table}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_registry_events;")
    op.execute("DROP TABLE IF EXISTS agent_registry;")
