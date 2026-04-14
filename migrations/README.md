# Schema migrations

**As of v4.9.0, Alembic is the sole schema authority.**

The deploy pipeline runs `scripts/alembic_migrate.py` as a pre-rollout
step in `.github/workflows/deploy.yml`. The wrapper is idempotent:

| DB state at deploy time                                 | Action taken                           |
| ------------------------------------------------------- | -------------------------------------- |
| `alembic_version` table present                         | `alembic upgrade head`                 |
| Legacy schema (tables but no `alembic_version`)         | `stamp v480_baseline` + `upgrade head` |
| Empty DB                                                | `alembic upgrade head` from base       |

The Helm chart sets `AGENTICORG_DDL_MANAGED_BY_ALEMBIC=true` so the
runtime startup path (`core.database.init_db()`) only verifies DB
connectivity and does **not** issue DDL.

The legacy DDL blocks in `init_db()` are retained as a compatibility net
for environments that have not yet been stamped; they are no-ops once the
flag is set.

## Version chain

```
v400_apex                  → v4.0.0  Project Apex tables
v410_companies             → v4.1.0  Multi-company model
v420_ca_features           → v4.2.0  CA add-on
v430_ca_phase2             → v4.3.0  GSTN credential vault
v440_persist_stores        → v4.4.0  In-memory → Postgres
v450_company_rls           → v4.5.0  RLS on agent_task_results
v460_enterprise            → v4.6.0  Departments, delegations, flags
v470_sso_invoices          → v4.7.0  SSO, approvals, invoices, A/B
v480_baseline              → v4.8.0  Alembic cutover (kpi_cache,
                                      agent_task_results, connector_configs,
                                      v4.7 RLS enforcement,
                                      audit_log immutability)
```

## Workflow for a schema change

1. Edit the ORM model under `core/models/`.
2. Create a new file under `migrations/versions/` (use
   `alembic revision -m "<title>"` or copy a neighbor). Set
   `down_revision` to the current head.
3. Write idempotent DDL in `upgrade()` (`CREATE ... IF NOT EXISTS`,
   guarded `ALTER`). Include a matching `downgrade()` when safe.
4. Run `alembic upgrade head` locally against a dev DB to validate.
5. Push. CI enforces that any change under `core/models/` or
   `core/database.py` includes a new migration
   (`scripts/check_migration_required.py`).

## Stamping on an existing environment

`scripts/alembic_migrate.py` handles the stamp automatically on the first
deploy that includes it. Manual stamping is therefore **not required**
for the cutover. If you do want to stamp by hand (e.g. from a developer
laptop), the command is:

```bash
AGENTICORG_DB_URL=<url> alembic stamp v480_baseline
```

## Local development

```bash
# fresh DB
alembic upgrade head

# stamp a DB that was built via init_db()
alembic stamp v480_baseline

# new migration
alembic revision -m "add foo column to bar"
```

## Why `init_db()` still contains DDL

During cutover, some dev/staging environments may not yet be stamped.
Keeping the legacy DDL behind the
`AGENTICORG_DDL_MANAGED_BY_ALEMBIC` flag means those environments still
self-heal on pod boot, while production and CI (which set the flag) get
the strict Alembic-managed path. Once every environment is stamped, the
legacy DDL can be deleted in a follow-up PR.
