# Packs and CA Remediation

Date: April 13, 2026

## Scope

This document captures:

- the live issues confirmed on `app.agenticorg.ai` on April 13, 2026
- the repo-side fixes applied in this patch
- the remaining gaps that still need follow-on work before calling the packs and CA surfaces fully enterprise-grade

## Live Findings Reconfirmed

### 1. Packs worked as a catalog, not as real product provisioning

Live validation showed:

- `/dashboard/packs` rendered correctly
- pack install and uninstall persisted correctly
- every pack modal opened and showed pack metadata

But after install:

- `/agents` count did not increase
- `/workflows` count did not increase
- no pack workflow names appeared in the tenant

Conclusion:

- the product had an install-state feature, not a true pack provisioning feature

### 2. CA backend flows were real, but company detail UX was still partially mocked

Live validation showed:

- CA trial activation worked
- company onboarding worked
- approval creation and approval decision flows worked
- deadline generation and mark-filed flows worked
- partner dashboard reflected backend state correctly

But the company surfaces still contained fake or fallback behavior:

- company list summary used derived fallback math instead of backend summary metrics
- company detail used hardcoded KPI values
- company detail approvals, credentials, and operational sections were still heavily stubbed
- the page implied real firm operations while still rendering demo data

## Fixes Applied In This Patch

### 1. Pack install now provisions real tenant assets

Changed file:

- `core/agents/packs/installer.py`

What changed:

- install now creates real `Agent` rows for pack agents
- install now creates initial `AgentVersion` rows for those agents
- install now creates real `WorkflowDefinition` rows for pack workflows
- created asset ids are persisted in `industry_pack_installs`
- install is still idempotent at the pack level
- pack-created assets now use deterministic names such as `Chartered Accountant Firm Pack - GST Filing Agent`

Implementation notes:

- agent prompts are built from pack metadata, optional pack prompt files, and prompt suffixes
- workflow definitions are created with real step objects and human review gates
- pack-created assets are tagged in agent config as `industry_pack` assets

### 2. Pack uninstall now cleans up owned assets

Changed file:

- `core/agents/packs/installer.py`

What changed:

- uninstall now reads the stored asset ids from `industry_pack_installs`
- uninstall removes the pack-owned workflow definitions and related workflow run data
- uninstall removes the pack-owned agents and related version/HITL/prompt/tool-call records
- uninstall still removes the install row itself

### 3. Company list summary now uses real partner metrics

Changed file:

- `ui/src/pages/CompanyDashboard.tsx`

What changed:

- dashboard now fetches `/partner-dashboard` along with `/companies`
- total clients, active clients, pending filings, and overdue counts now come from backend truth
- the fake `Recon Complete` KPI was removed and replaced with real `Overdue`

### 4. Company detail was rewritten to use live APIs instead of mock arrays

Changed file:

- `ui/src/pages/CompanyDetail.tsx`

What changed:

- overview metrics now come from real approvals, deadlines, and credentials
- recent activity is derived from real approvals, deadlines, and GSTN uploads
- compliance tab now renders real deadlines and real GSTN upload history
- approvals tab now lists live approvals and supports create/approve/reject actions
- settings tab now saves real company updates
- settings tab now loads and updates real company role mappings
- settings tab now loads, creates, verifies, and deactivates real GSTN credentials
- agents/workflows tabs now reflect live provisioned CA pack assets when present

What was explicitly removed:

- hardcoded KPI numbers
- fake filing approval arrays
- alert-based placeholder actions
- demo-only credential tables

### 5. Company-scoped ownership is now first-class for company automation assets

Changed files:

- `core/models/agent.py`
- `core/models/workflow.py`
- `core/models/audit.py`
- `core/schemas/api.py`
- `api/v1/agents.py`
- `api/v1/workflows.py`
- `api/v1/audit.py`
- `api/v1/companies.py`
- `core/agents/packs/installer.py`
- `ui/src/pages/CompanyDetail.tsx`

What changed:

- `Agent`, `WorkflowDefinition`, `WorkflowRun`, and `AuditLog` now expose nullable `company_id`
- agent and workflow create/list APIs now accept and emit `company_id`
- workflow runs now inherit `company_id` from the workflow definition
- audit queries can now filter by `company_id`
- CA pack install now reconciles per-company assets for all tenant companies instead of treating CA automation as tenant-global only
- company creation and onboarding now auto-provision CA pack assets when the CA pack is already installed
- company detail now queries `/agents` and `/workflows` by `company_id` instead of inferring ownership from pack-name prefixes

Result:

- CA automation ownership is now explicit and queryable at the platform layer
- company detail can show strict per-company automation assets instead of tenant-level heuristics

## Remaining Gaps After This Patch

### 1. Role removal is still not supported by the current company roles API

The UI now uses the real roles API, but the backend only supports add/update semantics.

Result:

- role editing works
- role deletion still needs backend support

### 2. External connector validation is still outside this patch

This patch provisions real pack assets, but it does not validate external integrations end to end for:

- GSTN
- Tally
- banking AA
- Zoho Books
- Epic
- Availity
- SAP
- DocuSign
- Salesforce

That requires sandbox or live test credentials plus workflow execution validation after deploy.

## Verification

Executed locally after the code changes:

- `python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_agents_and_sales.py tests/unit/test_ca_pack.py tests/unit/test_ca_features.py tests/unit/test_ca_api_functional.py`
  - result: `252 passed`
- `python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_api_endpoints.py -k "not TestEvalsEndpoints and not TestLoadScorecard"`
  - result: `113 passed, 8 deselected`
- `python -m ruff check --no-cache api/v1/agents.py api/v1/audit.py api/v1/companies.py api/v1/workflows.py core/agents/packs/installer.py core/models/agent.py core/models/audit.py core/models/workflow.py core/schemas/api.py tests/unit/test_agents_and_sales.py tests/unit/test_api_endpoints.py`
  - result: passed
- `cd ui && npm run build`
  - result: passed

Note:

- full `tests/unit/test_api_endpoints.py` still contains 8 unrelated `tmp_path`-based tests that fail in this Windows sandbox because temp directory creation is denied; the company-ownership and API serializer coverage in that file passed
- `npm run build` regenerated `ui/public/llms-full.txt`, `ui/public/llms.txt`, and `ui/public/sitemap.xml`; those generated files are unrelated to this patch scope

## Files Changed In This Patch

- `core/models/agent.py`
- `core/models/workflow.py`
- `core/models/audit.py`
- `core/schemas/api.py`
- `api/v1/agents.py`
- `api/v1/audit.py`
- `api/v1/companies.py`
- `api/v1/workflows.py`
- `core/agents/packs/installer.py`
- `ui/src/pages/CompanyDashboard.tsx`
- `ui/src/pages/CompanyDetail.tsx`
- `tests/unit/test_agents_and_sales.py`
- `tests/unit/test_api_endpoints.py`

## Recommended Next Follow-On

1. Add backend support for role removal on `/companies/{company_id}/roles`.
2. Add regression coverage for company onboarding plus CA pack asset reconciliation.
3. Re-run live production validation after deploy, including company-scoped `/agents`, `/workflows`, and `/audit` queries.
4. Validate connector-backed CA workflows against sandbox or live external systems.
