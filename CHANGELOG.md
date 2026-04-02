# Changelog

All notable changes to AgenticOrg are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [3.2.0] — 2026-04-02

### Added — Tier 1: Marketing Automation
- **Web Push Notifications**: One-tap approve/reject HITL decisions from browser push notifications (ServiceWorker + VAPID). Notification bell dropdown in dashboard header. Push permission toggle per user
- **Email Drip Engine**: Behavior-triggered email sequences — trigger on open, click, or time delay. Re-engage non-openers. Rescore leads after drip completion. New `email_drip_sequence` workflow template
- **A/B Testing**: Create campaign variants, auto-select winners by open rate or CTR, CMO override before sending to remaining audience. New `ab_test_campaign` workflow template
- **Email Webhooks**: SendGrid, Mailchimp, and MoEngage open/click tracking via inbound webhooks (`POST /webhooks/email/{provider}`). Events stored and linked to drip sequences
- **Intent Data Aggregation**: Bombora + G2 + TrustRadius connectors with weighted scoring (40/30/30) for account-level buying signals
- **ABM Dashboard** (`/dashboard/abm`): Target account management, intent heatmap, CSV upload, tier filtering, and one-click campaign launch. Endpoints: `GET/POST /abm/accounts`, `POST /abm/accounts/upload`, `GET /abm/accounts/{id}/intent`, `POST /abm/accounts/{id}/campaign`, `GET /abm/dashboard`
- **Wait Step**: Real time delays in workflows (was stub) — supports minutes, hours, and day-based delays
- **Wait-for-Event Step**: Pause workflow until email opened, link clicked, or form submitted. Used in `lead_nurture` template
- **3 new connectors**: Bombora (intent data API), G2 (buyer intent signals), TrustRadius (review + intent data)
- **4 new workflow templates**: `email_drip_sequence`, `ab_test_campaign`, `abm_campaign`, plus `lead_nurture` now has `wait_for_event` steps
- **Push notification endpoints**: `POST /push/subscribe`, `POST /push/unsubscribe`, `GET /push/vapid-key`, `POST /push/test`

### Changed
- Connector count: 51 → **54** (3 new intent data connectors)
- Tool count: 320+ → **340+** (12 new tools across Bombora, G2, TrustRadius)
- Workflow templates: 11 → **15** (4 new marketing automation templates)
- Marketing connector group: 16 → **19** (added Bombora, G2, TrustRadius)
- Backend tests: 1,196+ → **1,633**
- Frontend vitest: **93** tests
- Playwright E2E: 14 → **17** spec files
- CI E2E now runs against production on every merge to main
- Version: 3.1.0 → **3.2.0**

## [3.1.0] — 2026-04-02

### Added
- **7 new connectors**: GA4, MoEngage, NetSuite, WordPress, Twitter/X, YouTube, Mailchimp — all with real API endpoints from official documentation
- **8 new agents**: Treasury (cash management, sweep, forecast), Expense Manager (receipt OCR, policy enforcement, reimbursement), Rev Rec ASC 606 (performance obligation identification, revenue allocation, journal entries), Fixed Assets (depreciation schedules, impairment testing, disposal), Email Marketing (campaign creation, list segmentation, A/B testing), Social Media (scheduling, engagement monitoring, analytics), ABM (account targeting, intent signals, personalized outreach), Competitive Intel (competitor monitoring, pricing analysis, feature comparison)
- **CFO Dashboard** (`/dashboard/cfo`): Cash Runway, Burn Rate, DSO, DPO, AR/AP Aging (30/60/90/120+), P&L Summary, Bank Balances (via AA), Tax Calendar with filing deadlines
- **CMO Dashboard** (`/dashboard/cmo`): CAC by channel, MQLs/SQLs pipeline, Pipeline Value by stage, ROAS by Channel (Google/Meta/LinkedIn), Email Performance (open/CTR/unsub), Brand Sentiment trend, Content Performance
- **NL Query interface**: Cmd+K global search bar + slide-out chat panel with full conversational UI, agent attribution on every answer, and persistent chat history
- **Multi-company support**: company switcher in top nav for CA firms managing multiple client entities, isolated data per company, cross-company consolidated reporting, RBAC per entity
- **Scheduled Report Engine**: Celery beat scheduler with cron expressions, PDF/Excel output with branded templates, delivery to email/Slack/WhatsApp, Report Scheduler UI for create/manage/toggle/run-now
- **8 new workflow templates**: `month_end_close` (trial balance through close), `daily_treasury` (cash position, sweep, forecast, report), `tax_calendar` (deadline tracking, filing prep, DSC signing), `invoice_to_pay_v3` (OCR through payment execution), `campaign_launch` (brief through monitoring), `content_pipeline` (ideation through publish), `lead_nurture` (scoring through sales handoff), `weekly_marketing_report` (collect metrics, build report, deliver)
- **Report Scheduler UI**: create, manage, toggle on/off, and run-now scheduled reports from the dashboard
- **3 new blog posts**: month-end close optimization, honest ROI measurement framework, CFO story (200-person IT company)

### Fixed
- All 38 stub connectors rewritten with real API endpoints from official documentation — zero stubs remain
- **Tally**: fake REST replaced with proper XML/TDL protocol + bridge agent for remote on-premise instances (WebSocket tunnel, auto-reconnect, heartbeats)
- **Banking AA**: removed illegal payment tools, implemented full RBI-compliant consent flow (create consent, redirect, callback, FI session, fetch data). Connector is now read-only
- **GSTN**: fixed base URL + implemented real Adaequare 2-step authentication (POST /authenticate for session token) + DSC signing (PKCS#1 v1.5 RSA-SHA256 via cryptography library)
- **AP Processor**: wired to PineLabs Plural for actual payment execution (not simulated)
- ROI claims in marketing copy replaced with honest "measured during pilot" language throughout
- mypy errors resolved: grantex module typing, ChatAnthropic imports, LangGraph overload signatures
- bandit security scan clean: defusedxml for all XML parsing, nosec annotations for GAQL/SOQL query strings

### Changed
- Agent count: 27 → **35** (8 new specialist agents across Finance and Marketing)
- Connector count: 43 → **51** (7 new connectors, all with real endpoints)
- Tool count: 273 → **320+** (new tools across all 8 new connectors)
- Workflow templates: 3 → **11** (8 new production-ready templates)
- Landing page updated with correct agent/connector/tool counts
- Documentation: added CFO Guide, CMO Guide, updated API Reference with 12 new endpoints
- Version: 2.3.0 → **3.1.0**

## [2.3.0] - 2026-03-31

### Added — Security, Error Handling, SDKs & New Features
- **Password Reset Flow**: Full forgot-password + reset-password with JWT tokens, rate-limited, email enumeration safe
- **Connector Detail Page**: View/edit individual connector auth config, secret references, health checks
- **Connector Registry Endpoint**: `GET /connectors/registry` returns all registered connectors with tool counts
- **Connector Create Page**: `/connector-create` UI for adding new connector configurations
- **Email Workflow Triggers**: `email_received` trigger type matches on subject keywords for inbox-driven workflows
- **API Event Triggers**: `api_event` trigger type for event-driven workflow automation
- **Agent Tool Auto-Population**: 25 agent types + 5 domain fallbacks auto-assign relevant tools on creation
- **Slack Full Configuration**: Bot token auth, connector detail edit, Slack tools in support/ops agent defaults
- **API Key Management**: `ao_sk_` prefixed keys, admin-only endpoints (`POST/GET/DELETE /org/api-keys`), bcrypt-hashed at rest
- **Shadow Limit Enforcement**: Agents must pass shadow quality gates before promotion to active status
- **HITL via GraphInterrupt**: LangGraph-based HITL with `GraphInterrupt` for pause/resume at approval nodes
- **Tool Validation**: Scope enforcement ensures agents cannot call tools outside their authorized set
- **Secret Manager Integration**: GCP Secret Manager via `secret_ref` field in connector config
- **Auth Failure Clearing**: IP-based failure tracking with auto-block + success clears failure count
- **Python SDK** (`pip install agenticorg`): client.agents.run(), client.sop.parse_text(), client.a2a.agent_card()
- **TypeScript SDK** (`npm i agenticorg-sdk`): full agent/SOP/A2A/MCP client
- **MCP Server** (`npx agenticorg-mcp-server`): exposes 340+ tools to Claude Desktop, Cursor, ChatGPT
- **CLI**: `agenticorg agents list`, `agenticorg agents run`, `agenticorg sop parse`, `agenticorg mcp tools`
- **Integration Workflow Page**: `/integration-workflow` with visual protocol guide + SDK quickstart
- **Developer Section**: Landing page developer section with SDK/CLI/MCP quickstart
- **Comms Domain**: 3 comms agent types (Ops Commander, DevOps Scout, Slack Notifier) — 6 domains total
- **Negative Test Suite**: 22 unit tests + 19 E2E tests covering error paths (401, 400, 404, 409, 410, 429)
- **Regression Tests**: 55 regression tests (40 March 2026 + 15 April 2026 PR fixes)

### Fixed — QA Bug List (7 bugs)
- **AUTH-RESET-001**: Password reset email flow (was just an alert() stub)
- **ORG-INV-002**: Invite accept "Invalid issuer" — dynamic issuer matching for production
- **AGENT-CONFIG-003**: Tools auto-populated based on agent_type/domain
- **HITL-COUNT-004**: Decided tab shows decision badge instead of action buttons
- **HITL-EXP-005**: Expired items filtered from Pending queue (backend + frontend)
- **WF-CONN-006**: Email trigger + api_event added to workflow UI (was missing)
- **CONN-SLACK-007**: Slack connector end-to-end config from UI

### Security — All CodeQL + Dependabot Resolved
- Fixed 17 CodeQL alerts: stack trace exposure, clear-text logging, XSS, socket binding, workflow permissions
- Fixed 2 Dependabot alerts: picomatch 2.3.1→2.3.2, 4.0.3→4.0.4
- Auth middleware: generic error messages (no internal details leaked)
- Sales API: whitelisted response fields (no agent internals exposed)
- API key endpoints admin-only (`agenticorg:admin` scope required)
- Secret key hardening via GCP Secret Manager (`_get_secret()` in BaseConnector)
- Auth failure clearing — successful auth clears IP-based failure count

### Changed
- Workflow UI: 5 trigger types (manual, schedule, webhook, api_event, email_received)
- Approval card: readonly mode for decided items with decision + timestamp
- ConnectorCard: clickable, navigates to detail page
- All form pages: extract and display API error details instead of generic messages
- Settings/Workflows: user-facing error messages instead of console.error
- Integrations page: replaced curl examples with SDK/CLI quickstart
- Agent domains: 5 → **6** (added Comms domain)
- Agent skills: 25 pre-built + 3 comms = **28 total skills**
- Automated tests: 1,031 → **1,196+** (821 unit + 86 security + 174 connector harness + 55 regression + 62 integration + 370+ Playwright E2E + 148 production E2E)
- Version: 2.2.0 → **2.3.0**

## [2.2.0] - 2026-03-29

### Added — Agent-to-Connector Bridge (Agents That Act)
- **Tool Calling Pipeline**: Agents now parse LLM output for `tool_calls`, execute them via Tool Gateway against real external APIs, and synthesize results in a second LLM pass
- **GitHub Connector**: 9 real API v3 tools (list_repos, get_repo, issues, PRs, releases, search_code, actions)
- **Jira Connector**: 11 real Atlassian REST API tools (projects, issues, JQL search, transitions, comments, sprints, metrics)
- **HubSpot Connector**: 13 real CRM API v3 tools (contacts, deals, companies, pipelines, analytics) with OAuth auto-refresh
- **3 New Agents**: Ops Commander (Jira triage), CRM Intelligence (HubSpot analysis), DevOps Scout (GitHub + Jira health)
- **3 Pre-built Workflows**: Incident Response Pipeline, Lead-to-Revenue Pipeline, Weekly DevOps Health Report
- **Production Connector Test Suite**: 17 tests hitting real Jira/HubSpot/GitHub APIs

### Fixed — Critical Production Bugs
- **Workflow Engine**: `run_workflow` now actually executes the WorkflowEngine in background — creates StepExecution DB records, updates progress, creates HITLQueue entries for approval steps
- **Token Blacklist**: Changed Redis key from `token[:32]` (shared by ALL HS256 JWTs) to SHA-256 hash — one logout was blocking every user
- **Playground 401**: Token validation now guards empty JWKS URL; frontend handles demo login failure properly
- **Agent Promote**: `shadow_min_samples=0` now bypasses shadow validation (was blocking all promotions)
- **Base Connector Auth**: `_authenticate()` now runs before HTTP client creation so auth headers are included
- **Jira Search API**: Migrated from deprecated `/rest/api/3/search` (410 Gone) to `/rest/api/3/search/jql`
- **HubSpot OAuth**: Auto-refresh on token expiry + 401 retry with re-authentication

### Changed
- **Workflow `_execute_agent`**: Replaced hardcoded stub with real agent instantiation and LLM execution
- **ToolGateway**: Optional dependencies (works without rate limiter/audit), dynamic connector resolution from registry + DB
- **Playground UI**: Displays tool call results with connector name, status, and latency
- **WorkflowRun UI**: Auto-polls every 3s while workflow is running
- **Version**: 2.1.0 → 2.2.0

### Metrics
- Automated tests: 353 → **1,031** (pytest) + 125 production E2E
- Production E2E: **125/125 (100%)** — all 21 sections, all demo users, full lifecycle
- Connector tools: 54 connectors × **273 total tools**
- Real API verified: GitHub (9), Jira (11), HubSpot (13) — 14 Jira tickets created on production

## [2.1.0] - 2026-03-21

### Added — Full PRD v4 Compliance
- **Workflow Engine**: Dependency graph resolution via topological sort, timeout enforcement, retry integration with exponential backoff, HITL pause/resume, sub-workflow execution
- **Schedule Trigger**: Full cron expression matching (5-field) for time-based workflow triggers
- **Token Bucket Rate Limiter**: Redis Lua-based atomic token bucket replacing simple counter
- **JWT Issuer Validation**: `iss` claim validation against Grantex token server
- **Token Pool Refresh**: Background refresh via `delegate_agent_token()` at 50% TTL
- **API Endpoints**: GET `/workflows/runs/{id}`, POST `/dsar/export`, POST `/schemas`, PUT `/agents/{id}`
- **WebSocket Feed**: Registered `/ws/feed/{tenant_id}` in main router
- **Agent Prompts**: Token scope declarations and `<processing_sequence>` steps for all 24 agents
- **OpenTelemetry**: All 7 spans with full PRD attributes and proper SpanKind
- **LangSmith Integration**: Full httpx-based trace logging (log_trace, log_batch, update_run)
- **Alert Manager**: 11 PRD-defined threshold checks with Slack/email notification
- **Shadow Comparator**: 6 quality gates (accuracy, confidence calibration, HITL rate, hallucination, tool errors, latency)
- **HPA Integration**: Queue depth + CPU + schedule-based scaling signals
- **Cost Ledger**: Redis+DB dual persistence with daily/monthly budget enforcement
- **RLS on Tenants**: Row-level security now covers all 18 tables
- **CI/CD Approval Gate**: Manual approval stage before production deployment (9/9 stages)
- **Test Suite**: 161 test functions across 13 files (Finance 15, HR 12, Ops/Mkt 13, Performance 9, Reliability 7, Security Auth+LLM 22, Security Data+Infra 25, Agent Scaling 31)
- **UI Pages**: All 10 pages fully implemented (Agents, Workflows, Approvals, Connectors, Schemas, Audit, Settings, AgentDetail, WorkflowRun, Dashboard)
- **BaseConnector._get_secret()**: Proper credential retrieval via config/env/secret_ref

## [2.0.0] - 2026-03-21

### Added — Initial Platform
- 24 specialist agents + NEXUS orchestrator
- 43 typed connectors (PineLabs Plural for payments, Gmail)
- Workflow engine with 9 step types
- Full PostgreSQL DDL with pgvector, RLS, and time-range partitioning (6 migrations)
- 18 JSON Schema data templates
- OAuth2/Grantex auth with JWT, token pool, scope enforcement
- Tool Gateway with rate limiting, idempotency, PII masking, audit logging
- React 18 + TypeScript + Shadcn/ui frontend (10 pages, 8 components)
- OpenTelemetry tracing + Prometheus metrics
- Agent Factory with shadow mode, lifecycle FSM, cost ledger
- 9-stage CI/CD pipeline with Docker + Helm charts
- SOC2/GDPR/DPDP compliance tools built-in
- Apache 2.0 license
