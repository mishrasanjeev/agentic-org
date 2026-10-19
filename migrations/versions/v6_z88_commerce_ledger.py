# SPDX-License-Identifier: Apache-2.0
"""Commerce ledger: arbiter decisions, append-only OACP decision tables and the audit_log seal trigger.

Revision ID: v6z88_commerce_ledger
Revises: v6z87_platform_ctx_policies
Create Date: 2026-10-20

``commerce_arbiter_decisions`` keeps one row per commerce arbiter decision
(core/commerce/arbiter.py), tenant-scoped under forced row-level security. It
is append-only: its trigger refuses every UPDATE and DELETE except two one-time
transitions, an ``allow`` row being consumed (``consumed_at`` filled, nothing
else changed) and the Grantex countersignature reference being attached
(``grantex_grant_jti`` and ``grantex_recorded_at`` filled, nothing else changed).

``oacp_operator_decision_records``, ``oacp_audit_review_manifest_records`` and
``oacp_retention_disposition_decision_records`` become append-only: their
trigger refuses every UPDATE and DELETE (no transition is defined; their
writes are mirrored into the chained audit_log by core/commerce/ledger.py).

The audit_log append-only trigger is (re)installed with the sealing transition
(the same text as core.governance.audit_chain.SEAL_TRIGGER_SQL): a database
bootstrapped from an empty schema is stamped past the baseline that created the
trigger, so until now only core.database.init_db installed it there.
"""

from alembic import op

revision = "v6z88_commerce_ledger"
down_revision = "v6z87_platform_ctx_policies"
branch_labels = None
depends_on = None

# The same text as core.governance.audit_chain.SEAL_TRIGGER_SQL.
_SEAL_TRIGGER_SQL = """CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.chain_seq IS NULL AND OLD.chain_prev IS NULL
       AND OLD.chain_hash IS NULL AND OLD.sealed_at IS NULL
       AND NEW.chain_seq IS NOT NULL AND NEW.chain_prev IS NOT NULL
       AND NEW.chain_hash IS NOT NULL AND NEW.sealed_at IS NOT NULL
       AND (to_jsonb(NEW) - ARRAY['chain_seq', 'chain_prev', 'chain_hash', 'sealed_at'])
         = (to_jsonb(OLD) - ARRAY['chain_seq', 'chain_prev', 'chain_hash', 'sealed_at'])
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
      'audit_log is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

_DECISION_GUARD_SQL = """CREATE OR REPLACE FUNCTION commerce_arbiter_decisions_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.outcome = 'allow' AND OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL
       AND (to_jsonb(NEW) - 'consumed_at') = (to_jsonb(OLD) - 'consumed_at')
    THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND OLD.grantex_grant_jti IS NULL AND OLD.grantex_recorded_at IS NULL
       AND NEW.grantex_grant_jti IS NOT NULL AND NEW.grantex_recorded_at IS NOT NULL
       AND (to_jsonb(NEW) - ARRAY['grantex_grant_jti', 'grantex_recorded_at'])
         = (to_jsonb(OLD) - ARRAY['grantex_grant_jti', 'grantex_recorded_at'])
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
      'commerce_arbiter_decisions is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

_OACP_RECORD_GUARD_SQL = """CREATE OR REPLACE FUNCTION oacp_record_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      '% is append-only — UPDATE/DELETE rejected', TG_TABLE_NAME
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

OACP_APPEND_ONLY_TABLES: tuple[str, ...] = (
    "oacp_operator_decision_records",
    "oacp_audit_review_manifest_records",
    "oacp_retention_disposition_decision_records",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_arbiter_decisions (
            decision_id VARCHAR(80) PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            buyer_agent_id VARCHAR(160) NULL,
            action VARCHAR(40) NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            outcome VARCHAR(8) NOT NULL,
            rules_evaluated JSONB NOT NULL,
            failing_rule VARCHAR(64) NULL,
            rule_results JSONB NOT NULL,
            evidence JSONB NOT NULL,
            evidence_hashes JSONB NOT NULL,
            policy_version VARCHAR(40) NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL,
            grantex_grant_jti VARCHAR(255) NULL,
            grantex_recorded_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_commerce_arbiter_action CHECK
                (action IN ('purchase_handoff', 'mandate_request', 'pos_handoff', 'session_complete')),
            CONSTRAINT ck_commerce_arbiter_outcome CHECK (outcome IN ('allow', 'refuse')),
            CONSTRAINT ck_commerce_arbiter_failing_rule CHECK
                ((outcome = 'allow' AND failing_rule IS NULL) OR (outcome = 'refuse' AND failing_rule IS NOT NULL)),
            CONSTRAINT ck_commerce_arbiter_consumed_allow CHECK (consumed_at IS NULL OR outcome = 'allow'),
            CONSTRAINT ck_commerce_arbiter_payload_hash CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_commerce_arbiter_expiry CHECK (expires_at > decided_at)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_arbiter_decisions_tenant_decided "
        "ON commerce_arbiter_decisions(tenant_id, decided_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_arbiter_decisions_scope "
        "ON commerce_arbiter_decisions(tenant_id, merchant_id, seller_agent_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_arbiter_decisions_payload "
        "ON commerce_arbiter_decisions(tenant_id, payload_hash);"
    )
    op.execute("ALTER TABLE commerce_arbiter_decisions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE commerce_arbiter_decisions FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS commerce_arbiter_decisions_tenant_isolation ON commerce_arbiter_decisions;")
    op.execute("""
        CREATE POLICY commerce_arbiter_decisions_tenant_isolation
        ON commerce_arbiter_decisions
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)
    op.execute(_DECISION_GUARD_SQL)
    op.execute("DROP TRIGGER IF EXISTS commerce_arbiter_decisions_append_only ON commerce_arbiter_decisions;")
    op.execute("""
        CREATE TRIGGER commerce_arbiter_decisions_append_only
        BEFORE UPDATE OR DELETE ON commerce_arbiter_decisions
        FOR EACH ROW EXECUTE FUNCTION commerce_arbiter_decisions_guard();
    """)

    op.execute(_OACP_RECORD_GUARD_SQL)
    for table in OACP_APPEND_ONLY_TABLES:
        op.execute(f"""
            DO $$ BEGIN
                IF to_regclass('public.{table}') IS NOT NULL THEN
                    DROP TRIGGER IF EXISTS {table}_append_only ON {table};
                    CREATE TRIGGER {table}_append_only
                    BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION oacp_record_reject_mutation();
                END IF;
            END $$;
        """)

    op.execute(_SEAL_TRIGGER_SQL)
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('public.audit_log') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;
                CREATE TRIGGER audit_log_immutable
                BEFORE UPDATE OR DELETE ON audit_log
                FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # The audit_log trigger is left installed: removing a tamper control is not a rollback step.
    for table in OACP_APPEND_ONLY_TABLES:
        op.execute(f"""
            DO $$ BEGIN
                IF to_regclass('public.{table}') IS NOT NULL THEN
                    DROP TRIGGER IF EXISTS {table}_append_only ON {table};
                END IF;
            END $$;
        """)
    op.execute("DROP FUNCTION IF EXISTS oacp_record_reject_mutation();")
    op.execute("DROP TABLE IF EXISTS commerce_arbiter_decisions;")
    op.execute("DROP FUNCTION IF EXISTS commerce_arbiter_decisions_guard();")
