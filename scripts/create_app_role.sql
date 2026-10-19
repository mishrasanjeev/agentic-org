-- Create the NOBYPASSRLS application role for the AgenticOrg API / workers.
--
-- Row-level security (v6z16 onward; platform-context reads v6z87) is only a control when
-- the connected role is neither a superuser nor BYPASSRLS. Migrations keep
-- running as the schema owner (the role that ran `scripts/alembic_migrate.py`,
-- e.g. `agenticorg`); the application connects as `agenticorg_app`.
--
-- Usage (as a superuser or the database owner):
--
--   psql "$ADMIN_DB_URL" -v app_password="'<strong-password>'" -f scripts/create_app_role.sql
--
-- Then point the API and workers at the new role:
--
--   AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg_app:<password>@host:5432/agenticorg
--
-- See docs/RLS_APP_ROLE.md for the rollout checklist. Idempotent: safe to
-- re-run (the role is created only if missing; grants are re-applied).

\set ON_ERROR_STOP on

-- psql does not substitute :variables inside dollar-quoted DO blocks, so the
-- CREATE ROLE statement is built with format() and executed via \gexec.
SELECT format(
    'CREATE ROLE agenticorg_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
    :app_password
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticorg_app') \gexec

-- Existing role: enforce the RLS-relevant attributes (password unchanged).
ALTER ROLE agenticorg_app NOSUPERUSER NOBYPASSRLS;

-- Connection + schema access.
GRANT CONNECT ON DATABASE agenticorg TO agenticorg_app;
GRANT USAGE ON SCHEMA public TO agenticorg_app;

-- DML on every application table; DDL stays with the migration role.
-- audit_log is append-only at the trigger level (audit_log_immutable), and
-- UPDATE/DELETE are additionally withheld here as defense in depth.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agenticorg_app;
REVOKE UPDATE, DELETE ON audit_log FROM agenticorg_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agenticorg_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO agenticorg_app;

-- Tables / sequences created by future migrations (run by the current
-- role, i.e. the schema owner) inherit the same grants automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO agenticorg_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO agenticorg_app;

-- Verification: both columns must be false for RLS to be enforced.
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'agenticorg_app';
