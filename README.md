# AgenticOrg

**AI Virtual Employee Platform** — 50+ LangGraph agents, 1000+ integrations (via Composio), 54 native connectors, 340+ native tools. Agents call real APIs (Jira, HubSpot, GitHub, GSTN, Tally, Banking AA) — not just generate text. Voice agents, RAG knowledge base, smart LLM routing, industry packs, PII redaction, browser RPA, CFO/CMO dashboards, ABM dashboard, NL Query (Cmd+K), multi-company support, scheduled reports, A/B testing, email drip engine, web push HITL, Python/TypeScript SDKs, MCP server, human-in-the-loop governance, no-code builder.

[![Live](https://img.shields.io/badge/Live-agenticorg.ai-blue)](https://agenticorg.ai)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-green.svg)](https://python.org)
[![React](https://img.shields.io/badge/React-18-blue.svg)](https://react.dev)
[![Tests](https://img.shields.io/badge/Tests-1931%2B_passing-brightgreen.svg)](tests/)
[![E2E](https://img.shields.io/badge/E2E-17_Playwright_specs-brightgreen.svg)](ui/e2e/)
[![PyPI](https://img.shields.io/badge/PyPI-agenticorg-blue.svg)](https://pypi.org/project/agenticorg/)
[![npm](https://img.shields.io/badge/npm-agenticorg--sdk-blue.svg)](https://www.npmjs.com/package/agenticorg-sdk)
[![Version](https://img.shields.io/badge/Version-4.3.0-green.svg)](CHANGELOG.md)

**Live**: https://agenticorg.ai | **App**: https://app.agenticorg.ai | **Playground**: https://agenticorg.ai/playground

---

## What Is AgenticOrg?

AgenticOrg deploys **AI virtual employees** that automate enterprise back-office work. Each agent has a name, designation, specialization, and tailored instructions — like a real team member. They process invoices, run payroll, triage support tickets, launch campaigns, and reconcile bank statements — with human approval on every critical decision.

### Key Numbers

| Metric | Value |
|--------|-------|
| Pre-built Agents | **50+** across 6 domains + 4 industry packs |
| Custom Agents | 37+ created on demo tenant (unlimited via no-code wizard) |
| Enterprise Connectors | **1000+ integrations** (via Composio) + **54 native connectors, 340+ native tools** — ALL with real API endpoints |
| Dashboards | CFO Dashboard + CMO Dashboard (role-specific KPI views) |
| NL Query | Cmd+K search bar + slide-out chat panel with agent attribution |
| Multi-Company | Company switcher for CA firms managing multiple client entities |
| Scheduled Reports | Celery beat --> PDF/Excel --> email/Slack/WhatsApp delivery |
| Workflow Templates | **20+** production-ready templates |
| Prompt Templates | 27 production-tested |
| Automated Tests | **1,931+** backend + **93** frontend vitest + **17** Playwright E2E spec files |
| CI E2E | Enabled against production |
| SDKs | Python (`pip install agenticorg`), TypeScript (`npm i agenticorg-sdk`), MCP Server, CLI |
| LLM | Smart routing via RouteLLM: Gemini Flash (free) / Gemini Pro / Claude/GPT-4o. Air-gapped: Ollama/vLLM |
| Deployment | GKE Autopilot, ~$95/month |
| Version | **4.3.0** |

### What It Does

| Before | After |
|--------|-------|
| 5-day month-end close | **1.5 days** with AP + Recon + Close + Treasury agents |
| Manual invoice processing | **11 seconds** per invoice (OCR --> GSTIN --> 3-way match --> GL) |
| 40% ticket mis-routing | **88% auto-classification** accuracy |
| 2-week employee onboarding | **4 hours** (Darwinbox + Slack + email auto-provisioned) |
| Manual bank reconciliation | **99.7% auto-match** rate, done by 6 AM daily |
| Zero payroll errors | PF, ESI, TDS computed automatically |
| CFO drowning in reports | **CFO Dashboard**: Cash Runway, DSO, DPO, AR/AP Aging at a glance |
| CMO guessing ROI | **CMO Dashboard**: CAC, ROAS by Channel, MQL/SQL pipeline in real time |
| "Ask the analyst" queries | **NL Query**: Cmd+K --> "What's my cash position?" --> instant answer |
| CA firm juggling 20 clients | **Multi-company switcher**: one login, all client entities |
| Manual report emails | **Scheduled Reports**: Celery --> PDF/Excel --> email/Slack/WhatsApp |

### Agents That Act, Not Just Talk

Unlike AI chatbots that only generate text, AgenticOrg agents **execute real actions**:

```
You: "Production API returning 500 errors. CloudSQL connection pool exhausted."

Ops Commander (Aria Singh):
  → Gemini reasons about severity (636 tokens, 2.6s)
  → Creates Jira ticket KAN-5 via real API (1.1s)
  → Sets priority to Highest, assigns engineering team
  → Returns analysis + ticket link to you
```

Verified on production: agents have created **14 real Jira tickets**, read **HubSpot CRM data** (contacts, deals, companies), and queried **GitHub repo statistics** — all through the LLM → tool_calls → connector → API pipeline.

---

## Architecture

```
Landing Page (animations, interactive demo, blog, SEO, developer section)
    |
App Dashboard
    ├── CFO Dashboard (/dashboard/cfo) — Cash Runway, Burn Rate, DSO, DPO, AR/AP Aging
    ├── CMO Dashboard (/dashboard/cmo) — CAC, MQLs, SQLs, Pipeline, ROAS by Channel
    ├── NL Query (Cmd+K search bar + slide-out chat panel)
    ├── Multi-Company Switcher (CA firms managing multiple clients)
    ├── Agent Fleet, Workflows, Approvals, Audit, Sales Pipeline, Integrations
    └── Report Scheduler (create, manage, toggle, run-now scheduled reports)
    |
FastAPI Backend
    ├── Agent Registry (50+ agents) → Smart LLM Router (RouteLLM)
    │       ↓ tool_calls                    ↑ synthesis
    │   Tool Gateway → 1000+ Integrations (Composio) + 54 Native Connectors (340+ tools)
    │       ├── Jira (11 tools) ← verified, creates real tickets
    │       ├── HubSpot (13 tools) ← verified, reads real CRM
    │       ├── GitHub (9 tools) ← verified, reads real repos
    │       ├── GSTN (8 tools) ← real Adaequare 2-step auth + DSC signing
    │       ├── Tally (7 tools) ← XML/TDL protocol + bridge for on-premise
    │       ├── Banking AA (5 tools) ← RBI-compliant consent flow
    │       ├── Bombora, G2, TrustRadius ← intent data (weighted 40/30/30)
    │       └── GA4, MoEngage, NetSuite, WordPress, Twitter/X, YouTube, Mailchimp...
    ├── NL Query Engine → Agent attribution → Chat history
    ├── Scheduled Report Engine → Celery beat → PDF/Excel → email/Slack/WhatsApp
    ├── LangGraph Runtime → GraphInterrupt HITL → Shadow Mode
    ├── Workflow Engine (20+ templates) → real agent execution → HITL Queue
    ├── NEXUS Orchestrator → Audit Logger
    ├── A2A Protocol → Agent Discovery → Cross-platform Tasks
    ├── RAGFlow Engine → Document ingestion → Semantic search → Agent retrieval
    ├── LiveKit + Pipecat → Voice agents → STT/TTS → SIP telephony
    ├── RouteLLM → Smart model routing → 3-tier cost optimization
    ├── Presidio → Pre-LLM PII redaction → Aadhaar/PAN/GSTIN scrubbing
    ├── Composio → 1000+ tool marketplace → OAuth bridge
    ├── MCP Server → 340+ tools exposed to Claude/Cursor/ChatGPT
    ├── API Key Manager → ao_sk_ keys → SDK/CLI/MCP auth
    ├── SOP Parser → Upload SOPs → Deploy as agents
    └── Sales Agent → Gmail API → Email Sequences
    |
SDKs: Python (PyPI) | TypeScript (npm) | MCP Server | CLI
    |
PostgreSQL (Cloud SQL) + Redis + GCS + GCP Secret Manager
```

### Agent Domains (6 Domains, 50+ Agents)

| Domain | Agents | Key Agents |
|--------|--------|-----------|
| **Finance** | 10 | AP Processor, AR Collections, Reconciliation, Tax Compliance, Month-End Close, FP&A, Treasury, Expense Manager, Rev Rec (ASC 606), Fixed Assets |
| **HR** | 6 | Talent Acquisition, Onboarding, Payroll Engine, Performance Coach, L&D, Offboarding |
| **Marketing** | 9 | Content Factory, Campaign Pilot, SEO Strategist, CRM Intelligence, Brand Monitor, Email Marketing, Social Media, ABM, Competitive Intel |
| **Operations** | 5 | Support Triage, Contract Intelligence, Compliance Guard, IT Operations, Vendor Manager |
| **Back Office** | 3 | Risk Sentinel, Legal Ops, Facilities Agent |
| **Comms** | 3 | Ops Commander (Jira triage), DevOps Scout (GitHub + Jira health), Slack Notifier |
| **Sales** | 1 | Automated sales agent (qualification, email sequences, pipeline) |
| **Industry Packs** | 16 | Healthcare (4), Legal (4), Insurance (4), Manufacturing (4) — one-click install |
| **Custom** | 37+ on demo | Create via 5-step no-code wizard — unlimited |

> **50+ pre-built agents across 6 domains + 4 industry packs. 1000+ integrations (Composio) + 54 native connectors with 340+ tools. All endpoints real.**

---

## Core Features

### CFO Dashboard (`/dashboard/cfo`)
Role-specific KPI view for finance leaders. Real-time metrics powered by finance agents:
- **Cash Runway** — months of cash remaining at current burn rate
- **Burn Rate** — monthly cash outflow trend
- **DSO / DPO** — Days Sales Outstanding and Days Payable Outstanding
- **AR/AP Aging** — receivables and payables bucketed by 30/60/90/120+ days
- **P&L Summary** — revenue, COGS, gross margin, EBITDA
- **Bank Balances** — aggregated from all connected bank accounts via AA
- **Tax Calendar** — upcoming GST/TDS/advance tax deadlines with status

### CMO Dashboard (`/dashboard/cmo`)
Role-specific KPI view for marketing leaders. Real-time metrics powered by marketing agents:
- **CAC** — Customer Acquisition Cost by channel
- **MQLs / SQLs** — Marketing and Sales Qualified Leads pipeline
- **Pipeline Value** — total opportunity value by stage
- **ROAS by Channel** — Return on Ad Spend for Google, Meta, LinkedIn
- **Email Performance** — open rate, CTR, unsubscribe, deliverability
- **Brand Sentiment** — positive/negative/neutral trend from social monitoring
- **Content Performance** — top pages by traffic, engagement, conversions

### NL Query Interface
Natural language search across all your business data:
- **Cmd+K Search Bar** — global shortcut opens search from any page
- **Slide-out Chat Panel** — full conversational interface with agent attribution
- **Agent Attribution** — every answer shows which agent(s) provided the data
- **Example Queries**: "What's my cash position?", "Show me AP aging over 90 days", "How did Google Ads perform last week?", "What's our DSO this quarter?"

### Multi-Company Support
Built for CA firms and holding companies managing multiple entities:
- **Company Switcher** — dropdown in the top nav to switch between client entities
- **Isolated Data** — each company has its own agents, connectors, workflows, and audit trail
- **Cross-Company Reporting** — consolidated views across all managed entities
- **RBAC per Company** — different roles and permissions for each entity

### Scheduled Report Engine
Automated report generation and delivery:
- **Celery Beat Scheduler** — cron-based scheduling for any report
- **Output Formats** — PDF and Excel with branded templates
- **Delivery Channels** — email, Slack, and WhatsApp
- **Report Scheduler UI** — create, manage, toggle on/off, and run-now from the dashboard
- **Pre-built Schedules** — daily cash report, weekly P&L, monthly close package, weekly marketing digest

### Workflow Templates (15 Pre-built)
Production-ready workflow templates that combine multiple agents:
| Template | Domain | Description |
|----------|--------|-------------|
| `invoice_to_pay_v3` | Finance | OCR --> GSTIN --> 3-way match --> payment execution |
| `month_end_close` | Finance | Trial balance --> adjustments --> reconciliation --> close |
| `daily_treasury` | Finance | Cash position --> sweep --> forecast --> report |
| `tax_calendar` | Finance | Deadline tracking --> filing prep --> DSC signing |
| `campaign_launch` | Marketing | Brief --> content --> review --> publish --> monitor |
| `content_pipeline` | Marketing | Ideation --> draft --> SEO --> approval --> publish |
| `lead_nurture` | Marketing | Scoring --> segmentation --> drip --> wait_for_event --> handoff to sales |
| `weekly_marketing_report` | Marketing | Collect metrics --> build report --> deliver |
| `email_drip_sequence` | Marketing | Behavior-triggered email sequences (open/click/time delays) |
| `ab_test_campaign` | Marketing | Create variants --> run test --> auto-winner selection --> CMO override --> send winner |
| `abm_campaign` | Marketing | CSV upload targets --> intent scoring (Bombora/G2/TrustRadius) --> personalized outreach |
| `incident_response` | Ops | Triage --> Jira ticket --> assign --> monitor --> resolve |
| `lead_to_revenue` | Sales | Qualify --> outreach --> follow-up --> close |
| `weekly_devops_health` | Ops | GitHub + Jira metrics --> health score --> report |

### Virtual Employee System
Each agent is a virtual employee with persona, specialization, and routing:
- **Employee name** (Priya, Arjun, Maya) — appears in audit logs and approvals
- **Designation** (Senior AP Analyst - Mumbai) — role clarity
- **Specialization** (Domestic invoices < 5L) — task routing
- **Multiple agents per role** — 3 AP Processors for different regions with smart routing

### Organization Chart
Visual tree hierarchy per department. Each agent has an `org_level` field (e.g., Head → Manager → Analyst) and a parent reference, forming a reporting chain used for smart escalation — when an agent's confidence is below threshold, the task auto-escalates to its parent in the org tree. CSV bulk import lets you onboard entire departments in one upload.

### No-Code Agent Creator
5-step wizard: Persona → Role → Prompt → Behavior → Deploy as Shadow. Or describe the employee you need in plain English — the system auto-generates the full agent configuration.

### NL Workflow Builder
Describe business processes in plain English (e.g., "Automate invoice approval when amount > 5L") and the system generates the complete workflow definition. Preview before deploying.

### Multi-Language Support
Platform available in English and Hindi. Language picker in header. Agents respond in the user's preferred language. Extensible to Tamil, Telugu, Kannada.

### Content Safety
AI-generated content is checked for PII leakage, toxicity (via HuggingFace toxic-bert), and near-duplicate detection before delivery. Configurable thresholds per agent.

### Prompt Template Library
27 production-tested templates with `{{variable}}` substitution. Built-in templates are read-only — clone to customize. Full prompt audit trail with edit history. Prompt lock on active agents.

### Human-in-the-Loop (HITL)
Configurable confidence floors, trigger conditions, escalation chains, and timeout rules. Every HITL decision logged in audit trail. Agents cannot bypass their own gates. LangGraph-based HITL uses `GraphInterrupt` for pause/resume — the agent graph pauses at the approval node and resumes only after human decision.

### RBAC
- **CEO/Admin**: Full access, all domains
- **CFO**: Finance agents only
- **CHRO**: HR agents only
- **CMO**: Marketing agents only
- **COO**: Operations agents only
- **Auditor**: Read-only audit log

### Sales Agent
Automated lead qualification, personalized email outreach, follow-up sequences (Day 1/3/7/14), Gmail inbox monitoring, and pipeline dashboard. Demo request form → instant personalized response.

### 54 Enterprise Connectors (340+ Tools) — All Real API Endpoints
Finance (11): Oracle Fusion, SAP, Tally (XML/TDL + bridge), GSTN (Adaequare 2-step + DSC), Stripe, QuickBooks, Zoho Books, Banking AA (RBI consent), Income Tax India, Pine Labs (Plural), NetSuite
HR (8): Darwinbox, Okta, Greenhouse, LinkedIn Talent, DocuSign, Keka, Zoom, EPFO
Marketing (19): Salesforce, HubSpot, Google Ads, LinkedIn Ads, Meta Ads, Ahrefs, Mixpanel, Buffer, Brandwatch, GA4, MoEngage, WordPress, Twitter/X, YouTube, Mailchimp, Semrush, Bombora, G2, TrustRadius
Ops (7): Jira, ServiceNow, Zendesk, PagerDuty, Confluence, Sanctions API, MCA Portal
Comms (9): Slack, GitHub, Gmail, SendGrid, GCS, Google Calendar, Twilio, WhatsApp, LangSmith

> Every connector uses real API endpoints from official documentation. Zero stubs. The Tally connector uses native XML/TDL protocol with a bridge agent for on-premise instances. Banking AA follows RBI-compliant consent flow. GSTN uses Adaequare's 2-step authentication with DSC signing for filing.

### Tier 1: Marketing Automation (v3.2.0)

| Feature | What It Does |
|---------|-------------|
| **Web Push Notifications** | One-tap approve/reject HITL decisions from browser push notifications — no need to open the dashboard |
| **Email Drip Engine** | Behavior-triggered email sequences with open/click/time-delay steps and automatic re-engagement for non-openers |
| **A/B Testing** | Create campaign variants, auto-select winners by open rate or CTR, with CMO override before sending to remaining audience |
| **Email Webhooks** | Real-time SendGrid, Mailchimp, and MoEngage open/click tracking via inbound webhooks |
| **Intent Data** | Bombora + G2 + TrustRadius aggregation with weighted scoring (40/30/30) for account-level buying signals |
| **ABM Dashboard** | Target account management with intent heatmap, CSV upload, tier filtering, and one-click campaign launch |
| **Wait Step** | Real time delays in workflows (was stub) — supports minutes, hours, and day-based delays |
| **Wait-for-Event** | Pause workflow execution until a specific event occurs (email opened, link clicked, form submitted) |

### CA Firms -- Paid Add-on

**INR 4,999/month per client | 14-day free trial**

Purpose-built for Chartered Accountant firms managing multiple client companies from a single tenant.

| Feature | Details |
|---------|---------|
| **5 AI Agents** | GST Filing, TDS Compliance, Bank Reconciliation, FP&A Analyst, AR Collections |
| **Partner Dashboard** | Aggregate KPIs across all clients -- filing status, overdue counts, client health scores |
| **Filing Approvals** | Agent generates filing --> pending --> partner self-approve --> filed. Supports bulk approve across clients |
| **GSTN Credential Vault** | AES-256 encrypted storage per client. Passwords never returned in API responses. Supports key rotation via `encryption_key_ref` |
| **Compliance Calendar** | Auto-generated deadlines for GSTR-1, GSTR-3B, TDS 26Q/24Q. Email alerts at 7-day and 1-day before due date |
| **Tally Auto-Detect** | `POST /api/v1/companies/tally-detect` reads Tally bridge and auto-creates the company entity with GSTIN, PAN, and FY |
| **Demo Seeded** | 7 demo companies seeded for `demo@cafirm.agenticorg.ai` -- ready to explore immediately |

---

## Quick Start

### Prerequisites
- Python 3.11+ (tested on 3.12, 3.13) | Node.js 20+ | Docker

### Installation

AgenticOrg provides three requirements files for different deployment scenarios:

| File | Packages | Purpose | Required? |
|------|----------|---------|-----------|
| `requirements.txt` | 45 pinned | Core platform (API, agents, workflows, connectors, auth) | **Yes** |
| `requirements-v4.txt` | 4 | v4 features: Composio 1000+ tools, RouteLLM smart routing, Presidio PII redaction | Optional (graceful degradation) |
| `requirements-dev.txt` | 11 | Development: pytest, ruff, mypy, bandit, pre-commit | For contributors only |

All versions in `requirements.txt` are pinned to exact production-tested versions (2026-04-06).

### Local Development

```bash
git clone https://github.com/mishrasanjeev/agentic-org.git
cd agentic-org
cp .env.example .env  # Add your Gemini API key (free at aistudio.google.com)

# Install Python dependencies
pip install -r requirements.txt              # Core platform (required)
pip install -r requirements-v4.txt           # v4 features (optional)
pip install -r requirements-dev.txt          # Dev tools (optional)

# Start infrastructure (PostgreSQL + Redis)
docker compose up -d postgres redis

# Run API server
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Run frontend (separate terminal)
cd ui && npm install && npm run dev
```

### Optional Docker Services

```bash
docker compose --profile ragflow up -d       # Knowledge Base (RAGFlow)
docker compose --profile voice up -d         # Voice Agents (LiveKit)
docker compose --profile airgap up -d        # Local LLM (Ollama, CPU)
docker compose --profile airgap-gpu up -d    # Local LLM (vLLM, GPU)
```

### Docker Compose
```bash
docker compose up -d
# API: http://localhost:8000 | UI: http://localhost:3000
```

### GKE Production
```bash
gcloud container clusters get-credentials agenticorg-lean --region=asia-south1
helm upgrade --install agenticorg ./helm -n agenticorg -f helm/values-lean.yaml
```

### Demo Credentials
| Role | Email | Password |
|------|-------|----------|
| CEO | ceo@agenticorg.local | ceo123! |
| CFO | cfo@agenticorg.local | cfo123! |
| CHRO | chro@agenticorg.local | chro123! |
| CMO | cmo@agenticorg.local | cmo123! |
| COO | coo@agenticorg.local | coo123! |
| Auditor | auditor@agenticorg.local | audit123! |

---

## API Reference

Base URL: `https://app.agenticorg.ai/api/v1`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /health/liveness | No | Health check |
| POST | /auth/signup | No | Create account |
| POST | /auth/login | No | Get JWT token |
| POST | /auth/google | No | Google OAuth |
| POST | /auth/forgot-password | No | Request password reset email |
| POST | /auth/reset-password | No | Reset password with token |
| POST | /demo-request | No | Demo form → lead + sales agent |
| GET | /agents | JWT | List agents (RBAC filtered) |
| POST | /agents | JWT | Create agent (tools auto-populated by type/domain) |
| POST | /agents/{id}/run | JWT | Execute agent |
| PATCH | /agents/{id} | JWT | Update (prompt lock on active) |
| GET | /agents/org-tree | JWT | Org chart tree (department hierarchy) |
| POST | /agents/import-csv | JWT | Bulk import agents via CSV |
| POST | /agents/{id}/clone | JWT | Clone with persona |
| POST | /agents/{id}/promote | JWT | Shadow → Active (shadow limit enforcement) |
| GET | /agents/{id}/prompt-history | JWT | Prompt audit trail |
| GET | /prompt-templates | JWT | List templates |
| POST | /prompt-templates | JWT | Create template |
| GET | /sales/pipeline | JWT | Sales pipeline |
| POST | /sales/pipeline/process-lead | JWT | Trigger sales agent |
| POST | /sales/process-inbox | JWT | Process Gmail replies |
| GET | /sales/metrics | JWT | Weekly digest data |
| GET | /workflows | JWT | List workflows |
| GET | /approvals | JWT | HITL approval queue |
| GET | /audit | JWT | Audit log |
| GET | /kpis/cfo | JWT | CFO dashboard KPIs |
| GET | /kpis/cmo | JWT | CMO dashboard KPIs |
| POST | /chat/query | JWT | NL Query (natural language question) |
| GET | /chat/history | JWT | Chat history for current user |
| GET | /companies | JWT | List companies (multi-company) |
| POST | /companies | JWT | Create company entity |
| PATCH | /companies/{id} | JWT | Update company entity |
| GET | /report-schedules | JWT | List scheduled reports |
| POST | /report-schedules | JWT | Create scheduled report |
| PATCH | /report-schedules/{id} | JWT | Update scheduled report |
| POST | /report-schedules/{id}/run-now | JWT | Trigger immediate report run |
| DELETE | /report-schedules/{id} | JWT | Delete scheduled report |
| GET | /connectors | JWT | List 54 connectors |
| GET | /connectors/registry | JWT | Connector registry (all registered connectors + tool counts) |
| GET | /connectors/{id}/health | JWT | Connector health check |
| GET | /connectors/{id} | JWT | Connector details |
| PUT | /connectors/{id} | JWT | Update connector config |
| POST | /org/api-keys | Admin | Generate API key (ao_sk_ prefix) |
| GET | /org/api-keys | Admin | List API keys |
| DELETE | /org/api-keys/{id} | Admin | Revoke API key |
| GET | /a2a/agent-card | No | A2A agent discovery card |
| POST | /a2a/tasks | JWT/Grantex | Execute A2A task |
| GET | /mcp/tools | No | List MCP tools (340+ tools) |
| POST | /mcp/call | JWT/Grantex | Call MCP tool |
| POST | /sop/upload | JWT | Upload and parse SOP document |
| POST | /sop/parse-text | JWT | Parse SOP text |
| POST | /sop/deploy | JWT | Deploy parsed SOP as agent |
| POST | /agents/{id}/delegate | JWT | Grantex delegation setup |

## SDKs, CLI & MCP Server

### Python SDK

```bash
pip install agenticorg
```

```python
from agenticorg import AgenticOrg

client = AgenticOrg(api_key="ao_sk_your_key_here")
result = client.agents.run("ap_processor", inputs={"invoice_id": "INV-001"})
agents = client.agents.list()
sop = client.sop.parse_text("When invoice > 5L, require CFO approval")
card = client.a2a.agent_card()
```

### TypeScript SDK

```bash
npm install agenticorg-sdk
```

```typescript
import { AgenticOrg } from "agenticorg-sdk";

const client = new AgenticOrg({ apiKey: "ao_sk_your_key_here" });
const result = await client.agents.run("ap_processor", { inputs: { invoice_id: "INV-001" } });
const agents = await client.agents.list();
```

### MCP Server

Any MCP-compatible client (Claude Desktop, Cursor, ChatGPT) can use AgenticOrg agents and tools:

```bash
AGENTICORG_API_KEY=ao_sk_... npx agenticorg-mcp-server
```

Or add to your MCP client config:
```json
{
  "mcpServers": {
    "agenticorg": {
      "command": "npx",
      "args": ["agenticorg-mcp-server"],
      "env": { "AGENTICORG_API_KEY": "ao_sk_..." }
    }
  }
}
```

### CLI

```bash
pip install agenticorg

agenticorg agents list
agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'
agenticorg sop parse "When invoice > 5L, require CFO approval"
agenticorg mcp tools
```

See `sdk/README.md` and `mcp-server/README.md` for full documentation.

---

## API Keys

API keys provide programmatic access for SDKs, CLI, and MCP integrations.

- **Prefix**: All keys use the `ao_sk_` prefix (e.g., `ao_sk_a1b2c3...`)
- **Generation**: Admin users generate keys from **Settings > API Keys** in the app, or via `POST /api/v1/org/api-keys`
- **Admin-only**: API key endpoints require the `agenticorg:admin` scope
- **Security**: Keys are bcrypt-hashed at rest; the full key is shown only once at creation
- **Revocation**: Keys can be revoked instantly from Settings or via `DELETE /api/v1/org/api-keys/{id}`

---

## Integration Protocols

### A2A (Agent-to-Agent)
AgenticOrg implements Google's A2A protocol for cross-platform agent discovery and task execution:
- `GET /a2a/agent-card` — public agent discovery card (no auth required)
- `POST /a2a/tasks` — execute tasks via A2A protocol (JWT or Grantex auth)

### MCP (Model Context Protocol)
Full MCP server exposing all 340+ connector tools to any MCP-compatible client:
- `GET /mcp/tools` — list all available MCP tools (no auth required)
- `POST /mcp/call` — call any MCP tool (JWT or Grantex auth)

### Grantex Authorization & Scope Enforcement
OAuth2-based authorization with manifest-based scope enforcement (Grantex SDK v0.3.3+):
- **Manifest-based permissions**: 53 pre-built tool manifests define exact permission levels (READ/WRITE/DELETE/ADMIN) for every connector tool — no keyword guessing
- **`grantex.enforce()`**: Offline JWT verification + manifest permission check in <1ms per tool call (JWKS cached after first call)
- **Permission hierarchy**: `admin > delete > write > read` — write scope covers read, admin covers everything
- **LangGraph enforcement**: `validate_scopes` graph node checks every tool call before execution
- **ToolGateway enforcement**: API-direct tool calls also use `grantex.enforce()` as primary enforcement
- `POST /agents/{id}/delegate` — set up Grantex delegation for agent-to-agent trust
- Scope hierarchy enforced: child agents cannot elevate parent scopes
- Token pool with automatic refresh at 50% TTL
- Custom manifests: place JSON/YAML files in `GRANTEX_MANIFESTS_DIR` for custom connectors
- **Environment variables**: `GRANTEX_API_KEY` (required), `GRANTEX_BASE_URL` (optional), `GRANTEX_MANIFESTS_DIR` (optional)

### v4.0.0 Environment Variables
```
COMPOSIO_API_KEY              — 1000+ tool integrations (optional)
AGENTICORG_LLM_ROUTING        — auto | tier1 | tier2 | tier3 | disabled
AGENTICORG_LLM_MODE           — cloud | local | auto
AGENTICORG_PII_REDACTION_MODE — before_llm | logs_only | disabled
RAGFLOW_API_URL               — RAGFlow instance URL for knowledge base
RAGFLOW_API_KEY               — RAGFlow API key
LIVEKIT_URL                   — LiveKit server URL for voice agents
LIVEKIT_API_KEY               — LiveKit API key
LIVEKIT_API_SECRET            — LiveKit API secret
STRIPE_SECRET_KEY             — Stripe billing key (hosted tier)
STRIPE_WEBHOOK_SECRET         — Stripe webhook signing secret
```

### Knowledge Base (RAG)
Upload company documents (PDF, Word, Excel) and agents query them via semantic search. Powered by RAGFlow (Apache 2.0). Agents use `knowledge_base_search` tool automatically.

### Voice Agents
Real-time voice AI with telephony support. Default STT: Whisper (local, Apache 2.0). Default TTS: Piper (local, MIT). Configurable SIP provider (Twilio/Vonage). Powered by LiveKit (Apache 2.0) + Pipecat (BSD-2).

### Smart LLM Routing
Automatic multi-model routing via RouteLLM (Apache 2.0). Three tiers: Economy (Gemini Flash, free), Standard (Gemini Pro), Premium (Claude/GPT-4o). Reduces costs by 85% while maintaining accuracy. Air-gapped mode routes to Ollama/vLLM.

### Pre-LLM PII Redaction
Microsoft Presidio (MIT) scrubs sensitive data before it reaches the LLM. Custom India recognizers: Aadhaar, PAN, GSTIN, UPI. Configurable: `AGENTICORG_PII_REDACTION_MODE=before_llm|logs_only|disabled`.

### Industry Packs
Pre-built agent bundles: Healthcare (4 agents, HIPAA-aware), Legal (4 agents), Insurance (4 agents), Manufacturing (4 agents). One-click install via `POST /packs/{name}/install`.

### Browser RPA
Playwright-based automation for legacy web portals without APIs. Pre-built scripts for Indian government portals (EPFO, MCA, Income Tax). Sandboxed Docker execution with screenshot audit trail.

### Billing & Hosted Tier
Self-hosted is free forever. Hosted tier: Free (3 agents) / Pro ($49/mo) / Enterprise ($299/mo). India pricing: Free / Rs 999/mo / Rs 4999/mo. Stripe (global) + PineLabs Plural (India).

### Explainable AI
Every agent decision includes a "Why?" panel with 3-5 plain-English bullet points, confidence bar, tools cited, and Flesch-Kincaid readability scoring. Non-technical users can understand why the agent approved or rejected an action.

### Self-Improving Agents
Thumbs up/down feedback on every agent run. After 10+ feedback entries, the system auto-generates prompt amendments. Agents learn from corrections without manual prompt editing.

### Multi-Agent Collaboration
Workflow `collaboration` step type runs 2+ agents in parallel with shared context. Aggregation strategies: merge (combine outputs), vote (majority wins), first_complete (fastest agent wins).

### Customer Support Deflection
Pre-built `support_deflector` agent auto-resolves 60%+ of support tickets using FAQ matching + knowledge base (RAG) search. Tracks deflection rate metric on the dashboard.

### Real-Time CDC (Change Data Capture)
Webhook receivers + polling-based CDC for connected systems. When data changes externally (e.g., new HubSpot deal), CDC events trigger workflows automatically. HMAC-SHA256 signature validation, fail-closed security.

### Integration Workflow Page
The `/integration-workflow` page provides a visual guide for connecting external systems, with SDK/CLI quickstart examples and protocol documentation.

---

## Testing

```bash
# Backend tests (1931+ total)
pytest tests/

# Frontend vitest (93 tests)
cd ui && npx vitest run

# Playwright E2E — 17 spec files, CI runs against production
npx playwright test

# Production connector test — real Jira/HubSpot/GitHub API calls
python tests/test_production_connectors.py
```

| Suite | Tests | What It Covers |
|-------|-------|---------------|
| Backend (pytest) | **1,931+** | Unit, security, connector harness (54 connectors × 340+ tools), synthetic data, regression, integration, voice, RAG, RPA, packs |
| Frontend (vitest) | **93** | Component tests, hooks, utilities |
| Playwright E2E | **17 spec files** | Full browser flows: login, onboarding, agents, workflows, approvals, landing, SOP, dashboards, ABM, drip, A/B, push |
| CI E2E | Enabled | Runs against production on every merge to main |

---

## Project Structure

```
agenticorg/
├── api/v1/                 # FastAPI endpoints (agents, auth, sales, templates, connectors, api-keys, sop, a2a, mcp)
├── core/
│   ├── agents/             # 50+ agent types + 27 prompt templates
│   │   ├── prompts/        # Production system prompts
│   │   ├── registry.py     # Agent registry (built-in + custom type fallback)
│   │   └── base.py         # BaseAgent: LLM reasoning → tool calling → HITL
│   ├── langgraph/          # LangGraph agent graph, runner, Grantex auth, LLM factory
│   ├── orchestrator/       # NEXUS: task routing, smart routing, state machine
│   ├── llm/                # LLM router (Gemini primary, Claude/GPT-4o fallback)
│   ├── models/             # SQLAlchemy ORM (agents, workflows, HITL, leads, templates, api_keys)
│   ├── tool_gateway/       # Scope enforcement, rate limiting, PII masking, audit
│   ├── gmail_agent.py      # Gmail API integration (inbox monitor, send replies)
│   └── email.py            # SMTP email sending
├── connectors/             # 54 enterprise connectors (340+ tools)
│   ├── finance/            # Oracle, SAP, GSTN, Stripe, Tally, Banking AA, NetSuite... (11)
│   ├── hr/                 # Darwinbox, Okta, Greenhouse, EPFO, Zoom... (8)
│   ├── marketing/          # Salesforce, HubSpot, Google Ads, GA4, MoEngage, Bombora, G2, TrustRadius... (19)
│   ├── ops/                # Jira, Zendesk, ServiceNow, PagerDuty... (7)
│   ├── comms/              # Slack, GitHub, Gmail, SendGrid, GCS, Twilio... (9)
│   └── framework/          # BaseConnector, auth adapters, circuit breaker
├── auth/                   # Grantex middleware, registration
├── workflows/              # Workflow engine, triggers, conditions
├── sdk/                    # Python SDK (pip install agenticorg) + CLI
├── sdk-ts/                 # TypeScript SDK (npm i agenticorg-sdk)
├── mcp-server/             # MCP Server (npx agenticorg-mcp-server)
├── ui/src/
│   ├── pages/              # Landing, Dashboard, Agents, Sales, Blog, Resources, Integrations, IntegrationWorkflow, ConnectorCreate, ConnectorDetail, Settings
│   ├── components/         # AgentCard, ActivityTicker, InteractiveDemo, SocialProof
│   ├── pages/blog/         # 8 SEO blog articles
│   └── pages/resources/    # 26 SEO content pages across 7 topic clusters
├── tests/
│   ├── unit/               # Unit tests
│   ├── security/           # Security tests
│   ├── connector_harness/  # Connector tests (54 connectors × tools)
│   ├── regression/         # Regression tests
│   ├── integration/        # Integration tests
│   ├── synthetic_data/     # Invoice, resume, contract test data
│   └── e2e/                # Playwright browser tests
├── ui/e2e/                 # 17 Playwright E2E spec files
├── ui/tests/               # 93 vitest component tests
├── migrations/             # PostgreSQL DDL files
├── helm/                   # Kubernetes Helm charts
├── docs/                   # PRD, architecture, QA test plan
└── scripts/                # Seed data, deployment helpers
```

---

## SEO & AI Discoverability

| Asset | URL | Status |
|-------|-----|--------|
| Landing page | https://agenticorg.ai | 39 URLs in sitemap |
| Blog (8 articles) | /blog | Invoice processing, RPA vs AI, reconciliation, HITL, no-code, month-end close, ROI measurement, CFO story |
| Resources (26 pages) | /resources | 7 topic clusters, FAQ schemas, internal linking |
| llms.txt | /llms.txt | v4.0.0 product summary for AI crawlers |
| llms-full.txt | /llms-full.txt | 18.7KB complete documentation |
| JSON-LD | 7 schemas | Organization, Product, FAQ, Breadcrumb, SoftwareCompany, WebSite, SoftwareApplication |
| AI crawlers | robots.txt | GPTBot, ClaudeBot, PerplexityBot, ChatGPT-User, OAI-SearchBot, cohere-ai, Applebot-Extended |

---

## Pricing

**Self-hosted is free forever** — all agents, all connectors, unlimited tasks.

| Plan | Price | Agents | Integrations | Tasks |
|------|-------|--------|-------------|-------|
| Free (self-hosted) | $0 | 50+ | 1000+ | Unlimited |
| Free (hosted) | $0 | 3 | 20 | 500/day |
| Pro (hosted) | $49/mo (Rs 999/mo India) | Unlimited | 1000+ | Unlimited |
| Enterprise (hosted) | $299/mo (Rs 4999/mo India) | Unlimited | 1000+ | Unlimited + SLA |

Payments: Stripe (global) + PineLabs Plural (India: NEFT/RTGS/IMPS/UPI).

---

## India-First Connectors

Built for Indian enterprise — not retrofitted:
- **GSTN** — e-Invoice IRN, GSTR-1/2A/3B/9 filing, ITC reconciliation
- **EPFO** — ECR filing, UAN verification, passbook download
- **Income Tax India** — TDS 26Q/24Q, Form 16A, 26AS credit check
- **Darwinbox** — HRMS (payroll, attendance, performance, onboarding)
- **Tally Prime** — Native XML/TDL protocol + bridge agent for on-premise tunneling (vouchers, trial balance, GST reports)
- **Banking AA** — RBI-compliant Account Aggregator consent flow (read-only: statements, balances, transactions)

---

## Security & Compliance

| Feature | Implementation |
|---------|---------------|
| Tenant isolation | PostgreSQL RLS, Redis key namespacing, GCS prefix isolation |
| HITL governance | Configurable thresholds, prompt lock on active agents, escalation chains, GraphInterrupt |
| Audit trail | Every agent action logged, 7-year WORM retention, HMAC signed |
| PII masking | Default-on masking of email, phone, Aadhaar, PAN, bank accounts |
| SOC-2 ready | Password policy (bcrypt 12), token blacklist, rate limiting, HSTS, CSP |
| Prompt audit | Every prompt edit logged with user, timestamp, before/after, reason |
| API key security | `ao_sk_` prefix, bcrypt-hashed at rest, admin-only endpoints, instant revocation |
| Secret management | GCP Secret Manager integration for connector credentials (`secret_ref` field) |
| Auth failure tracking | IP-based failure tracking, auto-block on threshold, auth failure clearing on success |
| Shadow limit enforcement | Agents must pass shadow quality gates before promotion to active |
| Tool validation | Tool scope enforcement — agents cannot call tools outside their authorized scope |

---

## Documentation

| Document | Description |
|----------|-------------|
| [PRD](docs/PRD.md) | Complete product requirements (7 pages) |
| [API Reference](docs/api-reference.md) | Full API docs with Mermaid diagrams |
| [CFO Guide](docs/cfo_guide.md) | CFO user guide — dashboard, agents, NL query, reports |
| [CMO Guide](docs/cmo_guide.md) | CMO user guide — dashboard, agents, NL query, campaigns |
| [CA Firm Setup](docs/ca_firm_setup_guide.md) | End-to-end CA firm deployment guide |
| [QA Test Plan](tests/QA_MANUAL_TEST_PLAN.md) | 65 manual test cases with steps |
| [QA Test Cases](tests/QA_TEST_CASES.md) | 70 automated test results |
| [Architecture](docs/architecture.md) | 8-layer system design |
| [Python SDK](sdk/README.md) | Python SDK + CLI documentation |
| [MCP Server](mcp-server/README.md) | MCP Server setup and usage |
| [Changelog](CHANGELOG.md) | Version history and release notes |
| [Roadmap](ROADMAP.md) | Current status and future plans |

---

## Contributing

Fork → branch → test → PR. See [CONTRIBUTING.md](CONTRIBUTING.md).

- Python: `ruff check . && mypy --ignore-missing-imports .`
- TypeScript: `npx tsc --noEmit`
- Tests: `pytest tests/unit/ tests/connector_harness/`

## License

Apache License 2.0 — free for commercial use. See [LICENSE](LICENSE).

---

Built by [Edumatica Pvt Ltd](https://agenticorg.ai) | Bengaluru, India
