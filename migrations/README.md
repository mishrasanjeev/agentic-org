# Schema migrations

## How AgenticOrg actually applies schema changes today

The runtime authority for the database schema is
**`core.database.init_db()`**, which runs at API startup and applies
idempotent `ALTER TABLE … IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS`
blocks. This is what creates / upgrades your tables on every pod boot.

The `versions/` directory in this folder mirrors those changes as
Alembic-style migration files. **They are not executed by the running
app today** — they exist as documentation of the version chain so a
future Alembic adoption can pick up cleanly.

## Why we haven't fully adopted Alembic

1. **Operational simplicity** — `init_db()` runs every time a pod
   starts, so the schema is self-healing without an extra deploy step.
2. **Multi-instance safety** — Postgres `IF NOT EXISTS` clauses are
   atomic at the catalog level; multiple pods racing to call
   `init_db()` is safe.
3. **No baseline cost** — onboarding Alembic at v4.7.0 means
   stamping the existing prod DB at the right revision (`alembic
   stamp v470_sso_invoices`), wiring an env.py, and writing a CI
   guard for autogenerate. That's a separate work item, not a
   v4.7.0 hotfix.

## What to do when you change the schema

1. Add the corresponding `ALTER` / `CREATE` block to `init_db()` so
   the change ships with the next pod restart.
2. Add a matching `versions/v<X>_<Y>_<Z>_<title>.py` file here so the
   version chain stays in sync. Use the existing files as templates
   and set `down_revision` to the previous head.
3. Update the table at the top of `core/models/__init__.py` so the
   ORM imports the new model.
4. Run `python -m pytest tests/unit/test_<area>.py` locally before
   pushing.

## When we'll switch to real Alembic

Tracked as `P1.7` in `docs/ENTERPRISE_V4_7_0_SUMMARY.md`. The plan:

1. Generate an `alembic.ini` and `env.py` pointing at our SQLAlchemy
   metadata (`core.models.base.BaseModel.metadata`).
2. Run `alembic stamp v470_sso_invoices` against production so it
   knows we're already at this revision.
3. Add a CI guard: any PR touching `core/models/*.py` or
   `core/database.py::init_db` must have a corresponding new file in
   `versions/`. Implementation: `git diff HEAD~1 HEAD` + grep.
4. Cut over: stop hand-editing `init_db()` and use
   `alembic upgrade head` as a deploy pre-hook in
   `.github/workflows/deploy.yml`.

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
```
