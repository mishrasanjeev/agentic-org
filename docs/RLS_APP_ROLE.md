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

2. Create the application role. Run it as the owner role (the migration
   role), so the default privileges cover tables later migrations create and
   the platform spend function can be granted (see "Grants" below):

       psql "$OWNER_DB_URL" -v app_password="'<strong-password>'" -f scripts/create_app_role.sql

   The script runs in one transaction. It is idempotent: re-running it
   revokes and re-applies every grant, so the role converges on exactly the
   grants below. Its last check raises, and rolls everything back, if the
   result is broader (a superuser or `BYPASSRLS` role, `CREATE` on the
   schema, `TRUNCATE` on any table, `UPDATE`/`DELETE` on an append-only
   table, `EXECUTE` on a restricted function other than the spend total).
   Re-run it after every migration that adds an append-only table or a
   function the application calls. The last statement prints
   `rolsuper = f`, `rolbypassrls = f`; both must be `f`. Store the password
   in the secret manager, never in the repository.

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
   lookup, SSO and branding discovery, workflow state, CDC events, audit
   chain sealing) work with RLS actually enforced, and that the role is
   refused `DELETE` on `audit_log`, `TRUNCATE`, writes to append-only and
   migration-owned tables, DDL, and the restricted guard functions. It writes synthetic tenants; run it against a
   staging copy, not production data.

Rollback: point the runtime `AGENTICORG_DB_URL` back at the previous role and
unset `AGENTICORG_REQUIRE_NOBYPASSRLS_ROLE`. The policies are additive and the
grants apply only to `agenticorg_app`, so neither needs a rollback. To retire
the role: `REASSIGN OWNED BY agenticorg_app TO <owner>; DROP OWNED BY
agenticorg_app; DROP ROLE agenticorg_app;` as the owner, once nothing
connects as it.

## Grants

`scripts/create_app_role.sql` gives `agenticorg_app` least privilege per
table class. It revokes everything first, so a re-run removes what an older
version granted (it used to grant `EXECUTE` on all functions).

| Class | Tables | `agenticorg_app` gets |
|-------|--------|------------------------|
| Ordinary | every other table in `public` | `SELECT, INSERT, UPDATE, DELETE` |
| Append-only | `capability_evidence_records`, `capability_promotion_events` (trigger `prevent_capability_ledger_mutation`), `eval_dataset_versions` (trigger `eval_dataset_versions_immutable`) | `SELECT, INSERT` |
| Audit log | `audit_log` | `SELECT, INSERT`, `UPDATE (chain_seq, chain_prev, chain_hash, sealed_at)` |
| Append-only by rule (any table) | every table whose trigger uses `audit_log_reject_mutation`, `prevent_capability_ledger_mutation`, `eval_dataset_versions_immutable`, `commerce_arbiter_decisions_guard`, `oacp_record_reject_mutation`, `oacp_manifest_retention_guard`, `audit_chain_witness_reject_mutation` or `commerce_payment_record_reject_mutation` | loses each operation its trigger covers |
| Append-only, v6z88 | `oacp_operator_decision_records`, `oacp_audit_review_manifest_records`, `oacp_retention_disposition_decision_records`, `audit_chain_witness_checkpoints` | `SELECT, INSERT` |
| Append-only, v6z90 | `commerce_payment_operations`, `commerce_afa_evidence` (trigger `commerce_payment_record_reject_mutation`) | `SELECT, INSERT` |
| Append-only with a transition, v6z88 | `commerce_arbiter_decisions` | `SELECT, INSERT`, `UPDATE (consumed_at, grantex_grant_jti, grantex_recorded_at)` |
| No delete | `capability_readiness_records` (trigger refuses `DELETE`), `audit_chain_anchors` | `SELECT, INSERT, UPDATE` |
| Migration-owned | `alembic_version`, `checkpoint_migrations` | `SELECT` |
| Migration-only | `alembic_migration_progress` | nothing |
| Sequences | all | `USAGE, SELECT` |
| Schema | `public` | `USAGE` (no `CREATE`) |

The grants are rule-based and tolerate objects that do not exist yet, so the
same script runs on a database at `v6z87_platform_ctx_policies` or at any
later head:

* **Append-only rule.** Every table guarded by one of the trigger functions
  above loses `UPDATE` when its trigger fires on `UPDATE` and `DELETE` when
  it fires on `DELETE`, whether or not the script names the table. Column
  grants for allowed transitions are applied after the rule (a table-level
  `REVOKE` also revokes column privileges), and only where the table exists.
* **Function allow-list.** `EXECUTE` is granted on the `SECURITY DEFINER`
  functions in the allow-list that exist, and on no other.
* **Fail-closed check.** The final check uses the same lists. It refuses the
  run, and rolls it back, on any table-level privilege a guarding trigger
  refuses or any `EXECUTE` on a restricted function outside the allow-list.

* **`TRUNCATE`, `REFERENCES` and `TRIGGER` are never granted.** `TRUNCATE`
  skips row-level security and row triggers.
* **`audit_log`.** The sealing task (`core.tasks.audit_chain_tasks`) runs on
  a Celery worker, so after the cut-over it runs as this role. It fills the
  four chain columns of unsealed rows and upserts `audit_chain_anchors`.
  The column-level `UPDATE` covers that and its `SELECT ... FOR UPDATE`.
  The `audit_log_immutable` trigger is what stops a sealed row from being
  rewritten through those columns. `v6z87_platform_ctx_policies` (re)installs
  it with exactly `core.governance.audit_chain.SEAL_TRIGGER_SQL`, so a
  database bootstrapped from an empty schema has it too. Before v6z87, only
  the local-only legacy startup DDL installed it there. `UPDATE` of any other
  column and every `DELETE` are refused by privilege as well.
* **Commerce ledger (v6z88).** The arbiter decision guard allows two
  one-time transitions: an `allow` row being consumed, and the Grantex
  countersignature reference being attached. The column grant is exactly
  those three columns, and the trigger refuses any other change through
  them. Manifest retention deletes through
  `oacp_delete_due_manifest(text, text)`, a tenant-bound `SECURITY DEFINER`
  function. The application role holds `EXECUTE` on it and no `DELETE` on
  `oacp_audit_review_manifest_records`.
* **Functions.** `EXECUTE` is revoked on everything in `public`. Ordinary
  functions (extension functions, plain trigger functions) keep their
  default `PUBLIC` `EXECUTE`. Restricted functions stay closed when their
  migration closes them with `REVOKE ... FROM PUBLIC`. Examples are
  `guard_capability_ledger_scope()` and `connector_config_company_scope_guard()`,
  which are `SECURITY DEFINER` trigger functions; a trigger fires without the
  caller holding `EXECUTE`. The allow-list:
  * `public.gemini_daily_platform_spend()`: v6z85, the platform Gemini cap
    in `core/llm/router.py`. It is required, and its owner is the `NOLOGIN`
    role `agenticorg_spend_aggregate`. A non-superuser installer grants it
    through a temporary membership, as v6z85 does, and the script checks
    that the installer did not keep the membership. Without this grant the
    platform cap fails closed (`DailyBudgetExceeded`), so run the script
    before the cut-over.
  * `public.oacp_delete_due_manifest(text, text)`: v6z88. It is granted once
    it exists.
* **Later migrations add their own grants in the migration.**
  `ALTER DEFAULT PRIVILEGES` gives tables that later migrations create (as
  the role that ran the script) the ordinary row DML, and sequences
  `USAGE, SELECT`. Functions get no default grant beyond `PUBLIC`. So a
  migration that adds an append-only table or a function the application
  calls must set the grant itself, guarded so it is a no-op where the role
  does not exist:
  `DO $$ BEGIN IF to_regrole('agenticorg_app') IS NOT NULL THEN ... END IF; END $$`.
  For an append-only table, that means `REVOKE UPDATE, DELETE` plus any
  transition column grant. For a function, it means `REVOKE ALL ... FROM
  PUBLIC` and `GRANT EXECUTE ... TO agenticorg_app`. The same object also
  goes into the script's rules, by adding its trigger function, allow-list
  entry or transition columns, because a re-run of the script resets every
  grant to the rules. `tests/regression/test_app_role_grants.py` fails CI
  when a migration adds any of these that the script does not classify:
  * an append-only trigger;
  * an immutability-style trigger function;
  * a non-trigger `SECURITY DEFINER` function.

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
