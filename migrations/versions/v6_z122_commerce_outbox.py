# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Commerce merchant emails outbox and Plural payments no order matches.

Revision ID: v6z122_commerce_outbox
Revises: v6z115_insights_counters
Create Date: 2026-11-04

- ``commerce_merchant_notifications``: one row per email the merchant's
  administrators must get (a new order, or a Plural payment that needs their
  attention), written in the transaction of the business change. A beat sweep
  sends due rows (``pending`` or ``failed``) with backoff and marks them
  ``sent`` or, after the last attempt, ``dead``. One row per kind and payload
  reference (unique), so a repeated confirmation never queues a second email.
- ``commerce_unmatched_payments``: a Plural hosted payment that reached the
  merchant's Plural account with no matching order (or an order made while
  stock was short). It keeps the Plural order, the amount, the merchant's
  credential reference and the refund state, so an administrator can refund
  it once from AgenticOrg. One row per tenant and Plural order (unique).

Both tables are tenant scoped under forced row-level security.
"""

from alembic import op

revision = "v6z122_commerce_outbox"
down_revision = "v6z115_insights_counters"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"
TABLES = ("commerce_merchant_notifications", "commerce_unmatched_payments")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_merchant_notifications (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            payload_ref VARCHAR(160) NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error VARCHAR(120) NULL,
            sent_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_commerce_merchant_notifications_kind CHECK (kind IN ('order','payment_attention')),
            CONSTRAINT ck_commerce_merchant_notifications_status
                CHECK (status IN ('pending','sent','failed','dead')),
            CONSTRAINT ck_commerce_merchant_notifications_attempts CHECK (attempts >= 0),
            CONSTRAINT uq_commerce_merchant_notifications_ref UNIQUE (tenant_id, kind, payload_ref)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_merchant_notifications_due "
        "ON commerce_merchant_notifications(tenant_id, status, next_attempt_at);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_unmatched_payments (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            checkout_session_id VARCHAR(80) NOT NULL,
            reference VARCHAR(64) NOT NULL,
            provider_order_id VARCHAR(120) NOT NULL,
            reason VARCHAR(40) NOT NULL,
            amount_minor_units BIGINT NULL,
            currency VARCHAR(3) NULL,
            credential_ref VARCHAR(80) NULL,
            order_id VARCHAR(80) NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            refund_status VARCHAR(16) NOT NULL DEFAULT 'none',
            refund_attempts INTEGER NOT NULL DEFAULT 0,
            refund_reference VARCHAR(64) NULL,
            refund_error VARCHAR(120) NULL,
            refunded_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_commerce_unmatched_payments_refund_status
                CHECK (refund_status IN ('none','requested','pending','refunded','failed')),
            CONSTRAINT ck_commerce_unmatched_payments_amount
                CHECK (amount_minor_units IS NULL OR amount_minor_units >= 0),
            CONSTRAINT uq_commerce_unmatched_payments_provider_order UNIQUE (tenant_id, provider_order_id)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_unmatched_payments_scope "
        "ON commerce_unmatched_payments(tenant_id, merchant_id, created_at DESC);"
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
