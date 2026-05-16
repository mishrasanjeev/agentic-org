"""Durable bridge sessions and requests.

Revision ID: v4915_bridge_durability
Revises: v4912_workflow_event_waits
Create Date: 2026-05-16

This intentionally uses a unique v4915 revision while branching from v4912.
PR #553 and PR #554 also branch from v4912 with their own heads; once those
P0 PRs are merged together, Alembic will need a merge-head revision that
joins v4913/v4914/v4915-style heads.
"""

from __future__ import annotations

# ruff: noqa: S608

from alembic import op

revision = "v4915_bridge_durability"
down_revision = "v4912_workflow_event_waits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bridge_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            bridge_id VARCHAR(100) NOT NULL
                REFERENCES bridge_registry(bridge_id) ON DELETE CASCADE,
            connector_type VARCHAR(50) NOT NULL DEFAULT 'tally',
            status VARCHAR(20) NOT NULL DEFAULT 'disconnected',
            connected_at TIMESTAMPTZ,
            disconnected_at TIMESTAMPTZ,
            last_heartbeat TIMESTAMPTZ,
            tally_healthy BOOLEAN NOT NULL DEFAULT FALSE,
            connection_owner VARCHAR(255),
            process_id INTEGER,
            reconnect_count INTEGER NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_bridge_sessions_bridge_id UNIQUE (bridge_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_sessions_tenant_status "
        "ON bridge_sessions (tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_sessions_tenant_type "
        "ON bridge_sessions (tenant_id, connector_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_sessions_last_heartbeat "
        "ON bridge_sessions (last_heartbeat)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS bridge_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            request_id VARCHAR(100) NOT NULL,
            bridge_id VARCHAR(100) NOT NULL
                REFERENCES bridge_registry(bridge_id) ON DELETE CASCADE,
            connector_type VARCHAR(50) NOT NULL DEFAULT 'tally',
            method VARCHAR(100) NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            idempotency_key VARCHAR(255),
            response_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB,
            error JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            responded_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_bridge_requests_request_id "
        "ON bridge_requests (request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_requests_tenant_status "
        "ON bridge_requests (tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_requests_bridge_status "
        "ON bridge_requests (bridge_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bridge_requests_expires_at "
        "ON bridge_requests (expires_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bridge_requests_idempotency "
        "ON bridge_requests (tenant_id, bridge_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    for table_name in ("bridge_sessions", "bridge_requests"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
        op.execute(f"""
            CREATE POLICY {table_name}_tenant_isolation ON {table_name}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        """)


def downgrade() -> None:
    for table_name in ("bridge_requests", "bridge_sessions"):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")

    op.drop_index("uq_bridge_requests_idempotency", table_name="bridge_requests")
    op.drop_index("ix_bridge_requests_expires_at", table_name="bridge_requests")
    op.drop_index("ix_bridge_requests_bridge_status", table_name="bridge_requests")
    op.drop_index("ix_bridge_requests_tenant_status", table_name="bridge_requests")
    op.drop_index("ix_bridge_requests_request_id", table_name="bridge_requests")
    op.drop_table("bridge_requests")

    op.drop_index("ix_bridge_sessions_last_heartbeat", table_name="bridge_sessions")
    op.drop_index("ix_bridge_sessions_tenant_type", table_name="bridge_sessions")
    op.drop_index("ix_bridge_sessions_tenant_status", table_name="bridge_sessions")
    op.drop_table("bridge_sessions")
