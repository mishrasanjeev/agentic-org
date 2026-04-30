# Enterprise End-to-End Gap Analysis - 2026-04-30

Audience: Claude Code or another implementation agent.

Scope: code-level review plus local verification of frontend, backend, API contracts, and production-oriented Playwright smoke tests. This is intentionally blunt: the product has a large amount of functionality, but it is not yet enterprise-grade as a release candidate.

## Executive Verdict

Do not call this enterprise-ready yet.

The app builds and many unit/integration tests pass, but there are release blockers:

1. Frontend calls backend endpoints that do not exist: `/audit/enforce`, `/health/checks`, `/health/uptime`, and `POST /workflows/runs/{runId}/cancel`.
2. Auth/session is split between the backend's HttpOnly-cookie direction and the frontend's continued `localStorage` bearer-token model.
3. Full backend pytest is not a reliable release gate: one BGE-M3 test downloads model weights from Hugging Face at runtime and timed out, and the unit suite did not produce a clean full summary within 15 minutes.
4. Authenticated Playwright E2E is not runnable without `E2E_TOKEN`; public routes passed, authenticated flows were not verified.
5. Several UI flows silently swallow API errors and render empty states, which hides broken enterprise controls.
6. Coverage is only 56% against an 80% target and 90% critical-path target.

## Verification Results

| Area | Command | Result | Notes |
|---|---|---:|---|
| Frontend typecheck | `npm run typecheck` in `ui/` | PASS | TypeScript compiles. |
| Frontend lint | `npm run lint` in `ui/` | FAIL | `ui/src/components/ChatPanel.tsx:72` has `no-useless-assignment`; 16 hook-dependency warnings. |
| Frontend unit tests | `npm run test` in `ui/` | PASS | 8 test files, 96 tests passed. |
| Frontend build | `npm run build` in `ui/` | PASS | Vite build succeeded; generated sitemap and llms files. |
| Backend ruff | `uv run ruff check api auth core workflows connectors scaling observability audit tests` | PASS | Required dependency hydration first. |
| Backend full pytest | `uv run pytest -q` | FAIL/BLOCKED | Timed out in `tests/regression/test_bge_m3_pr_a.py::test_embed_with_bge_m3_returns_1024_dim_vectors` while downloading `BAAI/bge-m3`. |
| Backend integration tests | `.venv\Scripts\python.exe -m pytest tests\integration ...` | PASS | 19 passed, 84 skipped. Coverage output from this scoped run is not meaningful. |
| Backend unit suite | `.venv\Scripts\python.exe -m pytest tests\unit ...` | BLOCKED | Did not finish within 15 minutes. Targeted eval endpoint tests pass outside the filesystem sandbox. |
| Playwright public routes | `npm run test:e2e -- e2e/app-routes.spec.ts ...` | PARTIAL | Public marketing/login/404/landing tests passed. Authenticated sections failed because `E2E_TOKEN` is not set. |

## P0 Release Blockers

### 1. Frontend/Backend API Contract Drift

Observed by extracting 184 frontend API calls and 276 FastAPI routes.

Missing or unmatched real endpoints:

| Frontend call | Evidence | Backend state | Impact |
|---|---|---|---|
| `GET /audit/enforce` | `ui/src/pages/EnforceAuditLog.tsx:43`, `ui/src/pages/ScopeDashboard.tsx:68` | No route in `api/v1/audit.py`; only `GET /audit` exists. | Enforce Audit and Scope Dashboard can render false empty states instead of policy/audit data. |
| `GET /health/checks` | `ui/src/pages/SLAMonitor.tsx:34` | No route in `api/v1/health.py`. | SLA monitor cannot show real check history. |
| `GET /health/uptime` | `ui/src/pages/SLAMonitor.tsx:35` | No route in `api/v1/health.py`. | SLA chart is dead/unverifiable. |
| `POST /workflows/runs/{runId}/cancel` | `ui/src/pages/WorkflowRun.tsx:59` | `api/v1/workflows.py` only has run detail and replan-history routes around lines 620 and 657. | Cancel button on running workflows is broken. |

Acceptance criteria:

- Add backend routes or remove/replace UI calls.
- Add contract tests that enumerate frontend API calls and fail on missing backend routes.
- Add Playwright assertions that these pages display real errors when the API fails, not silent empty states.

### 2. Auth Session Model Is Not Enterprise-Grade Yet

Backend is already moving toward HttpOnly cookies via `agenticorg_session` in `api/v1/auth.py`, but the frontend still treats `localStorage` as the source of truth:

- `ui/src/contexts/AuthContext.tsx:30`, `48`, `70`, `88`, `99`
- `ui/src/lib/api.ts:7`
- `ui/src/pages/ReportScheduler.tsx:263`, `378`, `422`, `432`, `447`
- `ui/src/pages/BillingCallback.tsx:12`, `27`

Impact:

- XSS can exfiltrate bearer tokens.
- Cookie-only migration is incomplete.
- Cross-origin deployments using `VITE_API_URL` will need `credentials: "include"` and Axios `withCredentials: true`; current code does not set this.
- Authenticated Playwright tests seed `localStorage` directly, so they do not validate real login/session behavior.

Acceptance criteria:

- Make frontend session cookie-first.
- Remove browser access-token persistence except for explicit SDK/API-key flows.
- Set `credentials: "include"` / Axios `withCredentials` for browser API calls where required.
- Update login/logout/me flows and E2E tests to use real auth or a controlled test-login endpoint.
- Keep API-key bearer auth for SDK/MCP separate from browser session auth.

### 3. Test Suite Is Not a Reliable Release Gate

Problems:

- Full pytest timed out in `test_embed_with_bge_m3_returns_1024_dim_vectors` because it downloads BGE-M3 weights at test runtime.
- Full unit suite did not complete within 15 minutes.
- Existing `coverage_report.json` before this audit shows 56% global coverage, floor 55%, target 80%, critical target 90%.
- Coverage gaps are severe for enterprise-critical modules: `api/v1/auth.py` 50%, `api/v1/billing.py` 40%, `api/v1/connectors.py` 36%, `core/tasks/workflow_tasks.py` 15%.

Acceptance criteria:

- Mark the BGE-M3 model-load smoke test as opt-in, mocked, or CI-image-only. It must not download large external weights during normal unit/regression runs.
- Split tests into fast unit, integration, slow/model, production E2E, and load suites.
- Ensure `npm run lint`, `npm run test`, `npm run build`, backend ruff, fast pytest, and contract tests finish under a practical CI SLA.
- Raise global coverage to at least 80%; raise auth, tenant isolation, billing, connector credential, workflow execution, and audit paths toward 90%.

### 4. Authenticated E2E Is Not Verifiable Locally by Default

`ui/e2e/app-routes.spec.ts` requires `E2E_TOKEN`. Without it, public routes passed but authenticated dashboard, sidebar, create-flow, and data-quality tests failed before entering the app.

Acceptance criteria:

- Provide a documented local E2E path that starts DB/Redis/API/UI, seeds a test tenant, logs in through the UI, and runs authenticated Playwright.
- Do not rely on production tokens for normal PR validation.
- Keep a separate production smoke suite for post-deploy validation.

## UI/UX Flow Gap Analysis

### Public Marketing, Pricing, Blog, Evals, Playground, Login

Status: public route smoke passed.

Gaps:

- Demo-request forms use direct `fetch("/api/v1/demo-request")` across multiple pages. Confirm spam protection, rate limiting, validation, CRM handoff, and user-visible failure states.
- Build output shows large public content chunks; ensure Core Web Vitals are measured, not guessed.
- Some source/doc output shows mojibake in console (`â€”`, `â†’`). Verify source encoding and rendered production text.

### Signup, Login, Password Reset, Invite, SSO

Status: code exists, but E2E not verified.

Gaps:

- Token persistence remains in `localStorage`.
- No complete cookie-only browser test.
- No visible MFA enrollment/recovery flow, despite `mfa_enabled` in `/auth/me`.
- Login/session expiry behavior relies on global 401 redirect and can discard context.

Claude tasks:

- Add full auth E2E: signup, login, logout, expired token, password reset, invite accept, SSO callback hydration, role-gated 403.
- Add session-state UI for expired sessions and return-to-original-route behavior.

### Dashboard and Navigation

Status: role-filtered navigation exists in `Layout`.

Gaps:

- Company switching persists `company_id` in `localStorage` and calls `window.location.reload()` (`ui/src/components/CompanySwitcher.tsx:56`). This is a brittle UX and hides state/data invalidation bugs.
- Header widgets fail silently when their APIs fail.
- Navigation is role-hidden, but there is no centralized capability/permission manifest shared by UI and API.

Claude tasks:

- Replace reload-based company switching with context/query invalidation.
- Add visible, actionable error states for header widgets.
- Generate nav access from a shared role/capability manifest or test it against backend scopes.

### Agents

Status: agent CRUD/lifecycle API surface is broad and mostly matched by UI.

Gaps:

- Lint/hook warnings in agent pages mean stale closures are possible.
- Some mutations use native `window.confirm`; enterprise UX should use accessible confirmation dialogs with irreversible-action wording.
- Need E2E coverage for create, edit, promote, pause, resume, rollback, clone, delete, run, retest, feedback analysis, and prompt-history audit.

### Workflows

Status: create/list/run/detail APIs exist.

Blocking gap:

- Workflow run cancel UI calls a missing endpoint.

Other gaps:

- Background execution uses FastAPI background tasks; ensure durability across process restarts.
- Cancellation, retry, replan, HITL resume, and partial-failure states need end-to-end tests.

Claude tasks:

- Add `POST /workflows/runs/{run_id}/cancel`, persist cancellation, stop/resume engine state safely, and test UI behavior.
- Add workflow run E2E for running, waiting HITL, failed, cancelled, completed, and replan-history.

### Approvals / HITL

Status: endpoints and UI exist.

Gaps:

- Needs audited E2E for approve/reject, note requirements, expiry, assignee roles, notification delivery, and double-submit/idempotency.
- UI should expose backend errors and stale approval decisions clearly.

### Audit, Enforce Audit, Scope Dashboard

Status: regular audit endpoint exists.

Blocking gap:

- Enforce audit endpoint does not exist, but two protected pages depend on it.

Impact:

- Enterprise governance posture is overstated until enforcement events are persisted and queryable.

Claude tasks:

- Decide data source for enforce decisions: audit log event type, dedicated table, or tool gateway event stream.
- Implement `GET /audit/enforce` with filters, pagination, company/tenant isolation, and export.
- Update Scope Dashboard to consume the same backend contract.

### SLA / Health / Observability

Status: `/health`, `/health/liveness`, and `/health/diagnostics` exist.

Blocking gap:

- SLA UI calls `/health/checks` and `/health/uptime`, which do not exist.
- `/health` does not return `p95_latency`, `agent_success_rate`, or `hitl_response_time`, but the UI tries to render them.

Claude tasks:

- Implement real SLA metrics endpoints backed by telemetry/storage.
- Add uptime/check history persistence or remove the chart.
- Ensure diagnostics remains admin-gated and readiness remains safe for probes.

### Billing

Status: billing APIs and callback page exist.

Gaps:

- Billing callback uses `localStorage` to decide logged-in state and bearer auth. This will break or misclassify sessions under cookie-only auth.
- Need E2E for India payment flow, Stripe flow, webhook idempotency, cancellation, portal, failed/pending payment, and callback replay.

### Report Scheduler

Status: UI and API exist.

Gaps:

- `ReportScheduler` bypasses shared Axios client and manually reads `localStorage` token.
- Native `alert` and `confirm` are used for run/delete flows.
- Need E2E around create/edit/toggle/delete/run-now and delivery-channel validation.

### Knowledge Base / RAG

Status: APIs and tests exist.

Blocking test gap:

- BGE-M3 smoke test loads real model weights at test runtime.

Operational gaps:

- TEI fallback behavior must be tested under warm, cold, timeout, and degraded keyword-search modes.
- Backfill/cutover must be rehearsed with real data volume and rollback.

### Connectors / Integrations / Composio / MCP / A2A

Status: broad API surface exists.

Gaps:

- Need per-connector health, credential rotation, OAuth reconnect, and tool-call permission tests.
- Public tool discovery endpoints should be explicitly threat-modeled and documented.
- Connector test failures should be shown consistently in the UI, not hidden as empty lists.

### Companies / CA Firm Flows

Status: extensive company and partner-dashboard APIs exist.

Gaps:

- Company context is browser-local, not a first-class request/session context.
- Need E2E for company onboarding, roles, credentials, Tally bridge generation, filing approvals, deadlines, GSTN upload, and deletion/deactivation.
- Bank account and compliance identifiers need masking rules in UI and audit logs.

## Frontend Engineering Gaps

1. Lint is failing. Fix `ChatPanel.tsx:72` and resolve or intentionally document hook dependencies.
2. Too many pages swallow errors with bare `catch { ... }`. Enterprise users need clear failure states and retry actions.
3. Native `alert`/`confirm` appears in billing, report scheduler, agent delete, connector delete, company delete/deactivate, etc. Replace with accessible modal dialogs.
4. Shared API client is not used consistently. `ReportScheduler`, auth, billing callback, public forms, and playground use direct `fetch`.
5. No consistent request cancellation/loading/error pattern. Use React Query or a shared resource hook for app pages.
6. `localStorage` stores security-sensitive session data and company context.
7. Authenticated E2E relies on localStorage token seeding instead of real login.
8. No automated visual/mobile regression output was reviewed in this audit.

## Backend Engineering Gaps

1. Missing endpoints called by UI.
2. HTTP error response shapes are mixed: custom envelope in `api/error_handlers.py`, default FastAPI `detail` elsewhere, middleware JSON with `detail`, and some `{error: ...}` responses. Standardize.
3. Cookie migration is half-done. Finish it before treating browser auth as enterprise-grade.
4. Full pytest is non-hermetic and too slow.
5. Coverage target is not enterprise-grade yet.
6. Health/SLA endpoints need real telemetry backing, not UI placeholders.
7. Background workflow and report execution need durability, idempotency, cancellation, retry, and observability tests.
8. Sensitive data masking needs explicit tests across UI, audit, exports, and logs.
9. Public unauthenticated endpoints and webhook endpoints need a reviewed threat model.

## Claude Code Implementation Plan

### Phase 1 - Stop Broken Flows

- Fix frontend lint.
- Add missing backend routes or remove dead UI calls:
  - `GET /audit/enforce`
  - `GET /health/checks`
  - `GET /health/uptime`
  - `POST /workflows/runs/{run_id}/cancel`
- Add API contract test that fails on frontend/backend route drift.
- Replace silent empty states on Enforce Audit, Scope Dashboard, SLA Monitor, and Workflow Run with explicit error panels.

### Phase 2 - Auth and E2E Hardening

- Move browser auth to cookie-first.
- Add `withCredentials`/`credentials: "include"` where needed.
- Remove access-token `localStorage` usage from browser flows.
- Build local authenticated E2E with seeded tenant and real login.
- Keep production E2E as separate post-deploy smoke with documented `E2E_TOKEN`.

### Phase 3 - Test Gate Repair

- Split pytest suites and mark slow/model/network tests.
- Mock or opt-in the BGE-M3 model-load test.
- Make fast backend suite complete under 10 minutes.
- Enforce coverage thresholds per critical module.
- Add Playwright coverage for each major app route and mutation flow.

### Phase 4 - Enterprise UX Polish

- Replace native alerts/confirms with accessible modals.
- Replace hard reload company switching with state invalidation.
- Standardize loading, empty, partial, and error states.
- Add mobile and responsive E2E screenshots for all primary workflows.
- Add audit/export masking for PII and financial identifiers.

### Phase 5 - Operational Readiness

- Implement real SLA metrics and uptime history.
- Add workflow/report job durability and cancellation semantics.
- Add webhook idempotency and replay protection tests.
- Document and test backup/restore, tenant deletion/export, incident response, and secrets rotation.

## Definition of Done

The project can be considered enterprise-release candidate only when:

- `npm run lint`, `npm run typecheck`, `npm run test`, and `npm run build` pass.
- Backend ruff and fast pytest pass without network/model downloads.
- Full authenticated local Playwright suite passes from a clean seed.
- No frontend API call points to a missing backend route.
- Browser session auth no longer stores access tokens in `localStorage`.
- P0 pages show actionable failures instead of empty states.
- Coverage is at least 80% global and materially higher on auth, tenant isolation, billing, connector credentials, workflows, audit, and governance.
- SLA/health pages are backed by real telemetry.
- A production smoke suite validates public routes, login, dashboard, agent run, workflow run, approval decision, connector health, billing callback, and audit export.
