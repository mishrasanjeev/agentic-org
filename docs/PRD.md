# AgenticOrg — Product Requirements Document

**Version**: 2.1.0 | **Date**: 2026-03-25 | **Status**: Live (Production)
**URL**: https://agenticorg.ai | **App**: https://app.agenticorg.ai

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
     PostgreSQL     Redis      42 Connectors
     (Cloud SQL)   (Cache)    (SAP, Oracle, GSTN,
                              Darwinbox, Slack...)
```

**Infrastructure**: Google Cloud (GKE Autopilot, Cloud SQL, asia-south1)
**LLM**: Gemini 2.5 Flash (primary), with Claude/GPT-4o fallback
**Cost**: ~$95/month

---

## 3. Core Features

### 3.1 Agent Fleet (28 agents)

**24 built-in agents** across 5 domains + **custom agents** created by admin:

| Domain | Agents | Key Metrics |
|--------|--------|------------|
| **Finance** (6) | AP Processor, AR Collections, Reconciliation, Tax Compliance, Month-End Close, FP&A | 99.7% recon match, ₹69,800/mo saved |
| **HR** (6) | Onboarding, Payroll Engine, Talent Acquisition, Performance Coach, L&D, Offboarding | Zero payroll errors, 4-hour onboarding |
| **Marketing** (5) | Campaign Pilot, Content Factory, SEO Strategist, CRM Intelligence, Brand Monitor | 3.2x campaign ROI |
| **Operations** (5) | Support Triage, IT Operations, Compliance Guard, Contract Intelligence, Vendor Manager | 88% auto-classify, zero mis-routes |
| **Backoffice** (3) | Legal Ops, Risk Sentinel, Facilities Agent | |
| **Sales** (1) | Sales Agent (Aarav) — lead qualification, email outreach, pipeline management | |
| **Custom** (user-created) | Any type — admin creates via wizard with custom prompt | |

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
| CMO/Marketing | "3.2x campaign ROI on autopilot" | Campaigns launch in minutes |
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
