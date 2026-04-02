# AgenticOrg — Manual QA Test Plan

**Version**: 2.1.0
**Date**: 2026-03-25
**Environment**: https://app.agenticorg.ai
**Landing**: https://agenticorg.ai

---

## How the Platform Works (Read First)

AgenticOrg is an **AI virtual employee platform**. Companies create AI agents that automate back-office work — invoices, payroll, support tickets, campaigns. Each agent has a name, role, and instructions (like a real employee).

### Core Concepts

| Concept | What It Is |
|---------|-----------|
| **Agent** | An AI virtual employee (e.g., "Priya, AP Processor - Mumbai") |
| **Prompt Template** | The agent's instructions — what to do, how to do it, when to escalate |
| **HITL** | Human-in-the-Loop — agents ask a human before high-stakes decisions |
| **Shadow Mode** | Agent runs in parallel but takes no action — for testing before going live |
| **Pipeline** | Sales lead tracking — new → contacted → qualified → demo → trial → won/lost |
| **Domain** | Department — Finance, HR, Marketing, Ops, Backoffice |
| **RBAC** | Role-based access — CFO sees only finance, CHRO sees only HR |

### Key Flows

```
FLOW 1: Visitor → Demo Request → Sales Agent → Email → Pipeline
  Visitor fills demo form on landing page
  → Lead created in sales pipeline
  → Sales agent (Aarav) qualifies the lead (scores 0-100)
  → Personalized email sent to the prospect
  → Lead appears in Sales Pipeline dashboard

FLOW 2: Admin → Create Agent → Test → Promote
  Admin opens Agent Creator wizard (5 steps)
  → Sets persona (name, designation), picks role, writes prompt
  → Agent created in Shadow mode
  → Admin runs agent on test data
  → If good, promotes to Active

FLOW 3: Agent Runs a Task
  Task arrives (e.g., process invoice)
  → Agent loads prompt (instructions)
  → Calls Gemini LLM for reasoning
  → Returns result with confidence score
  → If confidence < threshold → HITL triggered (human reviews)

FLOW 4: CFO Logs In → Sees Only Finance
  CFO logs in with cfo@agenticorg.local
  → Dashboard shows only finance agents (AP, AR, Recon, Tax, Close, FPA)
  → Cannot see HR, Marketing, or Ops agents
  → Cannot access admin pages (Settings, Connectors)
```

---

## Test Credentials

| Role | Email | Password | What They See |
|------|-------|----------|---------------|
| CEO/Admin | ceo@agenticorg.local | ceo123! | Everything — all domains, all pages |
| CFO | cfo@agenticorg.local | cfo123! | Finance agents only |
| CHRO | chro@agenticorg.local | chro123! | HR agents only |
| CMO | cmo@agenticorg.local | cmo123! | Marketing agents only |
| COO | coo@agenticorg.local | coo123! | Operations agents only |
| Auditor | auditor@agenticorg.local | audit123! | Read-only audit log |

---

## Test Cases

### SECTION A: Landing Page & Public Pages

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| A1 | Landing page loads | Open https://agenticorg.ai | Page loads with hero, animations, no errors | | |
| A2 | Agent Activity Ticker | Look at right side of hero section | Scrolling feed showing agent names (Priya, Arjun, Maya...) with status badges, auto-updates every 3 seconds | | |
| A3 | Agents In Action section | Scroll down past the pain points section | 6 agent cards visible. Click any card — it expands showing step-by-step execution with typing animation | | |
| A4 | Interactive Demo terminal | Scroll to "Watch Agents Think, Execute & Decide" | Terminal shows agent execution. 4 tabs: Invoice Processing, Employee Onboarding, Support Triage, Bank Reconciliation. Click each — different scenario plays | | |
| A5 | Workflow Animation | Scroll to "How It Works" section | 5-stage animated flow: Task Arrives → Agent Picks Up → LLM Reasoning → Result → HITL Check. Progress bar moves between stages | | |
| A6 | Social Proof | Scroll to testimonials section | 5 testimonial cards with star ratings, auto-rotating. Names: Rajesh Mehta, Ananya Sharma, etc. | | |
| A7 | Book a Demo modal | Click "Book a Demo" button in navbar | Modal opens with form: Name, Email, Company, Role, Phone. Fill and submit → "Thanks!" confirmation | | |
| A8 | Mobile responsiveness | Open on phone or resize browser to 375px | Hamburger menu appears. All sections stack vertically. No horizontal scroll. | | |
| A9 | Blog page | Click "Blog" in navbar | /blog page with 5 articles listed. Each shows title, category badge, date | | |
| A10 | Blog article | Click any article | Full article with headings, paragraphs, keyword tags, related posts, CTA at bottom | | |
| A11 | Pricing page | Navigate to /pricing | 3 tiers: Free ($0), Pro ($499/mo), Enterprise (Contact). Feature comparison | | |
| A12 | Playground page | Navigate to /playground | 8 use cases listed. No login required. | | |
| A13 | Evals page | Navigate to /evals | Agent evaluation scores across 6 dimensions | | |

### SECTION B: Signup & Login

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| B1 | Signup — happy path | Go to /signup. Fill: Org name, Your name, Email (use a unique email), Password. Click Sign Up | Account created, redirected to onboarding. Token stored (check localStorage) | | |
| B2 | Signup — duplicate email | Try signing up again with same email | Error message: "Email already registered" or similar 409 error | | |
| B3 | Signup — empty fields | Leave Name blank, click Sign Up | Validation error — form should not submit | | |
| B4 | Login — CEO | Go to /login. Enter ceo@agenticorg.local / ceo123! | Logged in, redirected to /dashboard. Sidebar shows ALL menu items | | |
| B5 | Login — CFO | Login as cfo@agenticorg.local / cfo123! | Logged in. Sidebar shows: Dashboard, Observatory, Agents, Workflows, Approvals, Audit. NO Settings, Connectors, Schemas | | |
| B6 | Login — wrong password | Enter ceo@agenticorg.local / wrongpassword | Error: "Invalid credentials" | | |
| B7 | Logout | Click logout (if available) or clear localStorage and refresh | Redirected to /login | | |
| B8 | Session expiry | Login, wait 60+ minutes, try navigating | Should redirect to /login (token expired) | | |

### SECTION C: Dashboard & Navigation (Login as CEO)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| C1 | Dashboard home | Navigate to /dashboard | Shows overview stats: agents, workflows, recent activity | | |
| C2 | Agents page | Click "Agents" in sidebar | Grid of agent cards. Each shows: avatar circle, employee name, designation, type, domain, confidence, shadow samples, status badge | | |
| C3 | Agent detail | Click any agent card | Detail page with: Persona header (avatar, name, designation, specialization). 5 tabs: overview, config, prompt, shadow, cost | | |
| C4 | Agent Prompt tab | On agent detail, click "Prompt" tab | Shows system prompt text. Active agents show "Locked" badge. Shadow agents show "Editable" badge | | |
| C5 | Workflows page | Click "Workflows" | List of workflow definitions | | |
| C6 | Approvals page | Click "Approvals" | HITL approval queue — shows pending approvals (if any) | | |
| C7 | Audit page | Click "Audit Log" | List of audit events with: event type, actor, action, outcome, timestamp | | |
| C8 | Observatory | Click "Observatory" | Real-time agent monitoring with traces and metrics | | |
| C9 | Connectors | Click "Connectors" | List of 51 connectors with status | | |
| C10 | Settings | Click "Settings" | Admin settings page | | |
| C11 | Prompt Templates | Click "Prompt Templates" | 27 templates listed. Built-in badge shown. Click to expand and see template text | | |
| C12 | Sales Pipeline | Click "Sales Pipeline" | Pipeline dashboard: funnel bar, metrics cards, lead table | | |

### SECTION D: Agent Creation Wizard (Login as CEO)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| D1 | Open wizard | Click "Agents" → click "New Agent" button (or navigate to /dashboard/agents/new) | 5-step wizard with progress bar: Persona → Role → Prompt → Behavior → Review | | |
| D2 | Step 1: Persona | Fill: Employee Name = "Test Bot", Designation = "QA Analyst", Domain = Finance. Click Next | Moves to Step 2 | | |
| D3 | Step 2: Role | Select agent type from dropdown (e.g., ap_processor). Add specialization: "Test invoices". Add routing filter: key=env, value=qa. Click Next | Moves to Step 3 | | |
| D4 | Step 2: Custom type | Check "Create custom agent type". Type "customer_success". Click Next | Moves to Step 3 (custom type accepted) | | |
| D5 | Step 3: Prompt | Select a template from dropdown → template text loads in textarea. Fill any {{variable}} fields shown. Click Next | Template variables shown and fillable. Preview updates. Moves to Step 4 | | |
| D6 | Step 3: Custom prompt | Don't select template. Type custom prompt in textarea. Click Next | Custom prompt accepted | | |
| D7 | Step 4: Behavior | Adjust confidence slider to 90%. Set HITL condition: "amount > 100000". Click Next | Moves to Step 5 (Review) | | |
| D8 | Step 5: Review | Review all settings shown | Summary shows: avatar, name, type, domain, confidence, HITL, prompt preview | | |
| D9 | Create agent | Click "Create as Shadow" | Agent created. Redirected to agent detail page. Status = shadow | | |
| D10 | Verify in list | Go back to Agents page | New agent appears in the grid with correct name and status | | |

### SECTION E: Agent Execution (Login as CEO)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| E1 | Run built-in agent | Go to Playground. Select "Process Invoice" use case. Click Run | Agent executes. Trace shows: LLM reasoning, tool calls, confidence. Status: completed | | |
| E2 | Run with HITL trigger | Run an agent where confidence will be low | Status: hitl_triggered. HITL approval request shown | | |
| E3 | Run custom agent | Create a custom agent (Section D), then run it via Playground or API | Agent executes using custom prompt. Returns result | | |

### SECTION F: RBAC — Domain Isolation

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| F1 | CFO sees finance only | Login as CFO. Go to Agents page | Only finance agents shown (AP Processor, AR Collections, Recon, Tax, Close, FPA). NO HR, Marketing, Ops agents | | |
| F2 | CFO cannot create agents | Login as CFO. Try navigating to /dashboard/agents/new | Access denied or redirected (admin only) | | |
| F3 | CHRO sees HR only | Login as CHRO. Go to Agents page | Only HR agents shown (Onboarding, Payroll, Talent, Performance, L&D, Offboarding) | | |
| F4 | CMO sees marketing only | Login as CMO. Go to Agents page | Only marketing agents shown | | |
| F5 | COO sees ops only | Login as COO. Go to Agents page | Only ops agents shown | | |
| F6 | Auditor — read only | Login as Auditor. Check sidebar | Can see Audit Log. Cannot create/edit agents | | |

### SECTION G: Prompt Templates (Login as CEO)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| G1 | List templates | Go to Prompt Templates page | 27+ templates shown. Each has: name, domain, agent type, built-in badge | | |
| G2 | View template | Click any template | Template text shown with full prompt content | | |
| G3 | Create custom template | Click "Create Template". Fill: name, agent type, domain, template text. Click Create | Template created, appears in list without built-in badge | | |
| G4 | Edit custom template | Click the custom template created in G3. Edit the text. Save | Template updated | | |
| G5 | Delete custom template | Click delete on the custom template | Template removed from list (soft delete) | | |
| G6 | Built-in template is read-only | Try editing a built-in template (one with "Built-in" badge) | Error: "Cannot edit built-in templates. Clone it to create a custom version." | | |
| G7 | Filter by domain | Select "Finance" from domain dropdown | Only finance templates shown (ap_processor, ar_collections, recon_agent, tax_compliance, close_agent, fpa_agent) | | |

### SECTION H: Sales Pipeline (Login as CEO)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| H1 | View pipeline | Go to Sales Pipeline page | Funnel bar at top. 6 metric cards: Total Leads, This Week, Avg Score, Emails Sent, Stale Leads, Won. Lead table below | | |
| H2 | Filter by stage | Click a stage in the funnel bar (e.g., "Contacted") | Only leads in that stage shown in the table | | |
| H3 | View lead detail | Click any lead in the table | Detail card expands: name, email, company, role, score, stage, follow-up count, deal value | | |
| H4 | Run agent on lead | Click "Run Agent" button next to a lead | Button shows "Running...", then refreshes. Lead's score and stage may update | | |
| H5 | Demo form creates lead | Open https://agenticorg.ai in incognito. Fill demo request form. Submit | Go back to Sales Pipeline — new lead appears with stage "contacted" and a score | | |

### SECTION I: Email & Sales Agent

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| I1 | Demo request triggers email | Submit demo form with a REAL email address you can check | Within 2 minutes, receive email at that address. Subject is personalized by role. From: sanjeev@agenticorg.ai | | |
| I2 | Email content — CFO role | Submit demo form with role = "CFO" | Email subject contains "₹69,800" or AP-related pitch. Body mentions invoice processing, GSTIN, 3-way match | | |
| I3 | Email content — COO role | Submit demo form with role = "COO" | Email subject contains "88% tickets" or ops-related pitch. Body mentions support triage, P1 war rooms | | |
| I4 | Calendar link works | Click the calendar booking link in the email | Opens Google Calendar appointment page (https://calendar.app.google/p6P4DpRn85yxHua99) | | |
| I5 | Playground link works | Click the playground link in the email | Opens https://agenticorg.ai/playground | | |
| I6 | Signature correct | Check email signature | "Sanjeev Kumar, Founder, AgenticOrg" (NOT "Aarav" or "AI Sales Agent") | | |
| I7 | No "via gmail" | Check email sender details | From: sanjeev@agenticorg.ai. Should NOT show "sent via gmail.com" (may take 48h for SPF/DKIM to fully propagate) | | |

### SECTION J: Ads Landing Pages

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| J1 | Invoice processing page | Open /solutions/ai-invoice-processing | Hero: "Stop Losing ₹69,800/Month". Metric: "11 sec". Inline demo form on right. 6 features listed. Testimonial at bottom | | |
| J2 | Bank reconciliation page | Open /solutions/automated-bank-reconciliation | Hero: "99.7% Auto-Match Rate". Metric: "99.7%". Same structure as J1 | | |
| J3 | Payroll automation page | Open /solutions/payroll-automation | Hero: "Zero Payroll Errors". Metric: "0". Same structure | | |
| J4 | Ads form submission | Fill demo form on any ads page. Submit | "You're in!" confirmation. Playground link shown. Lead created in pipeline | | |

### SECTION K: SEO & Technical

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| K1 | Page title | Check browser tab on landing page | "AgenticOrg — AI Virtual Employees for Enterprise | Create & Deploy AI Agents" | | |
| K2 | Sitemap | Open https://agenticorg.ai/sitemap.xml | 12 URLs listed including blog posts | | |
| K3 | robots.txt | Open https://agenticorg.ai/robots.txt | Allows: /, /pricing, /playground, /evals, /blog. Disallows: /dashboard/, /api/ | | |
| K4 | llms.txt | Open https://agenticorg.ai/llms.txt | Product summary for AI crawlers. Mentions "Virtual Employee System" | | |
| K5 | JSON-LD schemas | View page source → search for "application/ld+json" | 7 schema blocks: Organization, SoftwareApplication, WebSite, FAQPage, BreadcrumbList, Product, SoftwareCompany | | |
| K6 | Accessibility — skip link | On landing page, press Tab key | "Skip to main content" link appears at top-left | | |
| K7 | Accessibility — mobile menu | On mobile, open menu, press Escape | Menu should close | | |

---

## Bug Reporting Template

If any test fails, log it with:

```
Test ID: [e.g., B1]
Summary: [one line]
Steps to Reproduce:
  1. ...
  2. ...
  3. ...
Expected: [what should happen]
Actual: [what actually happened]
Screenshot: [attach]
Browser: [Chrome/Firefox/Safari + version]
Device: [Desktop/Mobile + OS]
Severity: [Critical/High/Medium/Low]
```

---

## Sign-Off

| Tester | Date | Sections Tested | Pass/Fail Summary | Signature |
|--------|------|-----------------|-------------------|-----------|
| | | | | |
| | | | | |
