# Schema migrations

**As of v4.9.0, Alembic is the sole schema authority.**

The deploy pipeline runs `scripts/alembic_migrate.py` as a pre-rollout
step in `.github/workflows/deploy.yml`. The wrapper is idempotent:

| DB state at deploy time                                 | Action taken                           |
| ------------------------------------------------------- | -------------------------------------- |
| `alembic_version` table present                         | `alembic upgrade head`                 |
| Legacy schema (tables but no `alembic_version`)         | `stamp v480_baseline` + `upgrade head` |
| Empty DB                                                | `alembic upgrade head` from base       |

Application startup is now verify-only in strict runtimes
(`production`, `staging`, `preview`, and unknown environments).
`core.database.init_db()` checks database connectivity, verifies that
`alembic_version` exists, and fails fast unless the DB revision exactly
matches the Alembic head(s) bundled with the running app. It does
**not** issue `CREATE`, `ALTER`, `ENABLE RLS`, policy, trigger, or index
DDL in strict runtimes.

The old `AGENTICORG_DDL_MANAGED_BY_ALEMBIC` flag is no longer a safety
boundary. The only remaining legacy startup repair path is
`AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=1`, and that helper refuses to run
outside relaxed local/dev/test/ci environments.

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
5. Push. CI enforces that any ORM model change, or any new schema DDL
   added to `core/database.py`, includes a new migration
   (`scripts/check_migration_required.py`). The same guard fails on
   multiple Alembic heads unless the PR is explicitly a merge-head PR.

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

Local startup defaults to verification, not mutation. To repair an old
unstamped developer DB during migration testing, set
`AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=1` in a relaxed environment and
start the app once. Do not use that flag for production, staging, or
preview.

Production and staging runtime users should not have DDL privileges when
the platform can enforce separate migration and application DB roles.

## Why `init_db()` still contains DDL

The legacy DDL is quarantined in
`_legacy_startup_schema_repair_for_local_only()` only for local
unstamped database repair. It is not a production compatibility net.
Once all developer and demo DBs are stamped, delete the helper in a
follow-up PR.

## Parallel-head merge strategy

This branch is intentionally independent and currently contains only the
workflow durability head `v4912_workflow_event_waits`, so it does not add
a merge revision.

The P0 durability branches that were expected to create parallel heads
from `v4912` are:

- PR #553 CDC webhook durability
- PR #554 WebSocket feed durability
- PR #556 bridge session durability

When those migrations are all present in one branch, create an empty
Alembic merge revision, for example
`v4916_merge_p0_durability_heads`, with `down_revision` set to the
tuple of the CDC, feed, and bridge head revisions. That merge revision
must be the only head after the PR lands. CI should only allow multiple
heads temporarily by setting
`AGENTICORG_ALLOW_MULTIPLE_ALEMBIC_HEADS_FOR_MERGE_PR=1` on the
explicit merge-head PR.
