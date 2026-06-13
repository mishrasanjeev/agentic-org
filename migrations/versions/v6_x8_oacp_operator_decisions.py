"""C6X8 durable OACP operator decision records.

Revision ID: v6x8_oacp_operator_decisions
Revises: v6x4_oacp_cache
Create Date: 2026-06-13

This table is an AgenticOrg-owned local decision log for C6X7 operator
decision records. It stores only redacted, audit-safe metadata and opaque
reviewer references. It is not transaction authority and does not enable
checkout, payment, provider, merchant private API, public discovery, refresh,
eviction, quarantine, scheduler, or maintenance execution.

Rollback: drop the operator decision table and its indexes. No source-of-record
merchant, provider, payment, or canonical Grantex artifact state is stored here.
"""

from __future__ import annotations

from alembic import op

revision = "v6x8_oacp_operator_decisions"
down_revision = "v6x4_oacp_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS oacp_operator_decision_records (
            decision_id VARCHAR(180) PRIMARY KEY,
            review_packet_id VARCHAR(180) NOT NULL,
            maintenance_plan_id VARCHAR(180) NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL,
            decision_kind VARCHAR(64) NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160),
            seller_agent_id VARCHAR(160),
            buyer_agent_id VARCHAR(160),
            scope_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_family_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            artifact_families_affected JSONB NOT NULL DEFAULT '[]'::jsonb,
            redacted_reason_codes JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            reviewer_ref VARCHAR(160) NOT NULL,
            next_step_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            future_action_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            prepared_only BOOLEAN NOT NULL DEFAULT TRUE,
            operator_decision_only BOOLEAN NOT NULL DEFAULT TRUE,
            audit_safe_decision_record BOOLEAN NOT NULL DEFAULT TRUE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            no_checkout_payment_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_live_provider_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_oacp_operator_decision_kind
                CHECK (
                    decision_kind IN (
                        'approve_future_refresh_request',
                        'approve_future_eviction_request',
                        'approve_future_quarantine_request',
                        'request_more_evidence',
                        'reject_maintenance_action',
                        'defer_until_freshness_update',
                        'escalate_to_human_support',
                        'block_unsafe_action'
                    )
                ),
            CONSTRAINT ck_oacp_operator_decision_reviewer_ref
                CHECK (reviewer_ref LIKE 'operator_ref_%' OR reviewer_ref LIKE 'reviewer_ref_%'),
            CONSTRAINT ck_oacp_operator_decision_ordered_timestamps
                CHECK (generated_at <= decided_at),
            CONSTRAINT ck_oacp_operator_decision_non_execution_flags CHECK (
                allowed_to_execute IS FALSE
                AND future_action_allowed IS FALSE
                AND prepared_only IS TRUE
                AND operator_decision_only IS TRUE
                AND audit_safe_decision_record IS TRUE
                AND non_authoritative_for_transaction IS TRUE
                AND no_checkout_payment_enablement IS TRUE
                AND no_live_provider_enablement IS TRUE
                AND no_public_discovery_enablement IS TRUE
            )
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_tenant_id ON oacp_operator_decision_records (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_merchant_id ON oacp_operator_decision_records (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_seller_agent_id ON oacp_operator_decision_records (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_buyer_agent_id ON oacp_operator_decision_records (buyer_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_review_packet_id ON oacp_operator_decision_records (review_packet_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_maintenance_plan_id ON oacp_operator_decision_records (maintenance_plan_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_decision_kind ON oacp_operator_decision_records (decision_kind);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oacp_operator_decision_decided_at ON oacp_operator_decision_records (decided_at);")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_oacp_operator_decision_packet_kind_reviewer
            ON oacp_operator_decision_records (
                tenant_id,
                COALESCE(merchant_id, ''),
                COALESCE(seller_agent_id, ''),
                COALESCE(buyer_agent_id, ''),
                review_packet_id,
                decision_kind,
                reviewer_ref
            );
    """)
    op.execute("ALTER TABLE oacp_operator_decision_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE oacp_operator_decision_records FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS oacp_operator_decision_records_tenant_isolation ON oacp_operator_decision_records;")
    op.execute("""
        CREATE POLICY oacp_operator_decision_records_tenant_isolation
            ON oacp_operator_decision_records
            USING (tenant_id = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS oacp_operator_decision_records_tenant_isolation ON oacp_operator_decision_records;")
    op.execute("DROP INDEX IF EXISTS uq_oacp_operator_decision_packet_kind_reviewer;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_decided_at;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_decision_kind;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_maintenance_plan_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_review_packet_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_buyer_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_operator_decision_tenant_id;")
    op.execute("DROP TABLE IF EXISTS oacp_operator_decision_records;")
