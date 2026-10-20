# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Payment execution records, RBI AFA evidence and the arbiter ``refund`` action.

Revision ID: v6z90_mandates_rails
Revises: v6z89_commerce_sessions_orders
Create Date: 2026-10-20

* ``commerce_payment_operations``: every call the Plural executor
  (``core/commerce/executors/plural.py``) makes, recorded as a ``requested``
  row before the call and an outcome row (``succeeded``, ``failed``,
  ``unknown``) after it. ``idempotency_key`` is the merchant reference sent to
  Plural (derived from the session or order and the consumed decision), unique
  per operation and phase, so a repeated attempt finds the first instead of
  calling again. Refunds are bounded by the ``succeeded`` capture rows.
* ``commerce_afa_evidence``: the RBI additional-factor-of-authentication state
  of a UPI AutoPay mandate (``requested`` when the AFA step is returned to the
  buyer, ``authorized`` with an evidence reference when a verified Plural event
  confirms the mandate). The arbiter rule ``afa_evidence_present`` reads it.
* ``commerce_arbiter_decisions``: the action CHECK admits ``refund``.

Both new tables are tenant-scoped with ENABLE + FORCE row-level security and a
``USING`` + ``WITH CHECK`` tenant policy, and append-only (trigger
``commerce_payment_record_reject_mutation``). Where the NOBYPASSRLS
application role exists it gets SELECT and INSERT only (docs/RLS_APP_ROLE.md;
``scripts/create_app_role.sql`` classifies the trigger function). Forward-only:
``downgrade`` keeps the records, which are payment evidence.
"""

from alembic import op

revision = "v6z90_mandates_rails"
down_revision = "v6z89_commerce_sessions_orders"
branch_labels = None
depends_on = None

PAYMENT_RECORD_TABLES: tuple[str, ...] = ("commerce_payment_operations", "commerce_afa_evidence")

_REJECT_MUTATION_SQL = """CREATE OR REPLACE FUNCTION commerce_payment_record_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      '% is append-only — UPDATE/DELETE rejected', TG_TABLE_NAME
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

# The same rule as scripts/create_app_role.sql, so a migration run after the script
# leaves the same privileges.
_APP_ROLE_GRANTS_SQL = """DO $$ BEGIN
    IF to_regrole('agenticorg_app') IS NOT NULL THEN
        GRANT SELECT, INSERT ON commerce_payment_operations TO agenticorg_app;
        REVOKE UPDATE, DELETE ON commerce_payment_operations FROM agenticorg_app;
        GRANT SELECT, INSERT ON commerce_afa_evidence TO agenticorg_app;
        REVOKE UPDATE, DELETE ON commerce_afa_evidence FROM agenticorg_app;
    END IF;
END $$;"""


def _create_tables() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_payment_operations (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            checkout_session_id VARCHAR(80) NOT NULL,
            order_id VARCHAR(80) NULL,
            decision_id VARCHAR(80) NOT NULL,
            provider VARCHAR(40) NOT NULL,
            operation VARCHAR(24) NOT NULL,
            phase VARCHAR(16) NOT NULL,
            idempotency_key VARCHAR(64) NOT NULL,
            provider_ref VARCHAR(255) NULL,
            amount_minor_units BIGINT NULL,
            currency VARCHAR(3) NULL,
            mandate_hashes JSONB NOT NULL DEFAULT '{}'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_commerce_payment_operations UNIQUE (tenant_id, idempotency_key, operation, phase),
            CONSTRAINT ck_commerce_payment_operations_operation CHECK
                (operation IN ('create_order', 'create_payment', 'capture', 'refund')),
            CONSTRAINT ck_commerce_payment_operations_phase CHECK
                (phase IN ('requested', 'succeeded', 'failed', 'unknown')),
            CONSTRAINT ck_commerce_payment_operations_amount CHECK
                (amount_minor_units IS NULL OR amount_minor_units >= 0)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_payment_operations_session "
        "ON commerce_payment_operations (tenant_id, checkout_session_id);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_afa_evidence (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            checkout_session_id VARCHAR(80) NOT NULL,
            instrument_id VARCHAR(120) NOT NULL,
            mandate_reference VARCHAR(64) NOT NULL,
            terms_sha256 VARCHAR(64) NOT NULL,
            terms JSONB NOT NULL,
            phase VARCHAR(16) NOT NULL,
            provider VARCHAR(40) NOT NULL,
            evidence_ref VARCHAR(120) NULL,
            provider_event_sha256 VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_commerce_afa_evidence_phase UNIQUE (tenant_id, mandate_reference, terms_sha256, phase),
            CONSTRAINT ck_commerce_afa_evidence_phase CHECK (phase IN ('requested', 'authorized', 'failed')),
            CONSTRAINT ck_commerce_afa_evidence_terms CHECK (terms_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_commerce_afa_evidence_ref CHECK ((phase = 'authorized') = (evidence_ref IS NOT NULL))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_afa_evidence_session "
        "ON commerce_afa_evidence (tenant_id, checkout_session_id);"
    )


def upgrade() -> None:
    _create_tables()
    op.execute(_REJECT_MUTATION_SQL)
    for table in PAYMENT_RECORD_TABLES:
        # Table names are the constants above, never input.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """)
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION commerce_payment_record_reject_mutation();"
        )
    op.execute("ALTER TABLE commerce_arbiter_decisions DROP CONSTRAINT IF EXISTS ck_commerce_arbiter_action;")
    op.execute(
        "ALTER TABLE commerce_arbiter_decisions ADD CONSTRAINT ck_commerce_arbiter_action CHECK "
        "(action IN ('purchase_handoff', 'mandate_request', 'pos_handoff', 'session_complete', 'refund'));"
    )
    op.execute(_APP_ROLE_GRANTS_SQL)


def downgrade() -> None:
    # Forward-only: payment operation records and AFA evidence are kept (they are payment evidence).
    pass
