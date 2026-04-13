# AgenticOrg v4.9.0 — Detailed Requirements Document

**Date:** 2026-04-13
**Author:** Engineering
**Supersedes:** `docs/V4_9_0_ROADMAP.md` (high-level overview)
**Current state:** v4.8.0, 51/53 features working, 244 API routes, 3289 tests

This document provides implementation-ready requirements for every
v4.9.0 work item with acceptance criteria, test cases, affected files,
and exact current-state references so development can proceed with
zero ambiguity.

---

## REQ-01: Composio Marketplace — Fix Runtime Dependencies

### Current State
- SDK: `composio-core==0.7.21` installed in Docker image
- API key: `ak_SVwqxI0nGPoA...` set via `COMPOSIO_API_KEY` env var
- `api/v1/composio.py:85` calls `ComposioToolSet.get_apps()`
- Import guard at line 25: `_COMPOSIO_AVAILABLE = False` if SDK missing
- **Failure:** `libjpeg.so.62: cannot open shared object file` at runtime
- Dockerfile runtime stage has `libjpeg62-turbo` added (commit `b6eede1`) but may not have deployed

### Requirements
1. The Docker runtime stage must include `libjpeg62-turbo` and `zlib1g`
2. `GET /composio/apps` must return `total > 0` with valid app entries
3. Search and category filtering must work
4. Cache must refresh every 10 minutes

### Acceptance Criteria
- `curl /api/v1/composio/apps?limit=5` returns `total >= 100`
- `curl /api/v1/composio/apps?search=slack` returns Slack app
- `curl /api/v1/composio/categories` returns category list
- No `libjpeg` or import errors in pod logs

### Test Cases
```
TC-01: GET /composio/apps returns non-empty list
TC-02: GET /composio/apps?search=github returns filtered results
TC-03: GET /composio/apps?category=crm returns CRM-only apps
TC-04: Cache expiry: apps refresh after 10 minutes
TC-05: SDK import failure returns HTTP 503 with clear message
TC-06: Invalid API key returns HTTP 502 with error detail
```

### Files
- `Dockerfile:20-22` (runtime deps)
- `api/v1/composio.py:76-113` (endpoint)
- `api/v1/composio.py:52` (`_get_toolset()`)

### Effort: 1 hour (verify deploy) or 1 day (if key expired)

---

## REQ-02: Alembic as Sole DDL Delivery Path

### Current State
- `api/main.py:71` calls `init_db()` on every startup
- `core/database.py:75-750+` contains ~700 lines of runtime DDL
- `migrations/versions/` has 8 Alembic files (v400→v470) but they are documentation-only
- No `alembic.ini` or `env.py` exists
- `migrations/README.md` documents the dual-track reality

### Requirements
1. Generate `alembic.ini` pointing at `core.config.settings.db_url`
2. Generate `env.py` that imports `core.models.base.BaseModel.metadata`
3. Run `alembic stamp v470_sso_invoices` on production to mark the current head
4. Add a CI job: `alembic check` fails if `alembic revision --autogenerate` shows any diff
5. Add RLS policies to the v4.7.0 migration file (already done in `init_db()` but missing from migrations)
6. Add `connector_configs` table to a migration (currently only in `init_db()`)
7. Add `knowledge_documents` table to a migration
8. Reduce `init_db()` to: connectivity check + read-only validation
9. Document the stamp + migration process in `migrations/README.md`

### Acceptance Criteria
- A fresh database built from `alembic upgrade head` alone has ALL tables, indexes, RLS policies, and triggers
- `alembic check` returns 0 diff on the current codebase
- CI fails any PR that adds/changes an ORM model column without a corresponding migration
- `init_db()` contains zero `CREATE TABLE` or `ALTER TABLE` statements
- Startup without migrations present fails with a clear error ("run alembic upgrade head first")
- Production rollback via `alembic downgrade -1` works for the most recent migration

### Test Cases
```
TC-01: Fresh DB + alembic upgrade head → all tables exist
TC-02: Fresh DB + alembic upgrade head → RLS policies exist on all tenant tables
TC-03: Fresh DB + alembic upgrade head → audit_log immutability trigger exists
TC-04: alembic check returns "No new revision" on clean state
TC-05: Adding a column to an ORM model without migration fails CI
TC-06: alembic downgrade -1 removes the last migration cleanly
TC-07: Startup without running migrations logs an error and exits
TC-08: Production stamp at v470 succeeds without side effects
```

### Files
- `alembic.ini` (new)
- `migrations/env.py` (new)
- `migrations/versions/v4_8_0_remaining_tables.py` (new — connector_configs, knowledge_documents)
- `core/database.py` (reduce to connectivity only)
- `.github/workflows/deploy.yml` (add `alembic check` step)

### Effort: 3-5 days

---

## REQ-03: RLS Session Fix — `async_session_factory` → `get_tenant_session`

### Current State

The following files use `async_session_factory()` for tenant-scoped queries. Per the codebase audit, ALL of these query global tables (User, Tenant, APIKey) that do NOT have RLS, so there is no active data leak. However, the pattern is fragile — any new RLS table queried through these sessions will silently return 0 rows.

| File | Lines | Tables Queried | RLS? |
|---|---|---|---|
| `api/v1/invoices.py` | 51, 68 | Invoice | No (but should be) |
| `api/v1/workflow_variants.py` | 60, 77, 119 | WorkflowVariant | Yes (RLS enabled) |
| `api/v1/sso.py` | 57, 96, 196, 265, 291, 347 | SSOConfig | Yes (RLS enabled) |
| `api/v1/branding.py` | 148, 190, 206, 234 | TenantBranding | Yes (RLS enabled) |
| `api/v1/auth.py` | 99, 202, 282, 378, 421, 470 | User, Tenant | No (global) — SKIP |
| `api/v1/api_keys.py` | 89, 100, 135, 160, 192 | APIKey | No (global) — SKIP |
| `api/v1/org.py` | 83, 109, 150, 232, 287, 323 | User, Tenant | No (global) — SKIP |
| `api/v1/health.py` | 55, 95 | None (SQL SELECT 1) — SKIP | — |
| `api/v1/demo.py` | 65, 94 | Demo requests | No — SKIP |

### Requirements
1. Replace `async_session_factory()` with `get_tenant_session(tid)` in:
   - `invoices.py` (2 call sites)
   - `workflow_variants.py` (3 call sites)
   - `sso.py` (6 call sites)
   - `branding.py` admin endpoints only (4 call sites, public GET stays global)
2. Verify `tid = uuid.UUID(tenant_id)` is defined before each `get_tenant_session(tid)` call
3. DO NOT change `auth.py`, `api_keys.py`, `org.py`, `health.py`, `demo.py` — they correctly use global sessions for non-tenant-scoped tables

### Acceptance Criteria
- `GET /billing/invoices` returns invoices for the authenticated tenant
- `GET /workflows/{id}/variants` returns variants for the authenticated tenant
- `GET /sso/configs` returns SSO configs for the authenticated tenant
- `PUT /admin/branding` persists branding for the authenticated tenant
- Tenant A cannot see tenant B's invoices, variants, SSO configs, or branding

### Test Cases
```
TC-01: GET /billing/invoices with tenant A token returns only A's invoices
TC-02: GET /billing/invoices with tenant B token returns only B's invoices
TC-03: GET /workflows/{id}/variants returns 0 for workflow owned by another tenant
TC-04: GET /sso/configs returns empty for tenant with no SSO configured
TC-05: PUT /admin/branding persists and GET /admin/branding reads it back
TC-06: Branding public GET (no auth) returns the default, not another tenant's branding
```

### Files
- `api/v1/invoices.py` (lines 51, 68)
- `api/v1/workflow_variants.py` (lines 60, 77, 119)
- `api/v1/sso.py` (lines 57, 96, 196, 265, 291, 347)
- `api/v1/branding.py` (lines 190, 206, 234 — admin only)

### Effort: 1 day

---

## REQ-04: Auth Throttling + Token Revocation to Redis

### Current State

All auth security state is in per-process memory:

| Variable | File:Line | Purpose | Risk |
|---|---|---|---|
| `_failed_attempts` | `auth/middleware.py:15` | IP → failed auth timestamps | Resets on restart; split across pods |
| `_blocked_ips` | `auth/middleware.py:16` | Blocked IPs | Same |
| `_failed_attempts` | `auth/grantex_middleware.py:28` | Duplicate of above | Same |
| `_blacklisted_tokens` | `auth/jwt.py:26` | Token → expiry map | Revoked tokens revalidate after restart |
| `_signup_attempts` | `api/v1/auth.py:34` | IP → signup timestamps | 5/hour limit resets on restart |
| Login rate limit | `api/v1/auth.py:185-187` | Implicit in _failed_attempts | Same |

### Requirements
1. Create `core/auth_state.py` with async Redis-backed implementations:
   - `record_auth_failure(ip: str) -> bool` — returns True if IP should be blocked
   - `is_ip_blocked(ip: str) -> bool`
   - `blacklist_token(token_hash: str, expires_at: float)`
   - `is_token_blacklisted(token_hash: str) -> bool`
   - `check_signup_rate(ip: str) -> bool` — returns True if within limit
2. Use Redis `INCR` + `EXPIRE` for atomic counters (no race conditions)
3. Keep in-memory as L1 read cache with 5-second TTL
4. Token blacklist: hash the token with SHA-256 before storing (don't store raw JWTs in Redis)
5. All state must survive pod restart and be consistent across 2+ replicas

### Acceptance Criteria
- After 10 failed logins from IP X on pod A, IP X is blocked on pod B
- After `POST /auth/logout` on pod A, the token is rejected on pod B
- After pod A restarts, blocked IPs and blacklisted tokens persist
- Signup rate limit (5/hour) works correctly across 2 pods
- If Redis is down, auth falls back to in-memory (fail-open, logged)

### Test Cases
```
TC-01: 10 failed logins → IP blocked across pods
TC-02: Blocked IP auto-unblocks after 15 minutes
TC-03: POST /auth/logout → token rejected on different pod
TC-04: Token blacklist survives pod restart
TC-05: 6th signup from same IP within 1 hour returns 429
TC-06: Signup rate resets after 1 hour
TC-07: Redis down → auth works with in-memory fallback + warning log
TC-08: Token stored as SHA-256 hash (not raw JWT) in Redis
```

### Files
- `core/auth_state.py` (new)
- `auth/middleware.py` (lines 15-16, 58, 98-107)
- `auth/grantex_middleware.py` (lines 28, 95, 261-269)
- `auth/jwt.py` (lines 26, 56-64, 82-96)
- `api/v1/auth.py` (lines 34, 89-94, 185-187)

### Effort: 3-5 days

---

## REQ-05: Async Redis in SSO/Billing Handlers

### Current State

Sync Redis client used in async request handlers:

| File:Line | Function | What it does |
|---|---|---|
| `api/v1/sso.py:47` | `_redis()` | Returns sync `_get_redis()` for SSO state |
| `api/v1/sso.py:139-171` | `sso_login`, `sso_callback` | State store/retrieve for OIDC PKCE flow |
| `api/v1/billing.py:383` | `cancel_subscription` | Reads/writes billing state |
| `api/v1/status.py:52` | `_redis()` | Reads uptime/incidents |

### Requirements
1. Create `core/async_redis.py` with `async_redis_client()` that returns `redis.asyncio.Redis`
2. Replace all `_get_redis()` calls in async handlers with `await async_redis_client()`
3. SSO state operations (`setex`, `get`, `delete`) must use `await`
4. Billing cancel Redis reads/writes must use `await`
5. Connection pooling: reuse a single `redis.asyncio` connection pool per process

### Acceptance Criteria
- SSO login/callback flow works end-to-end with async Redis
- Billing cancel reads subscription_id from Redis without blocking
- No `RuntimeWarning: coroutine was never awaited` in logs
- Status page loads without event loop blocking

### Test Cases
```
TC-01: SSO login stores state in Redis (async)
TC-02: SSO callback reads state from Redis (async)
TC-03: SSO callback deletes state after use (one-shot)
TC-04: Billing cancel reads stripe_subscription_id (async)
TC-05: Redis connection pool reused across requests
TC-06: Redis connection failure returns HTTP 503 (fail-closed for SSO)
TC-07: No sync Redis calls remain in any async handler
```

### Files
- `core/async_redis.py` (new)
- `api/v1/sso.py` (lines 47, 139-171)
- `api/v1/billing.py` (line 383+)
- `api/v1/status.py` (line 52)

### Effort: 2-3 days

---

## REQ-06: SAML 2.0 via xmlsec Sidecar

### Current State
- ADR: `docs/adr/0007-saml-via-xmlsec-sidecar.md` (approved)
- OIDC: Fully implemented (`auth/sso/oidc.py`, `api/v1/sso.py`)
- `api/v1/sso.py:68-71`: SAML config raises HTTP 400 with pointer to ADR
- `SSOConfig.provider_type` already accepts "saml" in the schema

### Requirements
1. Build `infra/saml-sidecar/Dockerfile` with `python3-saml` + `libxmlsec1`
2. Sidecar exposes HTTP endpoints on unix socket `/var/run/agenticorg/saml.sock`:
   - `POST /authn-request` → returns base64 AuthnRequest + RelayState
   - `POST /authn-response` → validates signature, returns extracted attributes
3. Create `auth/sso/saml.py` that calls the sidecar via `httpx` unix transport
4. Wire into `api/v1/sso.py` — when `provider_type == "saml"`, route to SAML handler
5. Add sidecar container to Helm chart (conditional on `sso.saml.enabled`)
6. Test with Shibboleth test IdP Docker image

### Acceptance Criteria
- Admin can create SSO config with `provider_type: "saml"` and IdP metadata URL
- User clicks "Login with SAML" → redirected to IdP → authenticates → returned with valid session
- JIT provisioning works for SAML users (same as OIDC)
- Sidecar restart doesn't crash the main API (health check independent)

### Test Cases
```
TC-01: POST /sso/configs with provider_type=saml succeeds
TC-02: GET /auth/sso/{provider}/login redirects to IdP
TC-03: GET /auth/sso/{provider}/callback processes SAML response
TC-04: JIT provisioning creates user on first SAML login
TC-05: Invalid SAML response returns 400
TC-06: Sidecar down returns 503 for SAML operations (OIDC unaffected)
TC-07: Sidecar health probe (/healthz) returns 200
TC-08: SAML SLO is NOT supported (returns 501)
```

### Files
- `infra/saml-sidecar/Dockerfile` (new)
- `infra/saml-sidecar/app.py` (new)
- `auth/sso/saml.py` (new)
- `api/v1/sso.py` (modify SAML branch)
- `helm/templates/deployment.yaml` (add sidecar container)
- `helm/values.yaml` (add `sso.saml.enabled` flag)

### Effort: 3-6 days

---

## REQ-07: Connector Secret End-to-End Encryption

### Current State
- **Write path (CREATE):** `api/v1/connectors.py:195` sets `auth_config={}` on Connector model. Lines 209-226 encrypt secrets to `ConnectorConfig.credentials_encrypted` via `encrypt_for_tenant()`.
- **Write path (UPDATE):** `api/v1/connectors.py:271` blocks `auth_config` in `_blocked_fields`. Lines 278-305 encrypt to ConnectorConfig if `auth_config` in update body.
- **Read path (GATEWAY):** `core/tool_gateway/gateway.py:234-248` reads from `ConnectorConfig` first, decrypts `{"_encrypted": ...}` wrapper, falls back to `Connector.auth_config`.
- **Legacy:** `Connector.auth_config` (JSONB) still exists as a column. Old rows may have plaintext secrets.

### Requirements
1. **Backfill migration:** Create a one-time script that reads all `Connector` rows with non-empty `auth_config`, encrypts the values via `encrypt_for_tenant()`, writes to `ConnectorConfig.credentials_encrypted`, and nullifies the original `auth_config` field.
2. **Remove fallback:** After backfill is confirmed, remove the gateway fallback to `Connector.auth_config` (lines 249-258).
3. **API response:** Never return decrypted secrets in any API response. The `has_credentials: bool` field is sufficient.
4. **Audit:** Log every encryption/decryption event with tenant_id and connector_name (not the secret value).

### Acceptance Criteria
- No `Connector.auth_config` row in production has a non-empty value after backfill
- Gateway ONLY reads from `ConnectorConfig.credentials_encrypted`
- New connector create stores secrets ONLY in encrypted form
- Connector update cannot set `auth_config` directly
- Audit log records every credential access at execution time

### Test Cases
```
TC-01: POST /connectors with auth_config → secret stored encrypted in ConnectorConfig
TC-02: GET /connectors/{id} returns has_credentials=true, no secret values
TC-03: PUT /connectors/{id} with auth_config → encrypted update to ConnectorConfig
TC-04: Gateway reads from ConnectorConfig, not Connector.auth_config
TC-05: Backfill migrates existing plaintext to encrypted form
TC-06: After backfill, Connector.auth_config is empty for all rows
TC-07: Decryption failure returns clear error (not crash)
TC-08: BYOK tenant's KEK is used instead of platform KEK
```

### Files
- `core/crypto/backfill_connector_secrets.py` (new)
- `core/tool_gateway/gateway.py` (remove lines 249-258)
- `api/v1/connectors.py` (verify write path)
- `core/crypto/tenant_secrets.py` (audit logging)

### Effort: 3-5 days

---

## REQ-08: Real Connector Health Checks

### Current State
- Base: `connectors/base_connector.py:108` — `health_check()` does HTTP GET on `base_url` with timeout
- Test endpoint: `api/v1/connectors.py:340` — `POST /connectors/{id}/test` runs connect + health_check
- Most connectors inherit the base health_check without API-specific verification
- `_has_credentials()` guard returns "not_configured" if no token/key set

### Requirements
For each of the top 10 connectors, implement a `health_check()` override that calls a lightweight API endpoint:

| Connector | Health Check Call | Expected Response |
|---|---|---|
| zoho_books | `GET /api/v3/organizations` | 200 with org list |
| tally | TCP connect to bridge port | Connection success |
| gstn | `GET /taxpayerapi/v2.0/search` | 200 with status |
| hubspot | `GET /crm/v3/objects/contacts?limit=1` | 200 |
| salesforce | `GET /services/data/vXX.0/sobjects` | 200 |
| slack | `POST /api/auth.test` | `ok: true` |
| google_ads | `GET /v17/customers/` | 200 |
| stripe | `GET /v1/balance` | 200 with balance |
| quickbooks | `GET /v3/company/{id}/companyinfo/{id}` | 200 |
| jira | `GET /rest/api/3/myself` | 200 with user |

### Acceptance Criteria
- `POST /connectors/{id}/test` runs the API-specific health check (not generic HTTP probe)
- Health check timeout: 10 seconds
- Result includes: `tested: true/false`, `latency_ms`, `error` (if any)
- `connector.health_check_at` timestamp updated after each test

### Test Cases (per connector)
```
TC-01: Health check with valid credentials returns healthy
TC-02: Health check with invalid credentials returns unhealthy + error
TC-03: Health check with no credentials returns not_configured
TC-04: Health check timeout returns unhealthy + "timeout" error
TC-05: Health check updates connector.health_check_at in DB
```

### Files
- `connectors/finance/zoho_books.py` (add `health_check()`)
- `connectors/finance/tally.py`
- `connectors/finance/gstn.py`
- `connectors/marketing/hubspot.py`
- `connectors/marketing/salesforce.py`
- `connectors/comms/slack.py`
- `connectors/marketing/google_ads.py`
- `connectors/finance/stripe.py`
- `connectors/finance/quickbooks.py`
- `connectors/ops/jira.py`

### Effort: 5-7 days

---

## REQ-09: Chat with Real Tool Calls

### Current State
- `api/v1/chat.py:263-327`: Chat endpoint classifies domain, finds agent, calls LangGraph
- `api/v1/chat.py:298`: Uses `run_agent()` when agent_id found
- `api/v1/chat.py:330-337`: Generic fallback when no agent or tools available
- Confidence: `0.6` (no tools) / `0.85` (tools used) / extracted from LLM
- **No connectors have real credentials configured** → every query falls back to generic

### Requirements
1. Configure Zoho Books connector with real credentials (OAuth2 token for financetest@edumatica.io)
2. Configure at least one HR connector (Keka or Darwinbox) for CHRO queries
3. Ensure the chat agent resolves to the correct domain connector
4. Tool calls should execute real API operations (read-only for safety)
5. Confidence should reflect actual tool success/failure

### Acceptance Criteria
- "Show outstanding invoices" → calls Zoho Books API → returns real invoice list
- "What is my cash balance?" → calls Zoho Books balance API → returns real number
- "How many employees are on leave?" → calls HR connector → returns real data
- Confidence > 0.85 when tool call succeeds
- Confidence < 0.5 when tool call fails with clear error

### Test Cases
```
TC-01: Finance query → Zoho Books tool call → real invoice data
TC-02: HR query → HR connector tool call → real employee data
TC-03: Tool call failure → error message + low confidence
TC-04: Domain misclassification → fallback to general assistant
TC-05: Multiple tool calls in one query → aggregated result
TC-06: Query with no relevant connector → generic response at 0.6 confidence
```

### Effort: 2-3 days (credential setup + testing)

---

## REQ-10: Workflow Builder Visual Improvements

### Current State
- `ui/src/components/WorkflowBuilder.tsx`: 13 lines total
- Maps `definition.steps` to ReactFlow nodes — basic visualization only
- **Missing:** Template picker, validation, preview, step editing, edge/connection UI, drag-and-drop, domain filter

### Requirements
1. **Template picker:** Modal with domain-filtered workflow templates. User selects a template → pre-populates the builder canvas
2. **Step editor panel:** Click a node → right panel shows step config (agent type, tool, params, condition, timeout)
3. **Validation:** Red border on nodes with invalid config. "Deploy" button disabled until all steps valid
4. **Preview mode:** Tab that shows the JSON workflow definition before deploy
5. **Edge connections:** Drag from node output to next node input. Support sequential + parallel branching
6. **Domain filter:** Dropdown at the top to filter agents and tools by domain

### Acceptance Criteria
- User can create a 5-step workflow without writing JSON
- User can edit any step's config via the side panel
- Invalid steps are highlighted before deploy
- Preview tab shows the correct JSON that will be sent to the API
- Template picker shows at least 5 templates per domain

### Test Cases
```
TC-01: Open workflow builder → shows empty canvas with "Add Step" button
TC-02: Click "Add Step" → node appears on canvas
TC-03: Click node → side panel opens with config fields
TC-04: Edit step config → node label updates
TC-05: Invalid config → red border + error message
TC-06: Click "Preview" → JSON definition matches the canvas
TC-07: Click "Deploy" → POST /workflows creates the workflow
TC-08: Select template → canvas pre-populated with template steps
TC-09: Domain filter → only agents from selected domain shown in step dropdown
TC-10: Drag edge from node A to node B → sequential connection created
```

### Files
- `ui/src/components/WorkflowBuilder.tsx` (full rewrite — ~500 LOC)
- `ui/src/components/StepEditor.tsx` (new — ~200 LOC)
- `ui/src/components/TemplatePickerModal.tsx` (new — ~150 LOC)

### Effort: 5-10 days

---

## REQ-11 through REQ-22: Summary

| REQ | Title | Requirements | Test Cases | Effort |
|---|---|---|---|---|
| REQ-11 | Multi-region DR rehearsal | Apply terraform, promote replica, failover DNS, measure RTO/RPO | 5 TCs: failover, recovery, data consistency, DNS switch, rollback | 5-8 days |
| REQ-12 | Mobile responsive all pages | Tailwind responsive classes on agents detail, workflows, connectors, approvals, audit, settings | 10 TCs: one per page at 375px, 768px, 1200px | 5 days |
| REQ-13 | WCAG 2.1 AA audit | Run axe-core, fix violations, test NVDA, keyboard nav | 8 TCs: skip link, focus ring, contrast, tap targets, screen reader, reduced motion | 3-5 days |
| REQ-14 | Load testing CI | Nightly Locust job against staging, p95 comparison, alert on regression | 4 TCs: below-cap 0% 429, above-cap ~9% 429, gstn canary, CSV output | 2-3 days |
| REQ-15 | Bug bounty program | HackerOne private program, scope definition, payout table, invite researchers | 3 TCs: report submission, triage SLA, payout | 2 days setup |
| REQ-16 | Invoice PDF golden tests | Text extraction from rendered PDF, verify key fields match inputs | 3 TCs: line items, totals, tenant name | 1 day |
| REQ-17 | OIDC callback CI test | Wiremock/stub IdP, full authorize→callback flow in CI | 4 TCs: PKCE, nonce, JIT provisioning, domain allowlist | 2 days |
| REQ-18 | BYOK KMS integration test | Test KMS project, Workload Identity Federation, wrap/unwrap with real key | 3 TCs: platform KEK, customer KEK, key rotation | 2 days |
| REQ-19 | Approval engine E2E test | Testcontainers Postgres, create policy + HITL item + walk state machine | 5 TCs: single approver, 2-of-3 quorum, reject, condition branch, parallel | 3 days |
| REQ-20 | RPA user-defined scripts | DB table for custom scripts, visual step editor, per-tenant storage | 6 TCs: create, edit, run, delete, share, import | 5-10 days |
| REQ-21 | Voice agents (LiveKit) | Deploy LiveKit server, SIP config, webhook trigger, voice-to-text-to-agent | 5 TCs: inbound call, agent response, hangup, concurrent calls, error | 5-10 days |
| REQ-22 | Real-time WebSocket dashboards | Wire broadcast_to_tenant to agent completion events, UI subscribes | 4 TCs: task completed → KPI updates, connection drop → reconnect, multi-tab | 3-5 days |

---

## Test Coverage Summary

| Area | Existing Tests | New Tests Needed | Total |
|---|---|---|---|
| Composio | 0 | 6 | 6 |
| Alembic | 0 | 8 | 8 |
| RLS session fix | 0 | 6 | 6 |
| Auth to Redis | 0 | 8 | 8 |
| Async Redis | 0 | 7 | 7 |
| SAML | 0 | 8 | 8 |
| Connector secrets | 9 (envelope) | 8 | 17 |
| Connector health | 0 | 50 (5 per connector × 10) | 50 |
| Chat tools | 0 | 6 | 6 |
| Workflow builder | 0 | 10 | 10 |
| DR rehearsal | 0 | 5 | 5 |
| Mobile responsive | 0 | 10 | 10 |
| Accessibility | 0 | 8 | 8 |
| Load testing | 0 | 4 | 4 |
| Invoice PDF | 10 | 3 | 13 |
| OIDC CI | 8 | 4 | 12 |
| BYOK KMS | 9 | 3 | 12 |
| Approval E2E | 18 | 5 | 23 |
| RPA custom | 0 | 6 | 6 |
| Voice | 0 | 5 | 5 |
| WebSocket | 0 | 4 | 4 |
| **Total** | **54** | **164** | **218** |

Current test suite: **3,289 tests**
Target after v4.9.0: **3,453+ tests**

---

## Sprint Plan

### Sprint 1 (2 weeks): Foundation + Critical Fixes
- REQ-01: Composio (1 hour)
- REQ-03: RLS session fix (1 day)
- REQ-09: Chat with real tools (2-3 days)
- REQ-02: Alembic (3-5 days)

### Sprint 2 (2 weeks): Security + Auth
- REQ-04: Auth to Redis (3-5 days)
- REQ-05: Async Redis (2-3 days)
- REQ-07: Connector secret E2E (3-5 days)

### Sprint 3 (2 weeks): New Features
- REQ-06: SAML sidecar (3-6 days)
- REQ-08: Connector health checks (5-7 days)

### Sprint 4 (2 weeks): UX + Polish
- REQ-10: Workflow builder (5-10 days)
- REQ-12: Mobile responsive (5 days)

### Sprint 5 (2 weeks): Scale + Launch
- REQ-11: DR rehearsal (5-8 days)
- REQ-13: Accessibility (3-5 days)
- REQ-14: Load testing CI (2-3 days)
- REQ-15: Bug bounty (2 days)

### Backlog (ongoing)
- REQ-16 through REQ-22: As capacity allows
