# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Customer agent: customer sessions, mandates and prepared actions.

Revision ID: v6z84_customer_agent
Revises: v6z83_channel_bindings
Create Date: 2026-10-11

``customer_sessions``: one row per customer session the bank opened with a
signed assertion; the opaque session token is stored as a SHA-256 hash and
the assertion's id is unique per tenant, so an assertion opens one session.
``customer_mandates``: the autonomy level and limits a customer set per task
class. ``customer_actions``: every action the agent prepared, executed,
refused or saw cancelled, with the explanation shown and the approval given.
All three are tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z84_customer_agent"
down_revision = "v6z83_channel_bindings"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            company_id UUID NULL,
            customer_ref VARCHAR(128) NOT NULL,
            principal_id VARCHAR(128) NULL,
            channel VARCHAR(16) NOT NULL DEFAULT 'web',
            token_hash VARCHAR(64) NOT NULL,
            assertion_jti VARCHAR(128) NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NULL,
            disclosure_acknowledged_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_sessions_token_hash ON customer_sessions(token_hash);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_sessions_tenant_assertion "
        "ON customer_sessions(tenant_id, assertion_jti);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_sessions_tenant_customer "
        "ON customer_sessions(tenant_id, customer_ref, created_at DESC);"
    )
    _rls("customer_sessions")

    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_mandates (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            customer_ref VARCHAR(128) NOT NULL,
            task_class VARCHAR(64) NOT NULL,
            autonomy_level INTEGER NOT NULL DEFAULT 2,
            per_action_cap DOUBLE PRECISION NULL,
            period_cap DOUBLE PRECISION NULL,
            period VARCHAR(8) NOT NULL DEFAULT 'month',
            currency VARCHAR(3) NOT NULL DEFAULT 'INR',
            counterparties JSONB NOT NULL DEFAULT '[]'::jsonb,
            categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            time_window JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            expires_at TIMESTAMPTZ NULL,
            reconfirm_at TIMESTAMPTZ NULL,
            created_by VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_customer_mandates_level CHECK (autonomy_level >= 0 AND autonomy_level <= 4),
            CONSTRAINT ck_customer_mandates_period CHECK (period IN ('day','week','month')),
            CONSTRAINT ck_customer_mandates_status CHECK (status IN ('active','revoked','expired'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_mandates_tenant_customer_class "
        "ON customer_mandates(tenant_id, customer_ref, task_class);"
    )
    _rls("customer_mandates")

    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_actions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            customer_ref VARCHAR(128) NOT NULL,
            session_id UUID NULL,
            task_class VARCHAR(64) NOT NULL,
            intent VARCHAR(64) NOT NULL,
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            amount DOUBLE PRECISION NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'prepared',
            level_applied INTEGER NOT NULL DEFAULT 2,
            requires VARCHAR(16) NOT NULL DEFAULT 'confirm',
            approval JSONB NULL,
            explanation JSONB NOT NULL DEFAULT '{}'::jsonb,
            execution JSONB NULL,
            channel VARCHAR(16) NOT NULL DEFAULT 'web',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_customer_actions_status
                CHECK (status IN ('prepared','executed','cancelled','refused','failed','superseded'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_actions_tenant_customer_created "
        "ON customer_actions(tenant_id, customer_ref, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_actions_tenant_class_status "
        "ON customer_actions(tenant_id, customer_ref, task_class, status);"
    )
    _rls("customer_actions")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_actions;")
    op.execute("DROP TABLE IF EXISTS customer_mandates;")
    op.execute("DROP TABLE IF EXISTS customer_sessions;")
