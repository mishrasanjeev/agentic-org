# PR Summary: Company-Scoped CA Automation Ownership and Metadata Refresh

Date: April 13, 2026

## Suggested Title

`fix: add company-scoped ownership for CA automation assets`

## Commits In Scope

- `d1b2c11` `fix(seed): include tool_functions NOT NULL in connector INSERT`
- `e847e15` `chore: refresh llms and sitemap outputs`

Important note:

- the first commit message is misleading relative to its actual diff
- the real change set is primarily the CA company-ownership fix plus one small `seed_demo_data.py` correction

## Problem

The CA product surface had improved materially, but one platform-level gap was still blocking enterprise-grade behavior:

- `company_id` existed in startup DDL for operational tables, but not as a first-class model/API ownership field
- CA automation still behaved as tenant-level state under the hood, even when the UI was operating in a company-specific workflow
- company detail had to infer ownership from pack-name prefixes instead of querying company-owned assets directly

This PR closes that ownership gap and also includes the regenerated public metadata files after the product/catalog changes.

## What This PR Changes

### 1. Makes `company_id` first-class on automation records

Changed files:

- [core/models/agent.py](/C:/Users/mishr/agentic-org/core/models/agent.py)
- [core/models/workflow.py](/C:/Users/mishr/agentic-org/core/models/workflow.py)
- [core/models/audit.py](/C:/Users/mishr/agentic-org/core/models/audit.py)
- [core/schemas/api.py](/C:/Users/mishr/agentic-org/core/schemas/api.py)

What changed:

- `Agent` now exposes nullable `company_id`
- `WorkflowDefinition` and `WorkflowRun` now expose nullable `company_id`
- `AuditLog` now exposes nullable `company_id`
- API schemas for agent/workflow create paths now accept optional `company_id`

### 2. Exposes company ownership in agent, workflow, and audit APIs

Changed files:

- [api/v1/agents.py](/C:/Users/mishr/agentic-org/api/v1/agents.py)
- [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py)
- [api/v1/audit.py](/C:/Users/mishr/agentic-org/api/v1/audit.py)

What changed:

- `/agents` create/list now accepts and emits `company_id`
- `/workflows` create/list now accepts and emits `company_id`
- workflow runs inherit `company_id` from the workflow definition
- `/audit` now emits `company_id` and supports filtering by `company_id`
- invalid `company_id` input is rejected cleanly
- create paths validate that the company belongs to the current tenant

### 3. Makes CA pack provisioning company-scoped instead of tenant-global

Changed file:

- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py)

What changed:

- CA pack install now reconciles assets per company for all companies in the tenant
- CA agents and workflows are created with explicit `company_id`
- install metadata in `industry_pack_installs` is merged so pack state remains durable while company-scoped assets accumulate
- audit events now capture company-scoped pack sync/install details
- company-scoped asset names and workflow metadata are generated deterministically

Result:

- CA automation ownership is explicit and queryable
- company automation assets can be listed by `company_id` without name-based heuristics

### 4. Provisions CA assets automatically when new companies are created

Changed file:

- [api/v1/companies.py](/C:/Users/mishr/agentic-org/api/v1/companies.py)

What changed:

- company create now checks whether `ca-firm` is installed
- company onboard now checks whether `ca-firm` is installed
- when installed, the new company immediately receives its CA pack assets

Result:

- a tenant with CA pack already enabled no longer needs manual resync after onboarding a new client company

### 5. Removes company-detail ownership heuristics from the UI

Changed files:

- [ui/src/pages/CompanyDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/CompanyDashboard.tsx)
- [ui/src/pages/CompanyDetail.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/CompanyDetail.tsx)

What changed:

- company detail now queries `/agents` with `company_id=<company>`
- company detail now queries `/workflows` with `company_id=<company>`
- the CA asset tabs no longer depend on pack-name prefix matching
- company dashboard changes from the earlier CA remediation remain in scope with this commit chain

### 6. Includes one small seed-data safety fix

Changed file:

- [core/seed_demo_data.py](/C:/Users/mishr/agentic-org/core/seed_demo_data.py)

What changed:

- connector seed inserts now include `tool_functions` to satisfy the live schema requirement

This is unrelated to company ownership, but it is part of the actual commit diff and should be mentioned explicitly in the PR.

### 7. Refreshes generated public metadata artifacts

Changed files:

- [ui/public/llms.txt](/C:/Users/mishr/agentic-org/ui/public/llms.txt)
- [ui/public/llms-full.txt](/C:/Users/mishr/agentic-org/ui/public/llms-full.txt)
- [ui/public/sitemap.xml](/C:/Users/mishr/agentic-org/ui/public/sitemap.xml)

What changed:

- regenerated public catalog and sitemap outputs after the CA/packs remediation changes

## Full File List In PR

- [api/v1/agents.py](/C:/Users/mishr/agentic-org/api/v1/agents.py)
- [api/v1/audit.py](/C:/Users/mishr/agentic-org/api/v1/audit.py)
- [api/v1/companies.py](/C:/Users/mishr/agentic-org/api/v1/companies.py)
- [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py)
- [core/agents/packs/installer.py](/C:/Users/mishr/agentic-org/core/agents/packs/installer.py)
- [core/models/agent.py](/C:/Users/mishr/agentic-org/core/models/agent.py)
- [core/models/audit.py](/C:/Users/mishr/agentic-org/core/models/audit.py)
- [core/models/workflow.py](/C:/Users/mishr/agentic-org/core/models/workflow.py)
- [core/schemas/api.py](/C:/Users/mishr/agentic-org/core/schemas/api.py)
- [core/seed_demo_data.py](/C:/Users/mishr/agentic-org/core/seed_demo_data.py)
- [docs/PACKS_CA_REMEDIATION_2026-04-13.md](/C:/Users/mishr/agentic-org/docs/PACKS_CA_REMEDIATION_2026-04-13.md)
- [tests/unit/test_agents_and_sales.py](/C:/Users/mishr/agentic-org/tests/unit/test_agents_and_sales.py)
- [tests/unit/test_api_endpoints.py](/C:/Users/mishr/agentic-org/tests/unit/test_api_endpoints.py)
- [ui/public/llms.txt](/C:/Users/mishr/agentic-org/ui/public/llms.txt)
- [ui/public/llms-full.txt](/C:/Users/mishr/agentic-org/ui/public/llms-full.txt)
- [ui/public/sitemap.xml](/C:/Users/mishr/agentic-org/ui/public/sitemap.xml)
- [ui/src/pages/CompanyDashboard.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/CompanyDashboard.tsx)
- [ui/src/pages/CompanyDetail.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/CompanyDetail.tsx)

## Verification Completed

Backend lint:

```powershell
python -m ruff check --no-cache api/v1/agents.py api/v1/audit.py api/v1/companies.py api/v1/workflows.py core/agents/packs/installer.py core/models/agent.py core/models/audit.py core/models/workflow.py core/schemas/api.py tests/unit/test_agents_and_sales.py tests/unit/test_api_endpoints.py
```

Result:

- `All checks passed`

Targeted backend tests:

```powershell
python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_agents_and_sales.py tests/unit/test_ca_pack.py tests/unit/test_ca_features.py tests/unit/test_ca_api_functional.py
```

Result:

- `252 passed`

Additional API serializer/endpoint coverage:

```powershell
python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_api_endpoints.py -k "not TestEvalsEndpoints and not TestLoadScorecard"
```

Result:

- `113 passed, 8 deselected`

Frontend build:

```powershell
cd ui
npm run build
```

Result:

- success

## Verification Limitation

The full `tests/unit/test_api_endpoints.py` run still has 8 unrelated failures in `tmp_path`-based tests because this Windows environment denies temp-directory creation under the available sandbox paths.

Those failures are environmental, not caused by this PR’s ownership changes.

## Deployment Notes

1. Deploy frontend and backend together.
2. The generated public metadata files are safe to ship with this PR because they reflect the current built state.
3. The CA ownership behavior depends on runtime schema having the nullable `company_id` columns already created by startup DDL or prior migration path.
4. Existing non-CA packs remain tenant-scoped; this PR makes CA company automation explicitly company-scoped.

## Post-Deploy Smoke Test

1. Install `ca-firm` on a tenant with multiple companies.
2. Create or onboard a new company after CA pack install.
3. Verify `/agents?company_id=<company>` returns only that company’s CA assets.
4. Verify `/workflows?company_id=<company>` returns only that company’s workflows.
5. Verify company detail shows the correct company-specific agents and workflows.
6. Verify `/audit?company_id=<company>` returns company-scoped events for pack sync/install activity.
7. Verify demo seed still succeeds on a fresh environment with connector inserts.

## Residual Risks

- role removal on `/companies/{company_id}/roles` is still not implemented
- external CA connectors are still not validated end to end in this patch
- the first commit message should ideally be corrected later if you want history clarity, but that would require an explicit history rewrite

## Suggested PR Body

```md
## Summary

This PR closes the remaining company-ownership gap in the CA product surface.

It makes `company_id` a first-class field on agents, workflows, workflow runs, and audit entries; exposes that ownership through the APIs; and updates CA pack provisioning plus company onboarding so CA automation assets are created and queried per company rather than only at the tenant level.

It also includes:

- a small seed-data fix so connector seeds satisfy the current schema
- regenerated `llms.txt`, `llms-full.txt`, and `sitemap.xml`

## Verification

- `ruff check --no-cache ...` passed on all touched backend/model/test files
- targeted pytest suites passed: `252 passed`
- additional API endpoint coverage passed: `113 passed, 8 deselected`
- `cd ui && npm run build` passed

## Notes

- the main implementation commit message currently does not describe the real diff well
- role removal is still out of scope
- external CA connector validation is still out of scope
```
