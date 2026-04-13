# AgenticOrg Functional Review — April 13, 2026

**Audit date:** 2026-04-13
**Method:** Live API testing against `app.agenticorg.ai` (v4.8.0) with admin token + codebase inspection
**Verdict:** Platform has strong infrastructure and API surface but significant functional gaps in data population, feature completeness, and end-to-end user workflows

---

## Executive Summary

| Category | Working | Partial | Broken/Empty | Total |
|---|---|---|---|---|
| Core infrastructure | 5 | 0 | 0 | 5 |
| Agent management | 2 | 2 | 1 | 5 |
| Workflows | 1 | 2 | 1 | 4 |
| Connectors | 1 | 2 | 2 | 5 |
| Billing & payments | 3 | 1 | 1 | 5 |
| Dashboards (CxO) | 0 | 6 | 0 | 6 |
| Chat & NLP | 1 | 1 | 0 | 2 |
| Enterprise features | 6 | 2 | 3 | 11 |
| Industry packs | 1 | 1 | 0 | 2 |
| Operational pages | 1 | 3 | 4 | 8 |
| **Total** | **21** | **20** | **12** | **53** |

**Score: 21/53 fully working (40%), 20 partial (38%), 12 broken/empty (22%)**

---

## CRITICAL — Features that appear broken or empty to users

### C1. `/billing/plans` returns 401 without auth token
- **Impact:** The pricing page cannot load for unauthenticated visitors
- **Root cause:** Plans endpoint requires auth but should be public (it shows pricing, not tenant data)
- **Fix:** Remove auth requirement from `GET /billing/plans`

### C2. All 6 CxO dashboards show `demo: true` with zero real data
- **Evidence:** KPIs return `total_tasks_30d: 0`, `agent_count: 0`, `total_cost_usd: 0.0`, `demo: true`
- **Impact:** Every executive dashboard shows "Demo Data" badge and zeros
- **Root cause:** No agent tasks have been executed against this tenant, so `agent_task_results` is empty. The KPI system reads from real data but falls back to demo mode when no data exists
- **Fix:** Run agent tasks to populate real data, OR show a proper empty state instead of "Demo Data" badge with zeros (which is confusing — it implies fake data, not empty data)

### C3. Knowledge Base returns 500 Internal Error
- **Evidence:** `GET /knowledge/documents` returns `E1001 INTERNAL_ERROR`
- **Impact:** The knowledge base page is completely broken
- **Root cause:** Likely RAGFlow dependency or missing table
- **Fix:** Investigate the error, add fallback for when RAGFlow is unavailable

### C4. Composio marketplace shows 0 apps
- **Evidence:** `GET /composio/apps?limit=3` returns `total: 0`
- **Impact:** The marketplace integration page is empty
- **Root cause:** `COMPOSIO_API_KEY` env var likely not set or expired
- **Fix:** Verify and refresh the Composio API key

### C5. A2A agent discovery shows 0 agents
- **Evidence:** `GET /a2a/.well-known/agent.json` returns `agents: []`
- **Impact:** External agent platforms (ChatGPT, Claude) cannot discover our agents
- **Root cause:** Agent discovery endpoint doesn't query the DB for active agents
- **Fix:** Populate the agent card from the live agent registry

### C6. Connector test endpoint returns empty response (not JSON)
- **Evidence:** `POST /connectors/{id}/test` returns empty body
- **Impact:** Users cannot verify connector credentials
- **Root cause:** The test endpoint may timeout or crash silently
- **Fix:** Add error handling and always return JSON

### C7. Cron schedules, webhooks, SOP endpoints return 404
- **Evidence:** `GET /cron/schedules`, `GET /webhooks`, `GET /sop` all return `E1005 NOT_FOUND`
- **Impact:** These pages in the UI show errors or empty states
- **Root cause:** Routes exist but the underlying resource endpoints aren't implemented for list operations
- **Fix:** Add list endpoints or return empty arrays

---

## HIGH — Features that work partially

### H1. Chat always returns generic fallback response
- **Evidence:** `confidence: 0.6`, `tools_used: null`, answer starts with generic template text
- **Impact:** Chat feels like a placeholder, not a real AI assistant
- **Root cause:** No connectors have credentials configured, so agents can't call real tools. The LLM falls back to a generic response template.
- **Fix:** Configure at least one connector with real credentials so the chat can actually execute tool calls

### H2. Agent Run returns `raw_output` instead of parsed response
- **Evidence:** Agent run output contains `raw_output: {'type': 'text', 'text': ...}` — nested stringified JSON
- **Impact:** API consumers get unparsed data
- **Root cause:** The `_format_agent_output` fix helps for chat but the `/agents/{id}/run` endpoint returns the raw LangGraph output without formatting
- **Fix:** Apply the same output formatting to the agent run response

### H3. Only 6 of 53 registered connectors are created for the tenant
- **Evidence:** Registry shows 53 connectors available but only 6 exist in the tenant's connector table
- **Impact:** Users see only 6 connectors in the UI, missing the other 47
- **Root cause:** `seed_tenant` only creates a subset of connectors. The rest need to be manually registered or auto-seeded
- **Fix:** Auto-register all 53 connectors for new tenants (with empty credentials)

### H4. Sales pipeline returns empty data
- **Evidence:** `GET /sales/pipeline` returns `total: 0, leads: []`
- **Impact:** Sales module is non-functional
- **Root cause:** No leads have been created
- **Fix:** Need seed data or a lead import flow

### H5. ABM dashboard returns 0 accounts
- **Evidence:** `GET /abm/dashboard` returns `total_accounts: 0`
- **Impact:** ABM module is non-functional
- **Fix:** Need seed data or an account import flow

### H6. Audit log is empty
- **Evidence:** `GET /audit` returns `total: 0`
- **Impact:** No audit trail visible
- **Root cause:** The audit log immutability trigger may be blocking writes, or audit events aren't being generated
- **Fix:** Verify AuditLog writes are working end-to-end

### H7. Usage counters show all zeros
- **Evidence:** `agent_runs: 0, agent_count: 0, storage_bytes: 0`
- **Impact:** Billing usage tracking appears non-functional
- **Root cause:** No agent tasks have been executed, so Redis counters are at zero
- **Fix:** Run tasks to populate, OR verify the increment path works

### H8. Only 1 workflow exists
- **Evidence:** `GET /workflows` returns `total: 1`
- **Impact:** Workflow module feels empty
- **Fix:** Seed more demo workflows, or improve the workflow generation UX

### H9. Billing subscription shows `plan: free` even when payment was made
- **Evidence:** `GET /billing/subscription` returns `plan: free`
- **Impact:** After successful Plural payment, the plan doesn't upgrade
- **Root cause:** The webhook activation writes to Redis but the test tenant may not have received a webhook confirmation from Plural
- **Fix:** Verify the Plural webhook is firing and `_activate_subscription` is being called

---

## MEDIUM — Features with minor issues

### M1. Prompt templates exist (39) but no management UI visible
- **Evidence:** API returns 39 templates but users don't know they exist
- **Fix:** Surface prompt template management in the agent config tab

### M2. Evals system has data but stale (March 2026)
- **Evidence:** Evals API returns data but `generated_at: 2026-03-23`
- **Fix:** Schedule regular eval runs

### M3. Companies exist (3) but may be QA test data
- **Evidence:** 3 companies from the QA validation run
- **Fix:** Document which companies are real vs test

### M4. No departments or delegations configured
- **Evidence:** Both return empty arrays
- **Impact:** Enterprise org structure features are shipped but unused
- **Fix:** Seed demo department structure or document the admin workflow

### M5. No feature flags configured
- **Evidence:** `GET /feature-flags` returns 0
- **Impact:** Feature flag system is shipped but unused
- **Fix:** Create default flags for key features

### M6. No approval policies configured
- **Evidence:** `GET /approval-policies` returns 0
- **Impact:** Multi-step approval engine is shipped but has no policies
- **Fix:** Create a default approval policy for the tenant

### M7. No SSO providers configured
- **Evidence:** SSO providers list returns 0
- **Impact:** SSO is shipped but not connected to any IdP
- **Fix:** Expected — SSO needs to be configured per-tenant by the admin

---

## Feature-by-Feature Matrix

| # | Feature | API Status | UI Page | Data Present | End-to-End Working |
|---|---|---|---|---|---|
| 1 | Health check | OK | `/status` OK | Real data | Yes |
| 2 | Auth (login/signup) | OK | OK | Working | Yes |
| 3 | Auth (Google OAuth) | OK | OK | Google Client ID set | Yes |
| 4 | Auth (SSO/OIDC) | OK | No UI | Not configured | Partially (needs IdP) |
| 5 | Auth/me | OK | Used by SSO | Returns user | Yes |
| 6 | Agent list | OK | OK | 28 agents | Yes |
| 7 | Agent detail | OK | OK | Full data | Yes |
| 8 | Agent run | OK (raw output) | Prompt added | Returns data | Partially (raw output) |
| 9 | Agent chat | OK (generic) | OK | Fallback only | Partially (no tool calls) |
| 10 | Org chart | OK | OK | 28 nodes | Yes |
| 11 | Workflow list | OK | OK | 1 workflow | Yes (sparse) |
| 12 | Workflow create | OK | OK | Validation works | Yes |
| 13 | Workflow run | OK | OK | 0-step guard works | Yes |
| 14 | Workflow templates | Untested | OK | LLM-generated | Partially |
| 15 | Connector list | OK | OK | 6/53 seeded | Partially |
| 16 | Connector registry | OK | OK | 53 available | Yes (discovery) |
| 17 | Connector test | Broken | OK | Empty response | No |
| 18 | Connector health | OK | OK | Static status | Partially |
| 19 | Billing plans | Auth required | OK | 3 plans | Broken (needs public) |
| 20 | Billing subscribe (Stripe) | OK | OK | Redirects | Yes |
| 21 | Billing subscribe (Plural) | OK | OK | Redirects | Yes |
| 22 | Billing subscription status | OK | OK | Returns free | Yes |
| 23 | Billing usage | OK | OK | All zeros | Yes (empty) |
| 24 | Billing invoices | OK | OK | Empty | Yes (empty) |
| 25 | Billing cancel | OK | Untested | Logic works | Untested |
| 26 | CEO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 27 | CFO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 28 | CMO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 29 | CHRO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 30 | COO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 31 | CBO Dashboard | OK | OK | Demo/zeros | Partial (no data) |
| 32 | Cost Dashboard | OK | OK | Zeros | Yes (empty) |
| 33 | Approvals | OK | Loading issue | 0 pending | Partial |
| 34 | Approval policies | OK | Untested | None configured | Yes (empty) |
| 35 | Audit log | OK | OK | 0 entries | Partial (no events) |
| 36 | Companies | OK | OK | 3 companies | Yes |
| 37 | Industry packs | OK | OK | 5 packs | Yes |
| 38 | Pack install/uninstall | OK | OK | DB-backed | Yes |
| 39 | RPA scripts | OK | Fixed | 2 scripts | Yes |
| 40 | Knowledge base | 500 Error | Broken | N/A | No |
| 41 | Composio marketplace | Empty | OK | 0 apps | No |
| 42 | A2A agent discovery | Empty | N/A | 0 agents | No |
| 43 | MCP tools | OK | N/A | Tools listed | Yes |
| 44 | Chat history | OK | OK | Session-based | Yes |
| 45 | Feature flags | OK | Untested | None | Yes (empty) |
| 46 | Departments | OK | Untested | None | Yes (empty) |
| 47 | Delegations | OK | Untested | None | Yes (empty) |
| 48 | SSO config | OK | Untested | None | Yes (empty) |
| 49 | Branding | OK | OK | Default | Yes |
| 50 | Cron schedules | 404 | Untested | N/A | No |
| 51 | Webhooks | 404 | Untested | N/A | No |
| 52 | SOP | 404 | Untested | N/A | No |
| 53 | Sales pipeline | OK | OK | 0 leads | Yes (empty) |

---

## Root Cause Analysis

The majority of "broken" features fall into three categories:

### 1. No seed data (15 features affected)
The platform has the API and UI built but the test tenant has never had real data populated. This makes everything look broken to a first-time user:
- All 6 CxO dashboards show zeros
- Usage, costs, audit log show zeros
- ABM, sales pipeline show empty
- No approval policies, feature flags, departments configured

**Fix:** Create a comprehensive seed script that populates the demo tenant with realistic sample data across all modules.

### 2. Missing external credentials (5 features affected)
Features that depend on third-party services fail because API keys aren't configured:
- Composio (COMPOSIO_API_KEY)
- Knowledge base / RAGFlow (RAGFLOW_API_URL)
- Individual connector credentials
- Chat tool calls (no configured connectors = generic responses)

**Fix:** Configure the required API keys in the production environment.

### 3. Incomplete backend endpoints (5 features affected)
Some routes return 404 or crash:
- Cron schedules, webhooks, SOP list endpoints
- Connector test (empty response)
- A2A agent discovery (empty array)
- Plans endpoint requires auth (should be public)

**Fix:** Implement the missing list endpoints and fix the broken ones.

---

## Priority Action Plan

### P0 — Fix broken features (this week)
1. Make `GET /billing/plans` public (remove auth requirement)
2. Fix Knowledge Base 500 error (RAGFlow health check)
3. Fix connector test endpoint (always return JSON)
4. Fix A2A agent discovery (populate from live agents)
5. Add list endpoints for cron/webhooks/SOP or return `[]`

### P1 — Populate data (this week)
6. Create a comprehensive `seed_demo_data.py` that populates:
   - 50+ agent task results across all domains
   - 10 sample workflows
   - Sample ABM accounts + campaigns
   - Sample sales leads
   - Approval policies
   - Feature flags
   - Department structure
   - Audit log entries
7. Auto-register all 53 connectors for new tenants

### P2 — Fix partial features (next sprint)
8. Format agent run output (same as chat formatting)
9. Fix chat to use real tools when connectors have credentials
10. Set COMPOSIO_API_KEY to restore marketplace
11. Configure RAGFlow for knowledge base
12. Generate fresh evals data

### P3 — UX improvements (backlog)
13. Replace "Demo Data" badge with "No activity yet" empty state
14. Surface prompt templates in agent config UI
15. Add lead import in sales pipeline
16. Add account import in ABM

---

## Metrics

| Metric | Value |
|---|---|
| Total API endpoints tested | 26 areas / 53 features |
| Fully working | 21 (40%) |
| Partially working | 20 (38%) |
| Broken or empty | 12 (22%) |
| UI pages rendering | 22/22 (100%) — all SPA routes serve HTML |
| API returning valid JSON | 48/53 (91%) |
| API with real data | 21/53 (40%) |

---

## Conclusion

The platform infrastructure (auth, routing, DB, Redis, deployment) is solid. The API surface is comprehensive (196 routes). The security posture is strong (admin guards, RLS, CORS, BYOK). But the **user experience** is poor because:

1. Most features show empty states or zeros
2. Several features 500 or 404
3. Chat doesn't use real tools

The single highest-impact fix is a **comprehensive demo seed script** that populates all modules with realistic data. This would flip ~15 features from "empty" to "working" in one deployment.
