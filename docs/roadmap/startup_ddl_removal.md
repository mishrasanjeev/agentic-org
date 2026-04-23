# Roadmap: Remove Startup DDL from App Lifecycle

**Status:** scheduled — blocker H from Codex 2026-04-23 re-verification.
**Owner:** platform
**Target release:** v3.3.x

## Problem

`api/main.py` still calls `init_db()` on startup, and
`core/database.py::init_db()` conditionally owns large bootstrap DDL
(table `CREATE IF NOT EXISTS`, index creation, extension loads, seed
data insertion).

Two enterprise-grade problems:

1. **Deploy-safety.** Startup DDL races with Alembic. On a rolling
   deploy with three pods, the first pod to reach its lifespan hook
   runs `CREATE TABLE IF NOT EXISTS` at the same time as Alembic is
   still applying migration 0489. The race produces intermittent
   deploy failures — we've already seen this in the
   `report_schedules`-missing-table incident on 2026-04-22.
2. **Audit governance.** Every schema change belongs in an Alembic
   revision with migration metadata, a deterministic upgrade hash,
   and a traceable `alembic history` path. Anything that flows only
   through `init_db()` has none of that — the schema shifts silently
   whenever someone edits `core/database.py`.

## Design

Make Alembic the only path for schema evolution. `init_db()` keeps
**only** the read-side sanity checks (connection test, required
extension probe). No DDL.

### Steps

1. Inventory every `CREATE TABLE`, `CREATE INDEX`, `CREATE EXTENSION`
   executed inside `init_db()`. Export to
   `docs/roadmap/init_db_inventory.md` (internal).
2. For every statement, confirm that an equivalent Alembic migration
   already owns it. File new revisions for the gaps. Each revision
   MUST be backward-compatible (no drops without a guarded migration
   path).
3. Add `scripts/check_no_startup_ddl.py` — a preflight lint rule
   that greps `core/database.py` for `CREATE` / `ALTER` / `DROP`
   tokens inside `init_db()` and fails the build.
4. Replace `init_db()` body with:
   ```python
   async def init_db() -> None:
       async with engine.connect() as conn:
           await conn.execute(text("SELECT 1"))
           # Explicitly probe required extensions (pgvector, uuid-ossp);
           # fail the process if they are missing so the problem is
           # surfaced at start, not at first query.
           ...
   ```
5. Update `api/main.py` startup handler to log the Alembic head that
   the running process expects, and refuse to start if the DB head
   is behind it.

### Deploy contract

- CI/CD pipeline runs `alembic upgrade head` as a discrete
  **migration job** BEFORE the app Deployment rolls. Helm chart gets
  a `pre-install`/`pre-upgrade` hook that runs the migration image.
- `init_db()` at app startup asserts the DB head matches the code's
  expected head; otherwise pod refuses to become ready.

### Rollback

Keep `AGENTICORG_ALLOW_STARTUP_DDL=1` as a kill switch for one
release so if the migration job is misconfigured in a customer's
Helm values we can fall back. Remove the flag one release later.

## Tests

- `tests/unit/test_no_startup_ddl.py` — source-inspection test that
  asserts no DDL keywords in the `init_db()` function body.
- `tests/integration/test_migration_head_gate.py` — simulate a pod
  with a stale Alembic head and assert startup refuses readiness.

## Non-goals

- Migration runner redesign (we keep Alembic; not adopting
  sqitch/bytebase).
- Changing the DB dialect.
