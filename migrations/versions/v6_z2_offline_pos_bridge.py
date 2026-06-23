"""Add Offline POS Bridge handoff and confirmation records.

Revision ID: v6z2_offline_pos_bridge
Revises: v6z1_oacp_runtime_launch_closure
Create Date: 2026-06-23

This migration adds AgenticOrg-owned POS handoff packet and confirmation
records. They store non-sensitive evidence refs only and do not execute POS
transactions, orders, mandates, or payments.
"""

from __future__ import annotations

from alembic import op

revision = "v6z2_offline_pos_bridge"
down_revision = "v6z1_oacp_runtime_launch_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_offline_pos_handoff_packets (
            packet_id VARCHAR(180) PRIMARY KEY,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            buyer_agent_id VARCHAR(160),
            buyer_session_ref VARCHAR(180) NOT NULL,
            store_id VARCHAR(160) NOT NULL,
            pos_location JSONB NOT NULL DEFAULT '{}'::jsonb,
            packet JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(64) NOT NULL DEFAULT 'pos_handoff_packet_ready',
            expires_at TIMESTAMPTZ NOT NULL,
            idempotency_key VARCHAR(180) NOT NULL,
            raw_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            raw_payment_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            no_order_creation BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_c6z_pos_handoff_status CHECK (
                status IN ('pos_handoff_packet_ready', 'confirmed', 'expired', 'reconciled', 'blocked')
            ),
            CONSTRAINT ck_c6z_pos_handoff_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND raw_payload_stored IS FALSE
                AND raw_payment_payload_stored IS FALSE
                AND no_payment_execution IS TRUE
                AND no_order_creation IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_c6z_offline_pos_confirmations (
            confirmation_id VARCHAR(180) PRIMARY KEY,
            packet_id VARCHAR(180) NOT NULL,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            store_id VARCHAR(160) NOT NULL,
            confirmation_status VARCHAR(64) NOT NULL,
            callback_verified BOOLEAN NOT NULL DEFAULT FALSE,
            simulator_mode BOOLEAN NOT NULL DEFAULT FALSE,
            confirmation JSONB NOT NULL DEFAULT '{}'::jsonb,
            reconciliation JSONB NOT NULL DEFAULT '{}'::jsonb,
            provider_pos_evidence_ref TEXT,
            receipt_evidence_ref TEXT,
            inventory_refresh_required BOOLEAN NOT NULL DEFAULT FALSE,
            artifact_refresh_required BOOLEAN NOT NULL DEFAULT FALSE,
            confirmed_at TIMESTAMPTZ NOT NULL,
            raw_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            raw_payment_payload_stored BOOLEAN NOT NULL DEFAULT FALSE,
            no_payment_execution BOOLEAN NOT NULL DEFAULT TRUE,
            allowed_to_execute BOOLEAN NOT NULL DEFAULT FALSE,
            non_authoritative_for_transaction BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_c6z_pos_confirmation_status CHECK (
                confirmation_status IN (
                    'accepted',
                    'price_changed',
                    'out_of_stock',
                    'expired',
                    'needs_staff_review',
                    'unsupported',
                    'payment_pending',
                    'payment_confirmed',
                    'payment_failed',
                    'receipt_available'
                )
            ),
            CONSTRAINT ck_c6z_pos_confirmation_payment_success_evidence CHECK (
                confirmation_status NOT IN ('payment_confirmed', 'receipt_available')
                OR (
                    callback_verified IS TRUE
                    AND simulator_mode IS FALSE
                    AND provider_pos_evidence_ref IS NOT NULL
                )
            ),
            CONSTRAINT ck_c6z_pos_confirmation_non_execution CHECK (
                allowed_to_execute IS FALSE
                AND raw_payload_stored IS FALSE
                AND raw_payment_payload_stored IS FALSE
                AND no_payment_execution IS TRUE
                AND non_authoritative_for_transaction IS TRUE
            )
        );
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_tenant_id ON commerce_c6z_offline_pos_handoff_packets (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_merchant_id ON commerce_c6z_offline_pos_handoff_packets (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_seller_agent_id ON commerce_c6z_offline_pos_handoff_packets (seller_agent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_buyer_session_ref ON commerce_c6z_offline_pos_handoff_packets (buyer_session_ref);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_store_id ON commerce_c6z_offline_pos_handoff_packets (store_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_status ON commerce_c6z_offline_pos_handoff_packets (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_handoff_expires_at ON commerce_c6z_offline_pos_handoff_packets (expires_at);")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_c6z_pos_handoff_idempotency
            ON commerce_c6z_offline_pos_handoff_packets (
                tenant_id,
                merchant_id,
                seller_agent_id,
                buyer_session_ref,
                idempotency_key
            );
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_confirmation_tenant_id ON commerce_c6z_offline_pos_confirmations (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_confirmation_packet_id ON commerce_c6z_offline_pos_confirmations (packet_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_confirmation_merchant_id ON commerce_c6z_offline_pos_confirmations (merchant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_confirmation_status ON commerce_c6z_offline_pos_confirmations (confirmation_status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_c6z_pos_confirmation_confirmed_at ON commerce_c6z_offline_pos_confirmations (confirmed_at);")

    for table_name in (
        "commerce_c6z_offline_pos_handoff_packets",
        "commerce_c6z_offline_pos_confirmations",
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
        "commerce_c6z_offline_pos_confirmations",
        "commerce_c6z_offline_pos_handoff_packets",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name};")

    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_confirmation_confirmed_at;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_confirmation_status;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_confirmation_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_confirmation_packet_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_confirmation_tenant_id;")
    op.execute("DROP INDEX IF EXISTS uq_c6z_pos_handoff_idempotency;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_expires_at;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_status;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_store_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_buyer_session_ref;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_seller_agent_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_merchant_id;")
    op.execute("DROP INDEX IF EXISTS ix_c6z_pos_handoff_tenant_id;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_offline_pos_confirmations;")
    op.execute("DROP TABLE IF EXISTS commerce_c6z_offline_pos_handoff_packets;")
