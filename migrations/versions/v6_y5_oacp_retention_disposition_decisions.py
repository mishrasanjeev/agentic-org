"""C6Y5 durable OACP retention disposition decision records.

Revision ID: v6y5_retention_decisions
Revises: v6y4_repair_a2a_tasks
Create Date: 2026-06-14

This table is an AgenticOrg-owned internal retention disposition decision
repository. It stores only redacted, audit-safe decision metadata over C6Y4
dry-runs and operator review packets. It does not execute retention, delete or
purge records, write export files, schedule work, call Grantex live, call
providers, call merchant private APIs, or provide transaction authority.

Rollback: drop the disposition decision table, indexes, unique scope guard, and
RLS policy. No merchant source-of-record, provider, payment, checkout, export
file, retention execution, or canonical Grantex artifact state is stored here.
"""

from __future__ import annotations

from alembic import op

revision = "v6y5_retention_decisions"
down_revision = "v6y4_repair_a2a_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS oacp_retention_disposition_decision_records (
            disposition_decision_id VARCHAR(180) PRIMARY KEY,
            source_summary_id VARCHAR(180) NOT NULL,
            source_dry_run_id VARCHAR(180) NOT NULL,
            source_operator_packet_id VARCHAR(180) NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160),
            buyer_agent_id VARCHAR(160),
            generated_at TIMESTAMPTZ NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL,
            decision_kind VARCHAR(64) NOT NULL,
            retention_class VARCHAR(64) NOT NULL,
            retain_until TIMESTAMPTZ NOT NULL,
            manifest_count INTEGER NOT NULL DEFAULT 0,
            retention_due_count INTEGER NOT NULL DEFAULT 0,
            legal_hold_candidate_count INTEGER NOT NULL DEFAULT 0,
            artifact_family_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            risk_tier_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            blocked_capability_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            unsupported_capability_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            redacted_evidence_ref_count INTEGER NOT NULL DEFAULT 0,
            redacted_reason_codes JSONB NOT NULL DEFAULT '{}'::jsonb,
            reviewer_ref VARCHAR(160) NOT NULL,
            scope_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            next_step_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            future_retention_action_allowed BOOLEAN NOT NULL DEFAULT FALSE,
            records_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            retention_executed BOOLEAN NOT NULL DEFAULT FALSE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            no_execution BOOLEAN NOT NULL DEFAULT TRUE,
            retention_disposition_decision_only BOOLEAN NOT NULL DEFAULT TRUE,
            durable_disposition_decision_only BOOLEAN NOT NULL DEFAULT TRUE,
            export_file_written BOOLEAN NOT NULL DEFAULT FALSE,
            export_writer_added BOOLEAN NOT NULL DEFAULT FALSE,
            scheduler_added BOOLEAN NOT NULL DEFAULT FALSE,
            cli_added BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            no_checkout_payment_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_live_provider_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            no_public_discovery_enablement BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_oacp_retention_disposition_decision_kind
                CHECK (
                    decision_kind IN (
                        'approve_future_retention_review',
                        'approve_future_redaction_review',
                        'approve_future_legal_hold_review',
                        'request_more_evidence',
                        'reject_disposition',
                        'defer_until_recheck',
                        'block_unsafe_disposition'
                    )
                ),
            CONSTRAINT ck_oacp_retention_disposition_decision_retention_class
                CHECK (
                    retention_class IN (
                        'short_lived_internal_review',
                        'standard_internal_review',
                        'legal_hold_candidate'
                    )
                ),
            CONSTRAINT ck_oacp_retention_disposition_decision_counts CHECK (
                manifest_count >= 0
                AND retention_due_count >= 0
                AND legal_hold_candidate_count >= 0
                AND redacted_evidence_ref_count >= 0
            ),
            CONSTRAINT ck_oacp_retention_disposition_decision_reviewer_ref
                CHECK (reviewer_ref LIKE 'operator_ref_%' OR reviewer_ref LIKE 'reviewer_ref_%'),
            CONSTRAINT ck_oacp_retention_disposition_decision_ordered_timestamps
                CHECK (generated_at <= decided_at AND generated_at < retain_until),
            CONSTRAINT ck_oacp_retention_disposition_decision_non_execution_flags CHECK (
                future_retention_action_allowed IS FALSE
                AND records_deleted IS FALSE
                AND retention_executed IS FALSE
                AND allowed_to_execute IS FALSE
                AND no_execution IS TRUE
                AND retention_disposition_decision_only IS TRUE
                AND durable_disposition_decision_only IS TRUE
                AND export_file_written IS FALSE
                AND export_writer_added IS FALSE
                AND scheduler_added IS FALSE
                AND cli_added IS FALSE
                AND non_authoritative_for_transaction IS TRUE
                AND no_checkout_payment_enablement IS TRUE
                AND no_live_provider_enablement IS TRUE
                AND no_public_discovery_enablement IS TRUE
            )
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_tenant_id "
        "ON oacp_retention_disposition_decision_records (tenant_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_merchant_id "
        "ON oacp_retention_disposition_decision_records (merchant_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_seller_agent_id "
        "ON oacp_retention_disposition_decision_records (seller_agent_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_buyer_agent_id "
        "ON oacp_retention_disposition_decision_records (buyer_agent_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_kind "
        "ON oacp_retention_disposition_decision_records (decision_kind);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_summary_id "
        "ON oacp_retention_disposition_decision_records (source_summary_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_dry_run_id "
        "ON oacp_retention_disposition_decision_records (source_dry_run_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_packet_id "
        "ON oacp_retention_disposition_decision_records (source_operator_packet_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_retention_class "
        "ON oacp_retention_disposition_decision_records (retention_class);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_retain_until "
        "ON oacp_retention_disposition_decision_records (retain_until);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oacp_retention_disposition_decision_decided_at "
        "ON oacp_retention_disposition_decision_records (decided_at);"
    )
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_oacp_retention_disposition_decision_packet_kind_reviewer
            ON oacp_retention_disposition_decision_records (
                tenant_id,
                merchant_id,
                COALESCE(seller_agent_id, ''),
                COALESCE(buyer_agent_id, ''),
                source_operator_packet_id,
                decision_kind,
                reviewer_ref
            );
    """)
    op.execute("ALTER TABLE oacp_retention_disposition_decision_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE oacp_retention_disposition_decision_records FORCE ROW LEVEL SECURITY;")
    op.execute(
        "DROP POLICY IF EXISTS oacp_retention_disposition_decision_records_tenant_isolation "
        "ON oacp_retention_disposition_decision_records;"
    )
    op.execute("""
        CREATE POLICY oacp_retention_disposition_decision_records_tenant_isolation
            ON oacp_retention_disposition_decision_records
            USING (tenant_id = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS oacp_retention_disposition_decision_records_tenant_isolation "
        "ON oacp_retention_disposition_decision_records;"
    )
    op.execute("DROP INDEX IF EXISTS uq_oacp_retention_disposition_decision_packet_kind_reviewer;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_decided_at;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_retain_until;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_retention_class;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_packet_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_dry_run_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_summary_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_kind;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_buyer_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_oacp_retention_disposition_decision_tenant_id;")
    op.execute("DROP TABLE IF EXISTS oacp_retention_disposition_decision_records;")
