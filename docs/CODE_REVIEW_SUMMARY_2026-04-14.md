# Code Review Summary

Date: April 14, 2026
Repository: `agentic-org`
Scope: full read-only code review of current `main`, with test/build validation and flow-risk analysis

## Executive Summary

I did not make any code changes during this review.

The current codebase is not yet at an enterprise-clean state. The strongest remaining issues are:

- connector setup/test flow is internally inconsistent
- schema lifecycle still depends on runtime startup DDL instead of real migrations
- deployment can still pass while end-to-end regressions are non-blocking
- auth session state is inconsistent across login paths
- critical frontend regression coverage is currently red and partially stale

There are also positives:

- targeted enterprise authz gaps from prior reviews appear materially improved
- the frontend production build passes
- most backend unit/security coverage is green

The main blocker is not lack of features. It is lack of consistent safety around setup, rollout, and regression protection.

## Review Method

The review was performed as a read-only audit plus test/build execution.

Commands run:

- `python -m pytest -q --no-cov -p no:cacheprovider tests/unit tests/connector_harness tests/security`
- `cd ui; npm run build`
- `cd ui; npm run test`

Additional targeted reruns were executed on selected failing backend suites and individual tests to distinguish code defects from local environment failures.

## Verification Snapshot

### Backend

Command:

```powershell
python -m pytest -q --no-cov -p no:cacheprovider tests/unit tests/connector_harness tests/security
```

Result:

- `2855 passed`
- `15 failed`
- `31 skipped`
- `9 errors`

Important context:

- A meaningful share of backend errors were caused by Windows temp-path permission failures during test file creation.
- Those environment-specific failures should not all be treated as product defects.
- Some tests that failed in the large sweep passed in isolation, which suggests shared-state leakage or order sensitivity in parts of the suite.

### Frontend Build

Command:

```powershell
cd ui; npm run build
```

Result:

- passed

### Frontend Unit Tests

Command:

```powershell
cd ui; npm run test
```

Result:

- `54 passed`
- `41 failed`
- `19 errors`

This is review-significant. The frontend suite is not healthy enough to protect key dashboard and query flows.

## Findings

### 1. High: Connector create/update/test flow is still inconsistent

New connector secrets are written to encrypted storage in `connector_configs.credentials_encrypted`, but connector testing still reads only the legacy plaintext path.

Evidence:

- New connector writes store secrets encrypted in [api/v1/connectors.py:176](../api/v1/connectors.py) through [api/v1/connectors.py:225](../api/v1/connectors.py)
- Connector updates also write encrypted secrets in [api/v1/connectors.py:277](../api/v1/connectors.py) through [api/v1/connectors.py:305](../api/v1/connectors.py)
- Live test path still reads `connector.auth_config` only in [api/v1/connectors.py:340](../api/v1/connectors.py) through [api/v1/connectors.py:373](../api/v1/connectors.py)
- Runtime gateway still falls back to plaintext `auth_config` in [core/tool_gateway/gateway.py:216](../core/tool_gateway/gateway.py) through [core/tool_gateway/gateway.py:264](../core/tool_gateway/gateway.py)
- The legacy plaintext-capable fields still exist on the model in [core/models/connector.py:27](../core/models/connector.py) through [core/models/connector.py:29](../core/models/connector.py)

Why this matters:

- A connector can save successfully but fail the "test connector" flow.
- Secret-handling policy is not consistent across write, read, and execution paths.
- This is a direct end-to-end break in a control-plane setup flow.

Recommendation:

- Make encrypted `ConnectorConfig` the only read path for connector test and runtime execution.
- Remove or strictly gate plaintext fallback.
- Add an end-to-end test that creates a connector using encrypted credentials and then validates `test`, `health`, and execution against the same stored config.

### 2. High: Schema authority still lives in runtime startup instead of real migrations

`init_db()` continues to apply schema and RLS changes during application startup.

Evidence:

- Runtime DDL starts in [core/database.py:75](../core/database.py) and continues through a large sequence of `ALTER TABLE` and `CREATE TABLE IF NOT EXISTS` blocks
- Company-model startup DDL is present in [core/database.py:107](../core/database.py) through [core/database.py:139](../core/database.py)
- Runtime RLS enablement for v4.7 tables is still in [core/database.py:721](../core/database.py) through [core/database.py:739](../core/database.py)
- The repo explicitly documents that runtime startup is the real schema authority in [migrations/README.md:3](../migrations/README.md) through [migrations/README.md:13](../migrations/README.md)
- The same README says the migration files are not executed by the running app in [migrations/README.md:10](../migrations/README.md) through [migrations/README.md:13](../migrations/README.md)
- There is no working Alembic runtime setup such as `alembic.ini` or `alembic/env.py`

Why this matters:

- Schema changes are coupled to pod boot, which makes deployment riskier.
- Rollout failures can become application-start failures.
- It is harder to reason about versioned schema state across environments.
- Enterprise release governance is weaker without a real migration gate.

Recommendation:

- Move schema authority to a real migration system and execute migrations before rollout.
- Reduce `init_db()` to connectivity checks and strictly safe bootstrap logic only.
- Add CI enforcement that model/schema changes must include a real migration.

### 3. High: Deployment can still pass while E2E regressions are non-blocking

The production pipeline runs post-deploy E2E, but the key suites are configured as non-blocking.

Evidence:

- Python E2E is non-blocking in [deploy.yml:219](../.github/workflows/deploy.yml) through [deploy.yml:225](../.github/workflows/deploy.yml)
- Synthetic quality tests are non-blocking in [deploy.yml:226](../.github/workflows/deploy.yml) through [deploy.yml:231](../.github/workflows/deploy.yml)
- E2E token acquisition is non-blocking in [deploy.yml:233](../.github/workflows/deploy.yml) through [deploy.yml:255](../.github/workflows/deploy.yml)
- Playwright regression is non-blocking in [deploy.yml:260](../.github/workflows/deploy.yml) through [deploy.yml:266](../.github/workflows/deploy.yml)

Why this matters:

- A broken user journey can still deploy successfully.
- The repo can appear green while real browser/API regressions exist.
- This weakens trust in the release process, especially for enterprise customers.

Recommendation:

- Make at least one authenticated production-safe E2E suite blocking for `main`.
- Make auth-token acquisition fail closed for post-deploy E2E.
- Split synthetic/LLM-quality checks from deterministic product-path checks and keep only the deterministic checks blocking.

### 4. High: Production rollout diagnostics still dump high-sensitivity Kubernetes state to logs

The deploy workflow still emits full `kubectl describe` output for deployments and pods on rollout failure.

Evidence:

- [deploy.yml:340](../.github/workflows/deploy.yml) dumps deployment descriptions
- [deploy.yml:346](../.github/workflows/deploy.yml) dumps pod descriptions
- pod logs are also dumped in the same rollout-debug block

Why this matters:

- `kubectl describe` output often includes image details, environment wiring, secret references, mounted config, scheduling detail, and operational metadata.
- This is not ideal for enterprise production pipelines and expands log exposure unnecessarily.

Recommendation:

- Replace broad `describe` dumps with targeted safe diagnostics.
- Keep logs limited to readiness, events, container states, restart counts, and bounded application logs.

### 5. Medium: Auth session state is inconsistent between `/auth/login` and `/auth/me`

The same user can receive different onboarding state depending on the session hydration path.

Evidence:

- Signup returns tenant-backed onboarding state in [api/v1/auth.py:170](../api/v1/auth.py) through [api/v1/auth.py:181](../api/v1/auth.py)
- Login also returns tenant-backed onboarding state in [api/v1/auth.py:252](../api/v1/auth.py)
- `/auth/me` hardcodes `"onboarding_complete": True` in [api/v1/auth.py:451](../api/v1/auth.py) through [api/v1/auth.py:487](../api/v1/auth.py)
- Frontend login path trusts login payload in [ui/src/contexts/AuthContext.tsx:36](../ui/src/contexts/AuthContext.tsx) through [ui/src/contexts/AuthContext.tsx:56](../ui/src/contexts/AuthContext.tsx)
- Frontend token hydration trusts `/auth/me` in [ui/src/contexts/AuthContext.tsx:94](../ui/src/contexts/AuthContext.tsx) through [ui/src/contexts/AuthContext.tsx:114](../ui/src/contexts/AuthContext.tsx)

Why this matters:

- Onboarding routing can differ depending on whether the session came from password login, Google login, or token hydration.
- This creates inconsistent UI behavior and weakens confidence in session truth.

Recommendation:

- Return onboarding state from a single source of truth across all auth endpoints.
- Add integration coverage for login, SSO callback hydration, and page refresh/session restore.

### 6. Medium: Login throttling is process-local and weak under scale

Rate limiting for login attempts is stored in an in-memory Python dictionary.

Evidence:

- [api/v1/auth.py:185](../api/v1/auth.py) through [api/v1/auth.py:200](../api/v1/auth.py)

Why this matters:

- Multi-instance deployments do not share this state.
- Attackers can bypass limits by spreading attempts across pods.
- This is below enterprise-grade auth hardening.

Recommendation:

- Move auth rate limiting to Redis or an equivalent shared store.
- Add tenant-aware and account-aware throttling in addition to IP-based throttling.

### 7. Medium: Frontend dashboard regression coverage is red and partly stale

The CFO and CMO dashboard tests still mock an older KPI payload shape, while the current implementation expects a flattened basic-metrics contract.

Evidence:

- Current CFO component expects `agent_count`, `total_tasks_30d`, `success_rate`, `hitl_interventions`, and `total_cost_usd` in [ui/src/pages/CFODashboard.tsx:29](../ui/src/pages/CFODashboard.tsx) through [ui/src/pages/CFODashboard.tsx:120](../ui/src/pages/CFODashboard.tsx)
- Current CMO component expects the same flattened structure in [ui/src/pages/CMODashboard.tsx:29](../ui/src/pages/CMODashboard.tsx) through [ui/src/pages/CMODashboard.tsx:124](../ui/src/pages/CMODashboard.tsx)
- The KPI API computes and returns the flattened basic metrics in [api/v1/kpis.py:38](../api/v1/kpis.py) through [api/v1/kpis.py:132](../api/v1/kpis.py)
- CFO tests still mock legacy finance-specific fields in [ui/src/__tests__/CFODashboard.test.tsx:39](../ui/src/__tests__/CFODashboard.test.tsx) through [ui/src/__tests__/CFODashboard.test.tsx:99](../ui/src/__tests__/CFODashboard.test.tsx)
- CMO tests still mock legacy marketing-specific fields in [ui/src/__tests__/CMODashboard.test.tsx:40](../ui/src/__tests__/CMODashboard.test.tsx) through [ui/src/__tests__/CMODashboard.test.tsx:87](../ui/src/__tests__/CMODashboard.test.tsx)

Why this matters:

- The regression suite is not describing the current product contract.
- Failures are noisy and reduce confidence in test signal.
- A green build today would not reliably prove those dashboards are safe.

Recommendation:

- Rewrite the dashboard tests to the current API contract.
- Add one contract-level test at the API boundary and one rendering test per dashboard.

### 8. Medium: CFODashboard is not defensive against partial KPI payloads

The current component directly dereferences numeric fields without fallback guards.

Evidence:

- [ui/src/pages/CFODashboard.tsx:99](../ui/src/pages/CFODashboard.tsx) through [ui/src/pages/CFODashboard.tsx:120](../ui/src/pages/CFODashboard.tsx)
- Specifically `data.agent_count.toLocaleString()` in [ui/src/pages/CFODashboard.tsx:105](../ui/src/pages/CFODashboard.tsx)

Observed during test run:

- frontend test failure included `TypeError: Cannot read properties of undefined (reading 'toLocaleString')`

Why this matters:

- If API shape drifts or a partial payload is returned, the page can hard-crash instead of degrading safely.

Recommendation:

- Apply safe defaults before formatting display values.
- Treat missing KPI fields as recoverable UI conditions.

### 9. Low to Medium: NLQueryBar tests are partially stale relative to current UX

The component currently includes a `Dismiss` action and current dropdown behavior, but this area has had drift and needs a reliable contract definition.

Evidence:

- Current component behavior is in [ui/src/components/NLQueryBar.tsx:174](../ui/src/components/NLQueryBar.tsx) through [ui/src/components/NLQueryBar.tsx:266](../ui/src/components/NLQueryBar.tsx)
- Tests cover dropdown behavior in [ui/src/__tests__/NLQueryBar.test.tsx:263](../ui/src/__tests__/NLQueryBar.test.tsx) through [ui/src/__tests__/NLQueryBar.test.tsx:327](../ui/src/__tests__/NLQueryBar.test.tsx)

Why this matters:

- This is not the highest-risk product area, but current UI-query behavior should still have stable regression expectations.

Recommendation:

- Keep NLQueryBar tests aligned with the current shortcut, navigation, dropdown, and fallback UX.

## Flows Reviewed

The following areas were reviewed statically and, where applicable, through test/build validation:

- auth and session hydration
- connector registration, update, and test flows
- KPI/dashboard API and frontend contracts
- NL query entry flow
- schema bootstrap and migration posture
- deployment pipeline behavior
- existing enterprise authorization controls in workflows, org, billing, and packs

## Areas That Look Improved

These previously risky areas appear materially better in the current code snapshot:

- workflow replan config now appears admin-gated
- org invite logic is constrained by admin gating and role allowlists
- billing cancellation no longer appears to trust caller-supplied subscription identifiers
- packs router is admin-gated

These were not the main open concerns in this review.

## Bottom Line

Current `main` is functional in many areas, but it is not yet enterprise-clean.

If the requirement is "all major flows are complete, safe, and protected from breakage," the current gaps are:

- connector secret flow consistency
- migration/deploy architecture
- blocking post-deploy E2E enforcement
- session state consistency
- healthy, current dashboard regression coverage

Until those are closed, I would not describe the product as fully enterprise-grade from an operational reliability perspective.
