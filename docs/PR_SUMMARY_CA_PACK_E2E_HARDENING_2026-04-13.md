# PR Summary: CA Pack E2E Hardening

## Problem

The CA pack flow still had two enterprise-grade gaps even after the earlier pack provisioning work:

1. Tenants could have an active or trial CA subscription but no durable `ca-firm` install state, leaving `/packs/installed` empty and risking missing company-scoped CA assets until someone manually reinstalled the pack.
2. The company detail UI did not surface the real company audit feed, so the CA pack provisioning trail and later company-scoped automation events were invisible in the actual product.

There was also a testability gap:

3. The CA UI spec lived outside the active Vitest include path, so CI did not enforce the company-detail audit behavior.
4. The UI dependency graph was inconsistent: `vite` was on `8.x` while `@vitejs/plugin-react` remained on `4.7.0`, and local `react-dom` was stale. That blocked clean frontend test execution.

## Changes

- Added `ensure_ca_pack_subscription_sync_async()` in `core/agents/packs/installer.py`.
- Reconcile CA subscription/install drift from:
  - `GET /packs/installed`
  - `GET /companies`
  - `GET /companies/{company_id}`
  - `GET /ca-subscription`
  - `POST /ca-subscription/activate`
  - `GET /partner-dashboard`
- Updated `CompanyDetail` to:
  - fetch `/audit?company_id=...`
  - expose an `Audit Log` tab
  - merge real audit entries into the recent activity view
  - keep company-scoped agents/workflows/audit requests explicit
- Added an active Vitest regression at `ui/src/__tests__/CompanyDetail.test.tsx`.
- Upgraded `@vitejs/plugin-react` to `^5.2.0` to match the existing Vite 8 toolchain.

## Files

- `api/v1/companies.py`
- `api/v1/packs.py`
- `core/agents/packs/installer.py`
- `tests/unit/test_ca_api_functional.py`
- `tests/unit/test_industry_packs.py`
- `ui/src/pages/CompanyDetail.tsx`
- `ui/src/__tests__/CompanyDetail.test.tsx`
- `ui/package.json`
- `ui/package-lock.json`

## Verification

Backend:

- `python -m ruff check --no-cache api/v1/packs.py api/v1/companies.py core/agents/packs/installer.py tests/unit/test_ca_api_functional.py tests/unit/test_industry_packs.py`
- result: passed

- `python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_ca_api_functional.py tests/unit/test_industry_packs.py tests/unit/test_ca_pack.py tests/unit/test_ca_features.py tests/unit/test_company_model.py tests/unit/test_company_isolation.py tests/regression/test_ca_regression.py tests/regression/test_ca_bugs.py tests/e2e/test_ca_firm_workflow.py`
- result: `304 passed, 3 skipped`

Frontend:

- `cd ui && npm run test -- src/__tests__/CompanyDetail.test.tsx`
- result: `1 passed, 2 tests passed`

- `cd ui && npm run build`
- result: passed

## Known Out-Of-Scope Failures

Running the entire active frontend unit suite after the dependency fix exposed older non-CA failures in:

- `src/__tests__/CFODashboard.test.tsx`
- `src/__tests__/CMODashboard.test.tsx`
- `src/__tests__/NLQueryBar.test.tsx`

Those regressions predate this CA patch scope and should be handled separately.

## Deploy Notes

- Deploy backend and frontend together.
- Re-run production validation on:
  - `/dashboard/packs`
  - `/dashboard/partner`
  - `/dashboard/companies/{company_id}`
- Explicitly confirm that a tenant with an active CA subscription but no historical `industry_pack_installs` row self-heals on first access.
