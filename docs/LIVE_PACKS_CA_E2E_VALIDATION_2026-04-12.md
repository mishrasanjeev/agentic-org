# Live Packs + CA E2E Validation

Date: April 12, 2026

Environment:
- Base URL: `https://app.agenticorg.ai`
- API base: `https://app.agenticorg.ai/api/v1`
- Tenant used for validation: provided admin tenant `f7a096bd-a0ef-4652-9ebf-8efa9f38ba5b`
- Validation type: live API checks, live headless browser checks, local code audit, targeted local test/build verification

## Scope

This pass focused on the two flows called out as broken or high risk:

1. `Industry Packs` at `/dashboard/packs`
2. `CA / Partner Dashboard` at `/dashboard/partner`

The goal was to verify whether these flows work end to end on the live product and to distinguish:

- production failures confirmed on `app.agenticorg.ai`
- fixes already implemented locally in repo but not yet deployed
- residual gaps still open after the local fixes

## Live Actions Performed

The tenant initially had:

- no companies
- no CA subscription
- empty partner dashboard state

To make the CA path testable end to end, the following live actions were performed on April 12, 2026:

1. Activated a CA subscription trial via `POST /ca-subscription/activate`
2. Onboarded 3 sample companies via `POST /companies/onboard`
3. Generated compliance deadlines for those companies via `POST /companies/{company_id}/deadlines/generate?months_ahead=3`
4. Created 3 filing approvals via `POST /companies/{company_id}/approvals`
5. Re-read `GET /ca-subscription`, `GET /companies`, and `GET /partner-dashboard`
6. Re-checked `/dashboard/packs` and `/dashboard/partner` in a live browser session using the provided admin token plus hydrated user state

Sample companies created:

- `AO QA Apex Components Pvt Ltd`
- `AO QA Meridian Health Services LLP`
- `AO QA LexForge Advisory LLP`

Current live CA subscription state after activation:

- plan: `ca_pro`
- status: `trial`
- trial end: `2026-04-26T15:39:38.780875+00:00`

## Findings

### P0: Industry Packs page is broken in production

Confirmed live behavior:

- `GET /packs` returns `200` with 5 packs
- `/dashboard/packs` still renders `No industry packs available.`
- the live page showed `0` install actions even though the API was non-empty

Why this happens:

- the frontend was reading `data` or `items`, while the backend returns `{ "packs": [...] }`
- the frontend also expected a different pack object shape than the one returned by the API

Relevant code:

- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:19)
- [ui/src/pages/IndustryPacks.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/IndustryPacks.tsx:157)
- [ui/src/pages/IndustryPacks.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/IndustryPacks.tsx:169)
- [ui/src/pages/IndustryPacks.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/IndustryPacks.tsx:122)

Status:

- fixed locally in repo
- not yet deployed to production

### P0: Pack install state is not durable or operationally correct in production

Confirmed live API behavior from a controlled install/uninstall cycle:

1. `POST /packs/manufacturing/install` returned `installed`
2. first `GET /packs/installed` returned `[]`
3. second `GET /packs/installed` returned `["manufacturing"]`
4. `DELETE /packs/manufacturing` returned `not_installed`
5. final `GET /packs/installed` still returned `["manufacturing"]`

This is not a frontend problem. The live API state is inconsistent across requests and uninstall does not reliably remove state.

Most likely cause:

- production behavior matched the old in-memory installer model instead of a durable tenant-scoped persistence path

Relevant code:

- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py:29)
- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py:200)
- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:25)
- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:40)
- [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:210)

Status:

- fixed locally in repo by moving live pack state to `industry_pack_installs`
- not yet deployed to production

### P1: Partner Dashboard still fails to render upcoming deadlines in production

Confirmed live behavior after seeding real data:

- `GET /partner-dashboard` returned 18 upcoming deadlines
- live `/dashboard/partner` still rendered `Upcoming Deadlines` -> `No data yet.`

This is a real production UI/API contract bug.

Relevant code:

- backend returns `upcoming_deadlines` at [api/v1/companies.py](/C:/Users/mishr/agentic-org/api/v1/companies.py:2067)
- local repo fix accepts both `deadlines` and `upcoming_deadlines` at [ui/src/pages/PartnerDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/PartnerDashboard.tsx:101)

Status:

- fixed locally in repo
- not yet deployed to production

### P1: Partner Dashboard KPIs are materially wrong in production

After seeding 3 live companies and 3 live pending approvals, the API returned:

- `total_clients = 3`
- `active_clients = 3`
- `avg_health_score = 100.0`
- `total_pending_filings = 3`
- `total_overdue = 3`
- `revenue_per_month_inr = 14997`

But the live UI rendered:

- `3 Total Clients` and `3 Active` correctly
- `3 Pending Filings` correctly
- `0 Overdue` incorrectly
- `INR 34,993/month` incorrectly
- `Next billing: Apr 15, 2026` as stale/hardcoded-looking copy
- firm branding copy that does not reflect tenant data

This means the production Partner Dashboard is only partially wired to backend truth.

Relevant code:

- API source of truth: [api/v1/companies.py](/C:/Users/mishr/agentic-org/api/v1/companies.py:1960)
- local repo summary wiring: [ui/src/pages/PartnerDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/PartnerDashboard.tsx:79)
- local repo revenue card now reads API summary instead of hardcoded portfolio math/copy: [ui/src/pages/PartnerDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/PartnerDashboard.tsx:217)

Status:

- largely fixed locally in repo
- not yet deployed to production

### P1: Existing Playwright E2E auth helpers are stale for role-gated routes

The current E2E helper pattern stores only `localStorage.token`:

- [ui/e2e/v4-features.spec.ts](/C:/Users/mishr/agentic-org/ui/e2e/v4-features.spec.ts:23)
- [ui/e2e/ca-firms.spec.ts](/C:/Users/mishr/agentic-org/ui/e2e/ca-firms.spec.ts:21)

But role-gated routes now fail closed if there is a token without a hydrated user object:

- [ui/src/components/ProtectedRoute.tsx](/C:/Users/mishr/agentic-org/ui/src/components/ProtectedRoute.tsx:12)

Impact:

- live auth-required E2E tests can produce false negatives unless the helper hydrates both `token` and `user`

Status:

- still open in repo
- test harness should be updated before relying on Playwright for these protected flows

### P1: Packs admin APIs needed server-side admin enforcement

Pack install/uninstall is a tenant-wide control-plane operation. The local repo now applies admin enforcement at the router level:

- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:16)

Status:

- fixed locally in repo
- production status depends on deployment

## What Is Working End to End

These parts did work live after seeding:

- `POST /ca-subscription/activate`
- `POST /companies/onboard`
- `POST /companies/{company_id}/deadlines/generate`
- `POST /companies/{company_id}/approvals`
- `GET /companies`
- `GET /partner-dashboard`

So the CA backend path is substantially functional. The main remaining CA problems are in the production frontend contract and stale presentation logic, not the core API path.

## Local Fixes Already Present In Repo

The following repo-side fixes are now present locally:

1. `IndustryPacks` now normalizes the live `/packs` and `/packs/installed` response shapes and maps backend pack metadata into the UI contract
2. `PartnerDashboard` now reads `upcoming_deadlines`, binds summary KPIs from API output, removes stale hardcoded revenue behavior, and avoids zero-client math issues
3. pack state is now persisted through `industry_pack_installs` instead of in-memory request-local state
4. pack routes now require tenant admin authorization
5. compatibility DDL for `industry_pack_installs` exists in startup init code so the runtime does not crash before migrations are applied

Relevant files:

- [ui/src/pages/IndustryPacks.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/IndustryPacks.tsx:122)
- [ui/src/pages/PartnerDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/PartnerDashboard.tsx:73)
- [api/v1/packs.py](/C:/Users/mishr/agentic-org/api/v1/packs.py:16)
- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py:200)
- [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:210)

## Verification Run Locally

Backend:

- `python -m pytest -q --no-cov tests/unit/test_industry_packs.py tests/unit/test_ca_pack.py tests/unit/test_ca_features.py tests/unit/test_ca_api_functional.py`
- result: `151 passed`

Backend lint:

- `python -m ruff check api/v1/packs.py core/agents/packs/installer.py core/database.py`
- result: `All checks passed`

Frontend:

- `cd ui && npm run build`
- result: success

Known local verification limitation:

- `vitest` remains unhealthy in this repo because `react` and `react-dom` versions are mismatched in the current environment, so targeted frontend unit tests were not a trustworthy signal for this pass

## Recommended Next Steps

1. Deploy the current local fixes for packs and partner dashboard
2. Update Playwright auth helpers so protected-route tests hydrate both `token` and `user`
3. Re-run live browser validation on:
   - `/dashboard/packs`
   - `/dashboard/partner`
   - pack install/uninstall cycle
4. Add a durable regression test for the pack API contract:
   - `/packs` returns `packs`
   - `/packs/installed` returns string IDs or a stable documented shape
5. Add a frontend regression test for `PartnerDashboard` that explicitly covers:
   - `upcoming_deadlines`
   - `revenue_per_month_inr`
   - `total_overdue`
6. Decide whether to keep or remove the 3 QA client companies from the test tenant after signoff

## Bottom Line

The live product is not yet correct end to end for Packs, and CA is only partially correct end to end.

- Packs are broken in production both at the UI layer and in install-state durability
- CA backend flows work, but the production dashboard still misrenders deadlines and several KPIs
- The local repo now contains targeted fixes for the major UI/API contract and pack persistence problems, but those fixes need deployment plus one more live retest pass
