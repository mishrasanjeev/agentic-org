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
trigger refuses every UPDATE and DELETE (their writes are mirrored into the
chained audit_log by core/commerce/ledger.py). One transition is defined, on
``oacp_audit_review_manifest_records`` only: the retention task
(core/commerce/oacp_retention.py) may DELETE a manifest whose retention is due
under a named disposition decision (trigger ``oacp_manifest_retention_guard``,
which checks the due conditions itself), through the SECURITY DEFINER function
``oacp_delete_due_manifest`` so the application role needs no DELETE grant
(docs/oacp/arbiter-ledger.md).

``audit_chain_witness_checkpoints`` keeps each chain head Grantex witnessed
(core/governance/audit_witness.py): the countersignature and its witness id,
or the fork evidence of a refused head. Tenant-scoped under forced row-level
security and append-only.

The audit_log append-only trigger is (re)installed with the sealing transition
(the same text as core.governance.audit_chain.SEAL_TRIGGER_SQL): a database
bootstrapped from an empty schema is stamped past the baseline that created the
trigger. v6z87 installs the same trigger; reinstalling it here is idempotent.

Where the NOBYPASSRLS application role ``agenticorg_app`` exists, its grants
follow the same rules as scripts/create_app_role.sql: EXECUTE on
``oacp_delete_due_manifest`` for that role only (closed to PUBLIC), no UPDATE or
DELETE on the append-only tables, and on ``commerce_arbiter_decisions`` only the
column UPDATE its two transitions need.
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

# The one DELETE transition on review manifests: the session names a disposition
# decision (transaction-local GUC agenticorg.oacp_retention_disposition) and the
# trigger itself checks that the decision makes this manifest due. Everything else
# is refused as before. The same text as core.commerce.oacp_retention.MANIFEST_RETENTION_GUARD_SQL.
_MANIFEST_RETENTION_GUARD_SQL = """CREATE OR REPLACE FUNCTION oacp_manifest_retention_guard() RETURNS trigger AS $$
DECLARE
    disposition_id text := current_setting('agenticorg.oacp_retention_disposition', true);
BEGIN
    IF TG_OP = 'DELETE'
       AND disposition_id IS NOT NULL AND disposition_id <> ''
       AND OLD.tenant_id = NULLIF(current_setting('agenticorg.tenant_id', true), '')
       AND OLD.retention_class <> 'legal_hold_candidate'
       AND OLD.retain_until <= now()
       AND EXISTS (
           SELECT 1 FROM oacp_retention_disposition_decision_records d
           WHERE d.disposition_decision_id = disposition_id
             AND d.decision_kind = 'approve_future_retention_review'
             AND d.tenant_id = OLD.tenant_id
             AND d.merchant_id = OLD.merchant_id
             AND (d.seller_agent_id IS NULL OR d.seller_agent_id = OLD.seller_agent_id)
             AND (d.buyer_agent_id IS NULL OR d.buyer_agent_id = OLD.buyer_agent_id)
             AND d.retention_class = OLD.retention_class
             AND d.retain_until <= now()
             AND OLD.retain_until <= d.retain_until
             AND NOT EXISTS (
                 SELECT 1 FROM oacp_retention_disposition_decision_records h
                 WHERE h.tenant_id = d.tenant_id
                   AND h.merchant_id = d.merchant_id
                   AND (
                       (h.decision_kind IN ('approve_future_legal_hold_review', 'block_unsafe_disposition')
                        AND h.retention_class = d.retention_class)
                       OR (h.decision_kind IN ('reject_disposition', 'defer_until_recheck')
                           AND h.source_summary_id = d.source_summary_id)
                   )
             )
       )
    THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
      '% is append-only — UPDATE/DELETE rejected', TG_TABLE_NAME
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

# How the retention task deletes: as the table owner (SECURITY DEFINER), so the
# application role needs no DELETE privilege on the append-only table. Row-level
# security still binds the DELETE to the session's tenant and the trigger above
# still checks that the named disposition makes the row due. The same text as
# core.commerce.oacp_retention.DELETE_DUE_MANIFEST_SQL.
_DELETE_DUE_MANIFEST_SQL = """CREATE OR REPLACE FUNCTION oacp_delete_due_manifest(
    p_disposition_id text, p_manifest_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    deleted integer;
BEGIN
    PERFORM set_config('agenticorg.oacp_retention_disposition', p_disposition_id, true);
    -- The definer may bypass row-level security: bind the tenant explicitly.
    DELETE FROM oacp_audit_review_manifest_records
    WHERE manifest_id = p_manifest_id
      AND tenant_id = NULLIF(current_setting('agenticorg.tenant_id', true), '');
    GET DIAGNOSTICS deleted = ROW_COUNT;
    PERFORM set_config('agenticorg.oacp_retention_disposition', '', true);
    RETURN deleted = 1;
END;
$$;"""

_WITNESS_GUARD_SQL = """CREATE OR REPLACE FUNCTION audit_chain_witness_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      'audit_chain_witness_checkpoints is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

OACP_APPEND_ONLY_TABLES: tuple[str, ...] = (
    "oacp_operator_decision_records",
    "oacp_audit_review_manifest_records",
    "oacp_retention_disposition_decision_records",
)

# Least-privilege grants for the NOBYPASSRLS application role (docs/RLS_APP_ROLE.md).
# The retention function is closed to PUBLIC on every database, whether or not the
# application role exists yet (a role created later gets EXECUTE from
# scripts/create_app_role.sql), and is callable by the application role alone. The
# role-specific grants apply only where that role exists: the append-only tables lose
# UPDATE and DELETE; the arbiter table keeps a column-scoped UPDATE for its two
# one-time transitions. The same rules as scripts/create_app_role.sql, so a migration
# run after the script leaves the same privileges.
_APP_ROLE_GRANTS_SQL = """DO $$ BEGIN
    REVOKE ALL ON FUNCTION oacp_delete_due_manifest(text, text) FROM PUBLIC;
    IF to_regrole('agenticorg_app') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION oacp_delete_due_manifest(text, text) TO agenticorg_app;
        GRANT SELECT, INSERT ON audit_chain_witness_checkpoints TO agenticorg_app;
        REVOKE UPDATE, DELETE ON audit_chain_witness_checkpoints FROM agenticorg_app;
        IF to_regclass('public.oacp_operator_decision_records') IS NOT NULL THEN
            REVOKE UPDATE, DELETE ON oacp_operator_decision_records FROM agenticorg_app;
        END IF;
        IF to_regclass('public.oacp_audit_review_manifest_records') IS NOT NULL THEN
            REVOKE UPDATE, DELETE ON oacp_audit_review_manifest_records FROM agenticorg_app;
        END IF;
        IF to_regclass('public.oacp_retention_disposition_decision_records') IS NOT NULL THEN
            REVOKE UPDATE, DELETE ON oacp_retention_disposition_decision_records FROM agenticorg_app;
        END IF;
        GRANT SELECT, INSERT ON commerce_arbiter_decisions TO agenticorg_app;
        REVOKE UPDATE, DELETE ON commerce_arbiter_decisions FROM agenticorg_app;
        GRANT UPDATE (consumed_at, grantex_grant_jti, grantex_recorded_at)
            ON commerce_arbiter_decisions TO agenticorg_app;
    END IF;
END $$;"""

_APP_ROLE_DOWNGRADE_SQL = """DO $$ BEGIN
    IF to_regrole('agenticorg_app') IS NOT NULL THEN
        IF to_regclass('public.oacp_operator_decision_records') IS NOT NULL THEN
            GRANT UPDATE, DELETE ON oacp_operator_decision_records TO agenticorg_app;
        END IF;
        IF to_regclass('public.oacp_audit_review_manifest_records') IS NOT NULL THEN
            GRANT UPDATE, DELETE ON oacp_audit_review_manifest_records TO agenticorg_app;
        END IF;
        IF to_regclass('public.oacp_retention_disposition_decision_records') IS NOT NULL THEN
            GRANT UPDATE, DELETE ON oacp_retention_disposition_decision_records TO agenticorg_app;
        END IF;
    END IF;
END $$;"""


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

    op.execute(_MANIFEST_RETENTION_GUARD_SQL)
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('public.oacp_audit_review_manifest_records') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS oacp_audit_review_manifest_records_append_only
                    ON oacp_audit_review_manifest_records;
                CREATE TRIGGER oacp_audit_review_manifest_records_append_only
                BEFORE UPDATE OR DELETE ON oacp_audit_review_manifest_records
                FOR EACH ROW EXECUTE FUNCTION oacp_manifest_retention_guard();
            END IF;
        END $$;
    """)
    op.execute(_DELETE_DUE_MANIFEST_SQL)

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain_witness_checkpoints (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            head_seq BIGINT NOT NULL,
            head_hash VARCHAR(64) NOT NULL,
            anchored_at TIMESTAMPTZ NOT NULL,
            status VARCHAR(16) NOT NULL,
            witness_id VARCHAR(160) NULL,
            countersignature TEXT NULL,
            witnessed_at TIMESTAMPTZ NULL,
            fork_evidence JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_audit_chain_witness_status CHECK (status IN ('witnessed', 'fork')),
            CONSTRAINT ck_audit_chain_witness_hash CHECK (head_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_audit_chain_witness_seq CHECK (head_seq >= 1),
            CONSTRAINT ck_audit_chain_witness_shape CHECK (
                (status = 'witnessed' AND witness_id IS NOT NULL AND countersignature IS NOT NULL
                 AND witnessed_at IS NOT NULL AND fork_evidence IS NULL)
                OR (status = 'fork' AND fork_evidence IS NOT NULL)
            )
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_chain_witness_head "
        "ON audit_chain_witness_checkpoints(tenant_id, head_seq, head_hash, status);"
    )
    op.execute("ALTER TABLE audit_chain_witness_checkpoints ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_chain_witness_checkpoints FORCE ROW LEVEL SECURITY;")
    op.execute(
        "DROP POLICY IF EXISTS audit_chain_witness_checkpoints_tenant_isolation ON audit_chain_witness_checkpoints;"
    )
    op.execute("""
        CREATE POLICY audit_chain_witness_checkpoints_tenant_isolation
        ON audit_chain_witness_checkpoints
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)
    op.execute(_WITNESS_GUARD_SQL)
    op.execute(
        "DROP TRIGGER IF EXISTS audit_chain_witness_checkpoints_append_only ON audit_chain_witness_checkpoints;"
    )
    op.execute("""
        CREATE TRIGGER audit_chain_witness_checkpoints_append_only
        BEFORE UPDATE OR DELETE ON audit_chain_witness_checkpoints
        FOR EACH ROW EXECUTE FUNCTION audit_chain_witness_reject_mutation();
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

    op.execute(_APP_ROLE_GRANTS_SQL)


def downgrade() -> None:
    # The audit_log trigger is left installed: removing a tamper control is not a rollback step.
    # Without their triggers the OACP record tables are ordinary tables again, with row DML
    # for the application role as before (the dropped table and function take their grants along).
    op.execute(_APP_ROLE_DOWNGRADE_SQL)
    for table in OACP_APPEND_ONLY_TABLES:
        op.execute(f"""
            DO $$ BEGIN
                IF to_regclass('public.{table}') IS NOT NULL THEN
                    DROP TRIGGER IF EXISTS {table}_append_only ON {table};
                END IF;
            END $$;
        """)
    op.execute("DROP FUNCTION IF EXISTS oacp_record_reject_mutation();")
    op.execute("DROP FUNCTION IF EXISTS oacp_delete_due_manifest(text, text);")
    op.execute("DROP FUNCTION IF EXISTS oacp_manifest_retention_guard();")
    op.execute("DROP TABLE IF EXISTS audit_chain_witness_checkpoints;")
    op.execute("DROP FUNCTION IF EXISTS audit_chain_witness_reject_mutation();")
    op.execute("DROP TABLE IF EXISTS commerce_arbiter_decisions;")
    op.execute("DROP FUNCTION IF EXISTS commerce_arbiter_decisions_guard();")
