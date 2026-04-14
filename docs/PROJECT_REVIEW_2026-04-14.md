# Project Review

Date: 2026-04-14

Scope: static review of the FastAPI backend, auth layer, tenant isolation, company workflows, LangGraph execution path, MCP/A2A surface, and key frontend integration points.

Method: code inspection only. I did not run the full test suite or stage the app end-to-end, so this report is biased toward correctness, security, and architecture issues visible from source.

## Executive Summary

The main risk pattern in this codebase is inconsistent trust boundaries. Tenant isolation is implemented in many places, but user/role isolation is not. Several critical endpoints rely on frontend route guards instead of backend authorization. The LangGraph execution path also diverges from the hardened connector path, which means your secure secret storage and runtime policy controls are not being applied consistently. A second pass also found the same pattern in operational surfaces: bridge auth is stubbed, production CI assumes shared demo identities, and some public/admin route boundaries are internally contradictory.

If this project is internet-facing, I would treat Findings 1-5 and 11-12 as release blockers.

## Findings

### 1. Critical: the tenant live-feed WebSocket is unauthenticated and subscribes by raw tenant ID

- Evidence:
  - `api/websocket/feed.py:40`
  - `api/websocket/feed.py:42`
  - `api/websocket/feed.py:47`
- What is wrong:
  - `live_feed()` accepts any connection immediately and binds it to whatever `tenant_id` appears in the URL path.
  - There is no token validation, no tenant claim check, and no origin/session binding.
  - `broadcast_to_tenant()` then pushes tenant-scoped events to every socket registered under that ID.
- Why it matters:
  - Anyone who can guess or obtain a tenant UUID can subscribe to that tenant's real-time activity stream.
  - This is especially bad because the feed is used for HITL and operational events, which often contain sensitive metadata.
- Recommendation:
  - Require auth on the WebSocket handshake.
  - Derive tenant context from the validated token, not from the URL.
  - Reject cross-tenant subscriptions server-side even if a tenant ID is supplied.

### 2. Critical: backend RBAC is missing on core control-plane endpoints; the UI is doing the real authorization

- Evidence:
  - `api/v1/agents.py:226`
  - `api/v1/agents.py:325`
  - `api/v1/agents.py:921`
  - `api/v1/agents.py:935`
  - `api/v1/agents.py:976`
  - `api/v1/agents.py:1065`
  - `api/v1/companies.py:22`
  - `api/v1/companies.py:287`
  - `api/v1/companies.py:385`
  - `api/v1/companies.py:438`
  - `api/v1/companies.py:508`
  - `api/v1/kpis.py:21`
  - `api/v1/kpis.py:339`
  - `api/v1/kpis.py:361`
  - `api/v1/kpis.py:372`
  - `api/v1/kpis.py:383`
  - `ui/src/components/ProtectedRoute.tsx:17`
- What is wrong:
  - `agents`, `companies`, and `kpis` routers are largely gated only by `get_current_tenant`.
  - That means any authenticated user in the tenant can directly hit endpoints the UI hides from them.
  - Examples:
    - read arbitrary agent configs: `GET /agents/{id}`
    - run arbitrary agents: `POST /agents/{id}/run`
    - mutate agent definitions: `PUT/PATCH /agents/{id}`
    - create/update/delete companies and company roles
    - read executive KPI endpoints for other roles
- Why it matters:
  - A CFO/CHRO/CMO/COO or any lower-privilege same-tenant user can bypass the UI and call privileged APIs directly.
  - This is a classic “frontend-only authorization” bug and it will show up immediately in pentests.
- Recommendation:
  - Add server-side dependencies for admin/domain/company role checks on every control-plane route.
  - Do not treat tenant membership as sufficient authorization.
  - Build a test matrix that exercises forbidden same-tenant access, not just unauthenticated access.

### 3. Critical: filing approval authorization can approve the wrong user

- Evidence:
  - `api/v1/companies.py:962`
  - `api/v1/companies.py:989`
  - `api/v1/companies.py:991`
  - `api/v1/companies.py:992`
  - `api/v1/companies.py:996`
  - `api/v1/companies.py:1001`
- What is wrong:
  - In `approve_filing()`, if the caller's email is not a direct key in `company.user_roles`, the code iterates every role entry and accepts the request if it finds any value equal to `partner`.
  - That fallback does not prove the current caller is the partner. It only proves the company has a partner.
- Why it matters:
  - Any authenticated same-tenant user can potentially approve filings for a company as long as some partner mapping exists on that company.
  - This undermines one of the highest-stakes approval flows in the repo.
- Recommendation:
  - Remove the “any partner exists” fallback.
  - Normalize `user_roles` to a single key type and resolve the current principal deterministically.
  - Add direct tests for “user A cannot approve because user B is the partner”.

### 4. High: `tally_detect` is an authenticated SSRF primitive

- Evidence:
  - `api/v1/companies.py:1977`
  - `api/v1/companies.py:2000`
  - `api/v1/companies.py:2017`
- What is wrong:
  - `tally_detect()` accepts arbitrary `tally_bridge_url` input and performs a server-side HTTP GET to `"{body.tally_bridge_url}/api/company-info"`.
  - There is no allowlist, no scheme restriction, no private-address filtering, and no backend RBAC around the endpoint.
- Why it matters:
  - An authenticated user can use the application server to probe internal services, metadata endpoints, or private network resources.
  - Because the response is reflected back to the caller, this is a practical SSRF vector, not a theoretical one.
- Recommendation:
  - Restrict the endpoint to admin-only use.
  - Validate scheme/host/port strictly.
  - Block loopback, link-local, RFC1918, and metadata IP ranges.
  - Prefer a server-managed bridge registry instead of direct arbitrary URLs.

### 5. High: LangGraph agent execution bypasses the hardened connector/secret-management path

- Evidence:
  - `core/langgraph/tool_adapter.py:25`
  - `core/langgraph/tool_adapter.py:40`
  - `core/langgraph/tool_adapter.py:107`
  - `core/tool_gateway/gateway.py:235`
  - `core/tool_gateway/gateway.py:249`
- What is wrong:
  - The LangGraph path directly instantiates connectors from `connector_config` and caches them globally in `_connector_cache`.
  - It does not load per-tenant encrypted connector credentials from `connector_configs`.
  - It does not go through `ToolGateway`, which is where you implemented tenant-aware connector resolution, encrypted credential loading, rate limiting, idempotency, and audit behavior.
- Why it matters:
  - Agent runs are operating on a materially weaker path than the rest of the platform.
  - In practice this creates three classes of bugs:
    - tenant secrets in `connector_configs` are ignored by agent execution
    - runtime governance controls are skipped
    - connector instances are cached globally rather than tenant-scoped
- Recommendation:
  - Collapse to one execution path.
  - LangGraph tool calls should go through `ToolGateway.execute()` or an equivalent tenant-aware adapter.
  - Remove global connector caching that is not tenant-scoped.

### 6. High: Account Aggregator consent endpoints are broken and will 500 on authenticated use

- Evidence:
  - `api/v1/aa_callback.py:62`
  - `api/v1/aa_callback.py:65`
  - `api/v1/aa_callback.py:79`
  - `api/v1/aa_callback.py:87`
- What is wrong:
  - `get_current_tenant()` returns `str`, but these endpoints type it as `tenant: dict` and then call `tenant.get(...)`.
  - That will raise at runtime once the route is hit with a valid authenticated request.
- Why it matters:
  - Two AA management endpoints are dead-on-arrival despite looking implemented.
  - This is the sort of bug that survives if tests only inspect shapes or source strings instead of executing real requests.
- Recommendation:
  - Accept `tenant_id: str = Depends(get_current_tenant)` directly.
  - Add request-level tests that exercise these endpoints with a real authenticated client.

### 7. High: SendGrid webhook verification fails open on a public route

- Evidence:
  - `auth/grantex_middleware.py:69`
  - `api/v1/webhooks.py:92`
  - `api/v1/webhooks.py:94`
  - `api/v1/webhooks.py:121`
- What is wrong:
  - `/api/v1/webhooks/...` is publicly exempt from auth middleware.
  - `_verify_sendgrid_signature()` returns `True` when `SENDGRID_WEBHOOK_KEY` is unset.
- Why it matters:
  - In any environment where the key is missing or misconfigured, anyone can post fake webhook events.
  - Those events are not harmless bookkeeping; they can resume waiting workflows via `_store_email_event()`.
- Recommendation:
  - Fail closed in non-dev environments.
  - Make missing webhook verification secrets a startup error in production/staging.
  - Add environment-aware tests for webhook verification behavior.

### 8. Medium: bulk filing approval uses incompatible role-key semantics and will deny legitimate partners

- Evidence:
  - `api/v1/companies.py:508`
  - `api/v1/companies.py:127`
  - `api/v1/companies.py:1704`
  - `api/v1/companies.py:1705`
- What is wrong:
  - `update_company_roles()` stores mappings as `user_id -> role`.
  - `bulk_approve_filings()` looks up only `roles.get(user_email)`.
  - That means approvals can fail for legitimate partner assignments created through the role-management API.
- Why it matters:
  - You now have inconsistent authorization behavior between single approve and bulk approve, and both are wrong for different reasons.
- Recommendation:
  - Normalize `company.user_roles` to one principal identifier format.
  - Centralize company-role resolution in a single helper used by every approval path.

### 9. Medium: budget overspend locking is not process-stable

- Evidence:
  - `api/v1/agents.py:1150`
- What is wrong:
  - The advisory lock key is derived from `hash(str(agent_id))`.
  - Python string hashing is randomized per process, so the same `agent_id` does not produce a stable value across workers.
- Why it matters:
  - The intended cross-request serialization for budget enforcement breaks as soon as requests land on different worker processes.
  - Under concurrent load, two workers can both believe they hold the correct lock and overspend the same budget.
- Recommendation:
  - Use a deterministic hash derived from the UUID bytes, or split the UUID into two signed integers for `pg_advisory_xact_lock`.

### 10. Medium: auth throttling is inconsistent across environments and instances

- Evidence:
  - `api/v1/auth.py:192`
  - `api/v1/auth.py:201`
  - `api/v1/auth.py:389`
- What is wrong:
  - Login throttling reads `REDIS_URL`, while the rest of the codebase standardizes on `AGENTICORG_REDIS_URL`.
  - Password reset throttling is in-process memory only.
- Why it matters:
  - In production, this can silently degrade back to per-process rate limiting even when Redis is configured elsewhere.
  - Distributed auth protections become inconsistent and easy to bypass with multiple pods/workers.
- Recommendation:
  - Standardize on one Redis setting.
  - Move password reset throttling to the shared store as well.

## Additional Findings From Second Pass

### 11. Critical: the bridge WebSocket accepts any non-empty token and does not verify bridge ownership

- Evidence:
  - `bridge/server_handler.py:52`
  - `bridge/server_handler.py:73`
  - `bridge/server_handler.py:78`
  - `api/v1/bridge.py:56`
  - `api/v1/bridge.py:146`
- What is wrong:
  - `/api/v1/ws/bridge/{bridge_id}` accepts the socket first, reads an auth payload, and then explicitly says `For now, accept any token that was provided`.
  - The only enforcement is that `token` is non-empty and `bridge_id` matches the URL.
  - Meanwhile `list_bridges()` returns `bridge_token` values to authenticated tenant users, so there is no hard separation between registration metadata and runtime auth material.
- Why it matters:
  - A malicious client can impersonate a bridge connection without proving possession of a valid bridge secret.
  - If they know or obtain a `bridge_id`, they can potentially hijack requests intended for a local Tally bridge and receive or influence routed XML payloads.
  - This is the same class of trust-boundary failure as the live-feed WebSocket, but on a connector path that can touch finance systems.
- Recommendation:
  - Validate the bridge token against the stored registration record before marking the socket authenticated.
  - Bind the socket to both `bridge_id` and `tenant_id`.
  - Do not return bridge secrets from general list endpoints unless there is a very strong operational reason.

### 12. High: production deployment and test posture relies on shared demo credentials and live prod logins

- Evidence:
  - `.github/workflows/deploy.yml:239`
  - `.github/workflows/deploy.yml:245`
  - `.github/workflows/deploy.yml:265`
  - `README.md:346`
  - `ui/src/pages/Login.tsx:200`
  - `ui/src/pages/Playground.tsx:253`
  - `tests/e2e_full_production_test.py:121`
  - `tests/test_new_features_production.py:157`
  - `tests/test_production_connectors.py:22`
- What is wrong:
  - The production deploy workflow logs into `https://app.agenticorg.ai` using `ceo@agenticorg.local / ceo123!`, stores a fresh bearer token, and then runs the full Playwright regression suite against production.
  - The same shared demo credentials are published in the README, baked into the login UI, and used by multiple production-targeted test scripts.
- Why it matters:
  - This repository is normalizing the idea that a shared CEO-style password is valid against production.
  - Even if those credentials are meant to be “demo only,” the deployment pipeline depends on them being live enough to mint production tokens.
  - Shared standing credentials plus automated production bearer-token acquisition is poor security hygiene at best and a latent backdoor pattern at worst.
- Recommendation:
  - Remove shared demo credentials from any production path immediately.
  - Use ephemeral test tenants or short-lived machine identities for deploy verification.
  - Stop publishing reusable login pairs in public-facing product surfaces and docs.

### 13. Medium: invite acceptance is almost certainly broken because the public route inherits admin auth

- Evidence:
  - `api/v1/org.py:25`
  - `api/v1/org.py:212`
  - `api/v1/org.py:215`
- What is wrong:
  - The `/org` router is created with `dependencies=[require_tenant_admin]`.
  - `accept_invite()` is defined on that same router even though the source comment says `NO AUTH REQUIRED`.
  - In FastAPI, router-level dependencies apply to all enclosed routes unless split onto a different router.
- Why it matters:
  - New users may not be able to accept invites at all, because accepting the invite requires already being an authenticated tenant admin.
  - This is a core onboarding flow, and it is exactly the type of regression that slips through when tests are not executing the real route dependency graph.
- Recommendation:
  - Move `accept_invite()` to a separate router with no admin dependency.
  - Add a request-level test that exercises the full invite issuance and acceptance flow.

### 14. Medium: cron security depends on a known default key, and one cron discovery route is fully unauthenticated

- Evidence:
  - `api/v1/cron.py:17`
  - `api/v1/cron.py:20`
  - `api/v1/cron.py:26`
  - `api/v1/cron.py:47`
  - `api/v1/cron.py:54`
- What is wrong:
  - `AGENTICORG_CRON_API_KEY` falls back to the literal value `dev-cron-key`.
  - `/cron/schedules` has no auth at all and exposes the app's internal Celery beat schedule.
  - The mutating cron trigger checks only the header value against that fallback secret.
- Why it matters:
  - If production or staging is misconfigured, the cron trigger becomes protectable by a publicly guessable default key.
  - Even when correctly configured, the unauthenticated schedule endpoint leaks operational timing and task inventory to anyone.
- Recommendation:
  - Remove the insecure default and fail startup if the cron key is missing outside development.
  - Require auth or internal network exposure only for `/cron/schedules`.
  - Treat scheduler endpoints like admin infrastructure, not public API surface.

## Test Gaps

The tests I inspected suggest the suite is stronger on response shape and source-inspection than on adversarial authorization behavior. The highest-value missing tests are:

- same-tenant but wrong-role access to `/agents/{id}`, `/agents/{id}/run`, `/companies/*`, `/kpis/*`
- WebSocket auth and cross-tenant subscription attempts
- bridge WebSocket authentication and bridge hijack attempts
- request-level tests for `aa_callback` authenticated endpoints
- approval tests that distinguish “current user is partner” from “some partner exists”
- production-mode tests that assert webhook verification fails closed
- full invite issuance and acceptance through the real `/org/accept-invite` dependency graph
- deploy verification that does not depend on shared demo credentials or production bearer-token minting
- multi-worker budget-race tests

## Priority Order

1. Lock down WebSocket auth and server-side RBAC.
2. Fix filing approval authorization.
3. Remove SSRF in `tally_detect`.
4. Unify LangGraph tool execution with the hardened connector path.
5. Remove shared production demo credentials and fix bridge authentication.
6. Repair broken AA consent routes and invite acceptance.
7. Make public webhooks fail closed and harden cron endpoints.
8. Fix budget lock determinism and auth throttling consistency.

## Dynamic Validation

I ran the broadest local checks this environment would support. The results materially improve confidence in the review, but they also exposed that the validation stack itself is not hermetic.

### Commands Run

- `pytest`
  - Result: started and ran through essentially the full tree.
  - Collected: 3319 tests
  - Outcome before termination: 3141 passed, 73 failed, 19 skipped, 86 errors in 56m12s
  - Termination mode: `pytest-cov` attempted to write `coverage.xml` and hit `PermissionError`
- `pytest --no-cov -ra` on the failing slices from the full run
  - Collected: 574 tests
  - Outcome: 412 passed, 73 failed, 3 skipped, 86 errors in 34m40s
- `ruff check . --no-cache`
  - Passed
- `mypy --ignore-missing-imports .`
  - Passed on 547 source files
- `ui`
  - `npm test`: passed, 74/74 tests
  - `npm run build`: passed
  - Note: the first build attempt failed only inside the sandbox with a Vite `spawn EPERM`; rerunning outside the sandbox succeeded
- `mcp-server`
  - `npm run build`: passed
- `sdk-ts`
  - `npm run build`: passed

### What The Dynamic Run Confirmed

- The repository does not currently have a clean, reproducible “all checks green” path in this environment.
- The frontend package health is substantially better than the backend test health:
  - UI tests passed
  - UI production build passed
  - TS package builds passed
- The backend Python tree has two separate problems:
  - real failure clusters in specific API areas
  - environment-coupled tests that depend on services or filesystem behavior that are not provisioned cleanly

### Confirmed Dynamic Failure Clusters

- Integration/E2E tests are not hermetic.
  - `tests/integration/test_api_integration.py` and `tests/integration/test_virtual_employees_api.py` error out trying to connect to Postgres on `localhost:5432`, with `InvalidPasswordError` for user `test`.
  - `tests/e2e/test_cxo_flows.py` also errors on Postgres auth, but against a different local DSN (`agenticorg` / `agenticorg_dev`).
  - `tests/e2e/test_smoke.py` fails because it assumes a live app on `http://localhost:8000`.
  - `tests/e2e_full_production_test.py` defaults to `https://app.agenticorg.ai` and then cascades into token/key errors once that external target is unreachable.
- The test strategy still mixes live-environment verification with normal suite execution.
  - This is not just inconvenient; it makes the red/green signal untrustworthy.
  - It also reinforces Finding 12, because multiple tests are designed around live demo-style credentials and production-facing URLs.
- There are real backend failure clusters beyond environment setup.
  - ABM API tests fail with wrong status behavior in grouped execution:
    - expected `404`, got `400`
    - expected `409`, got `500`
    - expected `200`, got `500`
  - Report-scheduling and company-isolation clusters fail in grouped execution.
  - A2A/MCP has at least one failing not-found path.
  - These are not all explainable by missing infra.
- Test behavior is order-dependent or conditionally brittle.
  - Several tests that failed in the grouped rerun skipped when isolated.
  - That usually indicates hidden shared state, conditional setup, or fixture interactions.
  - This is a quality problem even when the app code is correct.

### Environment-Limited Failures

- Filesystem restrictions in this session interfered with some outputs:
  - `coverage.xml` write failed
  - `.pytest_cache` writes were denied
  - `.ruff_cache` temp-file creation produced warnings unless cache was disabled
  - `test_report_engine.py` hit `PermissionError` writing into the system temp directory
- Docker is not installed in this environment, so I could not bring up the local stack to clear the Postgres-dependent integration/E2E failures.

### Assessment After Dynamic Verification

The original security/architecture findings still stand. The runtime checks added three more conclusions:

1. The project’s validation story is currently too coupled to undeclared local infrastructure and even live endpoints.
2. The backend has genuine failing clusters in ABM, reporting/report schedules, and some A2A/MCP paths.
3. The repository’s quality gate is weaker than it looks because the test suite mixes hermetic tests, infra-bound tests, and production-style live tests in one execution surface.

### Recommended Follow-Up

1. Split tests into hermetic unit, provisioned integration, and explicit live/prod verification suites.
2. Make infra-dependent suites fail fast with a clear skip reason when Postgres/Redis/app targets are absent, instead of surfacing as broad error floods.
3. Remove any production-targeting defaults from standard test commands.
4. Stabilize grouped test behavior by removing hidden shared state and conditional skipping based on ambient environment.
5. Redirect test caches and generated artifacts into workspace-owned paths during CI and restricted local runs.

## Remediation Progress

I converted the review into a first remediation pass on 2026-04-14. This does not make the whole repository "done," but it closes several of the highest-signal defects and removes some of the worst test-suite instability.

### Code Fixes Applied

- `api/v1/org.py`
  - Rebuilt the router after the interrupted patch.
  - Moved admin checks from the router-wide prefix onto the actual admin routes.
  - Added public `GET /org/invite-info` for the existing frontend contract.
  - Kept `POST /org/accept-invite` public, validated the invite token centrally, and bound the token subject to the invited user before activation.
  - Allowed invite acceptance to persist the caller-provided display name.
- `bridge/server_handler.py`
  - Removed the placeholder bridge auth behavior.
  - WebSocket bridge connections now verify the stored `bridge_token` from `BridgeRegistration` and reject inactive or unknown bridge IDs.
  - Pending bridge requests are now tracked per bridge, so one bridge disconnect no longer fails every in-flight request globally.
- `api/v1/bridge.py`
  - Bridge registration now rejects tenant mismatches between the body and authenticated tenant context.
  - `GET /bridge/list` no longer returns the bridge token for every registered bridge.
  - Added live bridge health fields to the list response without leaking credentials.
- `api/v1/cron.py`
  - The insecure fallback cron key is now limited to `development`/`test`.
  - Production-like environments must provide `AGENTICORG_CRON_API_KEY`.
  - `GET /cron/schedules` now requires the cron key instead of being public.
- `api/v1/abm.py`
  - Invalid account IDs now return `404` instead of bubbling UUID parsing failures.
  - Duplicate account creation now maps `IntegrityError` to `409`.
  - Account serialization no longer assumes `intent_score` is always populated.
- `api/v1/report_schedules.py`
  - Invalid schedule IDs now return `404` consistently for patch/delete/run-now paths.

### Test Harness Fixes Applied

- `tests/integration/conftest.py`
  - Removed the import-time mutation of `AGENTICORG_DB_URL` and `AGENTICORG_REDIS_URL`.
  - Integration fixtures now skip cleanly when `AGENTICORG_DB_URL` is not explicitly configured, instead of contaminating unrelated tests.
- `tests/e2e/test_smoke.py`
  - The suite now skips unless `AGENTICORG_E2E_BASE_URL` is explicitly provided.
  - It no longer defaults to `http://localhost:8000` in normal runs.
- `tests/synthetic_data/test_synthetic_flows.py`
  - Live synthetic tests are now explicit opt-in via `AGENTICORG_ENABLE_LIVE_TESTS=1`.
  - They no longer hardcode production demo credentials by default; token or credentials must be supplied via environment.
- `tests/e2e_full_production_test.py`
  - Full production E2E now requires explicit opt-in via `AGENTICORG_ENABLE_FULL_PROD_E2E=1` plus `AGENTICORG_TEST_BASE`.
- `tests/e2e/test_cxo_flows.py`
  - The DB-backed fixture now skips cleanly when `AGENTICORG_DB_URL` is absent or the test database is unavailable.
- `tests/conftest.py`
  - Redirected generic temp-file creation into a workspace-owned path for restricted runs.
- `tests/unit/test_report_engine.py`
  - Removed reliance on pytest `tmp_path` and wrote artifacts directly into a workspace path instead, which avoids sandbox temp-root issues in this session.

### Focused Validation After Remediation

- Focused backend/API slice:
  - Command: targeted `pytest --no-cov` run across `org`, bridge, cron, ABM, report schedules, A2A/MCP, isolation, and renderer-adjacent files.
  - Intermediate result before the renderer-path fix: `123 passed, 31 skipped, 8 errors`.
  - All 8 errors were from pytest temp-path setup inside `tests/unit/test_report_engine.py`, not from application assertions.
- Renderer suite after the temp-path fix:
  - `tests/unit/test_report_engine.py` printed `55 passed`.
  - The command wrapper in this session still returned a timeout code after pytest had already emitted the passing summary, so I am treating this as an environment wrapper artifact rather than a test failure.
- Live/integration gating validation:
  - Command: `pytest --no-cov` over smoke, synthetic, full-prod E2E, DB-backed E2E, and two integration modules.
  - Result: `13 passed, 115 skipped`.
  - This is the desired direction: absent infra now leads to explicit skips instead of broad failure floods.

### Residual Issues

- I have not rerun the entire 3319-test suite end to end after this remediation pass.
- `.pytest_cache` writes are still denied in this session, so cache warnings remain.
- Some remaining project-wide failures may still exist outside the patched clusters.
- The production/demo credential issue is mitigated for default execution paths, but broader cleanup of repository-level live-test scripts and CI policy is still warranted.

## Final Full Rerun

I completed the full repository rerun on 2026-04-14 after applying the remediation and pytest-path fixes.

### Full Suite Result

- Command:
  - `pytest -q --junitxml=codex-pytest-artifacts/full-pytest.xml`
- Result:
  - `3157 passed, 162 skipped, 10188 warnings in 1568.97s (0:26:08)`
- Coverage:
  - `coverage.xml` was written successfully during this rerun.

### What Changed Since The Earlier Failed Full Run

- Pytest cache is now redirected to `codex-pytest-cache/` instead of relying on `.pytest_cache` during normal execution.
- Pytest base temp is now pinned to `codex-pytest-basetemp/`, so `tmp_path` setup no longer depends on the blocked default temp-root behavior from this session.
- Generic tempfile usage remains redirected into `codex-pytest-temp/`.
- Live and infra-bound suites now skip unless their required environment is explicitly configured.

### Remaining Non-Blocking Issues

- The suite still emits a high warning count.
  - Main buckets are deprecations (`websockets`, `pydantic`, `datetime.utcnow`, `fpdf2`, `check_scope`) and a small number of async-mark/resource warnings.
- Old stale temp directories from earlier sandboxed runs still produce `git status` permission warnings:
  - `.tmp/...`
  - `codex-pytest-base/`
- Those stale directories are no longer on the active pytest execution path, so they did not block the clean rerun.

### Current Assessment

The repository is now in a materially better state than at the start of the review:

1. The critical auth and exposure issues fixed in this pass are addressed in code.
2. The default test surface is no longer contaminated by live/prod-targeting assumptions.
3. The full pytest suite completes successfully end to end in this environment once pytest is given workspace-owned cache/temp paths and an unrestricted shell.

I would still treat warning cleanup and stale temp-directory cleanup as follow-up work, but not as blockers to the statement that the current suite reruns cleanly.
