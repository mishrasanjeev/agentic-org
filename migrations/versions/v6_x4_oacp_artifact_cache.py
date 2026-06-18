"""C6X4 durable OACP artifact cache records.

Revision ID: v6x4_oacp_cache
Revises: v4918_merge_ca_weekly_heads
Create Date: 2026-06-13

This table is an AgenticOrg-owned local cache for Grantex-issued OACP
artifact verifier results. It stores only non-sensitive artifact metadata and
redacted evidence references; it is not transaction authority and does not
enable checkout, payment, provider, merchant private API, or public discovery
execution.

Rollback: drop the cache table and its indexes. No source-of-record merchant,
provider, payment, or canonical Grantex artifact state is stored here.
"""

from __future__ import annotations

from alembic import op

revision = "v6x4_oacp_cache"
down_revision = "v4918_merge_ca_weekly_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS oacp_artifact_cache_records (
            cache_record_id VARCHAR(160) PRIMARY KEY,
            artifact_id VARCHAR(160) NOT NULL,
            artifact_type VARCHAR(64) NOT NULL,
            artifact_family VARCHAR(64) NOT NULL,
            authority VARCHAR(255) NOT NULL,
            issuer VARCHAR(255) NOT NULL,
            scope_kind VARCHAR(32) NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160),
            seller_agent_id VARCHAR(160),
            buyer_agent_id VARCHAR(160),
            source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            generated_at TIMESTAMPTZ NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            cached_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            freshness_status VARCHAR(32) NOT NULL,
            revocation_snapshot_status VARCHAR(32) NOT NULL,
            revocation_snapshot_age_seconds INTEGER,
            revocation_snapshot_observed_at TIMESTAMPTZ,
            ttl_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            ttl_policy_seconds INTEGER NOT NULL,
            risk_tier VARCHAR(32) NOT NULL,
            blocked_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            unsupported_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            verifier_result_ref TEXT,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            no_checkout_payment_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_live_provider_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_oacp_cache_scope_kind
                CHECK (scope_kind IN ('buyer_agent', 'seller_agent', 'tenant', 'merchant')),
            CONSTRAINT ck_oacp_cache_risk_tier
                CHECK (risk_tier IN ('informational', 'low', 'medium', 'high', 'critical')),
            CONSTRAINT ck_oacp_cache_ttl_positive CHECK (ttl_policy_seconds > 0),
            CONSTRAINT ck_oacp_cache_ordered_timestamps CHECK (generated_at <= cached_at AND cached_at < expires_at),
            CONSTRAINT ck_oacp_cache_non_execution_flags CHECK (
                allowed_to_execute IS FALSE
                AND non_authoritative_for_transaction IS TRUE
                AND no_checkout_payment_enablement IS TRUE
                AND no_live_provider_enablement IS TRUE
                AND no_public_discovery_enablement IS TRUE
            )
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_tenant_id ON oacp_artifact_cache_records (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_merchant_id ON oacp_artifact_cache_records (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_seller_agent_id ON oacp_artifact_cache_records (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_buyer_agent_id ON oacp_artifact_cache_records (buyer_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_artifact_id ON oacp_artifact_cache_records (artifact_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_artifact_type ON oacp_artifact_cache_records (artifact_type);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_cache_expires_at ON oacp_artifact_cache_records (expires_at);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_cache_freshness_status "
        "ON oacp_artifact_cache_records (freshness_status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_cache_revocation_status "
        "ON oacp_artifact_cache_records (revocation_snapshot_status);"
    )
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_oacp_cache_artifact_scope
            ON oacp_artifact_cache_records (
                tenant_id,
                COALESCE(merchant_id, ''),
                COALESCE(seller_agent_id, ''),
                COALESCE(buyer_agent_id, ''),
                artifact_id,
                artifact_type
            );
    """)
    op.execute("ALTER TABLE oacp_artifact_cache_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE oacp_artifact_cache_records FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS oacp_artifact_cache_records_tenant_isolation ON oacp_artifact_cache_records;")
    op.execute("""
        CREATE POLICY oacp_artifact_cache_records_tenant_isolation
            ON oacp_artifact_cache_records
            USING (tenant_id = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS oacp_artifact_cache_records_tenant_isolation ON oacp_artifact_cache_records;")
    op.execute("DROP INDEX IF EXISTS uq_oacp_cache_artifact_scope;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_revocation_status;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_freshness_status;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_expires_at;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_artifact_type;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_artifact_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_buyer_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_cache_tenant_id;")
    op.execute("DROP TABLE IF EXISTS oacp_artifact_cache_records;")
