"""Durable CDC webhook ingestion.

Revision ID: v4913_cdc_webhook_durability
Revises: v4912_workflow_event_waits
Create Date: 2026-05-16

CDC webhook ingestion previously depended on process-local lists and sets for
event history and deduplication. This migration extends the existing
``cdc_events`` table into the source of truth and adds a dead-letter table for
auditable processing failures.
"""

from __future__ import annotations

from alembic import op

revision = "v4913_cdc_webhook_durability"
down_revision = "v4912_workflow_event_waits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some legacy Alembic-cutover environments were stamped past v4.0.0
    # without receiving the original CDC event table. The durability columns
    # below extend that table, so recreate the canonical v4.0.0 table shape
    # when it is missing before applying the durable ingestion schema.
    op.execute("""
        CREATE TABLE IF NOT EXISTS cdc_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            connector VARCHAR(100) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id VARCHAR(200) NOT NULL,
            payload JSONB NOT NULL,
            processed BOOLEAN NOT NULL DEFAULT FALSE,
            event_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_cdc_events_tenant_id ON cdc_events (tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cdc_events_connector ON cdc_events (connector)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cdc_events_event_hash ON cdc_events (event_hash)")

    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS provider_event_id VARCHAR(255)")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)")
    op.execute(
        "UPDATE cdc_events SET fingerprint = left(event_hash, 32) || replace(id::text, '-', '') "
        "WHERE fingerprint IS NULL"
    )
    op.execute("ALTER TABLE cdc_events ALTER COLUMN fingerprint SET NOT NULL")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS raw_body_hash VARCHAR(64)")
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS "
        "signature_verification_status VARCHAR(30) NOT NULL DEFAULT 'valid'"
    )
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS "
        "processing_status VARCHAR(30) NOT NULL DEFAULT 'received'"
    )
    op.execute(
        "UPDATE cdc_events SET processing_status = 'processed' "
        "WHERE processed IS TRUE AND processing_status = 'received'"
    )
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS "
        "processing_outcome JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS "
        "replay_status VARCHAR(30) NOT NULL DEFAULT 'not_replayed'"
    )
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS "
        "replay_attempts INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS received_at "
        "TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS last_replayed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS last_replayed_by VARCHAR(255)")
    op.execute("ALTER TABLE cdc_events ADD COLUMN IF NOT EXISTS error_details JSONB")

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cdc_events_tenant_connector_fingerprint "
        "ON cdc_events (tenant_id, connector, fingerprint)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cdc_events_tenant_connector_provider_event "
        "ON cdc_events (tenant_id, connector, provider_event_id) "
        "WHERE provider_event_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_events_tenant_connector_status "
        "ON cdc_events (tenant_id, connector, processing_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_events_tenant_event_received "
        "ON cdc_events (tenant_id, event_type, received_at)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS cdc_event_dead_letters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cdc_event_id UUID NOT NULL REFERENCES cdc_events(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            connector VARCHAR(100) NOT NULL,
            event_hash VARCHAR(64) NOT NULL,
            failure_stage VARCHAR(100) NOT NULL,
            error_code VARCHAR(100) NOT NULL,
            error_message TEXT NOT NULL,
            error_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            replayable BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_event_dead_letters_tenant_created "
        "ON cdc_event_dead_letters (tenant_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_event_dead_letters_event "
        "ON cdc_event_dead_letters (cdc_event_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cdc_event_dead_letters_event")
    op.execute("DROP INDEX IF EXISTS ix_cdc_event_dead_letters_tenant_created")
    op.execute("DROP TABLE IF EXISTS cdc_event_dead_letters")

    op.execute("DROP INDEX IF EXISTS ix_cdc_events_tenant_event_received")
    op.execute("DROP INDEX IF EXISTS ix_cdc_events_tenant_connector_status")
    op.execute("DROP INDEX IF EXISTS uq_cdc_events_tenant_connector_provider_event")
    op.execute("DROP INDEX IF EXISTS uq_cdc_events_tenant_connector_fingerprint")

    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS error_details")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS last_replayed_by")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS last_replayed_at")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS processed_at")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS received_at")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS replay_attempts")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS replay_status")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS processing_outcome")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS processing_status")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS signature_verification_status")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS raw_body_hash")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS fingerprint")
    op.execute("ALTER TABLE cdc_events DROP COLUMN IF EXISTS provider_event_id")
