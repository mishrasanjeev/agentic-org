"""C6Y2 durable OACP audit review manifests.

Revision ID: v6y2_oacp_review_manifests
Revises: v6x9_audit_log_action_text
Create Date: 2026-06-13

This table is an AgenticOrg-owned internal review manifest repository. It
stores only redacted, audit-safe metadata and retention boundary fields for
C6Y1 audit review manifests. It is not transaction authority and does not
enable export-file writing, scheduling, checkout, payment, provider calls,
merchant private API calls, public discovery, or Grantex live calls.

Rollback: drop the review manifest table, indexes, unique scope guard, and RLS
policy. No merchant source-of-record, provider, payment, checkout, export file,
or canonical Grantex artifact state is stored here.
"""

from __future__ import annotations

from alembic import op

revision = "v6y2_oacp_review_manifests"
down_revision = "v6x9_audit_log_action_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS oacp_audit_review_manifest_records (
            manifest_id VARCHAR(180) PRIMARY KEY,
            bundle_id VARCHAR(180) NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL,
            bundle_generated_at TIMESTAMPTZ NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160),
            buyer_agent_id VARCHAR(160),
            scope_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            retention_class VARCHAR(64) NOT NULL,
            retention_days INTEGER NOT NULL,
            retain_until TIMESTAMPTZ NOT NULL,
            retention_clock_source VARCHAR(64) NOT NULL,
            artifact_family_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            cache_record_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            maintenance_plan_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            review_packet_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            decision_record_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            redacted_reason_codes JSONB NOT NULL DEFAULT '{}'::jsonb,
            redacted_source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            redacted_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            freshness_ttl_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            revocation_snapshot_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            risk_tier_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            unsupported_capability_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            blocked_capability_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            next_step_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            no_execution BOOLEAN NOT NULL DEFAULT TRUE,
            review_manifest_only BOOLEAN NOT NULL DEFAULT TRUE,
            retention_boundary_only BOOLEAN NOT NULL DEFAULT TRUE,
            audit_export_bundle_review_only BOOLEAN NOT NULL DEFAULT TRUE,
            export_file_written BOOLEAN NOT NULL DEFAULT FALSE,
            export_writer_added BOOLEAN NOT NULL DEFAULT FALSE,
            generated_artifact_written BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            no_checkout_payment_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_live_provider_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_oacp_review_manifest_retention_class
                CHECK (
                    retention_class IN (
                        'short_lived_internal_review',
                        'standard_internal_review',
                        'legal_hold_candidate'
                    )
                ),
            CONSTRAINT ck_oacp_review_manifest_retention_days CHECK (retention_days > 0),
            CONSTRAINT ck_oacp_review_manifest_ordered_timestamps
                CHECK (bundle_generated_at <= generated_at AND generated_at < retain_until),
            CONSTRAINT ck_oacp_review_manifest_non_execution_flags CHECK (
                allowed_to_execute IS FALSE
                AND no_execution IS TRUE
                AND review_manifest_only IS TRUE
                AND retention_boundary_only IS TRUE
                AND audit_export_bundle_review_only IS TRUE
                AND export_file_written IS FALSE
                AND export_writer_added IS FALSE
                AND generated_artifact_written IS FALSE
                AND non_authoritative_for_transaction IS TRUE
                AND no_checkout_payment_enablement IS TRUE
                AND no_live_provider_enablement IS TRUE
                AND no_public_discovery_enablement IS TRUE
            )
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_tenant_id "
        "ON oacp_audit_review_manifest_records (tenant_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_merchant_id "
        "ON oacp_audit_review_manifest_records (merchant_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_seller_agent_id "
        "ON oacp_audit_review_manifest_records (seller_agent_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_buyer_agent_id "
        "ON oacp_audit_review_manifest_records (buyer_agent_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_bundle_id "
        "ON oacp_audit_review_manifest_records (bundle_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_retention_class "
        "ON oacp_audit_review_manifest_records (retention_class);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_retain_until "
        "ON oacp_audit_review_manifest_records (retain_until);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_audit_review_manifest_generated_at "
        "ON oacp_audit_review_manifest_records (generated_at);"
    )
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_oacp_review_manifest_bundle_retention_scope
            ON oacp_audit_review_manifest_records (
                tenant_id,
                merchant_id,
                COALESCE(seller_agent_id, ''),
                COALESCE(buyer_agent_id, ''),
                bundle_id,
                retention_class
            );
    """)
    op.execute("ALTER TABLE oacp_audit_review_manifest_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE oacp_audit_review_manifest_records FORCE ROW LEVEL SECURITY;")
    op.execute(
        "DROP POLICY IF EXISTS oacp_audit_review_manifest_records_tenant_isolation "
        "ON oacp_audit_review_manifest_records;"
    )
    op.execute("""
        CREATE POLICY oacp_audit_review_manifest_records_tenant_isolation
            ON oacp_audit_review_manifest_records
            USING (tenant_id = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS oacp_audit_review_manifest_records_tenant_isolation "
        "ON oacp_audit_review_manifest_records;"
    )
    op.execute("DROP INDEX IF EXISTS uq_oacp_review_manifest_bundle_retention_scope;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_generated_at;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_retain_until;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_retention_class;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_bundle_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_buyer_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_audit_review_manifest_tenant_id;")
    op.execute("DROP TABLE IF EXISTS oacp_audit_review_manifest_records;")
