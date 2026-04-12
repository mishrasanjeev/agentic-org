# PR Summary: Fix Live Packs Catalog and CA Partner Dashboard Contract Drift

## Suggested Title

`fix: restore live packs catalog, persist pack installs, and bind CA partner dashboard to API truth`

## Problem

Live validation on `https://app.agenticorg.ai` confirmed that two enterprise-facing flows are not working correctly in production:

1. `Industry Packs` at `/dashboard/packs` renders an empty state even though `GET /packs` returns data
2. `Partner Dashboard` at `/dashboard/partner` misrenders real CA data:
   - deadlines do not show even when `upcoming_deadlines` is non-empty
   - some KPI values are stale or hardcoded instead of coming from the API
3. pack install state is not durable across requests and uninstall is operationally inconsistent

This patch aligns the frontend with the backend contracts, makes pack install state persistent, and closes an admin authorization gap on pack control-plane routes.

## Why This Patch Is Safe To Deploy

- It is a narrow fix set limited to packs and partner dashboard behavior
- It does not introduce a new schema beyond startup compatibility DDL for a table that already exists in Alembic migration `v4_0_0_project_apex_tables.py`
- Backend verification passed
- Frontend build passed
- The changes are based on confirmed live regressions, not speculative cleanup

## Included Files

### Backend

- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:1)
  - requires tenant admin on pack routes
  - switches installed/install/uninstall paths to async DB-backed helpers

- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py:1)
  - keeps sync in-memory helpers for unit tests
  - adds async DB-backed persistence for live pack installs via `industry_pack_installs`
  - keeps pack install/uninstall summaries stable

- [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:210)
  - adds compatibility `CREATE TABLE IF NOT EXISTS` and indexes for `industry_pack_installs`
  - note: this table already exists in Alembic at [migrations/versions/v4_0_0_project_apex_tables.py](/C:/Users/mishr/agentic-org/migrations/versions/v4_0_0_project_apex_tables.py:164)

### Frontend

- [ui/src/pages/IndustryPacks.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/IndustryPacks.tsx:151)
  - consumes `{ packs: [...] }` and `{ installed: [...] }`
  - normalizes live pack objects into the UI contract
  - derives stable IDs, agent counts, workflows, connectors, and icon fallbacks

- [ui/src/pages/PartnerDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/PartnerDashboard.tsx:60)
  - binds KPI summary directly from `/partner-dashboard`
  - accepts `upcoming_deadlines` as the backend source of truth
  - removes stale hardcoded firm/revenue copy
  - guards against zero-client width math producing invalid UI state

## Explicitly Out Of Scope

These should not be included in the PR for this fix set:

- `ui/public/llms-full.txt`
- `ui/public/llms.txt`
- `ui/public/sitemap.xml`

Those files are generated/build-side artifacts and are not part of the functional fix.

Also out of scope for this patch:

- Playwright auth-helper cleanup across all `ui/e2e/*.spec.ts`
- QA data cleanup on the live tenant
- broader CA role-model cleanup
- deploy pipeline changes

## Behavior Change Summary

### Packs

Before:

- `/dashboard/packs` could show `No industry packs available.` even when the API returned packs
- pack install state was effectively non-durable or inconsistent across requests
- pack install/uninstall routes did not enforce admin-only control-plane access

After:

- packs page can render the live API response shape
- installed state is read from persisted tenant-scoped storage
- install/uninstall calls persist and remove state through the database path
- pack control-plane routes require tenant admin

### CA Partner Dashboard

Before:

- live deadlines from `upcoming_deadlines` were ignored by the frontend
- revenue and some presentation text were stale/hardcoded
- overdue and summary values could diverge from API truth

After:

- dashboard consumes backend summary fields directly
- upcoming deadlines render from the actual API contract
- revenue card reflects `revenue_per_month_inr`
- dashboard behavior is stable for both empty and non-empty tenant states

## Verification Completed

### Live validation performed

Captured in:

- [docs/LIVE_PACKS_CA_E2E_VALIDATION_2026-04-12.md](/C:/Users/mishr/agentic-org/docs/LIVE_PACKS_CA_E2E_VALIDATION_2026-04-12.md:1)

Live tenant validation included:

- CA trial activation
- 3 sample client companies onboarded
- deadline generation
- 3 filing approvals created
- API and browser checks on `/dashboard/packs` and `/dashboard/partner`

### Local verification performed

Backend tests:

```powershell
python -m pytest -q --no-cov tests/unit/test_industry_packs.py tests/unit/test_ca_pack.py tests/unit/test_ca_features.py tests/unit/test_ca_api_functional.py
```

Result:

- `151 passed`

Backend lint:

```powershell
python -m ruff check api/v1/packs.py core/agents/packs/installer.py core/database.py
```

Result:

- `All checks passed`

Frontend build:

```powershell
cd ui
npm run build
```

Result:

- success

## Deployment Notes

1. No new Alembic migration is required for this patch because `industry_pack_installs` already exists in migration history.
2. `core/database.py::init_db()` includes compatibility DDL so pods do not fail on environments where migrations lag behind runtime.
3. This patch should be deployed together, not partially:
   - frontend-only deploy fixes rendering but not install durability
   - backend-only deploy fixes persistence/admin access but not the empty packs page or dashboard rendering

## Post-Deploy Smoke Test

Run these immediately after deploy on the same tenant or an equivalent staging tenant:

1. Open `/dashboard/packs`
   - expect visible cards for the available industry packs
   - expect no empty-state message while `GET /packs` is non-empty

2. Install one pack, then refresh
   - expect installed state to persist
   - uninstall should remove state cleanly

3. Open `/dashboard/partner`
   - expect total clients, pending filings, overdue, and revenue to match `GET /partner-dashboard`
   - expect upcoming deadlines to render when `upcoming_deadlines` is non-empty

4. Verify non-admin user behavior
   - expect pack install/uninstall to be denied server-side

## Rollback Plan

If post-deploy smoke tests fail:

1. roll back frontend and backend together
2. verify `GET /packs`, `GET /packs/installed`, and `GET /partner-dashboard`
3. check whether the issue is:
   - stale frontend asset deployment
   - runtime DB table mismatch
   - environment drift unrelated to this patch

## Residual Risks

- The repo E2E helpers still mostly store only `localStorage.token`, so protected-route Playwright coverage remains weaker than it should be until that helper pattern is fixed
- The live tenant now contains 3 QA companies plus an active CA trial from validation; decide whether to keep or remove them after signoff
- `.claude/` skill improvements are local-only because that directory is gitignored and are not part of this PR

## Reviewer Focus

Reviewers should concentrate on:

1. pack API contract and persistence semantics
2. admin authorization boundary on pack routes
3. frontend normalization logic for live response shapes
4. partner dashboard binding to backend summary truth instead of stale UI assumptions
