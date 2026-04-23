---
name: agenticorg-enterprise
description: Use this skill for enterprise hardening work in the AgenticOrg repository: whole-codebase audits, release sign-off, architecture review, and any meaningful code change that must meet enterprise-grade standards. Apply it for backend API changes, auth or tenant isolation work, billing, connectors, secrets, workflows, migrations, async runtime behavior, frontend auth flows, worker or deployment changes, and any task where correctness, security, scale, and production safety matter more than speed. This skill is especially important when touching FastAPI routes, request authorization, multi-tenant data access, Redis or Celery, workflow orchestration, Alembic migrations, health checks, metrics, or React session handling.
---

# AgenticOrg Enterprise

This skill is the project-specific implementation guide for shipping safe changes
in AgenticOrg. Use it to keep diffs small, preserve enterprise controls, and
verify the actual risk boundary you touched.

It is also the mandatory audit lens for enterprise-grade reviews in this repo.
If the task is a whole-codebase scan, enterprise hardening review, architecture
safety review, or release sign-off, this skill is not optional. If the task is
bug-sheet triage or reopen analysis, use this skill together with
`agenticorg-bug-fix-fail-closed`.

## Use This Skill When

- The change touches `api/`, `auth/`, `core/`, `migrations/`, `ui/`, `helm/`, `docker-compose.yml`, or `.github/workflows/`.
- The request affects auth, billing, org management, approvals, branding, SSO, invoices, connectors, tool execution, workers, or deployment behavior.
- The change could create a tenant-isolation, secret-handling, operability, or release-safety regression.
- Someone asks for enterprise hardening, production readiness, scale review,
  release sign-off, or a brutally honest audit of the whole product.
- The work touches workflow orchestration, event-driven execution, async runtime
  paths, browser session handling, startup/runtime DB mutation, or any control
  plane used by multiple tenants.

## Core Workflow

1. Inspect the existing implementation and the nearest local pattern before editing.
2. Reduce the task to the smallest safe diff that satisfies the request.
3. Identify the real control boundary:
   - authz
   - tenancy
   - secrets
   - async/runtime
   - schema
   - deployment
4. Implement without broad refactors.
5. Verify the exact boundary you changed.
6. Report what changed, what was verified, and any residual risk.

## Audit Workflow For Enterprise Reviews

When the user asks for a deep review, release sign-off, or enterprise
hardening audit, do all of the following before issuing a verdict:

1. Scan the repo for the known enterprise failure classes in this skill.
2. Inspect the real runtime path, not just the obvious route or component.
3. Compare sibling implementations for the same control surface.
4. Distinguish:
   - proven functional bugs,
   - scale and operability defects,
   - poor coding patterns with real regression risk.
5. Run the strongest feasible verification:
   - backend lint and security checks,
   - frontend lint, typecheck, and build,
   - targeted tests,
   - and the full suite if the user wants true release sign-off.
6. Report findings first, ordered by severity, with file references.
7. Refuse release sign-off if the evidence does not clear the relevant gate.

Code that "looks reasonable" is not evidence for enterprise sign-off.

## Stop-Ship Enterprise Gates

Do not grant enterprise release sign-off when any of these remain true:

- Workflow execution and workflow resume paths persist different state schemas.
- Event-driven workflows store match or filter criteria but the consumer
  resumes by event type only.
- Long-running workflows execute in request-process background tasks instead of
  a durable worker queue.
- Browser auth still depends primarily on script-readable bearer tokens in
  `localStorage` instead of a hardened cookie/session model.
- Token revocation, auth throttling, or other security controls can silently
  degrade to process-memory behavior in multi-pod production paths.
- Async request handlers or webhooks call synchronous Redis, HTTP, or DB
  helpers on the hot path.
- Runtime connector execution and connector test flows read credentials from
  different stores or with different fallback rules.
- Startup-time DDL or seed logic is still being treated as a primary schema
  management mechanism instead of a compatibility fallback.
- The audit depends only on source reading for auth, billing, workflow,
  connector, migration, or deployment-sensitive flows.
- Full-suite verification is incomplete and the user is asking for true release
  sign-off on the product rather than a narrow change.

## Known Failure Classes To Check First

These are recurring mistakes this repo must aggressively reject:

1. **Split-brain workflow state**: one path writes `step_results` /
   `waiting_step_id`, another reads `steps` / `current_step`.
2. **Over-broad event consumption**: listener registration stores a filter,
   webhook or event consumer ignores it.
3. **Non-durable orchestration**: important jobs run in FastAPI
   `BackgroundTasks` instead of Celery or another durable executor.
4. **Primary browser auth in localStorage**: `token` or `user` in localStorage
   remains the main session source after a cookie migration claim.
5. **Security fallback to process memory**: rate limits, blacklists, revocation,
   or tenant control state fall back to in-memory dicts in code that is meant
   to work across replicas.
6. **Sync I/O inside async handlers**: synchronous Redis, HTTP, or DB clients
   used from async API routes, billing callbacks, or high-volume webhooks.
7. **Runtime and test parity drift**: "test connection" or admin probe
   endpoints use different state, secrets, or validators than real execution.
8. **Startup DDL as governance**: `init_db()` or app startup owns schema
   evolution instead of Alembic.
9. **Broad exception swallowing on control paths**: `except Exception: pass`
   or silent downgrade logic in auth, billing, workflow, connector, or
   persistence layers.
10. **Client state hacks instead of proper context propagation**:
    `window.location.reload()`, localStorage-only company scoping, or similar
    shortcuts in core product flows.

## Hard Rules

### Authz and tenancy

- Never trust `tenant_id`, role, scope, or admin intent from client input when server auth context exists.
- Bind tenant-scoped work to authenticated tenant context on the server.
- Tenant-wide config and admin operations must enforce server-side authorization.
- Frontend role gating is not a security control.
- Fail closed on missing tenant, missing user, incomplete hydration, or ambiguous privilege state.
- Audit existing routers, not just the diff. As of `2026-04-12`, `api/v1/org.py`, `api/v1/connectors.py`, `api/v1/config.py`, and `api/v1/report_schedules.py` already carry admin guards, but `api/v1/workflows.py` still needs explicit review for mutating endpoints like `PUT /workflows/{wf_id}/replan-config`.
- Use `/org/invite` as the reference pattern: router-level admin enforcement plus a strict role allowlist.
- Never trust caller-supplied object IDs such as `subscription_id` for cross-tenant mutations. Resolve ownership from authenticated tenant state, and remove legacy fallbacks once server-side state exists.
- Check for route collisions before adding or moving handlers. FastAPI keeps both registrations and the first one wins. The previous duplicate `/billing/invoices` incident is fixed, but the class of bug remains real.
- Treat pack installation, connector configuration, approval policies, billing controls, and similar tenant-wide product toggles as admin-only control-plane actions even if the UI already hides them.

### Secrets and sensitive data

- Never add plaintext secrets to DB fields, config payloads, logs, metrics, or exceptions.
- PII masking is for logs and audit artifacts only, not live execution payloads.
- Do not add customer identifiers, user emails, or raw tenant IDs as metric labels.
- Connector credentials must be stored via tenant-aware encryption and executed only after decryption at the boundary. In this repo that means preferring `connector_configs.credentials_encrypted` plus `core.crypto.encrypt_for_tenant` / `decrypt_for_tenant`, not `Connector.auth_config` plaintext.

### Schema and persistence

- Every schema change requires an Alembic migration.
- Do not rely on startup-time DDL as the sole schema delivery path.
- Any new tenant-scoped table must get an explicit isolation strategy; prefer RLS.
- Think through rollout order, backfill needs, nullability, and downgrade impact.
- If a table is tenant-scoped, its RLS and policies must ship in the migration itself, not only in `core/database.py::init_db()`.
- Treat runtime DDL in `init_db()` as compatibility scaffolding, not the source of truth. If Alembic and `init_db()` disagree, fix the migration first.
- Never ship durable product state in process memory when the feature is exposed over multi-request APIs. Industry pack installs are the reference example: the live product must persist them tenant-safely, not track them in a module-level dict.

### Async and operability

- Do not use sync Redis, sync HTTP, or sync DB clients inside async request paths.
- Keep readiness checks cheap and local.
- Public health endpoints must not disclose sensitive environment or connector details.
- Worker and beat entrypoints in code, Compose, Helm, and CI must point to real modules.
- Do not keep auth throttling, temporary session state, or token revocation only in process memory when the behavior must survive restarts or scale-out.
- In this repo specifically, avoid `core.billing.usage_tracker._get_redis()` inside async API handlers such as SSO or billing cancellation paths.
- For long-running product workflows, request-process `BackgroundTasks` are not
  a durable orchestration layer. Use Celery or another persistent execution
  model for anything that must survive restarts, retries, or scale-out.
- Any event-driven resume path must prove that the producer and consumer agree
  on the exact persisted state schema and the same matching semantics.

### Frontend/API contract discipline

- Do not assume list endpoints return `data` or `items`. Inspect the actual backend response shape and bind the UI to that shape explicitly.
- Dashboard and catalog UIs must treat backend responses as the source of truth for KPIs, labels, and collections. Do not hardcode firm names, billing dates, revenue math, or placeholder portfolio stats once an API exists.
- When normalizing backend data for UI use, keep the mapping narrow and deterministic. Stable IDs must come from server fields such as `id` or `name`, not array position.
- If a page can show `No data yet`, verify that state against a non-empty live or seeded payload before considering the page complete.
- Do not call a browser auth flow "cookie-hardened" while shared API helpers,
  auth context, or SSO callbacks still read or write bearer tokens from
  localStorage as the primary path.
- `window.location.reload()` is a warning sign in core product flows. Treat it
  as a temporary workaround that needs explicit justification, not a default
  state propagation mechanism.

### Configuration and env vars

- Pydantic-settings uses `env_prefix = "AGENTICORG_"` (see `core/config.py:12`). Every Settings field `foo_bar` maps to env var `AGENTICORG_FOO_BAR`, not `FOO_BAR`. Always use the prefixed name when setting env vars via kubectl, Helm, or CI.
- Never add a startup `raise` that depends on an env var being set unless you have confirmed the var exists in production secrets. A warning plus safe fallback is safer than a crash that blocks rolling deploys.

### ORM model -> database sync

- Every column added to an ORM model in `core/models/` must also have a matching `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `core/database.py::init_db()`. Without this, the pod crashes on production when the DB has not been migrated yet.
- Every new table in an ORM model must have a matching `CREATE TABLE IF NOT EXISTS` in `init_db()`.
- This dual-write is a temporary requirement until Alembic is the sole DDL delivery path (see `migrations/README.md`).

### Version bumps

- Version must be updated in all of these locations atomically:
  - `pyproject.toml` -> `version`
  - `api/main.py` -> FastAPI app `version=`
  - `api/v1/health.py` -> `APP_VERSION`
  - `tests/integration/test_api_integration.py` -> version assertion
- Never bump version in one place and forget the others. CI will catch the integration test, but the drift itself signals governance problems to enterprise buyers.

### Delivery

- **ALL new development must go in a feature branch with a PR. Never commit directly to main.** Create a branch (`git checkout -b feat/<name>` or `fix/<name>`), push it, create a PR with `gh pr create`, wait for CI, then merge. Direct-to-main is only allowed for production-down hotfixes with explicit user approval.
- **Run `bash scripts/preflight.sh` before every `git push`.** The local preflight mirrors every blocking CI gate (ruff whole-tree, bandit api/auth/core, alembic revision id ≤32 chars, verify=False scan, pytest regression+unit, tsc, npm run build) so you catch the failure locally instead of burning a CI run. Pass `SKIP_UI=1` when iterating backend-only; pass `--fast` to skip the UI production build. The repo-tracked git hook at `.githooks/pre-push` runs this automatically after `scripts/install_hooks.sh`.
- For auth, billing, migrations, infra, workers, secrets, and deployment changes, verification is required.
- Keep Docker, Helm, and CI contracts aligned when they touch the same runtime path.
- Do not treat "probably fine" as a release standard.

## Hotspots To Inspect First

### Request auth and tenant context

- `api/deps.py`
- `auth/jwt.py`
- `auth/middleware.py`
- `auth/grantex_middleware.py`
- `core/billing/usage_tracker.py`
- `core/rbac.py`

### High-risk API surfaces

- `api/v1/auth.py`
- `api/v1/connectors.py`
- `api/v1/org.py`
- `api/v1/billing.py`
- `api/v1/invoices.py`
- `api/v1/approval_policies.py`
- `api/v1/branding.py`
- `api/v1/sso.py`
- `api/v1/workflows.py`
- `api/v1/workflow_variants.py`

### Connectors, secrets, and tool execution

- `core/tool_gateway/gateway.py`
- `core/tool_gateway/audit_logger.py`
- `core/tool_gateway/pii_masker.py`
- `core/models/connector.py`
- `core/models/connector_config.py`
- `core/crypto/`

### DB and migrations

- `core/database.py`
- `migrations/`

### Frontend auth and route safety

- `ui/src/contexts/AuthContext.tsx`
- `ui/src/components/ProtectedRoute.tsx`
- `ui/src/pages/SSOCallback.tsx`
- `ui/src/lib/api.ts`
- `ui/e2e/`

### Config and env

- `core/config.py` (`env_prefix = "AGENTICORG_"`, all field->env mappings)
- `.env.example`

### Runtime and delivery

- `core/tasks/`
- `docker-compose.yml`
- `helm/templates/`
- `.github/workflows/`

### Version sources (must stay in sync)

- `pyproject.toml`
- `api/main.py`
- `api/v1/health.py`
- `tests/integration/test_api_integration.py`

## CI Failure Patterns (Lessons Learned)

These are recurring CI failure modes from the April 2026 enterprise program. Check for each before pushing.

1. **TypeScript strict mode**: `noUnusedLocals` is enabled. If you declare a const like `const isDowngrade = ...` but only use `isUpgrade`, the build fails with TS6133. Remove it or prefix with `_`.

2. **Regression tests that inspect source code**: Some tests in `test_bugs_april06_2026.py` grep function definition lines for `Depends`. If your function signature spans multiple lines, `Depends` must appear on the `def` line itself or the test fails. Keep `Depends` on the same line as the function name for short signatures.

3. **Integration tests with hardcoded versions**: `test_api_integration.py` asserts the version string from `/health`. When you bump the version, update the test too.

4. **CORS startup crash**: The CORS check reads `settings.cors_allowed_origins` which maps to `AGENTICORG_CORS_ALLOWED_ORIGINS` (not `CORS_ALLOWED_ORIGINS`). A hard `raise` blocks deploys if the var is unset. Use a warning plus safe fallback.

5. **Route collisions**: FastAPI silently registers both handlers for the same path and the first registered one wins. The old duplicate `/billing/invoices` bug is fixed, but you should still check for collisions before adding or moving handlers. Search with `rg -n "/invoices|prefix=.*billing" api/v1`.

6. **Production release gates**: Tagged releases now require `approval-gate`, and production health now passes only on `"status":"healthy"`. Do not weaken either rule by reintroducing `"degraded"` acceptance or bypassing approval for release tags.

7. **Regression tests that assert old permissive behavior**: When tightening a security or operational gate, always search for tests that still encode the old behavior. Search with `rg -n "degraded|subscription_id|invite|Depends|permissive" tests`.

8. **Legacy security fallbacks**: Be suspicious of compatibility branches that keep insecure behavior alive for "old tenants" or "old data". The current Stripe cancellation fallback to caller-supplied `subscription_id` is the model example of what must be burned down, not normalized.

9. **E2E auth helpers that only set a token**: Role-gated routes now fail closed when `user` hydration is missing. If Playwright stores only `localStorage.token`, protected pages can redirect or render false negatives even though the backend session is valid.

10. **Frontend/API contract drift on dashboards**: Recent live regressions came from pages expecting `items` or `deadlines` while APIs returned `packs` or `upcoming_deadlines`. When touching dashboards, catalogs, or summary pages, verify the exact JSON keys against the backend handler before shipping.

11. **Concurrent deploy pipelines race on the same Helm release**: If multiple PRs merge to main within minutes, every CI/CD run enters the `deploy-production` stage, each `helm upgrade` updates the same Kubernetes Deployment, and earlier pipelines' `kubectl rollout status` sees a newer image and times out. The deploy job reports "failure" even though production is healthy. Add `concurrency: production-deploy` at the job level to serialize, or use `gh pr merge --merge-queue`. Before declaring a real deploy failure, curl `/api/v1/health` first — rollout-status timing out and prod being HTTP 200 is the usual pattern, not a code regression.

12. **Liveness probe `initialDelaySeconds` must accommodate image size and startup work**: The API image is 3.2 GB and loads 54 connectors + LangGraph at boot. `initialDelaySeconds=10s` plus `period=30s`/`failure=3` gives only ~100s before kubelet kills the container, which is not enough on slow Autopilot nodes. Prefer a startup probe, or bump liveness initial delay to 60–90s. Symptom: `Readiness probe failed: connect: connection refused` events even though the pod eventually logs "Application startup complete".

13. **`continue-on-error: true` on e2e jobs silently accumulates drift**: The 438-test Playwright regression suite had been green-marked by CI for many deploys while mostly failing, because `continue-on-error: true` masks the real conclusion. Any e2e/integration job with this flag deserves a periodic human audit (run locally against prod, inspect the pass/fail count). When rewriting the suite to green, flip to `continue-on-error: false` the moment it is stable — leaving the flag on is how drift comes back.

14. **Regression tests that pin exact serialized key sets**: Tests like `assert set(result.keys()) == expected_keys` in `test_agents_and_sales.py::test_all_expected_keys_present` break immediately when a new field is added to `_agent_to_dict` (BUG-013 added `connector_ids` and CI went red on the next push). Before merging a serializer change, grep `tests/` for `set(result.keys())`, `expected_keys`, and literal dict-key assertions — update them in the same PR.

15. **Regression tests that grep source code for specific literals**: Some tests in `test_ca_api_functional.py` inspect function source with `inspect.getsource()` and assert substrings like `'"approved"' in source` or `"gst_auto_file" in source`. These check implementation details, not behavior. When you tighten a gate (e.g., remove an auto-approval branch), these tests fail for the right reason but block the PR. Grep for `inspect.getsource` in tests before refactoring control-flow and update the assertions to match the new intent, not the old.

## Verification Rules

### Backend code

- Run targeted tests for the touched area.
- Run `ruff check` on the touched backend paths.
- If pytest fails only because coverage output is locked, rerun the targeted suite with `--no-cov` and state that explicitly.

### Auth, billing, org, connectors, approvals, or tenancy

- Verify negative cases, not only happy paths.
- Confirm that tenant A cannot affect tenant B.
- Confirm non-admin users cannot perform admin actions.
- If the task is release sign-off or enterprise audit, do not stop at targeted
  tests. Run the strongest end-to-end or full-suite signal available and say
  explicitly when it is missing.

### Migrations and persistence

- Add the migration.
- Sanity-check upgrade behavior and any code path that assumes the new column or table exists.
- Verify that tenant-scoped tables have RLS or another explicit isolation control in the migration, not only in runtime startup code.

### Frontend changes

- Run targeted `vitest` when feasible.
- Run `cd ui && npm run build` for auth, routing, shared API, or type changes.
- For protected-route E2E coverage, hydrate both `token` and `user` in local storage when the app expects a hydrated session.
- For dashboard, packs, and billing-style pages, test at least one non-empty payload path. Empty-state-only verification is insufficient.

### Infra and workers

- Verify the referenced command or module actually exists in the repo.
- If one runtime contract changes, update every manifest that depends on it.

## Output Contract For Enterprise Audits

For enterprise reviews and sign-off requests, the output must include:

- explicit verdict: `sign-off granted` or `sign-off refused`;
- findings first, ordered by severity;
- file references for each blocker;
- what was verified versus what remains unverified;
- whether each problem is a functional bug, scale-hardening defect, or poor
  coding pattern with real operational risk.

Do not hide behind soft language such as "mostly fine", "looks good overall",
or "probably shippable". If a stop-ship gate is still open, say so directly.

## Practical Commands

- `ruff check api auth core connectors workflows`
- `python -m pytest -q`
- `python -m pytest -q --no-cov <tests...>`
- `python -m bandit -r api auth core -x migrations,tests -f json`
- `cd ui && npm test`
- `cd ui && npm run build`

## Bug Fix Patterns (April 2026)

8. **Null safety in aggregation queries**: Dashboard endpoints that `float()` or index dict results from SQL aggregates MUST handle None. Always use `float(x) if x is not None else 0.0`.

9. **Never return secrets in API responses**: Return `has_credentials: bool` not actual values. Secrets live in `connector_configs.credentials_encrypted`.

10. **0-step workflow guard**: Validate at BOTH creation AND run time. Creation validation exists but data can be modified directly.

11. **Chat output formatting**: `raw_output` may contain a nested JSON string. Always try `json.loads()` before displaying.

12. **Dead code that is actually a model field**: When ruff flags a line like `phone: str = ""` as dead code after a `return` statement, check whether it was supposed to be a Pydantic model field that got displaced. Moving it to the correct class fixes both the lint warning and the `AttributeError: object has no attribute` crash.

13. **Dockerfile installs base deps only**: `pip install .` does NOT install optional dependency groups. If a feature requires `composio-core`, `presidio`, or other packages listed under `[project.optional-dependencies]`, the Dockerfile must use `pip install ".[v4]"` or the SDK will be missing at runtime.

14. **RLS-protected tables require `get_tenant_session(tid)` not `async_session_factory()`**: Any endpoint that queries an RLS-protected table MUST use `get_tenant_session()` which sets the `agenticorg.tenant_id` GUC. Using `async_session_factory()` bypasses the GUC and returns 0 rows because FORCE ROW LEVEL SECURITY blocks the query. This caused approval_policies, invoices, and workflow_variants to return empty despite having data.

15. **KPI endpoints must return structured fields, never raw task_output**: CxO dashboards bind to `agent_count`, `total_tasks_30d`, `success_rate`, `hitl_interventions`, `total_cost_usd`, `domain_breakdown[]`. If the KPI builder returns raw task_output dicts instead (like `{"items": 8, "result": "Demo bank_reconciliation"}`), the UI shows NaN. Always use `_compute_basic_metrics()` with SQL aggregation.

16. **Domain names in ROLE_DOMAIN_MAP must match agent_task_results.domain exactly**: COO maps to `["operations", "it", "support"]` not `"ops"`. CBO maps to `["legal", "risk", "corporate", "comms"]` not `"strategy"`. The SQL uses `domain = ANY(:domains)` so partial matches fail silently with 0 results.

17. **Seed data must match the ORM model schema exactly**: When inserting via raw SQL, check ALL NOT NULL columns in the ORM model. Missing `tool_functions` (JSONB NOT NULL) in connector INSERT crashes. Missing `mfa_enabled` (BOOLEAN NOT NULL) in user INSERT crashes. Always read the model first.

18. **VAPID keys and API keys must be set BEFORE testing**: Features that depend on env vars (COMPOSIO_API_KEY, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY) will silently return empty/0 if the var is missing. Always verify the env var is set on the running pod before concluding the feature is broken.

19. **Always verify you are on `main` before committing**: After PR merges or branch switches, `git branch` may show a stale codex branch. Run `git checkout main && git pull` before committing. If you committed on the wrong branch, `git cherry-pick <hash>` onto main.

20. **`async_session_factory()` vs `get_tenant_session()` audit**: When a new RLS-protected table is created, grep ALL API files for `async_session_factory` and verify none of them query the new table. The following files still use `async_session_factory` and may break if they touch RLS tables: `invoices.py`, `workflow_variants.py`, `sso.py`, `branding.py`.

21. **Keep documentation and SEO assets in sync with code**: When adding agents, connectors, or solution pages, update ALL of these:
    - `README.md` — agent count, connector count, version badge
    - `ui/index.html` — meta tags (description, og:description, twitter:description), JSON-LD `softwareVersion`, pricing JSON-LD
    - `ui/public/sitemap.xml` — add new public routes
    - `ui/scripts/generate-sitemap.mjs` — add new routes to `staticPages[]`
    - `ui/public/llms.txt` and `ui/public/llms-full.txt` — auto-generated on build, but verify agent/connector counts
    - `ui/package.json` — keep version in sync with `pyproject.toml`
    - Do NOT claim more agents/connectors than actually exist in code. Count from `_AGENT_TYPE_DEFAULT_TOOLS` in `api/v1/agents.py` and connector directories under `connectors/`.

22. **Version must be synchronized across all locations**: When bumping version, update ALL of:
    - `pyproject.toml` → `version`
    - `api/main.py` → FastAPI `version=`
    - `api/v1/health.py` → `APP_VERSION`
    - `ui/package.json` → `version`
    - `ui/index.html` → JSON-LD `softwareVersion`
    - `README.md` → version badge
    - `tests/integration/test_api_integration.py` → version assertion

23. **E2E tests must grant admin scopes**: The test fixture must set `agenticorg:scopes: ["agenticorg:admin"]` — empty scopes will cause 403 on all admin-gated routes (report-schedules, connectors, companies, workflows, etc.).

24. **Connector test endpoint must read encrypted credentials**: The `/connectors/{id}/test` endpoint must read from `connector_configs.credentials_encrypted` first (decrypting at runtime), falling back to legacy `Connector.auth_config` only if no encrypted config exists. Never read only from `auth_config`.

25. **Login throttling must use Redis**: Auth rate limiting uses Redis with atomic TTL keys for cross-pod consistency. In-memory dict is fallback only. Never rely on process-local state for security controls in a multi-pod deployment.

26. **`/auth/me` must read onboarding_complete from tenant settings**: Never hardcode `onboarding_complete: True`. Read from `tenant.settings.get("onboarding_complete", False)` — same as `/login` and `/signup`. Session state must be consistent across all auth hydration paths.

27. **E2E and Playwright tests must block deploy**: Post-deploy E2E tests in `deploy.yml` must NOT use `continue-on-error: true`. Only synthetic/LLM-quality tests (non-deterministic) may be non-blocking. Deterministic product-path tests must fail the pipeline.

28. **Do not dump kubectl describe or pod logs in CI**: Rollout diagnostics should show only pod name, status, ready, and restart count — never full `describe` or `logs` output which may expose secrets, env vars, or internal state.

29. **Pre-flight validation must not hard-reject on auto-populated defaults**: When adding request-time validation like `run_agent`'s `_validate_authorized_tools` check, remember that `create_agent` intentionally skips validation for tools auto-populated from `_AGENT_TYPE_DEFAULT_TOOLS` / `_DOMAIN_DEFAULT_TOOLS`. Many of those names (`check_order_status`, `schedule_social_post`, `search_content_fulltext`) are not in the connector registry. A hard 400 on any missing name regresses every newly-created default agent. The correct shape is: filter unresolvable tools, log a warning, only fail when the resolvable set is empty. Codex flagged this as P1 on PR #150.

30. **CSV export must neutralize formula injection**: Audit / report exports that only escape quotes still emit executable formulas when a cell starts with `=`, `+`, `-`, `@`, CR, LF, or TAB. Action and Actor fields carry user-controlled text (filing types, emails, rejection reasons), so Excel/Sheets will execute the formula on the reviewer's machine. Prefix such cells with a single quote before quoting. Apply to every CSV export path, not just the audit log. Pattern:
    ```ts
    const csvEscape = (v: string) => {
      const guarded = /^[=+\-@\r\n\t]/.test(v) ? `'${v}` : v;
      return `"${guarded.replace(/"/g, '""')}"`;
    };
    ```

31. **E2E helpers must never cache fallback values**: When a setup API call (`/auth/me`, `/companies`, `/tenants/current`) fails transiently, returning a hardcoded fallback is fine, but caching that fallback locks the entire Playwright run to the wrong identity/tenant. Only populate the cache on a 2xx response. On failure, return the fallback without storing it so the next call retries the live API. Codex P2 on PR #151 for `ui/e2e/helpers/auth.ts::getProfile`.

32. **E2E helpers must not return cross-tenant hardcoded IDs**: A fixed UUID fallback in `getCompanyId()` (or any `get<Entity>Id` helper) is correct for exactly one tenant. In any other `BASE_URL` / env it silently routes every CompanyDetail assertion against an entity that does not exist, producing persistent false negatives. Throw a clear error instead — the test fails cleanly and the retry path re-hits the real API. Codex P2 on PR #151.

33. **E2E auth seeding must mirror what `AuthContext` would hydrate**: `Layout.tsx` filters sidebar nav by `localStorage.user.role`. Seeding a fake role like `"ceo"` hides every CxO nav link because the real demo account is `role: "admin"`. Result: dozens of tests redirect to onboarding or fail to find nav links, even though auth is technically valid. Fetch the profile live from `/api/v1/auth/me` and seed that verbatim, or keep one hardcoded profile per known account and verify it matches the live response. Never invent the user shape.

34. **E2E selectors must scope to `<main>` for in-page elements that share labels with sidebar nav**: `page.getByText("Approvals", { exact: true }).first()` resolves to the sidebar's `/dashboard/approvals` link before any CompanyDetail tab button named "Approvals". Clicking navigates away from the company entirely. For in-page tabs, buttons, and any element whose name could collide with a nav link, scope with `page.locator("main button").filter({ hasText: /^Approvals$/ }).first()` (or use the `tabButton()` helper in `ui/e2e/helpers/auth.ts`).

35. **Dependabot bumps for proprietary SaaS must be closed, not merged**: The project rule is open-source only (no LangSmith, no proprietary APM). A silent merge of a `langsmith` or `langfuse` version bump violates that rule and pulls a SaaS dependency into the runtime. When triaging dependabot PRs, check the package's license and hosting model first. Close with a comment pointing to the policy and the OSS equivalent (e.g., OpenTelemetry, Phoenix Arize OSS).

36. **Dependabot dep-conflict failures expose pre-existing latent pins**: The `testcontainers >=4.14` bump in PR #144 triggered a `uv pip install` resolution failure because `testcontainers[redis]>=4.14` transitively requires `redis>=7`, but `celery[redis]>=5.4.0` requires `redis<6.5` via `kombu 5.6`. The ceiling pin in `pyproject.toml` (`testcontainers>=4.8.0,<4.14.0`) is intentional and documented. Before merging any dep bump, grep `pyproject.toml` and `requirements*.txt` for `<`-pinned comments — those pins encode real conflicts and are not just conservative. Close conflicting bumps with a reference to the comment line.

37. **Singleton thread-safety: flip the initialized flag LAST, not first.** Session 5 BUG-S5-005 (`core/pii/redactor.py`) crashed under concurrent agent startup because `__init__` set `self._initialized = True` before binding `self._analyzer`. A second caller's `PIIRedactor()` saw `_initialized=True`, skipped the init block, and hit AttributeError on the next `redact()`. Two invariants must hold for any lazy-init singleton in this codebase: (a) wrap the whole `__init__` body in `with self._lock:` — not just the assignment step; (b) declare the attributes it intends to bind as class-level defaults (`_analyzer: Any = None`) so reads never hit AttributeError even mid-init; (c) flip `_initialized = True` only as the very last statement, after every other attribute is bound and recognizers are registered. A regression test must spin up >1 threads calling the constructor simultaneously — single-threaded tests won't catch the bug.

38. **Frontend/backend validators must share a regex.** Session 5 TC-007/TC-009/TC-012: SIP URI and E.164 phone validators existed in neither layer. When you add input validation, add the SAME regex to both `api/v1/<domain>.py` and `ui/src/pages/<Page>.tsx`, and cross-reference them in a comment on both sides. One-sided validation on the frontend is a UX hint but not a security control; one-sided validation on the backend gives users a generic "422" with no context. The regression test suite should cover both layers: pytest parametrizes bad inputs against the backend validator, Playwright parametrizes the same inputs against the form's inline-error path.

39. **Response-shape drift between frontend and backend is silent.** Session 5 TC-002: the backend returned `imported: <int>` but the UI read `data.imported?.length` on the number, collapsing to `undefined → 0`. The import banner reported `0` even for successful multi-lead imports. When changing an endpoint's response shape, grep the repo for every consumer and update the read path. If a field's semantics change (`list` → `count`), rename it so stale reads fail loudly instead of quietly.

40. **DB-level `UniqueConstraint` must match the soft-delete filter.** Session 5 TC-003: the Python query filtered `is_active=True`, but the `UniqueConstraint("tenant_id", "name", "agent_type")` in the model did NOT — so even after the user soft-deleted a template, the DB still rejected a new insert with the same name. For any table with `is_active` (or `deleted_at`) soft-delete semantics, use a **partial unique index** scoped to live rows: `Index(..., unique=True, postgresql_where="is_active = true")`. The Alembic migration must drop the old full constraint before creating the partial index.

41. **"Fallback-only" DB mirrors hide the primary source's flakiness.** Session 5 TC-013: `upload_document()` in `api/v1/knowledge.py` only wrote to Postgres when RAGFlow failed, treating the DB as a disaster-recovery backup. When RAGFlow succeeded but its search index lagged, the document disappeared from the UI after a refresh (the list call fell back to DB, which had no record). When a feature has two data stores (primary + fallback), always **mirror to both** on every write and **merge both on every read** with a dedupe key. Otherwise, any eventual-consistency gap in the primary silently drops data from the UI.

42. **CSV imports need emptiness + encoding + header validation BEFORE the row loop.** Session 5 TC-005: the old endpoint accepted any file, parsed what it could, and returned `{"imported": 0}`. Users were misled into thinking an invalid file imported "0 leads" successfully. At the import boundary, validate in order: (a) file extension (`.csv`), (b) non-empty body, (c) UTF-8/BOM-tolerant decoding with a 422 on UnicodeDecodeError, (d) required headers (accept known aliases like `full_name` → `name`). Return 422 with a structured `detail.message` that the UI can surface verbatim. Never return 200 with `imported=0` for a clearly-invalid input.

43. **Missing backend endpoint shows up as 404/405 on the frontend, not a crash.** Session 5 TC-006 (`/voice/test-connection`) and BUG-S5-001 (`/companies/test-tally`) both had UI code calling routes that didn't exist — the frontend rendered "Connection test unavailable (API offline)" and kept moving, which users read as "probably fine". When adding a new feature, do a parity audit: grep the UI for `api.post(` / `api.get(` / fetch() URLs that don't exist in any `@router.post`/`@router.get` decorator and add the missing endpoints (or remove the dead UI). Treat UI-to-API URL drift as a shipping blocker, not a warning.

44. **CI `services:` containers must use credentials that match the app's default Settings, OR the app's env var names must match what you set.** Session 5 e2e-tests regression: I created the Postgres service container with `POSTGRES_USER: e2e` and set `AGENTICORG_DATABASE_URL`, but the Settings field is named `db_url` (→ `AGENTICORG_DB_URL`) and its default URL uses `user=agenticorg`. Result: the app ignored my DB env var, fell back to the default URL, and authentication failed. Before shipping a service-container addition: (a) grep `core/config.py` for the exact env var name — remember `env_prefix = "AGENTICORG_"` maps `db_url` to `AGENTICORG_DB_URL`; (b) if the goal is zero env-var plumbing, create the service with the Settings default credentials so the app works without any overrides.

45. **`init_db()` adds columns and policies; it does NOT create tables from scratch.** In the e2e-tests CI job (session 5), a fresh Postgres service container + `asyncio.run(init_db())` left the `agents`/`companies`/... tables missing, because `init_db()` only issues `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and `ENABLE ROW LEVEL SECURITY`, not `CREATE TABLE`. Tests that spin up an empty DB must call `conn.run_sync(BaseModel.metadata.create_all)` first. Production is immune because Alembic or a prior `init_db` run on an existing DB already created the tables — but any new hermetic-test scenario needs `create_all` explicitly.

46. **Alembic `revision = "..."` must be ≤32 characters.** The `alembic_version.version_num` column is `VARCHAR(32)`. A 37-char revision (e.g. `v483_prompt_template_partial_unique`) passes ruff, passes local unit tests, and then crashes integration tests on `subprocess.CalledProcessError` from `scripts/alembic_migrate.py` with `value too long for type character varying(32)` buried in the Postgres logs. The repo preflight (`scripts/preflight.sh`) now greps every `migrations/versions/*.py` for the literal and fails the push if any revision exceeds the limit.

47. **Seed test fixtures with real UUIDs and full NOT-NULL sets.** The `test_cxo_flows.py` fixture used `f"e2e-tenant-{hex8}"` as tenant_id and omitted `slug`. Both problems are silent in unit tests — they only fire when the fixture touches Postgres. Before writing a fixture INSERT: open the model file, list every column with `nullable=False` AND no default/server_default, and include all of them. Fake string IDs never cross an FK to a UUID column. For any hermetic-DB fixture, seed inside the **module-scoped** fixture (single `asyncio.run()`), not in a per-test fixture — pytest-asyncio gives per-test fixtures a fresh event loop, and any `Future` produced against the module-scoped engine will raise "attached to a different loop".

48. **`verify=False` in production paths is Bandit B501 High and will fail CI.** Any time you need to skip TLS verification inside `api/`, `auth/`, `core/`, or `connectors/`, stop and redesign. The one we shipped in `api/v1/voice.py` for a SIP reachability probe was replaced with a raw TCP socket connect because SIP isn't HTTP anyway — the "bypass TLS to probe HTTPS" shape was doubly wrong. For genuine dev-only TLS skips, keep them in `scripts/`, `tests/`, or behind a `settings.environment == "test"` guard that the preflight still inspects.

49. **Run the full local preflight before `git push`, not the partial one.** Three separate sessions landed ruff `I001` (import-sort) violations on CI because each session ran `ruff check <touched-file>` locally. `I001` cascades — touching one import in a file can require reordering imports elsewhere that didn't change, and the per-file invocation doesn't see those siblings. Always run `ruff check .` (the whole tree). The same logic applies to alembic revisions, bandit scans, and tsc — run the full check, not the scoped one, because CI will.

## Expected Output

- Small, explicit diff.
- No new authz or tenant-boundary regression.
- No new secret or PII leak.
- Verification included.
- Residual risks called out when checks are partial.
