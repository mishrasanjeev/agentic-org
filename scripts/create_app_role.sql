-- Create the NOBYPASSRLS application role for the AgenticOrg API / workers,
-- with least-privilege grants.
--
-- Row-level security (v6z16 onward; platform-context reads v6z87) is only a control when
-- the connected role is neither a superuser nor BYPASSRLS. Migrations keep
-- running as the schema owner (the role that ran `scripts/alembic_migrate.py`,
-- e.g. `agenticorg`); the application connects as `agenticorg_app`.
--
-- Usage (as the schema owner, i.e. the role that runs the migrations):
--
--   psql "$OWNER_DB_URL" -v app_password="'<strong-password>'" -f scripts/create_app_role.sql
--
-- Then point the API and workers at the new role:
--
--   AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg_app:<password>@host:5432/agenticorg
--
-- See docs/RLS_APP_ROLE.md for the rollout checklist and the privilege classes.
-- Idempotent: safe to re-run. The role is created only if missing; every
-- grant is first revoked and then re-applied inside one transaction, so a
-- re-run converges on exactly the grants below (and removes anything an
-- older version of this script granted) without a window where the running
-- application has no privileges. The checks at the end raise, and roll the
-- whole transaction back, if the result is broader than intended.
--
-- Privilege classes (tests/regression/test_app_role_grants.py keeps them in
-- step with the migrations):
--
--   ordinary tables      SELECT, INSERT, UPDATE, DELETE (never TRUNCATE,
--                        REFERENCES or TRIGGER)
--   append-only tables   SELECT, INSERT (their triggers refuse UPDATE and
--                        DELETE; the grants refuse them too)
--   audit_log            SELECT, INSERT, and UPDATE of the four hash-chain
--                        columns only, for the sealing task
--   no-delete tables     SELECT, INSERT, UPDATE
--   migration-owned      SELECT only, or nothing
--   functions            EXECUTE only on the restricted functions in section 7;
--                        trigger functions need no EXECUTE to fire

\set ON_ERROR_STOP on

BEGIN;

-- psql does not substitute :variables inside dollar-quoted DO blocks, so the
-- CREATE ROLE statement is built with format() and executed via \gexec.
SELECT format(
    'CREATE ROLE agenticorg_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
    :app_password
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticorg_app') \gexec

-- Existing role: enforce the RLS-relevant attributes (password unchanged).
ALTER ROLE agenticorg_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- Connection + schema access. No CREATE on the schema: DDL stays with the
-- migration role.
SELECT format('GRANT CONNECT ON DATABASE %I TO agenticorg_app', current_database()) \gexec
GRANT USAGE ON SCHEMA public TO agenticorg_app;
REVOKE CREATE ON SCHEMA public FROM agenticorg_app;

-- 1. Start from nothing (a REVOKE on a table also revokes its column grants).
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agenticorg_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM agenticorg_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM agenticorg_app;

-- 2. Ordinary tables: row DML only. TRUNCATE skips row-level security and
-- row triggers, so it is never granted.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agenticorg_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agenticorg_app;

-- 3. Append-only tables: written once, never changed or removed. The
-- triggers prevent_capability_ledger_mutation (v6z5) and
-- eval_dataset_versions_immutable (v6z44) refuse UPDATE/DELETE; withholding
-- the privileges keeps the rule even where a trigger is missing.
REVOKE UPDATE, DELETE ON capability_evidence_records FROM agenticorg_app;
REVOKE UPDATE, DELETE ON capability_promotion_events FROM agenticorg_app;
REVOKE UPDATE, DELETE ON eval_dataset_versions FROM agenticorg_app;

-- 3b. The same rule for every table, named or not: a table guarded by one of
-- these append-only trigger functions loses each operation its trigger
-- covers (UPDATE: tgtype bit 16, DELETE: bit 8). Functions that do not exist
-- yet simply match nothing, so the script runs on any head; tables a later
-- migration guards with a listed function are classified automatically.
-- Runs before the column grants below: a table-level REVOKE also revokes
-- column privileges.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT tg.tgrelid::regclass AS rel,
               bool_or(tg.tgtype & 16 <> 0) AS refuses_update,
               bool_or(tg.tgtype & 8 <> 0) AS refuses_delete
        FROM pg_trigger tg
        JOIN pg_proc p ON p.oid = tg.tgfoid
        JOIN pg_class c ON c.oid = tg.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE NOT tg.tgisinternal AND n.nspname = 'public'
          AND p.proname IN ('audit_log_reject_mutation', 'prevent_capability_ledger_mutation',
                            'eval_dataset_versions_immutable', 'commerce_arbiter_decisions_guard',
                            'oacp_record_reject_mutation', 'oacp_manifest_retention_guard',
                            'audit_chain_witness_reject_mutation', 'commerce_payment_record_reject_mutation')
        GROUP BY tg.tgrelid
    LOOP
        IF r.refuses_update THEN
            EXECUTE format('REVOKE UPDATE ON %s FROM agenticorg_app', r.rel);
        END IF;
        IF r.refuses_delete THEN
            EXECUTE format('REVOKE DELETE ON %s FROM agenticorg_app', r.rel);
        END IF;
    END LOOP;
END $$;

-- 4. audit_log: append-only, except the one transition the sealing task
-- (core.tasks.audit_chain_tasks, a Celery worker on this role) makes: it
-- fills the four chain columns of an unsealed row
-- (core.governance.audit_chain.SEAL_TRIGGER_SQL). Column-level UPDATE also
-- covers the sealer's SELECT ... FOR UPDATE.
REVOKE UPDATE, DELETE ON audit_log FROM agenticorg_app;
GRANT UPDATE (chain_seq, chain_prev, chain_hash, sealed_at) ON audit_log TO agenticorg_app;

-- 5. No-delete tables: updated in place, never removed. Readiness records
-- refuse DELETE by trigger (v6z5); a removed chain anchor would hide a
-- truncated audit chain.
REVOKE DELETE ON capability_readiness_records FROM agenticorg_app;
REVOKE DELETE ON audit_chain_anchors FROM agenticorg_app;

-- 5b. Append-only tables from v6z88_commerce_ledger, classified only once
-- that migration has run (on an older head the tables are absent, or the
-- OACP record tables are still ordinary and have no append-only trigger).
-- commerce_arbiter_decisions allows two one-time transitions in its trigger
-- (commerce_arbiter_decisions_guard): consumed_at filled on an allow row, and
-- grantex_grant_jti + grantex_recorded_at filled together; the column grant
-- is exactly those three columns. The OACP decision records define no
-- transition (oacp_record_reject_mutation).
DO $$
BEGIN
    IF to_regclass('public.commerce_arbiter_decisions') IS NOT NULL THEN
        REVOKE UPDATE, DELETE ON commerce_arbiter_decisions FROM agenticorg_app;
        GRANT UPDATE (consumed_at, grantex_grant_jti, grantex_recorded_at)
            ON commerce_arbiter_decisions TO agenticorg_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE NOT tgisinternal
               AND tgname = 'oacp_operator_decision_records_append_only') THEN
        REVOKE UPDATE, DELETE ON oacp_operator_decision_records FROM agenticorg_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE NOT tgisinternal
               AND tgname = 'oacp_audit_review_manifest_records_append_only') THEN
        REVOKE UPDATE, DELETE ON oacp_audit_review_manifest_records FROM agenticorg_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE NOT tgisinternal
               AND tgname = 'oacp_retention_disposition_decision_records_append_only') THEN
        REVOKE UPDATE, DELETE ON oacp_retention_disposition_decision_records FROM agenticorg_app;
    END IF;
    -- Grantex witness checkpoints of the audit chain head: SELECT, INSERT.
    IF to_regclass('public.audit_chain_witness_checkpoints') IS NOT NULL THEN
        REVOKE UPDATE, DELETE ON audit_chain_witness_checkpoints FROM agenticorg_app;
    END IF;
    -- v6z90_mandates_rails: Plural payment operation records and RBI AFA evidence
    -- (commerce_payment_record_reject_mutation): SELECT, INSERT.
    IF to_regclass('public.commerce_payment_operations') IS NOT NULL THEN
        REVOKE UPDATE, DELETE ON commerce_payment_operations FROM agenticorg_app;
    END IF;
    IF to_regclass('public.commerce_afa_evidence') IS NOT NULL THEN
        REVOKE UPDATE, DELETE ON commerce_afa_evidence FROM agenticorg_app;
    END IF;
END $$;

-- 6. Migration-owned tables. init_db() and the checkpoint store only read
-- the schema versions; the encrypted-migration progress table is used by
-- migrations alone.
REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM agenticorg_app;
REVOKE INSERT, UPDATE, DELETE ON checkpoint_migrations FROM agenticorg_app;
REVOKE ALL ON alembic_migration_progress FROM agenticorg_app;

-- 7. Functions. Everything in the schema was revoked above; ordinary
-- functions (extensions, trigger functions) keep their PUBLIC EXECUTE, and
-- the restricted ones (REVOKE ... FROM PUBLIC in their migration) stay
-- closed. A trigger fires without EXECUTE on its function. The application
-- calls the SECURITY DEFINER functions in this allow-list, and no others:
--
--   public.gemini_daily_platform_spend()             v6z85, platform Gemini cap
--                                                    (core/llm/router.py); required
--   public.oacp_delete_due_manifest(text, text)      v6z88, tenant-bound manifest
--                                                    retention; granted once it exists
--   public.gemini_daily_ledger_spend()               v6z105, platform Gemini cap on
--                                                    recorded spend (core/llm/router.py);
--                                                    granted once it exists
--
-- A function owned by the installer is granted directly. One owned by a
-- NOLOGIN role (the v6z85 aggregation role) is granted through a temporary
-- membership, the way v6z85 does; the installer must not keep it.
DO $$
DECLARE
    fn text;
    owner_role name;
BEGIN
    IF to_regprocedure('public.gemini_daily_platform_spend()') IS NULL THEN
        RAISE EXCEPTION 'public.gemini_daily_platform_spend() is missing: migrate to head first';
    END IF;
    FOREACH fn IN ARRAY ARRAY['public.gemini_daily_platform_spend()',
                              'public.oacp_delete_due_manifest(text, text)',
                              'public.gemini_daily_ledger_spend()'] LOOP
        CONTINUE WHEN to_regprocedure(fn) IS NULL;
        SELECT r.rolname INTO owner_role
        FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
        WHERE p.oid = to_regprocedure(fn);
        -- Closed to PUBLIC here too, so a function created before this role
        -- existed is not left callable by every role.
        IF owner_role = current_user OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fn);
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO agenticorg_app', fn);
        ELSE
            EXECUTE format('GRANT %I TO %I', owner_role, current_user);
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fn);
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO agenticorg_app', fn);
            EXECUTE format('REVOKE %I FROM %I', owner_role, current_user);
            IF pg_has_role(current_user, owner_role, 'USAGE') OR pg_has_role(current_user, owner_role, 'SET') THEN
                RAISE EXCEPTION 'Installer retained membership of %', owner_role;
            END IF;
        END IF;
    END LOOP;
END $$;

-- 8. Objects created later by the role running this script (the schema
-- owner, i.e. future migrations) get the ordinary-table grants; functions get
-- nothing beyond PUBLIC. A migration that adds an append-only table, or a
-- function the application must call, narrows or extends the grant itself
-- (guarded by to_regrole('agenticorg_app')) and adds it to this script.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM agenticorg_app;

-- 9. Fail closed: refuse (and roll back) any result broader than the above.
DO $$
DECLARE
    bad text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticorg_app'
               AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
        RAISE EXCEPTION 'agenticorg_app must be NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB';
    END IF;
    IF has_schema_privilege('agenticorg_app', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'agenticorg_app can CREATE in schema public (revoke CREATE from PUBLIC)';
    END IF;
    SELECT string_agg(c.relname, ', ') INTO bad
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND (has_table_privilege('agenticorg_app', c.oid, 'TRUNCATE')
           OR has_table_privilege('agenticorg_app', c.oid, 'REFERENCES')
           OR has_table_privilege('agenticorg_app', c.oid, 'TRIGGER'));
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'agenticorg_app has TRUNCATE/REFERENCES/TRIGGER on: %', bad;
    END IF;
    SELECT string_agg(t, ', ') INTO bad
    FROM unnest(ARRAY['audit_log', 'capability_evidence_records', 'capability_promotion_events',
                      'eval_dataset_versions']) AS t
    WHERE has_table_privilege('agenticorg_app', t, 'UPDATE')
       OR has_table_privilege('agenticorg_app', t, 'DELETE');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'agenticorg_app can UPDATE or DELETE append-only tables: %', bad;
    END IF;
    -- Any table whose append-only trigger refuses UPDATE (tgtype bit 16) or
    -- DELETE (bit 8) must not grant that operation at table level, including
    -- tables a later migration adds before this script learns their names.
    SELECT string_agg(DISTINCT tg.tgrelid::regclass::text, ', ') INTO bad
    FROM pg_trigger tg JOIN pg_proc p ON p.oid = tg.tgfoid
    WHERE NOT tg.tgisinternal
      AND p.proname IN ('audit_log_reject_mutation', 'prevent_capability_ledger_mutation',
                        'eval_dataset_versions_immutable', 'commerce_arbiter_decisions_guard',
                        'oacp_record_reject_mutation', 'oacp_manifest_retention_guard',
                        'audit_chain_witness_reject_mutation', 'commerce_payment_record_reject_mutation')
      AND ((tg.tgtype & 16 <> 0 AND has_table_privilege('agenticorg_app', tg.tgrelid, 'UPDATE'))
           OR (tg.tgtype & 8 <> 0 AND has_table_privilege('agenticorg_app', tg.tgrelid, 'DELETE')));
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'agenticorg_app holds an operation an append-only trigger refuses on: %', bad;
    END IF;
    SELECT string_agg(p.oid::regprocedure::text, ', ') INTO bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prosecdef
      AND p.oid NOT IN (SELECT to_regprocedure(f)::oid FROM unnest(ARRAY['public.gemini_daily_platform_spend()',
                            'public.oacp_delete_due_manifest(text, text)',
                            'public.gemini_daily_ledger_spend()']) AS f WHERE to_regprocedure(f) IS NOT NULL)
      AND has_function_privilege('agenticorg_app', p.oid, 'EXECUTE');
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'agenticorg_app can EXECUTE restricted functions: %', bad;
    END IF;
    IF NOT has_function_privilege('agenticorg_app', 'public.gemini_daily_platform_spend()', 'EXECUTE') THEN
        RAISE EXCEPTION 'agenticorg_app cannot EXECUTE public.gemini_daily_platform_spend()';
    END IF;
END $$;

COMMIT;

-- Verification: both columns must be false for RLS to be enforced.
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'agenticorg_app';
