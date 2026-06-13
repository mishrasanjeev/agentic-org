# AgenticOrg — Product Requirements Document

**Version**: 2.1.0 | **Date**: 2026-03-25 | **Status**: Live (Production)
**URL**: https://agenticorg.ai | **App**: https://app.agenticorg.ai

> **Latest posture (2026-06-13):** Production deployment is Cloud Run-first, with API/UI Cloud Run services, Cloud SQL, Redis, Secret Manager, and Artifact Registry images. Commerce work through C6X5 is an OACP-grounded preview/cache foundation only: it supports public-safe artifact evaluation, prepared handoffs, refusals, and cache maintenance planning, but it does not enable public OACP publication, live checkout, live payments, live provider rails, merchant private APIs, or production commerce readiness.

---

## 1. What Is AgenticOrg?

AgenticOrg is an **AI virtual employee platform** for enterprises. Instead of hiring people for repetitive back-office tasks (processing invoices, running payroll, triaging support tickets), companies deploy AI agents that do the work — with human approval on every critical decision.

**One-line pitch**: "Name them. Train them. Deploy them. AI virtual employees that run your back office."

### Who Uses It

| User | Role | What They Do |
|------|------|-------------|
| **CEO / Admin** | Full access | Creates agents, manages platform, views all departments |
| **CFO** | Finance head | Monitors finance agents (AP, AR, Recon, Tax), approves high-value transactions |
| **CHRO** | HR head | Monitors HR agents (Onboarding, Payroll, Talent), approves offers and payroll |
| **CMO** | Marketing head | Monitors marketing agents (Campaigns, SEO, CRM), reviews content |
| **COO** | Operations head | Monitors ops agents (Support Triage, IT Ops, Compliance), handles P1 escalations |
| **Auditor** | Compliance | Read-only access to audit logs across all departments |

### What It Replaces

| Before AgenticOrg | After AgenticOrg |
|-------------------|-----------------|
| 3 FTEs processing invoices manually | AP Processor agent: 11 seconds per invoice |
| 5-day month-end close | 1-day close with AI agents |
| 40% support tickets mis-routed | 88% auto-classification accuracy |
| 2-week employee onboarding | 4-hour automated onboarding |
| Manual bank reconciliation (3 FTEs) | 99.7% auto-match, done by 6 AM daily |

---

## 2. Platform Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     LANDING PAGE                             │
│  Hero + Activity Ticker + Agents In Action + Interactive     │
│  Demo + Social Proof + Blog + Ads Landing Pages              │
└──────────────────────┬──────────────────────────────────────┘
                       │ Demo Request / Signup
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     APP (Dashboard)                          │
│  Agents | Workflows | Approvals | Audit | Observatory        │
│  Sales Pipeline | Prompt Templates | Connectors | Settings   │
└──────────────────────┬──────────────────────────────────────┘
                       │ API (FastAPI)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                     BACKEND                                  │
│  Agent Registry → LLM Router (Gemini) → Tool Gateway         │
│  NEXUS Orchestrator → HITL Queue → Audit Logger              │
│  Sales Agent → Gmail API → Email Sequences                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     PostgreSQL     Redis      43 Connectors
     (Cloud SQL)   (Cache)    (SAP, Oracle, GSTN,
                              Darwinbox, Slack...)
```

**Infrastructure**: Google Cloud (Cloud Run services in asia-southeast1, Artifact Registry in asia-south1, Cloud SQL, Redis, Secret Manager)
**LLM**: Gemini 2.5 Flash (primary), with Claude/GPT-4o fallback
**Cost**: Deployment-dependent; see `docs/deployment.md` for the current Cloud Run path and sizing assumptions.

---

## 3. Core Features

### 3.1 Agent Fleet (28 agents)

**24 built-in agents** across 5 domains + **custom agents** created by admin:

| Domain | Agents | Key Metrics |
|--------|--------|------------|
| **Finance** (6) | AP Processor, AR Collections, Reconciliation, Tax Compliance, Month-End Close, FP&A | 99.7% recon match, ₹69,800/mo saved |
| **HR** (6) | Onboarding, Payroll Engine, Talent Acquisition, Performance Coach, L&D, Offboarding | Zero payroll errors, 4-hour onboarding |
| **Marketing** (current: 9 core agents plus email path; target: 9 production CMO pillars) | Production-strength today: Campaign Pilot. Beta: Content Factory, Email Marketing, Social Media, ABM, Competitive Intel, Brand Monitor, SEO Strategist, and CRM Intelligence (deepened by CMO-4.3 with deterministic pipeline / funnel / scoring / churn / segments / SQL promotion / account health and policy/approval/audit/write-confirmation gates). Beta agents are not production-ready without real-vendor proof. | ROI claims must come from connected tenant data, not demo values |
| **Operations** (5) | Support Triage, IT Operations, Compliance Guard, Contract Intelligence, Vendor Manager | 88% auto-classify, zero mis-routes |
| **Backoffice** (3) | Legal Ops, Risk Sentinel, Facilities Agent | |
| **Sales** (1) | Sales Agent (Aarav) — lead qualification, email outreach, pipeline management | |
| **Custom** (user-created) | Any type — admin creates via wizard with custom prompt | |

### 3.1a CMO Production Replan: Real Marketing Department Requirements

The CMO product must be usable by real companies and their marketing teams, not just demo tenants. A CMO capability is not production-ready until it works against real tenant data, real connector configuration, real approvals, real audit trails, and a real operator UX.

**Product rule:** mocks and stubs are allowed only inside automated tests and local development harnesses. They must never be used as proof that a customer-facing CMO feature is production-ready.

| Area | Production requirement |
|------|------------------------|
| Data | KPIs must come from configured tenant systems such as Google Ads, Meta Ads, LinkedIn Ads, HubSpot, Salesforce, GA4, WordPress, Mailchimp, SendGrid, Buffer, Brandwatch, Ahrefs, Bombora, G2, and TrustRadius where enabled. Canonical CMO KPIs must use the unified KPI schema and return formula refs, source lineage, freshness, confidence, reconciliation status, and blocked/degraded status when required source facts are missing or cross-source totals do not reconcile. |
| Connectors | Each connector needs setup UI, credential validation, read/write permission separation, health checks, last-sync metadata, data freshness/TTL, policy-backed retry/degraded-mode behavior, idempotency metadata, external write confirmation, audit evidence, and clear reconnect actions. The CMO KPI API exposes real-company `connector_setup` and `connector_contracts` projections so missing, stale, expired-auth, insufficient-scope, timeout, rate-limit, vendor-error, partial-data, malformed-payload, quota-exhausted, disabled, read-ready, write-safe, write-unconfirmed, healthy, and degraded states are visible before production readiness is claimed. |
| Agents | Every marketing agent must have domain-specific execution logic, canonical input/output schemas, policy checks, confidence scoring, per-agent contract tests for happy path, invalid input, degraded connector input, HITL/policy behavior, audit refs, source refs, external-write safety, and truthful production/beta/stub/unavailable status. |
| Governance | All spend, publishing, audience, pricing, claim-making, crisis, and externally visible actions require a machine-checkable marketing policy manifest, explicit approval policy, approval timeout outcomes, escalation matrix routes, per-workflow promotion, structured decision-audit packages, and audit logging. |
| UX | The CMO dashboard must be a working marketing cockpit: KPI drill-downs, work queue, approvals, campaign timeline, data freshness, confidence, connector health, and next-best actions. |
| Onboarding | A company must be able to connect systems, import historical data, configure brand/legal/budget policies, run agents in shadow mode, then promote workflows to production one workflow at a time. |
| Reporting | Weekly and monthly reports must include formulas, data lineage, reconciliation status, freshness, exceptions, confidence, policy/audit/approval state, and a report quality gate. Missing critical fields or failed quality gates should block trusted report generation/delivery instead of silently producing weak output. |
| Pilot proof | `/kpis/cmo` must expose a code-backed pilot proof package that distinguishes real-vendor, vendor-sandbox, demo, test-double, and unknown evidence. Demo or test-double proof must never count as production readiness. Social Media, ABM, Competitive Intel, and Brand Monitor beta capabilities must remain unproven for production without real-vendor or pilot proof. |

#### Real-Company CMO Activation Journey

1. Admin selects company size, region, primary CRM, ad platforms, email platform, web analytics, CMS, and social/listening stack.
2. Admin connects each system through OAuth or approved API-key flow. The product validates scopes and runs a read-only health check.
3. Marketing Ops maps source fields: lifecycle stage, campaign IDs, UTM conventions, opportunity stages, revenue fields, account ownership, and consent fields.
4. CMO configures brand voice, blocked claims, legal review categories, budget thresholds, approval owners, SLA timers, and escalation paths.
5. Agents run in shadow mode for at least one full reporting cycle. The product compares recommendations against actual historical decisions and records precision/recall where measurable.
6. CMO promotes individual workflows from shadow to active. Promotion is per workflow, not a blanket switch for the whole marketing department.
7. The dashboard shows only real connected data in production tenants. Demo data is allowed only in explicitly labeled demo tenants.

#### CMO User Experience Bar

The CMO experience should feel like an operations console for a serious marketing team:

- One screen answers: what changed, what needs approval, what is at risk, what should we do next, and which data is stale.
- Every KPI card has drill-down, source, formula, last sync, confidence, owner, and affected campaigns/accounts.
- Approval screens show before/after previews, budget impact, audience impact, brand/legal/policy risk flags, source refs, agent rationale, policy/escalation/timeout/write/audit state, allowed reviewer actions, and rollback or stop plan.
- No empty decorative cards. If a connector is missing, the UI shows the missing system, why it matters, and the exact setup path.
- The dashboard supports saved views for Demand Gen, Content, ABM, Brand, and Executive Review.
- Tables support sorting, filtering, export, and row-level action history.
- Charts always have text equivalents and usable empty/error states.

#### CMO Production Readiness Gate

The CMO product is production-ready only when:

- zero customer-facing endpoints return hardcoded or demo KPI values for production tenants;
- CAC, MQL, SQL, MQL-to-SQL conversion, ROAS, pipeline contribution, conversion rates, LTV/CAC, experiment velocity, content, email, brand, and ABM KPI outputs use the unified CMO KPI schema and formula helpers before being treated as production-ready;
- canonical CMO KPI drill-downs expose formula inputs, source refs, connector refs, mappings, backfills, reconciliation, freshness, confidence, work queue/report refs, audit refs, owner, blockers, and next action before KPI cards or reports can be treated as explainable production output;
- paid spend, campaign conversions, GA4/web conversions, email engagement, content traffic, ABM account domains, currency, timezone, stale-sync, and partial-data reconciliation checks pass or visibly block/degrade affected KPI readiness before production reports trust those KPIs;
- zero CMO capabilities are marked production while backed only by `super().execute()` or generic LLM behavior;
- every CMO/marketing agent surface has deterministic contract tests proving stable output shape, truthful status, policy/HITL/audit/write-safety behavior, and production blocking for stub or unavailable agents;
- every production CMO workflow can run against configured real connectors or clearly fail with an actionable setup/degraded-state message;
- every marketing workflow passes lint for known agents, declared actions, capability state, connector readiness, shadow-mode read-only behavior, and safe external-write metadata before production promotion;
- connector contracts distinguish read readiness from write readiness, block missing write scopes, expose retry/idempotency metadata, and require external write confirmation with explicit external object IDs and audit evidence before active write steps are considered complete;
- required CMO field mappings and historical backfill states are present, valid, fresh, and visible before KPI confidence can be marked ready;
- weekly, daily ad, monthly ROI, campaign ad-hoc, and executive summary report quality gates pass before reports are delivered as trusted production output; blocked or warning reports are labeled `draft_only` or `internal_only`;
- the CMO dashboard and `/kpis/cmo` expose a prioritized work queue for approvals, escalations, connector issues, mapping/backfill blockers, workflow blockers, external-write failures, policy/audit gaps, KPI/reconciliation problems, and report gate blockers before those issues can be treated as resolved;
- the CMO dashboard and `/kpis/cmo` expose approval review projections for approval-sensitive actions, including preview/diff, budget and audience impact, risk flags, source refs, rationale, policy/escalation/timeout/write/audit refs, rollback/stop plan, blocked reasons, allowed actions, and CTA; approval fails closed when policy, write readiness, timeout, or audit prerequisites are unsafe or missing;
- `/kpis/cmo` exposes a CMO pilot proof package/summary with status, score, proven and unproven capabilities, blockers, risks, source/report/audit/test evidence refs, and next actions; demo and test-double environments cannot return production-passed proof, vendor-sandbox proof cannot be marketed as real-vendor proof, and Social Media, ABM, Competitive Intel, and Brand Monitor beta capabilities cannot be marketed as production without real-vendor/pilot proof;
- every CMO workflow exposes a shadow, blocked, ready, active, degraded, paused, or unavailable activation state and remains read-only until that individual workflow is explicitly promoted;
- every externally visible action has HITL policy, timeout outcome, escalation route, structured decision-audit package, audit record, and rollback or stop behavior;
- dashboard and docs distinguish production, beta, shadow, unavailable, demo, and degraded states;
- at least one pilot tenant completes onboarding with real connected marketing systems and generates a weekly report from real data.

### 3.2 Virtual Employee System

Each agent is a **virtual employee** with:

| Field | Purpose | Example |
|-------|---------|---------|
| **Employee Name** | Persona identity | "Priya" |
| **Designation** | Role title | "Senior AP Analyst - Mumbai" |
| **Specialization** | What they focus on | "Domestic invoices under ₹5L" |
| **Routing Filter** | Auto-route tasks to the right agent | `{"region": "west"}` |
| **System Prompt** | Instructions (the agent's "training") | 2000+ word prompt with steps, rules, guardrails |

**Multiple agents per role**: You can have 3 AP Processors — each for a different region, vendor type, or threshold. Smart routing picks the right one.

### 3.2b Per-Agent LLM Selection

Each agent can use a different LLM model. The platform supports Gemini 2.5 Flash (default), Claude 3.5 Sonnet, and GPT-4o. If the required API key isn't configured, the agent safely falls back to the global default (Gemini). This means agents created with "claude" won't break if the Anthropic key is removed — they'll just use Gemini instead.

### 3.2c Org Chart Hierarchy

Agents can report to other agents via `parent_agent_id` and `reporting_to` fields. This creates a management hierarchy: AP Analysts report to VP Finance, who reports to CFO. When an agent fails or triggers HITL, the task can escalate to the parent agent automatically. The hierarchy is visible in the agent detail view.

### 3.2d Per-Agent Budget Enforcement

Each agent can have a monthly cost cap (e.g., $200/month). Before every execution, the system checks if the agent's monthly spend has exceeded the cap. If exceeded, the agent returns `E1008 budget_exceeded` instead of making an LLM call. Cost is tracked per execution in the `AgentCostLedger` table. Admins can view budget usage via `GET /agents/{id}/budget`. Agents without budget limits run unlimited.

### 3.3 Agent Creator Wizard (Admin Only)

5-step no-code wizard at `/dashboard/agents/new`:

| Step | What User Does |
|------|---------------|
| 1. Persona | Sets employee name, designation, avatar, domain |
| 2. Role | Picks agent type (from 24+ built-in or types custom), sets specialization and routing filters |
| 3. Prompt | Selects from 27 prompt templates or writes custom instructions. Fills `{{variable}}` placeholders |
| 4. Behavior | Sets confidence floor (%), HITL condition, max retries |
| 5. Review | Reviews summary, clicks "Create as Shadow" |

### 3.4 Agent Execution Pipeline

When an agent runs (via API, Playground, or automated trigger):

```
1. Load prompt (from file for built-in, from DB for custom)
2. Send to LLM (Gemini 2.5 Flash): system prompt + task context
3. Parse JSON response
4. Compute confidence score (0.0 - 1.0)
5. Check HITL: if confidence < floor OR condition met → escalate to human
6. Return result: {status, output, confidence, reasoning_trace}
```

### 3.5 Prompt Templates

- **27 templates** (26 agent-specific + 1 sales agent)
- Each template has sections: `<processing_sequence>`, `<escalation_rules>`, `<anti_hallucination>`, `<output_format>`
- Templates support `{{variable}}` placeholders (e.g., `{{org_name}}`, `{{ap_hitl_threshold}}`)
- **Built-in templates are read-only** — clone to customize
- **Prompt lock**: Once an agent is promoted to Active, its prompt is frozen. Must clone to edit.
- **Audit trail**: Every prompt edit logged with who, when, before/after, reason

### 3.6 Human-in-the-Loop (HITL)

Agents escalate to humans when:
- Confidence drops below the configured floor (e.g., 88%)
- A business rule triggers (e.g., invoice > ₹5 lakhs)
- The agent encounters an error it can't handle

The human sees: full context, agent's analysis, confidence score, recommendation. They can: Approve, Reject, or Override.

### 3.7 RBAC (Role-Based Access Control)

| Role | Domains Visible | Can Create Agents | Can Edit Prompts | Can See Settings |
|------|----------------|-------------------|-----------------|-----------------|
| Admin (CEO) | All | Yes | Yes (own domain) | Yes |
| CFO | Finance only | No | Finance prompts | No |
| CHRO | HR only | No | HR prompts | No |
| CMO | Marketing only | No | Marketing prompts | No |
| COO | Ops only | No | Ops prompts | No |
| Auditor | All (read-only) | No | No | No |

---

## 4. Sales Pipeline & Agent

### 4.1 Inbound Flow (Automated)

```
Visitor fills demo form on landing page
    ↓ (same HTTP request — under 2 minutes)
Lead created in pipeline (deduplicated by email)
    ↓
Sales agent qualifies: scores 0-100 by role, company, domain
    ↓
Personalized email sent from sanjeev@agenticorg.ai
    ↓
Lead stage: new → contacted
    ↓
Daily CronJob (9 AM IST) sends follow-ups:
  Day 1: Value-add email with use case
  Day 3: Social proof email
  Day 7: Direct ask
  Day 14: Breakup email
    ↓
Inbox monitor (every 5 min) detects replies:
  → Matches reply to pipeline lead
  → Upgrades stage to "qualified"
  → Sales agent drafts contextual response
  → Sends reply in same Gmail thread
```

### 4.2 Lead Scoring

| Factor | Points |
|--------|--------|
| CXO role | 30 |
| VP role | 25 |
| Director | 20 |
| Enterprise company (500+) | 25 |
| Mid-size (100-500) | 20 |
| Finance/Ops domain | 25 |
| HR domain | 20 |
| Marketing domain | 15 |

### 4.3 Pipeline Stages

`new` → `contacted` → `qualified` → `demo_scheduled` → `demo_done` → `trial` → `negotiation` → `closed_won` / `closed_lost`

### 4.4 Email Personalization

The sales agent sends **different emails based on role**:

| Role | Subject Line | Key Metric |
|------|-------------|-----------|
| CFO/Finance | "₹69,800/mo your AP team is leaving on the table" | Invoice processing in 11 seconds |
| CHRO/HR | "Zero payroll errors, onboarding in hours not weeks" | 4-hour onboarding, zero PF/ESI errors |
| COO/CTO/Ops | "88% of your support tickets auto-triaged" | War rooms in 30 seconds |
| CMO/Marketing | "Connect your marketing stack before trusting ROI" | Real connected campaigns, approvals, and KPI lineage |
| CEO/Unknown | "Your back office, fully automated" | 3 bullet metrics |

All emails sign as **"Sanjeev Kumar, Founder, AgenticOrg"** — not as AI. Calendar link: https://calendar.app.google/p6P4DpRn85yxHua99

---

## 5. Landing Page & Marketing

### 5.1 Page Sections (in order)

1. **Navbar** — Platform, Solutions, Pricing, Playground, Blog, Sign In, Book a Demo
2. **Hero** — "Your Back Office Runs Itself" + Agent Activity Ticker (animated)
3. **Logo Bar** — Oracle, SAP, Salesforce, Slack, GSTN, Darwinbox, Stripe, HubSpot
4. **Pain Points** — 72 hours close cycle, ₹12L/year lost, 40% mis-routed
5. **Agents In Action** — 6 animated agent cards with step-by-step execution
6. **Platform Overview** — 4 cards: Agent Fleet, Observatory, HITL Approvals, Agent Creator
7. **Role Solutions** — CFO, CHRO, CMO, COO — each with pain, agents, metric
8. **How It Works** — 3 steps + Workflow Animation (5-stage pipeline)
9. **Interactive Demo** — Terminal showing real agent execution (4 scenarios)
10. **Trust & Security** — HITL, Shadow Mode, Audit Trail, Tenant Isolation
11. **Social Proof** — 5 testimonials with star ratings
12. **ROI Calculator** — Interactive calculator
13. **India-First Connectors** — GSTN, EPFO, Darwinbox, Pine Labs, Tally, DigiLocker
14. **Final CTA** — "Stop paying people to do what AI virtual employees can do better"
15. **Footer** — Platform, Solutions, Resources, Company links + Blog

### 5.2 Blog (5 articles)

| Article | Target Keywords |
|---------|----------------|
| AI Invoice Processing for Indian Enterprises | AI invoice processing, GSTN, 3-way matching |
| AI Virtual Employees vs RPA Bots | virtual employee, RPA vs AI |
| Automated Bank Reconciliation: 99.7% Match Rate | bank reconciliation, automated reconciliation |
| HITL Governance: Why Enterprise AI Needs Human Approval | HITL governance, AI safety, SOC-2 |
| No-Code AI Agent Builder: Custom Virtual Employees in 5 Min | no-code agent builder, custom AI agent |

### 5.3 Google Ads Landing Pages (3 pages)

| URL | Keyword | Metric |
|-----|---------|--------|
| /solutions/ai-invoice-processing | AI invoice processing India | 11 sec per invoice |
| /solutions/automated-bank-reconciliation | automated bank reconciliation | 99.7% match rate |
| /solutions/payroll-automation | payroll automation India | Zero errors |

Each has: dedicated hero, inline demo form, feature list, testimonial, CTA. Form submissions create leads in pipeline.

---

## 6. SEO & Discoverability

| Asset | Status |
|-------|--------|
| Meta title | "AgenticOrg — AI Virtual Employees for Enterprise" |
| Meta keywords | 30+ keywords including "virtual employee", "no-code agent builder" |
| OG / Twitter cards | Updated with virtual employee messaging |
| JSON-LD schemas (7) | Organization, SoftwareApplication, WebSite, FAQPage (7 Q&As), BreadcrumbList, Product (3 pricing tiers), SoftwareCompany |
| sitemap.xml | 12 URLs (landing, pricing, playground, evals, signup, login, blog index, 5 blog posts) |
| robots.txt | Allows all crawlers + GPTBot, PerplexityBot, ChatGPT-User, Google-Extended |
| llms.txt | 4.6KB product summary for AI discovery |
| llms-full.txt | 18.7KB complete documentation for LLMs |

---

## 7. Technical Reference

### API Endpoints

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | /health/liveness | No | Health check |
| POST | /auth/signup | No | Create account |
| POST | /auth/login | No | Get JWT token |
| POST | /auth/google | No | Google OAuth |
| POST | /demo-request | No | Demo form (creates lead + triggers agent) |
| GET | /agents | JWT | List agents (RBAC filtered) |
| POST | /agents | JWT+Admin | Create agent |
| GET | /agents/{id} | JWT | Agent detail |
| PATCH | /agents/{id} | JWT | Update agent (prompt lock on active) |
| POST | /agents/{id}/run | JWT | Execute agent |
| POST | /agents/{id}/clone | JWT | Clone agent |
| GET | /agents/{id}/prompt-history | JWT | Prompt edit audit trail |
| GET | /prompt-templates | JWT | List prompt templates |
| POST | /prompt-templates | JWT+Admin | Create template |
| PUT | /prompt-templates/{id} | JWT+Admin | Update (rejects built-in) |
| GET | /sales/pipeline | JWT | Sales pipeline |
| POST | /sales/pipeline/process-lead | JWT | Trigger sales agent on lead |
| GET | /sales/metrics | JWT | Sales metrics |
| POST | /sales/process-inbox | JWT | Process Gmail inbox replies |
| GET | /workflows | JWT | List workflows |
| GET | /approvals | JWT | HITL approval queue |
| GET | /audit | JWT | Audit log |

### Automated Jobs

| Job | Schedule | What It Does |
|-----|----------|-------------|
| sales-followup | Daily 9 AM IST | Processes all leads needing follow-up emails |
| sales-inbox-monitor | Every 5 minutes | Reads Gmail inbox, matches replies to leads, triggers agent responses |

### Pricing

| Plan | Price | Agents | Connectors | Tasks |
|------|-------|--------|-----------|-------|
| Free | $0 | 3 | 5 | 100/day |
| Pro | $499/mo | 12 | 20 | Unlimited |
| Enterprise | Custom | All 24+ | All 42 | Unlimited + SLA |

---

*End of PRD — 7 pages*
