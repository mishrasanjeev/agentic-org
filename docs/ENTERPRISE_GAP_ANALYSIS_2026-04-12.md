# Enterprise Gap Analysis (April 12, 2026)

## Scope
This review focused on the enterprise-control surfaces of the product end to end: authentication, authorization, tenancy isolation, billing, secrets handling, tool execution, migrations, health and operability, frontend session handling, observability, and release engineering.

## Executive Summary
- Overall posture: not enterprise-ready.
- Critical: 2
- High: 8
- Medium: 5
- Main blockers: missing server-side authorization on tenant control-plane APIs, cross-tenant billing trust boundaries, plaintext-effective connector secret handling, missing RLS on new tenant tables, and fragile deployment/runtime controls.

If this product is sold as enterprise-grade in its current state, the most likely failure modes are tenant-admin abuse by ordinary users, cross-tenant billing impact, silent corruption of live tool calls, credential exposure, worker outages, and unsafe production releases.

## Findings

### 1) Tenant-wide control-plane endpoints lack server-side admin authorization (**Critical, open**)
**What was found**

`get_current_tenant()` only returns tenant context and does not validate role or scopes: `api/deps.py:18-22`. `require_scope()` exists, but several tenant-wide mutation surfaces do not use it: `api/deps.py:32-38`.

Examples reviewed:

- Approval policy CRUD: `api/v1/approval_policies.py:70-171`
- Branding admin CRUD: `api/v1/branding.py:185-243`
- SSO config CRUD: `api/v1/sso.py:260-358`
- Org member list/invite/onboarding/deactivate: `api/v1/org.py:104-198`, `api/v1/org.py:272-329`
- Workflow variant mutation: `api/v1/workflow_variants.py:70-131`
- Invoice generation route is documented as admin-only but is not enforced: `api/v1/invoices.py:80-93`

**Risk**

Any authenticated tenant user can mutate enterprise-wide configuration, invite or deactivate users, change SSO posture, alter workflow behavior, and trigger tenant-level operations that should be admin-only.

**Recommended remediation**

- Apply router-level `require_scope("agenticorg:admin")` or finer-grained scopes to every tenant control-plane router.
- Add negative authorization tests for non-admin roles across each endpoint family.
- Introduce a single shared "tenant admin" dependency so future control-plane routes cannot forget the guard.

### 2) Billing APIs trust caller-supplied `tenant_id` instead of the authenticated tenant context (**Critical, open**)
**What was found**

Authenticated billing request models accept `tenant_id` from the caller: `api/v1/billing.py:34-63`.

Sensitive routes pass `body.tenant_id` directly to billing backends:

- Stripe subscribe: `api/v1/billing.py:80-104`
- India subscribe: `api/v1/billing.py:110-134`
- Customer portal: `api/v1/billing.py:291-309`
- Cancel subscription and downgrade: `api/v1/billing.py:382-412`

**Risk**

This is a BOLA and cross-tenant control failure. An authenticated user can attempt billing actions against another tenant by naming a different `tenant_id` in the request body. `/cancel` is the worst case because it mutates tenant billing state directly in Redis.

**Recommended remediation**

- Remove `tenant_id` from authenticated request bodies.
- Bind all billing actions to `request.state.tenant_id` or `Depends(get_current_tenant)`.
- Add abuse tests that prove a token from tenant A cannot affect tenant B billing state.

### 3) Runtime connector credentials are still effectively stored and loaded in plaintext (**High, open**)
**What was found**

The runtime gateway loads connector config from `Connector.auth_config`: `core/tool_gateway/gateway.py:215-239`. `Connector.auth_config` is plain JSONB: `core/models/connector.py:27-30`.

An encrypted credential path exists in a different model and table, but the gateway does not use it: `core/models/connector_config.py:43-48`, `core/database.py:314-339`.

**Risk**

A database compromise exposes downstream system credentials. That does not meet enterprise expectations for secret-at-rest handling and weakens any compliance or BYOK/CMEK positioning.

**Recommended remediation**

- Make encrypted credential storage the only runtime source of connector secrets.
- Separate secret material from connector metadata and decrypt only at execution time.
- Backfill existing plaintext secrets out of `connectors.auth_config` and add tests proving the field is empty in steady state.

### 4) The tool gateway anonymizes request payloads before live execution (**High, open**)
**What was found**

The gateway masks PII before it calls the connector: `core/tool_gateway/gateway.py:146-151`. The same masked payload is then used for audit hashing: `core/tool_gateway/gateway.py:163-174`.

**Risk**

Real business actions can execute with corrupted values such as redacted emails, account numbers, or identifiers. This creates silent failures, wrong-side effects, and low-trust automation.

**Recommended remediation**

- Use raw params for execution and a separately masked copy for logs and audit only.
- Add regression tests that verify audit redaction never mutates execution payloads.

### 5) Application startup performs schema mutation instead of enforcing migration discipline (**High, open**)
**What was found**

Every API start calls `init_db()`: `api/main.py:70-75`. `init_db()` executes substantial DDL and schema evolution logic at runtime: `core/database.py:75-339` and continues beyond that range.

**Risk**

Booting the service changes production schema outside an explicit change-management path. That increases rollout risk, causes drift from Alembic, and weakens rollback discipline.

**Recommended remediation**

- Restrict startup to connectivity and readiness checks only.
- Move all schema changes to versioned migrations executed separately in CI/CD.
- Fail startup when the expected schema revision is missing.

### 6) New v4.7 tenant tables were introduced without RLS or `FORCE ROW LEVEL SECURITY` (**High, open**)
**What was found**

`v4_7_0_sso_approvals_invoices.py` creates `sso_configs`, `approval_policies`, `approval_steps`, `invoices`, `tenant_branding`, and `workflow_variants` without enabling RLS or defining tenant policies: `migrations/versions/v4_7_0_sso_approvals_invoices.py:46-178`.

The previous enterprise migration explicitly states tenant tables should be RLS-protected and shows that pattern: `migrations/versions/v4_6_0_enterprise_readiness.py:15-17`, `migrations/versions/v4_6_0_enterprise_readiness.py:93-121`, `migrations/versions/v4_6_0_enterprise_readiness.py:159-214`.

**Risk**

Tenant isolation depends on application query filters instead of database enforcement. That is brittle and below enterprise multi-tenant standards.

**Recommended remediation**

- Add RLS plus `FORCE ROW LEVEL SECURITY` for every tenant-scoped v4.7 table.
- Add a migration review gate that rejects new tenant tables without an accompanying RLS policy.

### 7) Health endpoint is public, expensive, and wired into readiness gating (**High, open**)
**What was found**

Health endpoints are auth-exempt: `auth/grantex_middleware.py:56-72`, `auth/grantex_middleware.py:74-80`.

`/api/v1/health` checks DB, Redis, every registered connector, and returns connector details plus `env`: `api/v1/health.py:43-101`.

Kubernetes readiness uses `/api/v1/health`: `helm/templates/deployment.yaml:37-43`.

**Risk**

Anonymous callers can enumerate environment and connector posture. Readiness depends on third-party connector reachability, so external outages can block deployments or flap pods.

**Recommended remediation**

- Split health into `liveness`, `readiness`, and privileged diagnostics.
- Keep readiness limited to local critical dependencies only.
- Remove connector details and environment disclosure from public responses.

### 8) Worker deployments point at a module that does not exist (**High, open**)
**What was found**

Docker Compose worker command uses `celery -A core.worker`: `docker-compose.yml:77-95`. Helm worker command uses the same nonexistent module: `helm/templates/deployment.yaml:88-101`.

The actual Celery app is in `core/tasks/celery_app.py`: `core/tasks/celery_app.py:17-46`.

**Risk**

Clean worker deployments fail immediately. Scheduled reports, invoice generation, and other async jobs become unavailable.

**Recommended remediation**

- Point worker and beat entrypoints to `core.tasks.celery_app:app` or another real import path.
- Add a CI smoke test that boots the exact worker command used in deployment manifests.

### 9) Frontend session hydration is broken for SSO, and client-side role guards are weak (**High, open**)
**What was found**

`loginWithToken()` stores the token before user hydration and then calls `/auth/me`: `ui/src/contexts/AuthContext.tsx:94-110`.

Repo search only found `/auth/me` in the frontend; there is no matching backend route in `api/v1/auth.py`.

The failure path explicitly treats token presence as sufficient for protected routing: `ui/src/contexts/AuthContext.tsx:111-113`.

`ProtectedRoute` only enforces `allowedRoles` when `user` is non-null: `ui/src/components/ProtectedRoute.tsx:10-15`.

`SSOCallback` navigates to the dashboard on resolved `loginWithToken()` even if hydration never produced a user object: `ui/src/pages/SSOCallback.tsx:42-49`.

**Risk**

Users can land in privileged UI routes with an unhydrated session. This weakens client-side role segregation and hides backend authorization defects until an unsafe API call succeeds.

**Recommended remediation**

- Add a real `/auth/me` endpoint or decode and validate JWT claims client-side.
- Treat missing `user` as unauthorized on role-gated routes.
- Make the SSO callback fail closed when hydration does not produce a complete user session.

### 10) Production release pipeline ships before meaningful verification and bypasses approval-gate wiring (**High, open**)
**What was found**

`approval-gate` exists, but `deploy-production` only depends on `build`: `.github/workflows/deploy.yml:268-282`.

E2E tests run after production deploy: `.github/workflows/deploy.yml:186-215`.

Playwright and synthetic tests are non-blocking: `.github/workflows/deploy.yml:220-256`.

Production health gate accepts `healthy`, `degraded`, or `ok`: `.github/workflows/deploy.yml:324-338`.

**Risk**

Main or tag pushes can reach production without a wired human approval dependency, and broken user flows can ship because tests are advisory after the fact.

**Recommended remediation**

- Make production deployment depend on approval and successful pre-deploy test stages.
- Keep post-deploy smoke tests blocking for rollback decisions.
- Do not treat `degraded` as release success unless explicitly justified and documented.

### 11) Manual invoice generation is both under-authorized and not tenant-scoped (**High, open**)
**What was found**

The route comment says "admin only" but the handler only depends on tenant presence: `api/v1/invoices.py:80-93`.

The handler calls `generate_invoices_for_period()` with no tenant filter: `api/v1/invoices.py:89-93`.

**Risk**

Any authenticated user can trigger a global invoice generation run. That is both an authorization failure and an operational blast-radius problem.

**Recommended remediation**

- Require admin scope for the route.
- Pass explicit tenant scope to the invoice generator or move the bulk operation to an internal admin-only job path.
- Add tests proving non-admins cannot trigger invoice generation and that one tenant cannot trigger work for all tenants.

### 12) Auth throttling and token revocation still rely on per-process memory (**Medium, open**)
**What was found**

Middleware stores failed attempts and IP blocks in memory: `auth/middleware.py:14-19`, `auth/middleware.py:51-57`, `auth/middleware.py:95-106`.

Signup and login throttles use module-level dictionaries: `api/v1/auth.py:34-36`, `api/v1/auth.py:185-187`.

Token blacklist keeps in-memory state as the primary path and Redis as best-effort: `auth/jwt.py:23-29`, `auth/jwt.py:56-79`, `auth/jwt.py:82-97`.

**Risk**

Controls behave inconsistently in multi-replica deployments and disappear on restart. That is weak evidence for enterprise auth-control enforcement.

**Recommended remediation**

- Move all rate-limit and revocation state to Redis with atomic TTL-backed keys.
- Keep local memory only as a performance cache, never as the source of truth.

### 13) Async request paths call a synchronous Redis client (**Medium, open**)
**What was found**

`_get_redis()` returns a synchronous Redis client: `core/billing/usage_tracker.py:32-36`.

SSO uses that client in async handlers for state storage, lookup, and delete: `api/v1/sso.py:43-49`, `api/v1/sso.py:139-171`.

Billing cancellation also uses the same sync client in a request path: `api/v1/billing.py:390-404`.

**Risk**

These calls block the event loop under load and reduce concurrency on login and billing paths.

**Recommended remediation**

- Use `redis.asyncio` throughout async request handlers.
- Constrain sync Redis usage to background jobs or explicitly isolated worker threads.

### 14) Metrics design leaks tenant identifiers and invites high-cardinality failures (**Medium, open**)
**What was found**

Most Prometheus metrics include raw `tenant`, and several add `agent_id`, `tool_name`, or reference IDs as labels: `observability/metrics.py:5-37`.

**Risk**

This can explode cardinality, destabilize Prometheus storage and query performance, and leak customer identifiers into observability systems.

**Recommended remediation**

- Remove raw tenant IDs from metric labels.
- Keep high-cardinality identifiers in logs and traces, not metrics.
- Add observability review criteria for multi-tenant telemetry design.

### 15) CORS production fallback is permissive and unsafe or broken (**Medium, open**)
**What was found**

In production, empty `cors_allowed_origins` falls back to `["*"]` with `allow_credentials=True`: `api/main.py:126-140`.

**Risk**

This is not an enterprise-safe default. Depending on browser behavior it can either overexpose cross-origin access or break authenticated browser flows in a non-obvious way.

**Recommended remediation**

- Fail startup in production if `cors_allowed_origins` is unset.
- Require explicit origin allowlists per environment.

### 16) Documentation, packaging, and runtime versioning are materially out of sync (**Medium, open**)
**What was found**

README shows version `4.3.0`: `README.md:13`.

Package and app version are `4.0.0`: `pyproject.toml:8-14`, `api/main.py:114-121`.

Project metadata targets Python 3.12: `pyproject.toml:20`, `pyproject.toml:95`, `pyproject.toml:116`.

Dockerfile builds and runs on Python 3.14: `Dockerfile:1`, `Dockerfile:16`, `Dockerfile:20`.

**Risk**

This undermines release discipline, supportability, and reproducibility. Enterprise buyers will read this as a governance problem, not only a docs problem.

**Recommended remediation**

- Use a single version source and enforce it in CI.
- Align the Docker runtime with the tested and tooled Python version.
- Treat docs and runtime-version drift as release-blocking.

## Enterprise-grade gap themes
- Security and tenancy: server-side authorization and DB-level isolation are inconsistent.
- Secrets and compliance: credential handling does not match enterprise claims.
- Reliability and operations: health checks, workers, and startup schema mutation create fragile production behavior.
- Delivery governance: release gates are weak and versioning is inconsistent.
- Observability: current metric labels are not safe for enterprise multi-tenant scale.

## Must-fix before enterprise rollout
1. Add server-side admin authorization to every tenant-control surface and close billing BOLA paths.
2. Enforce RLS on all new tenant tables and stop runtime schema mutation.
3. Move connector secrets to encrypted or vault-backed runtime retrieval only.
4. Fix worker entrypoints and split health, readiness, and diagnostics.
5. Repair SSO session hydration and make client auth fail closed.
6. Harden CI/CD so approval and blocking tests happen before production deploy.
7. Replace in-memory auth state and sync Redis-in-async patterns.
8. Reduce metric cardinality and remove tenant identifiers from labels.

## Validation performed
- Focused review across backend runtime, control-plane APIs, migrations, deployment manifests, and frontend auth flow.
- `python -m pytest -q --no-cov tests\\unit\\test_auth.py tests\\unit\\test_company_model.py tests\\security\\test_auth_security.py` -> `64 passed`.
- `ruff check api auth core connectors workflows --no-cache` -> passed.
- `python -m bandit -r api auth core -x migrations,tests -f json` -> 32 low-severity findings, mostly low-signal items and false positives; no blocker stronger than the issues above.
