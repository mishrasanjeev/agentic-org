# Enterprise Gap Analysis Recheck

Date: 2026-04-12
Scope: full repo re-audit after the previously reported gaps in `docs/ENTERPRISE_GAP_ANALYSIS_2026-04-12.md` were said to be implemented
Verdict: improved materially, but still not enterprise-ready

## Executive Summary

The previous review drove several real fixes. Tenant binding in billing is better, multiple admin routers were corrected, the health surface is safer, SSO session hydration now exists, and the worker/runtime issues are cleaner.

The product is still short of enterprise grade because the remaining gaps are concentrated in high-impact control planes:

1. non-admin users can still mutate tenant-wide configuration and, in one path, effectively create new admins
2. connector secret hardening is only partially wired, so plaintext-style credential storage is still the active write path
3. schema and RLS guarantees still depend on `init_db()` instead of migrations
4. some billing and release paths still trust the wrong boundary

## Findings

### 1. Critical: control-plane authorization is still inconsistent, and `/org/invite` is a privilege-escalation path

**Evidence**

- [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:24) defines the organization router without `require_tenant_admin`.
- [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:134) lets any authenticated tenant principal call `POST /org/invite`.
- [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:165) persists `role=body.role` directly, with no role validation or admin-only check.
- [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:95), [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:246), and [api/v1/org.py](/C:/Users/mishr/agentic-org/api/v1/org.py:289) expose member listing, onboarding mutation, and member deactivation to any authenticated tenant user.
- [api/v1/connectors.py](/C:/Users/mishr/agentic-org/api/v1/connectors.py:17), [api/v1/connectors.py](/C:/Users/mishr/agentic-org/api/v1/connectors.py:165), and [api/v1/connectors.py](/C:/Users/mishr/agentic-org/api/v1/connectors.py:214) allow connector create/update with only tenant authentication.
- [api/v1/config.py](/C:/Users/mishr/agentic-org/api/v1/config.py:15) and [api/v1/config.py](/C:/Users/mishr/agentic-org/api/v1/config.py:39) allow any authenticated tenant user to change fleet limits.
- [api/v1/report_schedules.py](/C:/Users/mishr/agentic-org/api/v1/report_schedules.py:133), [api/v1/report_schedules.py](/C:/Users/mishr/agentic-org/api/v1/report_schedules.py:174), [api/v1/report_schedules.py](/C:/Users/mishr/agentic-org/api/v1/report_schedules.py:239), and [api/v1/report_schedules.py](/C:/Users/mishr/agentic-org/api/v1/report_schedules.py:261) allow schedule creation, delivery-target mutation, deletion, and run-now without admin scope.
- [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py:20), [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py:95), [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py:219), [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py:262), and [api/v1/workflows.py](/C:/Users/mishr/agentic-org/api/v1/workflows.py:599) leave workflow generation, deployment, create, delete, and replan-config mutation as auth-only operations.

**Impact**

A normal tenant user, or any bearer principal that only needs tenant auth, can still:

- invite a new user with `role="admin"`
- enumerate all tenant users
- deactivate other users
- raise tenant fleet limits
- redirect or replace tenant connectors
- create or alter scheduled report delivery targets
- create or mutate shared workflows

For enterprise environments, this is a control-plane break, not just a missing polish item.

**Recommendation**

Apply `require_tenant_admin` to every tenant control-plane mutation route unless there is a deliberate lower-privilege role model. If some of these actions are intentionally delegated, define explicit scopes per action and add negative tests proving non-admins get `403`.

### 2. High: connector secret hardening is only partially implemented, and plaintext-style credential storage remains the write path

**Evidence**

- [core/schemas/api.py](/C:/Users/mishr/agentic-org/core/schemas/api.py:172) and [core/schemas/api.py](/C:/Users/mishr/agentic-org/core/schemas/api.py:184) still expose `auth_config` directly in connector create/update APIs.
- [api/v1/connectors.py](/C:/Users/mishr/agentic-org/api/v1/connectors.py:172) writes `auth_config=body.auth_config` into the legacy connector row.
- [api/v1/connectors.py](/C:/Users/mishr/agentic-org/api/v1/connectors.py:228) updates arbitrary connector fields, including `auth_config`.
- [core/models/connector.py](/C:/Users/mishr/agentic-org/core/models/connector.py:27) still stores `auth_config` as JSONB on the main `connectors` table.
- [core/tool_gateway/gateway.py](/C:/Users/mishr/agentic-org/core/tool_gateway/gateway.py:216) says it prefers encrypted config, but [core/tool_gateway/gateway.py](/C:/Users/mishr/agentic-org/core/tool_gateway/gateway.py:242) merges `credentials_encrypted` directly into runtime config without decrypting secret fields.
- [core/tool_gateway/gateway.py](/C:/Users/mishr/agentic-org/core/tool_gateway/gateway.py:249) falls back to plaintext `Connector.auth_config`.
- [core/crypto/tenant_secrets.py](/C:/Users/mishr/agentic-org/core/crypto/tenant_secrets.py:62) and [core/crypto/tenant_secrets.py](/C:/Users/mishr/agentic-org/core/crypto/tenant_secrets.py:79) provide tenant-aware encryption helpers, but the connector CRUD/runtime path does not call them.

**Impact**

The repo now has encryption primitives, but connectors do not consistently use them. In practice, connector credentials can still be persisted in DB JSON and treated as ready-to-use runtime config. That is below the bar for enterprise secrets handling.

**Recommendation**

Make `Connector.auth_config` non-secret metadata only, or retire it entirely for credentials. Encrypt per-secret on write through tenant-aware secret helpers, decrypt at runtime in the gateway, and instrument a migration plan plus telemetry for any remaining plaintext rows.

### 3. High: schema correctness and tenant isolation still depend on `init_db()` instead of Alembic

**Evidence**

- [api/main.py](/C:/Users/mishr/agentic-org/api/main.py:71) still calls `init_db()` during application startup.
- [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:314) creates `connector_configs` at runtime; repo search found no Alembic migration for that table.
- [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:703) adds the v4.7 RLS policy set at runtime.
- [migrations/versions/v4_7_0_sso_approvals_invoices.py](/C:/Users/mishr/agentic-org/migrations/versions/v4_7_0_sso_approvals_invoices.py:48) creates `sso_configs`, `approval_policies`, `invoices`, `tenant_branding`, and `workflow_variants`, but it does not create the corresponding RLS policies now added only in runtime DDL.

**Impact**

Fresh environments can diverge depending on whether they were bootstrapped by migrations or by app startup. That is a change-management and disaster-recovery failure mode. Enterprise deployments need migrations to be the source of truth for schema and RLS.

**Recommendation**

Move the remaining runtime DDL into Alembic, including `connector_configs` and the v4.7 RLS policies, then reduce `init_db()` to connectivity or read-only validation.

### 4. High: Stripe subscription cancellation still trusts caller-supplied `subscription_id`

**Evidence**

- [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:375) accepts `subscription_id` from the request body.
- [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:402) forwards that ID to the Stripe cancel helper.
- [core/billing/stripe_client.py](/C:/Users/mishr/agentic-org/core/billing/stripe_client.py:340) deletes the Stripe subscription by ID alone, with no tenant ownership check.

**Impact**

The earlier tenant-binding fix removed caller-supplied `tenant_id`, but this path still trusts caller-supplied object identity. If a Stripe subscription ID leaks, one tenant can cancel another tenant's subscription.

**Recommendation**

Resolve the active subscription server-side from tenant-owned billing state and ignore caller-supplied subscription IDs.

### 5. Medium: duplicate `GET /api/v1/billing/invoices` routes create authorization and data-source ambiguity

**Evidence**

- [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:334) defines `GET /billing/invoices` against `billing_invoices`.
- [api/v1/invoices.py](/C:/Users/mishr/agentic-org/api/v1/invoices.py:25) defines an admin-only router, and [api/v1/invoices.py](/C:/Users/mishr/agentic-org/api/v1/invoices.py:45) also defines `GET /billing/invoices`.
- [api/main.py](/C:/Users/mishr/agentic-org/api/main.py:191) mounts `billing.router` before [api/main.py](/C:/Users/mishr/agentic-org/api/main.py:201) mounting `invoices.router`.
- Runtime route inspection confirmed that the first registered handler for `GET /api/v1/billing/invoices` is `api.v1.billing.list_invoices`, with the admin router's collection handler registered later on the same path.

**Impact**

This leaves the collection endpoint behavior ambiguous:

- the active handler is the legacy billing view
- the newer admin-scoped invoice collection handler is effectively shadowed
- the repo now has two competing invoice data models and two access policies

That is not acceptable for enterprise billing APIs.

**Recommendation**

Collapse invoice history to one canonical route and one storage model, then enforce an explicit finance/admin policy on that route.

### 6. Medium: production release gates are still too permissive for enterprise rollout

**Evidence**

- [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:280) does not require `approval-gate` before `deploy-production`.
- [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:186) runs E2E and Playwright only after production deploy.
- [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:215) and [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:256) mark those checks `continue-on-error`.
- [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:324) still accepts `"healthy|degraded|ok"` as a passing production health gate.

**Impact**

The pipeline is better than before, but it can still ship to production without enforced human approval on tagged releases, without blocking end-to-end verification, and while the system is degraded.

**Recommendation**

Require `approval-gate` for production releases, move blocking smoke/E2E checks earlier or onto a canary gate, and fail the prod gate unless the service is truly healthy or an explicit waiver exists.

### 7. Medium: auth and request-path resilience controls are still pod-local or blocking in async paths

**Evidence**

- [auth/grantex_middleware.py](/C:/Users/mishr/agentic-org/auth/grantex_middleware.py:27) keeps failed-attempt tracking in module-level dicts.
- [api/v1/auth.py](/C:/Users/mishr/agentic-org/api/v1/auth.py:34), [api/v1/auth.py](/C:/Users/mishr/agentic-org/api/v1/auth.py:185), and [api/v1/auth.py](/C:/Users/mishr/agentic-org/api/v1/auth.py:359) implement signup/login/reset rate limits in memory.
- [auth/jwt.py](/C:/Users/mishr/agentic-org/auth/jwt.py:26) keeps blacklist state in memory and only best-effort mirrors it to Redis.
- [core/billing/usage_tracker.py](/C:/Users/mishr/agentic-org/core/billing/usage_tracker.py:32) returns a synchronous Redis client.
- [api/v1/sso.py](/C:/Users/mishr/agentic-org/api/v1/sso.py:43) and [api/v1/sso.py](/C:/Users/mishr/agentic-org/api/v1/sso.py:139) use that sync client inside async request handlers.
- [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:385) does the same in the cancel flow.

**Impact**

In a multi-pod deployment, these controls can be bypassed across restarts or pod boundaries, and the sync Redis calls can block the event loop under load.

**Recommendation**

Move auth/rate-limit state to an async shared store and remove synchronous Redis clients from async request handlers.

## Verified Fixes From The Previous Review

The following prior findings appear fixed in the current codebase:

- approval policies are now admin-gated: [api/v1/approval_policies.py](/C:/Users/mishr/agentic-org/api/v1/approval_policies.py:21)
- branding admin routes are now admin-gated: [api/v1/branding.py](/C:/Users/mishr/agentic-org/api/v1/branding.py:32)
- SSO admin routes are now admin-gated: [api/v1/sso.py](/C:/Users/mishr/agentic-org/api/v1/sso.py:37)
- workflow variants are now admin-gated: [api/v1/workflow_variants.py](/C:/Users/mishr/agentic-org/api/v1/workflow_variants.py:17)
- billing endpoints no longer trust caller-supplied `tenant_id`: [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:34), [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:77), [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:105), [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:284), [api/v1/billing.py](/C:/Users/mishr/agentic-org/api/v1/billing.py:375)
- the tool gateway now executes with raw params and masks only for audit: [core/tool_gateway/gateway.py](/C:/Users/mishr/agentic-org/core/tool_gateway/gateway.py:146)
- the health surface is split into safe readiness and admin diagnostics: [api/v1/health.py](/C:/Users/mishr/agentic-org/api/v1/health.py:44), [api/v1/health.py](/C:/Users/mishr/agentic-org/api/v1/health.py:83)
- worker startup commands were corrected: [docker-compose.yml](/C:/Users/mishr/agentic-org/docker-compose.yml:94), [helm/templates/deployment.yaml](/C:/Users/mishr/agentic-org/helm/templates/deployment.yaml:91)
- high-cardinality tenant/tool labels were removed from metrics: [observability/metrics.py](/C:/Users/mishr/agentic-org/observability/metrics.py:1)
- `/auth/me` now exists and the SSO callback fails closed if the user cannot be hydrated: [api/v1/auth.py](/C:/Users/mishr/agentic-org/api/v1/auth.py:451), [ui/src/contexts/AuthContext.tsx](/C:/Users/mishr/agentic-org/ui/src/contexts/AuthContext.tsx:94), [ui/src/pages/SSOCallback.tsx](/C:/Users/mishr/agentic-org/ui/src/pages/SSOCallback.tsx:42), [ui/src/components/ProtectedRoute.tsx](/C:/Users/mishr/agentic-org/ui/src/components/ProtectedRoute.tsx:9)
- runtime RLS for the new v4.7 tables has been added, but only in `init_db()`: [core/database.py](/C:/Users/mishr/agentic-org/core/database.py:703)
- production deploy now waits on build, integration tests, and security scan: [.github/workflows/deploy.yml](/C:/Users/mishr/agentic-org/.github/workflows/deploy.yml:280)

## Validation Performed

- code audit across `api/`, `auth/`, `core/`, `migrations/`, `ui/`, `helm/`, and `.github/workflows/`
- route registration inspection on the live FastAPI app object to confirm duplicate `GET /api/v1/billing/invoices` ordering
- targeted test reruns without coverage:
  - `pytest --no-cov tests/unit/test_api_endpoints.py -q -k "register_connector_happy or create_workflow_happy or update_fleet_limits_happy"` -> `3 passed`
  - `pytest --no-cov tests/unit/test_auth_and_core.py -q -k "list_members_returns_users or deactivate_member_not_found"` -> `2 passed`
  - `pytest --no-cov tests/unit/test_envelope_encryption.py -q` -> `9 passed`

## Testing Gap

The current automated suite still normalizes several control-plane mutations as ordinary authenticated-user behavior instead of proving admin-only enforcement:

- [tests/integration/test_api_integration.py](/C:/Users/mishr/agentic-org/tests/integration/test_api_integration.py:214) treats workflow creation as a standard authenticated path
- [tests/integration/test_api_integration.py](/C:/Users/mishr/agentic-org/tests/integration/test_api_integration.py:348) treats connector registration as a standard authenticated path
- [tests/e2e_full_production_test.py](/C:/Users/mishr/agentic-org/tests/e2e_full_production_test.py:147) exercises org mutation with the happy path only
- [tests/unit/test_api_endpoints.py](/C:/Users/mishr/agentic-org/tests/unit/test_api_endpoints.py:514) and [tests/unit/test_api_endpoints.py](/C:/Users/mishr/agentic-org/tests/unit/test_api_endpoints.py:1698) directly validate non-admin control-plane helpers without any `403` assertions

That means the remaining authorization regressions are unlikely to be caught automatically until explicit negative tests are added.

## Recommended Remediation Order

1. Lock down `org`, `connectors`, `config`, `report-schedules`, and workflow mutation endpoints with admin or explicit scoped RBAC.
2. Remove plaintext connector secret writes and wire tenant-aware encryption end-to-end.
3. Move all remaining runtime DDL and RLS work into Alembic, including `connector_configs`.
4. Bind Stripe cancellation to tenant-owned server-side billing state.
5. Collapse duplicate invoice routes into one canonical API.
6. Tighten release gates and add negative authorization tests for every tenant control-plane mutation path.
