"""C6Z Seller Commerce Agent runtime vertical demo records.

Revision ID: v6z_runtime_vertical_demo
Revises: v6y5_retention_decisions
Create Date: 2026-06-14

This migration adds AgenticOrg-owned tenant-scoped runtime tables for read-only
Shopify connector evidence, Seller Commerce Agent onboarding packets, and
provider mandate capability evidence. The tables store redacted metadata only
and do not enable checkout, payment, order, mandate, public discovery, provider
execution, or merchant private API mutation.

Rollback: drop the C6Z tables and indexes. No Shopify token, raw provider
payload, payment record, order, mandate, or Grantex canonical artifact state is
stored here.
"""

from __future__ import annotations

from alembic import op

revision = "v6z_runtime_vertical_demo"
down_revision = "v6y5_retention_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_seller_onboarding_packets (
            packet_id VARCHAR(180) PRIMARY KEY,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            merchant_display_name VARCHAR(255) NOT NULL,
            public_brand_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
            commerce_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
            connector_choice VARCHAR(64) NOT NULL DEFAULT 'shopify',
            connector_mode VARCHAR(64) NOT NULL DEFAULT 'read_only',
            requested_grantex_authority_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_cache_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_freshness_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            connector_metadata_redacted JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(64) NOT NULL DEFAULT 'received',
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_c6z_onboarding_connector
                CHECK (connector_choice = 'shopify' AND connector_mode = 'read_only'),
            CONSTRAINT ck_c6z_onboarding_status
                CHECK (status IN (
                    'received',
                    'pending_sandbox_review',
                    'rejected',
                    'artifact_issuance_ready'
                )),
            CONSTRAINT ck_c6z_onboarding_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND no_payment_execution IS TRUE
                AND no_public_discovery_enablement IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_connector_evidence_records (
            evidence_id VARCHAR(180) PRIMARY KEY,
            packet_id VARCHAR(180) NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            source_system VARCHAR(64) NOT NULL DEFAULT 'shopify',
            source_mode VARCHAR(64) NOT NULL DEFAULT 'read_only',
            source_evidence_ref TEXT NOT NULL,
            source_observed_at TIMESTAMPTZ NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL,
            currency VARCHAR(16),
            products JSONB NOT NULL DEFAULT '[]'::jsonb,
            product_count INTEGER NOT NULL DEFAULT 0,
            variant_count INTEGER NOT NULL DEFAULT 0,
            idempotency_key VARCHAR(180),
            hmac_verified BOOLEAN NOT NULL DEFAULT FALSE,
            raw_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_c6z_connector_source
                CHECK (source_system = 'shopify' AND source_mode = 'read_only'),
            CONSTRAINT ck_c6z_connector_counts
                CHECK (product_count >= 0 AND variant_count >= 0),
            CONSTRAINT ck_c6z_connector_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND raw_payload_stored IS FALSE
                AND no_payment_execution IS TRUE
                AND no_public_discovery_enablement IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_provider_capability_evidence (
            evidence_id VARCHAR(180) PRIMARY KEY,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160),
            buyer_agent_id VARCHAR(160),
            provider VARCHAR(64) NOT NULL DEFAULT 'plural_pine',
            capability_type VARCHAR(80) NOT NULL,
            result_status VARCHAR(64) NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            redacted_evidence_ref TEXT NOT NULL,
            provider_environment VARCHAR(64) NOT NULL,
            external_validation_performed BOOLEAN NOT NULL DEFAULT FALSE,
            missing_env_vars JSONB NOT NULL DEFAULT '[]'::jsonb,
            raw_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            no_live_provider_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_c6z_capability_provider CHECK (provider = 'plural_pine'),
            CONSTRAINT ck_c6z_capability_status CHECK (
                result_status IN (
                    'available',
                    'unavailable',
                    'unknown',
                    'blocked_missing_credentials',
                    'blocked_provider_error'
                )
            ),
            CONSTRAINT ck_c6z_capability_ordered_timestamps CHECK (checked_at < expires_at),
            CONSTRAINT ck_c6z_capability_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND raw_payload_stored IS FALSE
                AND no_payment_execution IS TRUE
                AND no_live_provider_enablement IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_onboarding_tenant_id ON commerce_c6z_seller_onboarding_packets (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_onboarding_merchant_id ON commerce_c6z_seller_onboarding_packets (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_onboarding_seller_agent_id ON commerce_c6z_seller_onboarding_packets (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_onboarding_status ON commerce_c6z_seller_onboarding_packets (status);")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_c6z_onboarding_scope
            ON commerce_c6z_seller_onboarding_packets (tenant_id, merchant_id, seller_agent_id);
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_tenant_id ON commerce_c6z_connector_evidence_records (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_merchant_id ON commerce_c6z_connector_evidence_records (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_seller_agent_id ON commerce_c6z_connector_evidence_records (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_packet_id ON commerce_c6z_connector_evidence_records (packet_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_synced_at ON commerce_c6z_connector_evidence_records (synced_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_connector_evidence_source_ref ON commerce_c6z_connector_evidence_records (source_evidence_ref);")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_c6z_connector_evidence_idempotency
            ON commerce_c6z_connector_evidence_records (
                tenant_id,
                merchant_id,
                seller_agent_id,
                COALESCE(idempotency_key, evidence_id)
            );
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_tenant_id ON commerce_c6z_provider_capability_evidence (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_merchant_id ON commerce_c6z_provider_capability_evidence (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_seller_agent_id ON commerce_c6z_provider_capability_evidence (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_buyer_agent_id ON commerce_c6z_provider_capability_evidence (buyer_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_provider ON commerce_c6z_provider_capability_evidence (provider);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_result_status ON commerce_c6z_provider_capability_evidence (result_status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_capability_expires_at ON commerce_c6z_provider_capability_evidence (expires_at);")

    for table_name in (
        "commerce_c6z_seller_onboarding_packets",
        "commerce_c6z_connector_evidence_records",
        "commerce_c6z_provider_capability_evidence",
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name};")
        op.execute(f"""
            CREATE POLICY {table_name}_tenant_isolation
                ON {table_name}
                USING (tenant_id = current_setting('agenticorg.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
        """)


def downgrade() -> None:
    for table_name in (
        "commerce_c6z_provider_capability_evidence",
        "commerce_c6z_connector_evidence_records",
        "commerce_c6z_seller_onboarding_packets",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name};")

    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_expires_at;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_result_status;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_provider;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_buyer_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_capability_tenant_id;")
    op.execute("DROP INDEX IF EXISTS uq_c6z_connector_evidence_idempotency;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_source_ref;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_synced_at;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_packet_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_connector_evidence_tenant_id;")
    op.execute("DROP INDEX IF EXISTS uq_c6z_onboarding_scope;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_onboarding_status;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_onboarding_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_onboarding_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_onboarding_tenant_id;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_provider_capability_evidence;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_connector_evidence_records;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_seller_onboarding_packets;")
