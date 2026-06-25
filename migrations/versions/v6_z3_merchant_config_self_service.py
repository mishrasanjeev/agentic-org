"""Add merchant-scoped OACP commerce self-service config.

Revision ID: v6z3_merchant_config_selfsvc
Revises: v6z2_offline_pos_bridge
Create Date: 2026-06-24

This table stores redacted merchant configuration for source connectors,
buyer channels, provider-owned payment rails, public publishing, and Offline
POS stores. It does not store raw secrets, raw provider payloads, orders,
mandates, or payment execution records.
"""

from __future__ import annotations

from alembic import op

revision = "v6z3_merchant_config_selfsvc"
down_revision = "v6z2_offline_pos_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_merchant_configs (
            config_id VARCHAR(180) PRIMARY KEY,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL DEFAULT 'default',
            merchant_display_name VARCHAR(255) NOT NULL,
            public_brand_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
            commerce_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_connectors JSONB NOT NULL DEFAULT '[]'::jsonb,
            buyer_channels JSONB NOT NULL DEFAULT '{}'::jsonb,
            payment_providers JSONB NOT NULL DEFAULT '[]'::jsonb,
            offline_pos_stores JSONB NOT NULL DEFAULT '[]'::jsonb,
            public_publishing JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_freshness_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            provider_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            readiness JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(64) NOT NULL DEFAULT 'configured',
            raw_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_c6z_merchant_config_scope UNIQUE (
                tenant_id,
                merchant_id,
                seller_agent_id
            ),
            CONSTRAINT ck_c6z_merchant_config_status CHECK (
                status IN ('configured', 'disabled', 'needs_review')
            ),
            CONSTRAINT ck_c6z_merchant_config_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND raw_payload_stored IS FALSE
                AND no_payment_execution IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_merchant_config_tenant_id ON commerce_c6z_merchant_configs (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_merchant_config_merchant_id ON commerce_c6z_merchant_configs (merchant_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_c6z_merchant_config_seller_agent_id "
        "ON commerce_c6z_merchant_configs (seller_agent_id);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_merchant_config_status ON commerce_c6z_merchant_configs (status);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_c6z_merchant_config_status;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_merchant_config_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_merchant_config_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_merchant_config_tenant_id;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_merchant_configs;")
