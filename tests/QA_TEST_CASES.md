# AgenticOrg — QA Test Cases

**Last Run**: 2026-03-25 03:05 UTC
**Environment**: Production (https://app.agenticorg.ai)
**Total Tests**: 70 | **Passed**: 69 | **Warnings**: 1

---

## 1. Infrastructure & Health (T01-T02)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T01 | Liveness probe | GET /api/v1/health/liveness | HTTP 200, `{"status":"alive"}` | 200, alive | PASS |
| T02 | Readiness probe | GET /api/v1/health | HTTP 200, db=healthy, redis=healthy | 200, all healthy | PASS |

## 2. Public Pages (T03-T20)

| ID | Test Case | URL | Expected | Actual | Status |
|----|-----------|-----|----------|--------|--------|
| T03 | Landing page | / | HTTP 200 | 200 | PASS |
| T04 | Login page | /login | HTTP 200 | 200 | PASS |
| T05 | Signup page | /signup | HTTP 200 | 200 | PASS |
| T06 | Pricing page | /pricing | HTTP 200 | 200 | PASS |
| T07 | Playground page | /playground | HTTP 200 | 200 | PASS |
| T08 | Evals page | /evals | HTTP 200 | 200 | PASS |
| T09 | Blog index | /blog | HTTP 200 | 200 | PASS |
| T10 | Blog: AI Invoice Processing | /blog/ai-invoice-processing-india | HTTP 200 | 200 | PASS |
| T11 | Blog: Virtual Employee vs RPA | /blog/virtual-employee-vs-rpa | HTTP 200 | 200 | PASS |
| T12 | Blog: Bank Reconciliation | /blog/automated-bank-reconciliation | HTTP 200 | 200 | PASS |
| T13 | Blog: No-Code Agent Builder | /blog/no-code-ai-agent-builder | HTTP 200 | 200 | PASS |
| T14 | Ads: Invoice Processing | /solutions/ai-invoice-processing | HTTP 200 | 200 | PASS |
| T15 | Ads: Bank Reconciliation | /solutions/automated-bank-reconciliation | HTTP 200 | 200 | PASS |
| T16 | Ads: Payroll Automation | /solutions/payroll-automation | HTTP 200 | 200 | PASS |
| T17 | sitemap.xml | /sitemap.xml | HTTP 200 | 200 | PASS |
| T18 | robots.txt | /robots.txt | HTTP 200 | 200 | PASS |
| T19 | llms.txt | /llms.txt | HTTP 200 | 200 | PASS |
| T20 | manifest.json | /manifest.json | HTTP 200 | 200 | PASS |

## 3. Authentication (T21-T30)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T21 | Login as CEO | POST /auth/login {ceo@agenticorg.local, ceo123!} | 200 + access_token | Token returned | PASS |
| T22 | Login as CFO | POST /auth/login {cfo@agenticorg.local, cfo123!} | 200 + access_token | Token returned | PASS |
| T23 | Login as CHRO | POST /auth/login {chro@agenticorg.local, chro123!} | 200 + access_token | Token returned | PASS |
| T24 | Login as CMO | POST /auth/login {cmo@agenticorg.local, cmo123!} | 200 + access_token | Token returned | PASS |
| T25 | Login as COO | POST /auth/login {coo@agenticorg.local, coo123!} | 200 + access_token | Token returned | PASS |
| T26 | Login as Auditor | POST /auth/login {auditor@agenticorg.local, audit123!} | 200 + access_token | Token returned | PASS |
| T27 | Signup — new account | POST /auth/signup {org_name, admin_name, admin_email, password} | HTTP 201 + token | 201, token OK | PASS |
| T28 | Signup — duplicate email | POST /auth/signup with same email | HTTP 409 | 409 | PASS |
| T29 | Login — wrong password | POST /auth/login with wrong password | HTTP 401 | 401 | PASS |
| T30 | Google OAuth — fake token | POST /auth/google {credential: "fake"} | HTTP 401 | 401 | PASS |

## 4. Dashboard Pages — Authenticated (T31-T42)

| ID | Test Case | URL | Expected | Actual | Status |
|----|-----------|-----|----------|--------|--------|
| T31 | Dashboard home | /dashboard | HTTP 200 | 200 | PASS |
| T32 | Agents list | /dashboard/agents | HTTP 200 | 200 | PASS |
| T33 | Workflows | /dashboard/workflows | HTTP 200 | 200 | PASS |
| T34 | Approvals | /dashboard/approvals | HTTP 200 | 200 | PASS |
| T35 | Audit log | /dashboard/audit | HTTP 200 | 200 | PASS |
| T36 | Observatory | /dashboard/observatory | HTTP 200 | 200 | PASS |
| T37 | SLA Monitor | /dashboard/sla | HTTP 200 | 200 | PASS |
| T38 | Connectors | /dashboard/connectors | HTTP 200 | 200 | PASS |
| T39 | Schemas | /dashboard/schemas | HTTP 200 | 200 | PASS |
| T40 | Settings | /dashboard/settings | HTTP 200 | 200 | PASS |
| T41 | Sales Pipeline | /dashboard/sales | HTTP 200 | 200 | PASS |
| T42 | Prompt Templates | /dashboard/prompt-templates | HTTP 200 | 200 | PASS |

## 5. Agent CRUD & Execution (T43-T50)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T43 | List agents | GET /agents | 28 agents, persona fields present | 28 agents, all fields present | PASS |
| T44 | Get agent detail | GET /agents/{id} | HTTP 200 with full config | 200 | PASS |
| T45 | Create agent with persona | POST /agents {employee_name, designation, specialization, routing_filter} | 201 + agent_id | Created OK | PASS |
| T46 | Run built-in agent (AP Processor) | POST /agents/{ap_id}/run | status=completed, confidence > 0 | completed, 0.88 | PASS |
| T47 | Run custom type agent (no Python class) | POST /agents/{custom_id}/run | status=completed or hitl_triggered | hitl_triggered, 0.85 | PASS |
| T48 | Prompt lock on active agent | PATCH /agents/{active_id} {system_prompt_text} | HTTP 409 "Prompt is locked" | 409 | PASS |
| T49 | Clone agent with persona | POST /agents/{id}/clone {overrides: {employee_name}} | clone_id returned, status=shadow | Clone created | PASS |
| T50 | Prompt edit history | GET /agents/{id}/prompt-history | Array of history entries | [] (no edits on new agent) | PASS |

## 6. RBAC — Domain Isolation (T51-T52)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T51 | CFO sees only finance | Login as CFO, GET /agents | Only domain=finance agents | finance only, 13 agents | PASS |
| T52 | CHRO sees only HR | Login as CHRO, GET /agents | Only domain=hr agents | hr only, 6 agents | PASS |

## 7. Prompt Templates (T53-T57)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T53 | List templates | GET /prompt-templates | 27 templates, 27 built-in | 27 total | PASS |
| T54 | Create template | POST /prompt-templates {name, agent_type, domain, template_text} | 201 + id | Created OK | PASS |
| T55 | Update custom template | PUT /prompt-templates/{id} {template_text} | 200 | 200 | PASS |
| T56 | Delete custom template | DELETE /prompt-templates/{id} | 200 (soft delete) | 200 | PASS |
| T57 | Edit built-in rejected | PUT /prompt-templates/{builtin_id} | 409 "Cannot edit built-in" | 409 | PASS |

## 8. Workflows, Approvals, Audit (T58-T60)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T58 | List workflows | GET /workflows | HTTP 200 | 200 | PASS |
| T59 | List approvals | GET /approvals | HTTP 200 | 200 | PASS |
| T60 | Query audit log | GET /audit | HTTP 200 | 200 | PASS |

## 9. Sales Pipeline & Agent (T61-T66)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T61 | Demo request creates lead + triggers agent | POST /demo-request {name, email, company, role} | lead_id returned, agent_status=completed | Lead created, agent completed | PASS |
| T62 | Pipeline overview | GET /sales/pipeline | total, funnel, leads array | 25 leads, funnel shown | PASS |
| T63 | Get lead detail | GET /sales/pipeline/{id} | Lead with all fields | Score 70, stage contacted | PASS |
| T64 | Update lead | PATCH /sales/pipeline/{id} {notes, budget} | HTTP 200 | 200 | PASS |
| T65 | Sales metrics | GET /sales/metrics | total_leads, new_this_week, avg_score, emails_sent | All present | PASS |
| T66 | Due followups | GET /sales/pipeline/due-followups | Array of leads needing followup | 1 due | PASS |

## 10. Gmail Integration (T67)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T67 | Read inbox via Gmail API | Service account + domain delegation | Returns messages | 20 messages read, 0 non-bounce | PASS |

## 11. SEO & Discoverability (T68-T69)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T68 | Meta tags | Check title, description, keywords, OG | All present with "virtual employee" | All correct | PASS |
| T69 | JSON-LD schemas | Count schema types | 7+ schemas | 16 schema types | PASS |

## 12. Infrastructure — CronJobs (T70)

| ID | Test Case | Steps | Expected | Actual | Status |
|----|-----------|-------|----------|--------|--------|
| T70 | CronJobs running | kubectl get cronjobs | sales-followup (daily 9AM), sales-inbox-monitor (every 5min) | Both active | PASS |

---

## Manual QA Checklist (UI — requires browser)

These tests require a browser and should be done manually by the QA team:

### Landing Page
- [ ] Hero section loads with Agent Activity Ticker (animated, scrolling feed)
- [ ] "Agents In Action" section shows 6 animated agent cards (click to focus)
- [ ] Interactive Demo terminal cycles through 4 scenarios (Invoice, Onboarding, Triage, Recon)
- [ ] Workflow Animation shows 5-stage pipeline with progress bar
- [ ] Social Proof section shows 5 testimonials with auto-rotation
- [ ] "Book a Demo" modal opens, all 5 form fields work
- [ ] Blog link in navbar navigates to /blog
- [ ] Footer links all work
- [ ] Mobile menu opens/closes (test on phone or responsive mode)
- [ ] Skip-to-content link visible on Tab key press

### Signup Flow
- [ ] Go to /signup, fill all fields, submit
- [ ] Account created, redirected to onboarding
- [ ] Login with newly created credentials works

### Dashboard (Login as CEO)
- [ ] Agent fleet page shows 28 agents with avatars and employee names
- [ ] Click agent → detail page shows persona header (name, designation, avatar)
- [ ] "Prompt" tab shows system prompt (locked badge on active agents)
- [ ] Agent Create page (/dashboard/agents/new) shows 5-step wizard
- [ ] Sales Pipeline page shows funnel visualization and lead table
- [ ] "Run Agent" button on a lead triggers the sales agent
- [ ] Prompt Templates page lists 27 templates, built-in badge shown

### RBAC (Login as CFO)
- [ ] Can only see finance agents (not HR, Marketing, Ops)
- [ ] Cannot access /dashboard/agents/new (admin only)
- [ ] Cannot access /dashboard/settings

### Playground
- [ ] 8 use cases load
- [ ] Click "Run" on any use case → agent executes, trace displayed
- [ ] Real Gemini LLM response (not mocked)

### Ads Landing Pages
- [ ] /solutions/ai-invoice-processing — hero, metric, form, features, testimonial
- [ ] Submit demo form → "You're in!" confirmation
- [ ] /solutions/automated-bank-reconciliation — same structure
- [ ] /solutions/payroll-automation — same structure

### Blog
- [ ] /blog lists all 5 posts with category badges
- [ ] Click post → full article with proper headings
- [ ] Related posts section at bottom
- [ ] CTA to signup/playground at bottom

### Email
- [ ] Submit demo request → check sanjeev@agenticorg.ai inbox for:
  - Personalized sales email from "Sanjeev Kumar, Founder, AgenticOrg"
  - Subject personalized by role (CFO gets ₹69,800 subject, COO gets 88% triage)
  - Calendar booking link works (https://calendar.app.google/p6P4DpRn85yxHua99)
  - Playground link works
  - Sent from sanjeev@agenticorg.ai (not gmail.com)

---

## Test Data

### Demo Credentials
| Role | Email | Password |
|------|-------|----------|
| CEO/Admin | ceo@agenticorg.local | ceo123! |
| CFO | cfo@agenticorg.local | cfo123! |
| CHRO | chro@agenticorg.local | chro123! |
| CMO | cmo@agenticorg.local | cmo123! |
| COO | coo@agenticorg.local | coo123! |
| Auditor | auditor@agenticorg.local | audit123! |

### Key URLs
| Page | URL |
|------|-----|
| Landing | https://agenticorg.ai |
| App | https://app.agenticorg.ai |
| Playground | https://app.agenticorg.ai/playground |
| Blog | https://app.agenticorg.ai/blog |
| Sales Pipeline | https://app.agenticorg.ai/dashboard/sales |
| Agent Creator | https://app.agenticorg.ai/dashboard/agents/new |

### API Base
- Production: `https://app.agenticorg.ai/api/v1/`
- Auth header: `Authorization: Bearer {token from /auth/login}`
