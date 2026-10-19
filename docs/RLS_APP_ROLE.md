# Running the API under a NOBYPASSRLS role

Row-level security (RLS) is enabled and forced on every tenant-scoped table
(`v6z16_rls_coverage`, `v6z68_force_rls_programme_tables` and the
per-feature revisions since), but PostgreSQL only *applies* the policies to
roles that are neither `SUPERUSER` nor `BYPASSRLS`. Until the application
connects as such a role, RLS is documentation, not a control (FINDINGS
A-105).

This page is the rollout checklist. The companion SQL is
`scripts/create_app_role.sql`; the runtime contract lives in
`core/database.py` (`get_tenant_session`, `bind_tenant_context`,
`get_platform_session`, `verify_db_role_rls_posture`) and is exercised
end-to-end by `tests/integration/test_rls_nobypass_role.py`.

## Two roles, two jobs

| Role             | Attributes                          | Used by                                            |
|------------------|-------------------------------------|----------------------------------------------------|
| `agenticorg`     | schema owner (may be superuser)     | the migrate job (`scripts/alembic_migrate.py`), break-glass, operator CLIs |
| `agenticorg_app` | `LOGIN NOSUPERUSER NOBYPASSRLS`     | API services, Celery workers and beat              |

Migrations keep running as the owner: the migrate job must keep its own
owner-role `AGENTICORG_DB_URL`. The application never needs DDL at runtime in
strict environments (`init_db()` only verifies the Alembic head).

## Rollout (operator step)

Switching production `AGENTICORG_DB_URL` to the application role is an
operator step. Nothing in this repository changes it, and the deploy script
does not create or select database roles.

1. Apply migrations as the owner. The database must be at
   `v6z87_platform_ctx_policies` or later so the platform-context policies
   exist:

       python scripts/alembic_migrate.py

2. Create the application role (idempotent; re-running re-applies grants).
   Run it as the owner role so the default privileges cover tables later
   migrations create:

       psql "$OWNER_DB_URL" -v app_password="'<strong-password>'" -f scripts/create_app_role.sql

   The last statement prints `rolsuper = f`, `rolbypassrls = f`; both must
   be `f`. Store the password in the secret manager, never in the repository.

3. Point the runtime services, and only them, at the new role.
   `core/config.py` uses `env_prefix = "AGENTICORG_"`, so the variable is
   `AGENTICORG_DB_URL`:

       AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg_app:<password>@<host>:5432/agenticorg

   Set it on the API services, the Celery worker and beat. Leave the migrate
   job on the owner URL. On Kubernetes (`infra/gcp-setup-lean.sh`) the one
   `agenticorg-secrets` secret currently feeds both, so split the migrate
   job's URL into its own secret first.

4. Restart and check the startup log. `init_db()` runs
   `verify_db_role_rls_posture()`:

   * `db_role_rls_enforced role=agenticorg_app`: good.
   * `DB_ROLE_BYPASSES_RLS ...` (warning): the URL still points at a
     superuser or `BYPASSRLS` role.
   * Set `AGENTICORG_REQUIRE_NOBYPASSRLS_ROLE=1` (default off) once the
     cut-over is done, so a regression (someone pasting the owner URL back)
     fails startup instead of silently disabling isolation.
   * `AGENTICORG_SKIP_DB_ROLE_RLS_CHECK=1` skips the probe for throwaway CI
     databases that only have a superuser.

5. Prove it against the environment:

       AGENTICORG_RLS_TEST_DB_URL=postgresql+asyncpg://agenticorg_app:<password>@<host>:5432/agenticorg \
           python -m pytest -q --no-cov tests/integration/test_rls_nobypass_role.py

   The module skips unless that variable is set and refuses to run if the
   role can bypass RLS, so a green run means the flows it covers (signup,
   login, agent list and run, approvals, billing subscription and expiry
   sweep, DSAR, report and RPA schedule claims, compliance cron, API-key
   lookup, SSO and branding discovery, workflow state, CDC events) work with
   RLS actually enforced. It writes synthetic tenants; run it against a
   staging copy, not production data.

Rollback: point the runtime `AGENTICORG_DB_URL` back at the previous role and
unset `AGENTICORG_REQUIRE_NOBYPASSRLS_ROLE`. The policies and grants are
additive and need no rollback.

## Application contract

* **Tenant-scoped work** (every request handler, every per-tenant beat
  step) opens `get_tenant_session(tenant_id[, company_id])` or binds an
  existing session with `bind_tenant_context(session, tenant_id)`. The
  context is transaction-local and re-bound after each `commit()`.
* **Pre-auth cross-tenant lookups** go through `get_platform_session()`,
  which sets `agenticorg.platform_ctx = '1'` for the transaction: signup and
  Google sign-up (then `bind_tenant_context` for the new tenant), login and
  password reset by e-mail, API-key prefix lookup (HTTP and WebSocket),
  Grantex agent-DID resolution, public SSO provider and branding discovery,
  health probes and the health snapshot.
* **The marker is read-only and narrow.** Only the tables in
  `migrations/versions/v6_z87_platform_ctx_policies.py` honour it, for
  `SELECT` only: `sso_configs`, `tenant_branding`, `agents`. Tables without
  `tenant_id` (`tenants`, `health_check_history`, `demo_requests`) have no
  RLS, and the pre-auth `users` / `api_keys` policies already allow the
  no-tenant lookup.
* **Cross-tenant beat jobs** enumerate the `tenants` catalogue in a plain
  session behind `SET LOCAL row_security = off` and then do every read and
  write in that tenant's own session (RPA and report schedule claims,
  invoices, budgets, compliance alerts, token refresh, synthetic checks,
  retention prunes). `row_security = off` grants nothing: it makes
  PostgreSQL raise instead of silently filtering if a query would hit a
  policy, which is why it is used only on the `tenants` catalogue.
* **Operator CLIs** that aggregate across tenants (`python -m
  core.billing.spend`) run as the owner or a `BYPASSRLS` operator role; under
  the application role they raise instead of reporting zero.
* `tests/regression/test_raw_session_inventory.py` fails CI when a new raw
  `async_session_factory()` use appears outside the justified list, and
  `tests/regression/test_egress_async_callers.py` fails when async code calls
  the blocking `validate_public_url` instead of `validate_public_url_async`.
  Widen the platform table list only in a **new** migration whose docstring
  names the caller, never by reaching for `BYPASSRLS`.

## Break-glass

Operators who need cross-tenant SQL use the owner role (or a dedicated
`BYPASSRLS` SRE role, see ADR 0002). That access is audited and never
handed to the application.
