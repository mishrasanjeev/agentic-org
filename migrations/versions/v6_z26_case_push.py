# SPDX-License-Identifier: Apache-2.0
"""Case push endpoints, the case push outbox and provider webhook receipts.

Revision ID: v6z26_case_push
Revises: v6z25_governed_cases
Create Date: 2026-09-15

PRD A-8: a governed case is handed to the operator's system of record by a
signed webhook. ``case_push_endpoints`` holds each tenant's destination and
its HMAC signing keys, encrypted with the tenant's key
(``core.crypto.tenant_secrets.encrypt_for_tenant``); plaintext secrets are
never written. ``case_push_outbox`` is written in the same transaction as the
case change that causes a push, so a failed delivery never loses the case;
it carries retry state and the dead-letter status. ``provider_webhook_receipts``
records every inbound provider webhook (never its body) and makes a verified
event id single-use.

Additive and forward-only: three new tables with row-level security in the
same shape as their neighbours. ``downgrade`` keeps them: dropping the outbox
would discard undelivered hand-offs. ``upgrade`` is idempotent; when the
endpoints table already exists only the row-level security statements are
re-applied and the encrypted-column gates, which exist for transformations,
are skipped.
"""

from alembic import op
from sqlalchemy import text

from core.crypto.migration_helpers import encrypted_migration

revision = "v6z26_case_push"
down_revision = "v6z25_governed_cases"
branch_labels = None
depends_on = None

_TABLES = ("case_push_endpoints", "case_push_outbox", "provider_webhook_receipts")


def _enable_row_level_security() -> None:
    for table in _TABLES:
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
        CREATE TABLE IF NOT EXISTS case_push_endpoints (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            url VARCHAR(2048) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            signing_keys_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            active_key_id VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_case_push_endpoints_tenant UNIQUE (tenant_id)
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS case_push_outbox (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id UUID NOT NULL REFERENCES governed_cases(id) ON DELETE CASCADE,
            event_id UUID NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            payload JSONB NOT NULL,
            payload_sha256 VARCHAR(71) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL,
            last_error VARCHAR(64),
            last_status_code INTEGER,
            replay_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivered_at TIMESTAMPTZ,
            dead_lettered_at TIMESTAMPTZ,
            CONSTRAINT uq_case_push_outbox_tenant_event UNIQUE (tenant_id, event_id),
            CONSTRAINT ck_case_push_outbox_status CHECK (status IN ('pending', 'delivered', 'dead_lettered')),
            CONSTRAINT ck_case_push_outbox_attempts CHECK (attempts >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_push_outbox_tenant_status_due "
        "ON case_push_outbox (tenant_id, status, next_attempt_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_case_push_outbox_case ON case_push_outbox (case_id);")
    op.execute("""
        CREATE TABLE IF NOT EXISTS provider_webhook_receipts (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            provider VARCHAR(64) NOT NULL,
            verified BOOLEAN NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            event_id VARCHAR(128),
            event_type VARCHAR(64),
            body_sha256 VARCHAR(71) NOT NULL,
            requeried_cases INTEGER NOT NULL DEFAULT 0,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_provider_webhook_receipts_outcome CHECK (outcome IN ('accepted', 'duplicate', 'unverified'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_webhook_receipts_accepted_event "
        "ON provider_webhook_receipts (tenant_id, provider, event_id) WHERE outcome = 'accepted';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provider_webhook_receipts_tenant_received "
        "ON provider_webhook_receipts (tenant_id, received_at);"
    )


def upgrade() -> None:
    existed = op.get_bind().execute(text("SELECT to_regclass('public.case_push_endpoints') IS NOT NULL")).scalar()
    _create_tables()
    if existed:
        _enable_row_level_security()
        return

    with encrypted_migration(
        revision=revision,
        table="case_push_endpoints",
        columns=["signing_keys_encrypted"],
        rollback_doc=(
            "The table is new in this revision and starts empty, so rollback does not transform or "
            "discard pre-existing ciphertext. Remove the tenant's push endpoint through the API first; "
            "the tables can then be dropped manually once no undelivered outbox row depends on them."
        ),
    ) as ctx:
        pre_count = ctx.snapshot_row_count()
        ctx.dry_run_decrypt_sample(n=50)
        for _offset in ctx.iter_resumable_batches(batch=500):
            raise RuntimeError("new case_push_endpoints table unexpectedly contained rows")

        _enable_row_level_security()

        ctx.assert_decrypt_after(n=50)
        ctx.record_audit({"pre_count": pre_count, "post_count": ctx.snapshot_row_count()})


def downgrade() -> None:
    # Forward-only: keep undelivered hand-offs and encrypted signing keys.
    pass
