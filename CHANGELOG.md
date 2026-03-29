# Changelog

All notable changes to AgenticOrg are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

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
- Connector tools: 42 connectors × **269 total tools**
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
- 42 typed connectors (PineLabs Plural for payments)
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
