# AgenticOrg

**AI Virtual Employee Platform** — Create custom AI agents or deploy 24+ pre-built agents across Finance, HR, Marketing, Operations, and Back Office. Human-in-the-loop governance, 42 enterprise connectors, no-code agent builder.

[![Live](https://img.shields.io/badge/Live-agenticorg.ai-blue)](https://agenticorg.ai)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-green.svg)](https://python.org)
[![React](https://img.shields.io/badge/React-18-blue.svg)](https://react.dev)
[![Tests](https://img.shields.io/badge/Tests-328_passing-brightgreen.svg)](tests/)

**Live**: https://agenticorg.ai | **App**: https://app.agenticorg.ai | **Playground**: https://agenticorg.ai/playground

---

## What Is AgenticOrg?

AgenticOrg deploys **AI virtual employees** that automate enterprise back-office work. Each agent has a name, designation, specialization, and tailored instructions — like a real team member. They process invoices, run payroll, triage support tickets, launch campaigns, and reconcile bank statements — with human approval on every critical decision.

### Key Numbers

| Metric | Value |
|--------|-------|
| Pre-built Agents | 24 across 5 domains |
| Custom Agents | Unlimited (no-code wizard) |
| Enterprise Connectors | 42 (SAP, Oracle, GSTN, Darwinbox, Slack, Stripe...) |
| Prompt Templates | 27 production-tested |
| Automated Tests | 328 (unit + connector + synthetic + browser) |
| LLM | Gemini 2.5 Flash (primary), Claude/GPT-4o fallback |
| Deployment | GKE Autopilot, ~$95/month |

### What It Does

| Before | After |
|--------|-------|
| 5-day month-end close | **1 day** with AP + Recon + Close agents |
| Manual invoice processing | **11 seconds** per invoice (OCR → GSTIN → 3-way match → GL) |
| 40% ticket mis-routing | **88% auto-classification** accuracy |
| 2-week employee onboarding | **4 hours** (Darwinbox + Slack + email auto-provisioned) |
| Manual bank reconciliation | **99.7% auto-match** rate, done by 6 AM daily |
| Zero payroll errors | PF, ESI, TDS computed automatically |

---

## Architecture

```
Landing Page (animations, interactive demo, blog, SEO)
    ↓
App Dashboard (agents, workflows, approvals, audit, sales pipeline)
    ↓
FastAPI Backend
    ├── Agent Registry → LLM Router (Gemini 2.5 Flash) → Tool Gateway
    ├── NEXUS Orchestrator → HITL Queue → Audit Logger
    ├── Sales Agent → Gmail API → Email Sequences
    └── 42 Connectors (SAP, Oracle, GSTN, Darwinbox, Slack, Stripe...)
    ↓
PostgreSQL (Cloud SQL) + Redis + GCS
```

### Agent Domains

| Domain | Agents | Key Agents |
|--------|--------|-----------|
| **Finance** | 6 | AP Processor, AR Collections, Reconciliation, Tax Compliance, Month-End Close, FP&A |
| **HR** | 6 | Talent Acquisition, Onboarding, Payroll Engine, Performance Coach, L&D, Offboarding |
| **Marketing** | 5 | Content Factory, Campaign Pilot, SEO Strategist, CRM Intelligence, Brand Monitor |
| **Operations** | 5 | Support Triage, Contract Intelligence, Compliance Guard, IT Operations, Vendor Manager |
| **Back Office** | 3 | Risk Sentinel, Legal Ops, Facilities Agent |
| **Sales** | 1 | Automated sales agent (qualification, email, pipeline) |
| **Custom** | Unlimited | Create via 5-step no-code wizard |

---

## Core Features

### Virtual Employee System
Each agent is a virtual employee with persona, specialization, and routing:
- **Employee name** (Priya, Arjun, Maya) — appears in audit logs and approvals
- **Designation** (Senior AP Analyst - Mumbai) — role clarity
- **Specialization** (Domestic invoices < 5L) — task routing
- **Multiple agents per role** — 3 AP Processors for different regions with smart routing

### No-Code Agent Creator
5-step wizard: Persona → Role → Prompt → Behavior → Deploy as Shadow

### Prompt Template Library
27 production-tested templates with `{{variable}}` substitution. Built-in templates are read-only — clone to customize. Full prompt audit trail with edit history. Prompt lock on active agents.

### Human-in-the-Loop (HITL)
Configurable confidence floors, trigger conditions, escalation chains, and timeout rules. Every HITL decision logged in audit trail. Agents cannot bypass their own gates.

### RBAC
- **CEO/Admin**: Full access, all domains
- **CFO**: Finance agents only
- **CHRO**: HR agents only
- **CMO**: Marketing agents only
- **COO**: Operations agents only
- **Auditor**: Read-only audit log

### Sales Agent
Automated lead qualification, personalized email outreach, follow-up sequences (Day 1/3/7/14), Gmail inbox monitoring, and pipeline dashboard. Demo request form → instant personalized response.

### 42 Enterprise Connectors
Finance: Oracle Fusion, SAP, Tally, GSTN, Stripe, QuickBooks, Zoho Books, Banking AA, Income Tax India, Pine Labs
HR: Darwinbox, Okta, Greenhouse, LinkedIn Talent, DocuSign, Keka, Zoom, EPFO
Marketing: Salesforce, HubSpot, Google Ads, LinkedIn Ads, Meta Ads, Ahrefs, Mixpanel, Buffer, Brandwatch
Ops: Jira, ServiceNow, Zendesk, PagerDuty, Confluence, Sanctions API, MCA Portal
Comms: Slack, GitHub, SendGrid, GCS, Google Calendar, Twilio, WhatsApp, LangSmith

---

## Quick Start

### Prerequisites
- Python 3.12+ | Node.js 20+ | Docker

### Local Development

```bash
git clone https://github.com/mishrasanjeev/agentic-org.git
cd agentic-org
cp .env.example .env  # Add your Gemini API key

# Backend
pip install -e ".[dev]"
docker compose up -d postgres redis
python -m scripts.seed_data
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd ui && npm install && npm run dev
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
| POST | /demo-request | No | Demo form → lead + sales agent |
| GET | /agents | JWT | List agents (RBAC filtered) |
| POST | /agents | JWT | Create agent |
| POST | /agents/{id}/run | JWT | Execute agent |
| PATCH | /agents/{id} | JWT | Update (prompt lock on active) |
| POST | /agents/{id}/clone | JWT | Clone with persona |
| POST | /agents/{id}/promote | JWT | Shadow → Active |
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
| GET | /connectors | JWT | List connectors |
| GET | /connectors/{id}/health | JWT | Connector health check |

---

## Testing

```bash
# Unit tests (124 tests, 5s)
pytest tests/unit/

# Connector harness — all 42 connectors (174 tests, 142s)
pytest tests/connector_harness/

# Synthetic data — invoice/resume/contract flows (15 tests, 189s)
pytest tests/synthetic_data/

# Playwright browser tests (15 tests, 90s)
cd ui && npx playwright test --config=playwright-prod.config.ts

# Full production E2E (25 checks, 30 agents)
python tests/final_production_test.py

# All tests
pytest tests/unit/ tests/connector_harness/ && cd ui && npx playwright test
```

| Suite | Tests | What It Covers |
|-------|-------|---------------|
| Unit | 124 | Agents, registry, schemas, routing, prompts, RBAC |
| Connector harness | 174 | 42 connectors × all tools via mock server |
| Synthetic data | 15 | Invoice OCR → match, resume screening, contract analysis |
| Playwright browser | 15 | Signup, login, agent CRUD, HITL, templates, mobile |
| Production E2E | 25 | Fresh company, 30 agents created + run, full lifecycle |
| **Total** | **353** | |

---

## Project Structure

```
agenticorg/
├── api/v1/                 # FastAPI endpoints (agents, auth, sales, templates, connectors)
├── core/
│   ├── agents/             # 25 agent implementations + 27 prompt templates
│   │   ├── prompts/        # Production system prompts
│   │   ├── registry.py     # Agent registry (built-in + custom type fallback)
│   │   └── base.py         # BaseAgent: LLM reasoning, confidence, HITL
│   ├── orchestrator/       # NEXUS: task routing, smart routing, state machine
│   ├── llm/                # LLM router (Gemini primary, Claude/GPT-4o fallback)
│   ├── models/             # SQLAlchemy ORM (agents, workflows, HITL, leads, templates)
│   ├── tool_gateway/       # Scope enforcement, rate limiting, PII masking, audit
│   ├── gmail_agent.py      # Gmail API integration (inbox monitor, send replies)
│   └── email.py            # SMTP email sending
├── connectors/             # 42 enterprise connectors
│   ├── finance/            # Oracle, SAP, GSTN, Stripe, Tally, Banking AA...
│   ├── hr/                 # Darwinbox, Okta, Greenhouse, EPFO, Zoom...
│   ├── marketing/          # Salesforce, HubSpot, Google Ads, Ahrefs...
│   ├── ops/                # Jira, Zendesk, ServiceNow, PagerDuty...
│   ├── comms/              # Slack, GitHub, SendGrid, GCS, Twilio...
│   └── framework/          # BaseConnector, auth adapters, circuit breaker
├── workflows/              # Workflow engine, triggers, conditions
├── ui/src/
│   ├── pages/              # Landing, Dashboard, Agents, Sales, Blog, Resources, Ads
│   ├── components/         # AgentCard, ActivityTicker, InteractiveDemo, SocialProof
│   └── pages/blog/         # 5 SEO blog articles
│   └── pages/resources/    # 26 SEO content pages across 7 topic clusters
├── tests/
│   ├── unit/               # 124 unit tests
│   ├── connector_harness/  # Mock server + 174 connector tests
│   ├── synthetic_data/     # Invoice, resume, contract test data + 15 tests
│   └── e2e/                # Playwright browser tests
├── migrations/             # 8 PostgreSQL DDL files
├── helm/                   # Kubernetes Helm charts
├── docs/                   # PRD, architecture, QA test plan
└── scripts/                # Seed data, deployment helpers
```

---

## SEO & AI Discoverability

| Asset | URL | Status |
|-------|-----|--------|
| Landing page | https://agenticorg.ai | 39 URLs in sitemap |
| Blog (5 articles) | /blog | Invoice processing, RPA vs AI, reconciliation, HITL, no-code |
| Resources (26 pages) | /resources | 7 topic clusters, FAQ schemas, internal linking |
| llms.txt | /llms.txt | 4.6KB product summary for AI crawlers |
| llms-full.txt | /llms-full.txt | 18.7KB complete documentation |
| JSON-LD | 7 schemas | Organization, Product, FAQ, Breadcrumb, SoftwareCompany, WebSite, SoftwareApplication |
| AI crawlers | robots.txt | GPTBot, ClaudeBot, PerplexityBot, ChatGPT-User, OAI-SearchBot, cohere-ai, Applebot-Extended |

---

## Pricing

| Plan | Price | Agents | Connectors | Tasks |
|------|-------|--------|-----------|-------|
| Free | $0 | 35 | 20 | 500/day |
| Pro | $499/mo | Unlimited | 42 | Unlimited |
| Enterprise | Custom | Unlimited | 42 | Unlimited + SLA |

---

## India-First Connectors

Built for Indian enterprise — not retrofitted:
- **GSTN** — e-Invoice IRN, GSTR-1/2A/3B/9 filing, ITC reconciliation
- **EPFO** — ECR filing, UAN verification, passbook download
- **Income Tax India** — TDS 26Q/24Q, Form 16A, 26AS credit check
- **Darwinbox** — HRMS (payroll, attendance, performance, onboarding)
- **Tally Prime** — Accounting (vouchers, trial balance, GST reports)
- **Banking AA** — RBI-compliant Account Aggregator framework

---

## Security & Compliance

| Feature | Implementation |
|---------|---------------|
| Tenant isolation | PostgreSQL RLS, Redis key namespacing, GCS prefix isolation |
| HITL governance | Configurable thresholds, prompt lock on active agents, escalation chains |
| Audit trail | Every agent action logged, 7-year WORM retention, HMAC signed |
| PII masking | Default-on masking of email, phone, Aadhaar, PAN, bank accounts |
| SOC-2 ready | Password policy (bcrypt 12), token blacklist, rate limiting, HSTS, CSP |
| Prompt audit | Every prompt edit logged with user, timestamp, before/after, reason |

---

## Documentation

| Document | Description |
|----------|-------------|
| [PRD](docs/PRD.md) | Complete product requirements (7 pages) |
| [QA Test Plan](tests/QA_MANUAL_TEST_PLAN.md) | 65 manual test cases with steps |
| [QA Test Cases](tests/QA_TEST_CASES.md) | 70 automated test results |
| [Architecture](docs/architecture.md) | 8-layer system design |

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
