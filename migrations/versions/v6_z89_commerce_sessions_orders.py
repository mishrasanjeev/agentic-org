# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""UCP transactions: checkout sessions, carts, orders, order webhooks and idempotency records.

Revision ID: v6z89_commerce_sessions_orders
Revises: v6z88_commerce_ledger
Create Date: 2026-10-20

The UCP REST binding (``api/v1/commerce_ucp.py``) keeps its state here:

* ``commerce_checkout_sessions``: one row per UCP checkout session, with the
  status machine (``incomplete``, ``requires_escalation``, ``ready_for_complete``,
  ``complete_in_progress``, ``completed``, ``canceled``) as a CHECK. At most one
  open session per cart (partial unique index), so cart-to-checkout conversion
  is idempotent under concurrency.
* ``commerce_carts``: merchant-side UCP carts (``dev.ucp.shopping.cart``).
* ``commerce_orders``: the order entity a completed session produces, one per
  session, with a revision counter for full-snapshot order webhooks.
* ``commerce_order_webhook_subscriptions``: where order snapshots are sent:
  the platform's ``dev.ucp.shopping.order`` ``config.webhook_url`` (RFC 9421
  signed) or a merchant-managed endpoint, optionally with the HMAC scheme of
  ``core.cases.push`` (keys encrypted with the tenant key, never plaintext).
* ``commerce_order_webhook_deliveries``: the delivery outbox (retry state,
  dead letters); one row per subscription and order revision.
* ``commerce_ucp_idempotency``: ``Idempotency-Key`` records (key, SHA-256 of
  the raw body, the stored response) kept for at least 24 hours (CHECK).

Every table is tenant-scoped with ENABLE + FORCE row-level security and a
``USING`` + ``WITH CHECK`` tenant policy, as in v6z88. Additive and
forward-only: ``downgrade`` keeps the tables, because dropping them would
discard orders and undelivered webhooks. ``upgrade`` is idempotent; when the
subscriptions table already exists only the row-level security statements
are re-applied and the encrypted-column gates, which exist for
transformations, are skipped.
"""

from alembic import op
from sqlalchemy import text

from core.crypto.migration_helpers import encrypted_migration

revision = "v6z89_commerce_sessions_orders"
down_revision = "v6z88_commerce_ledger"
branch_labels = None
depends_on = None

UCP_TABLES: tuple[str, ...] = (
    "commerce_checkout_sessions",
    "commerce_carts",
    "commerce_orders",
    "commerce_order_webhook_subscriptions",
    "commerce_order_webhook_deliveries",
    "commerce_ucp_idempotency",
)


def _enable_row_level_security() -> None:
    for table in UCP_TABLES:
        # Table names are the constants above, never input.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """)


def _create_tables() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_checkout_sessions (
            id VARCHAR(80) PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            serving_host VARCHAR(255) NOT NULL,
            ucp_version VARCHAR(10) NOT NULL,
            status VARCHAR(32) NOT NULL,
            currency VARCHAR(3) NULL,
            platform_profile_url VARCHAR(2048) NULL,
            cart_id VARCHAR(80) NULL,
            state JSONB NOT NULL,
            order_id VARCHAR(80) NULL,
            decision_id VARCHAR(80) NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ NULL,
            canceled_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_commerce_checkout_sessions_status CHECK (status IN (
                'incomplete', 'requires_escalation', 'ready_for_complete',
                'complete_in_progress', 'completed', 'canceled')),
            CONSTRAINT ck_commerce_checkout_sessions_order CHECK
                ((status = 'completed') = (order_id IS NOT NULL)),
            CONSTRAINT ck_commerce_checkout_sessions_version CHECK (ucp_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_checkout_sessions_scope "
        "ON commerce_checkout_sessions (tenant_id, merchant_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_checkout_sessions_status "
        "ON commerce_checkout_sessions (tenant_id, status, expires_at);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commerce_checkout_sessions_open_cart "
        "ON commerce_checkout_sessions (tenant_id, merchant_id, cart_id) "
        "WHERE cart_id IS NOT NULL AND status NOT IN ('completed', 'canceled');"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_carts (
            id VARCHAR(80) PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            serving_host VARCHAR(255) NOT NULL,
            ucp_version VARCHAR(10) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            currency VARCHAR(3) NULL,
            platform_profile_url VARCHAR(2048) NULL,
            state JSONB NOT NULL,
            checkout_session_id VARCHAR(80) NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_commerce_carts_status CHECK (status IN ('active', 'canceled'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_carts_scope ON commerce_carts (tenant_id, merchant_id, created_at DESC);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_orders (
            id VARCHAR(80) PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            checkout_session_id VARCHAR(80) NOT NULL,
            serving_host VARCHAR(255) NOT NULL,
            ucp_version VARCHAR(10) NOT NULL,
            platform_profile_url VARCHAR(2048) NULL,
            currency VARCHAR(3) NOT NULL,
            total_minor_units BIGINT NOT NULL,
            snapshot JSONB NOT NULL,
            decision_id VARCHAR(80) NOT NULL,
            executor VARCHAR(64) NOT NULL,
            provider_order_ref VARCHAR(255) NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_commerce_orders_session UNIQUE (tenant_id, checkout_session_id),
            CONSTRAINT ck_commerce_orders_revision CHECK (revision >= 1)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_orders_scope ON commerce_orders (tenant_id, merchant_id, created_at DESC);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_order_webhook_subscriptions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            url VARCHAR(2048) NOT NULL,
            scheme VARCHAR(16) NOT NULL,
            source VARCHAR(24) NOT NULL,
            platform_profile_url VARCHAR(2048) NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            signing_keys_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            active_key_id VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_commerce_order_webhook_subscriptions_target UNIQUE (tenant_id, merchant_id, url, scheme),
            CONSTRAINT ck_commerce_order_webhook_subscriptions_scheme CHECK (scheme IN ('ucp', 'acp_hmac')),
            CONSTRAINT ck_commerce_order_webhook_subscriptions_source CHECK (source IN ('platform_profile', 'merchant')),
            CONSTRAINT ck_commerce_order_webhook_subscriptions_keys CHECK (scheme <> 'acp_hmac' OR active_key_id IS NOT NULL)
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_order_webhook_deliveries (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            subscription_id UUID NOT NULL REFERENCES commerce_order_webhook_subscriptions(id) ON DELETE CASCADE,
            merchant_id VARCHAR(160) NOT NULL,
            order_id VARCHAR(80) NOT NULL,
            order_revision INTEGER NOT NULL,
            webhook_id UUID NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            payload JSONB NOT NULL,
            payload_sha256 VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL,
            last_error VARCHAR(64) NULL,
            last_status_code INTEGER NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivered_at TIMESTAMPTZ NULL,
            dead_lettered_at TIMESTAMPTZ NULL,
            CONSTRAINT uq_commerce_order_webhook_deliveries_revision UNIQUE
                (tenant_id, subscription_id, order_id, order_revision),
            CONSTRAINT ck_commerce_order_webhook_deliveries_status CHECK
                (status IN ('pending', 'delivered', 'dead_lettered')),
            CONSTRAINT ck_commerce_order_webhook_deliveries_event CHECK
                (event_type IN ('order.created', 'order.updated')),
            CONSTRAINT ck_commerce_order_webhook_deliveries_attempts CHECK (attempts >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_order_webhook_deliveries_due "
        "ON commerce_order_webhook_deliveries (tenant_id, status, next_attempt_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_order_webhook_deliveries_order "
        "ON commerce_order_webhook_deliveries (tenant_id, order_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_order_webhook_deliveries_subscription "
        "ON commerce_order_webhook_deliveries (subscription_id);"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_ucp_idempotency (
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            idempotency_key VARCHAR(255) NOT NULL,
            operation VARCHAR(120) NOT NULL,
            body_sha256 VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            response_status INTEGER NULL,
            response_body JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, merchant_id, idempotency_key),
            CONSTRAINT ck_commerce_ucp_idempotency_status CHECK (status IN ('pending', 'complete')),
            CONSTRAINT ck_commerce_ucp_idempotency_body CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_commerce_ucp_idempotency_retention CHECK (expires_at >= created_at + interval '24 hours'),
            CONSTRAINT ck_commerce_ucp_idempotency_response CHECK
                (status = 'pending' OR (response_status IS NOT NULL AND response_body IS NOT NULL))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_ucp_idempotency_expiry ON commerce_ucp_idempotency (tenant_id, expires_at);"
    )


def upgrade() -> None:
    existed = (
        op.get_bind()
        .execute(text("SELECT to_regclass('public.commerce_order_webhook_subscriptions') IS NOT NULL"))
        .scalar()
    )
    _create_tables()
    if existed:
        _enable_row_level_security()
        return

    with encrypted_migration(
        revision=revision,
        table="commerce_order_webhook_subscriptions",
        columns=["signing_keys_encrypted"],
        rollback_doc=(
            "The table is new in this revision and starts empty, so rollback does not transform or "
            "discard pre-existing ciphertext. Disable the merchant's order webhook subscriptions through "
            "the API first; the tables can then be dropped manually once no undelivered webhook depends on them."
        ),
    ) as ctx:
        pre_count = ctx.snapshot_row_count()
        ctx.dry_run_decrypt_sample(n=50)
        for _offset in ctx.iter_resumable_batches(batch=500):
            raise RuntimeError("new commerce_order_webhook_subscriptions table unexpectedly contained rows")

        _enable_row_level_security()

        ctx.assert_decrypt_after(n=50)
        ctx.record_audit({"pre_count": pre_count, "post_count": ctx.snapshot_row_count()})


def downgrade() -> None:
    # Forward-only: keep orders, carts, sessions, undelivered order webhooks and encrypted signing keys.
    pass
