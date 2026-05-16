"""Durable live feed events.

Revision ID: v4913_feed_events
Revises: v4912_workflow_event_waits
Create Date: 2026-05-16

Live feed WebSocket delivery is now backed by a tenant-scoped append-only
event log so missed socket events can be replayed after reconnect.
"""

from __future__ import annotations

from alembic import op

revision = "v4913_feed_events"
down_revision = "v4912_workflow_event_waits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS feed_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            sequence BIGINT NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            source VARCHAR(100),
            correlation_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_feed_events_tenant_sequence UNIQUE (tenant_id, sequence)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feed_events_tenant_sequence "
        "ON feed_events (tenant_id, sequence)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feed_events_tenant_created "
        "ON feed_events (tenant_id, created_at)"
    )
    op.execute("ALTER TABLE feed_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE feed_events FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS feed_events_tenant_isolation ON feed_events")
    op.execute("""
        CREATE POLICY feed_events_tenant_isolation ON feed_events
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS feed_events_tenant_isolation ON feed_events")
    op.drop_index("ix_feed_events_tenant_created", table_name="feed_events")
    op.drop_index("ix_feed_events_tenant_sequence", table_name="feed_events")
    op.drop_table("feed_events")
