# AgenticOrg — Manual QA Test Plan

**Version**: 3.2.0
**Date**: 2026-04-02
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
| C9 | Connectors | Click "Connectors" | List of 1000+ connectors with status (54 native + Composio) | | |
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

---

## v3.2.0 Test Cases — Tier 1: Marketing Automation

### SECTION O: Web Push Notifications

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| O1 | Push permission request | Login as CEO. Navigate to Settings or bell icon. Enable push notifications | Browser shows native push permission dialog. After "Allow", subscription is created via POST /push/subscribe | | |
| O2 | Receive push notification | Trigger a HITL approval (e.g., run an agent with low confidence). Wait for push | Browser notification appears with agent name, action summary, and approve/reject buttons | | |
| O3 | One-tap approve via push | Click "Approve" on the push notification | HITL item is approved. Notification dismisses. Dashboard shows "Approved" status on the item | | |
| O4 | One-tap reject via push | Trigger another HITL item. Click "Reject" on the push notification | HITL item is rejected. Audit log shows rejection with "push_notification" as the source | | |
| O5 | Notification bell dropdown | Click the bell icon in the dashboard header | Dropdown shows recent notifications with timestamps, agent names, and status badges | | |
| O6 | Push toggle off/on | Go to Settings. Toggle push notifications OFF. Trigger a HITL item | No push notification received. Toggle back ON — next HITL item sends a push | | |
| O7 | VAPID key endpoint | Call GET /push/vapid-key | Returns a valid VAPID public key string | | |
| O8 | Test push endpoint | Call POST /push/test with a valid subscription | Test notification is received in the browser | | |

### SECTION P: A/B Testing

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| P1 | Create A/B campaign | Login as CMO. Navigate to Workflows. Create new workflow from `ab_test_campaign` template. Define variant A and variant B (different subject lines) | Workflow created with two variants. Status: "running" | | |
| P2 | Auto-winner selection | Wait for test period to complete (or simulate). Check workflow status | System automatically selects winner based on open rate or CTR. Winner variant is marked | | |
| P3 | CMO override | Before auto-send, navigate to Approvals. Find the A/B test approval item. Click "Override" and select the other variant | Override is accepted. The CMO-selected variant is now the winner. Audit log records the override | | |
| P4 | Send winner to remaining | After winner is selected (auto or override), confirm the send step | Winner variant is sent to the remaining audience (those not in the test group). Email metrics update | | |
| P5 | A/B test metrics | Navigate to the campaign detail page | Shows: Variant A open rate, Variant B open rate, winner, sample size, confidence level | | |

### SECTION Q: Email Drip Sequences

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| Q1 | Create drip sequence | Login as CMO. Navigate to Workflows. Create from `email_drip_sequence` template. Define 3 steps: Welcome (immediate), Follow-up (2 days), Last chance (5 days) | Workflow created with 3 email steps and time delays. Status: active | | |
| Q2 | Behavior trigger on open | Enroll a test lead. Simulate email open (via webhook). Check next step trigger | After open event, the next drip step is triggered based on the "on_open" condition | | |
| Q3 | Time delay works | Enroll a test lead. Check the second step | Second step is scheduled for 2 days after enrollment. Status shows "waiting" with countdown | | |
| Q4 | Re-engage non-openers | After the initial email, a lead does not open for 24 hours | Re-engagement email variant is automatically sent. Audit log shows "re-engage_non_opener" action | | |
| Q5 | Rescore after drip | A lead completes the full drip sequence (3 steps). Check lead score | Lead score is updated based on engagement (opens, clicks). Score change is visible in CRM Intelligence | | |
| Q6 | Wait-for-event step | In the lead_nurture workflow, check the wait_for_event step | Workflow pauses at the wait step. When the target event (email opened/clicked) occurs, workflow resumes | | |

### SECTION R: ABM Dashboard

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| R1 | Access ABM dashboard | Login as CMO. Navigate to /dashboard/abm | ABM dashboard loads with target accounts table, intent heatmap, and summary stats | | |
| R2 | Upload CSV of target accounts | Click "Upload Accounts" button. Select a CSV file with columns: company, domain, tier. Upload | Accounts are imported. Table shows new accounts with default intent score of 0 | | |
| R3 | View intent scores | Click on a target account row | Detail panel shows intent scores from Bombora (40% weight), G2 (30%), TrustRadius (30%), and blended score | | |
| R4 | Launch campaign from ABM | Select one or more accounts. Click "Launch Campaign" | Campaign creation form opens pre-populated with selected accounts. Submit creates a personalized outreach workflow | | |
| R5 | Filter by tier | Use the tier filter dropdown. Select "Tier 1" | Only Tier 1 accounts are shown in the table | | |
| R6 | Intent heatmap | Scroll to the intent heatmap section | Heatmap shows account intent levels (low/medium/high) color-coded across time periods | | |
| R7 | ABM API endpoints | Call GET /abm/accounts, POST /abm/accounts, GET /abm/dashboard | All endpoints return valid JSON responses with correct data | | |

### SECTION S: Email Webhooks

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| S1 | SendGrid open tracking | Send a test email via SendGrid connector. Simulate an open event by calling POST /webhooks/email/sendgrid with open payload | Event is stored. The email's open count increments. Event appears in the audit log | | |
| S2 | Mailchimp click tracking | Send a test email via Mailchimp connector. Simulate a click event by calling POST /webhooks/email/mailchimp with click payload | Event is stored. The email's click count increments. Drip sequence reacts if configured | | |
| S3 | MoEngage event tracking | Call POST /webhooks/email/moengage with a sample event payload | Event is stored and linked to the corresponding contact/lead | | |
| S4 | Verify events stored | After sending webhook events, query the events via API or check the audit log | All webhook events are persisted with: timestamp, event_type, email_id, recipient, metadata | | |
| S5 | Webhook auth validation | Call POST /webhooks/email/sendgrid without proper auth headers | Returns 401 or 403. Event is NOT stored | | |

---

## v3.1.0 Test Cases (Added 2026-04-02)

### SECTION G: CFO Dashboard (/dashboard/cfo)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| G1 | Page loads for CFO role | Login as CFO (cfo@agenticorg.local). Navigate to /dashboard/cfo | Page loads with title "Finance Dashboard". No console errors | | |
| G2 | Cash Runway card shows number + unit | On /dashboard/cfo, locate "Cash Runway" KPI card | Card displays a numeric value followed by "months" unit (e.g., "8.2 months") | | |
| G3 | Burn Rate card shows INR amount with trend | On /dashboard/cfo, locate "Burn Rate" KPI card | Card shows amount in INR format (e.g., "INR 12,50,000") with a trend arrow (up/down) indicating direction | | |
| G4 | DSO card shows days value | On /dashboard/cfo, locate "DSO" (Days Sales Outstanding) KPI card | Card shows a numeric value in days (e.g., "42 days") | | |
| G5 | DPO card shows days value | On /dashboard/cfo, locate "DPO" (Days Payable Outstanding) KPI card | Card shows a numeric value in days (e.g., "35 days") | | |
| G6 | AR Aging chart renders with 4 bars | On /dashboard/cfo, scroll to "AR Aging" chart | Bar chart renders with exactly 4 bars labeled: 0-30, 31-60, 61-90, 90+ days. Each bar has a visible value | | |
| G7 | AP Aging chart renders with 4 bars | On /dashboard/cfo, scroll to "AP Aging" chart | Bar chart renders with exactly 4 bars labeled: 0-30, 31-60, 61-90, 90+ days. Each bar has a visible value | | |
| G8 | P&L table shows all rows | On /dashboard/cfo, scroll to "P&L" (Profit & Loss) table | Table shows rows for: Revenue, COGS, Gross Margin, OPEX, Net Income. Each row has a numeric value | | |
| G9 | Bank Balances section | On /dashboard/cfo, scroll to "Bank Balances" section | Section lists account names (e.g., "HDFC Current A/c") with corresponding balance amounts in INR | | |
| G10 | Tax Calendar shows upcoming filings | On /dashboard/cfo, scroll to "Tax Calendar" section | Shows upcoming filing dates (e.g., GST, TDS) with status badges (e.g., "Due", "Filed", "Overdue") | | |
| G11 | Loading state | Navigate to /dashboard/cfo. Observe before data loads (throttle network to Slow 3G in DevTools) | Spinner or skeleton placeholders are visible while data is loading. No flash of empty content | | |
| G12 | Error state | Navigate to /dashboard/cfo. Disconnect network (DevTools > Network > Offline) after page load begins | Page shows a user-friendly error message (e.g., "Failed to load data"). No white screen or crash | | |
| G13 | Empty state — new tenant | Login as CFO for a newly created tenant with no financial data | Page shows "No data yet" or similar empty state message. No crash or broken layout | | |
| G14 | CMO role cannot access CFO dashboard | Login as CMO (cmo@agenticorg.local). Navigate to /dashboard/cfo | User is redirected away or sees a 403 Forbidden message. CFO data is NOT displayed | | |
| G15 | Responsive layout on mobile | Navigate to /dashboard/cfo. Resize browser to 375px width (or use DevTools mobile emulation) | KPI cards stack vertically in a single column. Charts resize to fit viewport. No horizontal scrollbar | | |

### SECTION H: CMO Dashboard (/dashboard/cmo)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| H1 | Page loads for CMO role | Login as CMO (cmo@agenticorg.local). Navigate to /dashboard/cmo | Page loads with title "Marketing Dashboard". No console errors | | |
| H2 | CAC card shows currency value | On /dashboard/cmo, locate "CAC" (Customer Acquisition Cost) KPI card | Card displays a currency amount (e.g., "INR 2,450") | | |
| H3 | MQLs card shows count | On /dashboard/cmo, locate "MQLs" (Marketing Qualified Leads) KPI card | Card shows a numeric count (e.g., "342") | | |
| H4 | SQLs card shows count | On /dashboard/cmo, locate "SQLs" (Sales Qualified Leads) KPI card | Card shows a numeric count (e.g., "87") | | |
| H5 | Pipeline card shows value | On /dashboard/cmo, locate "Pipeline" KPI card | Card shows a currency value representing pipeline total (e.g., "INR 1.2 Cr") | | |
| H6 | ROAS chart renders | On /dashboard/cmo, scroll to "ROAS" (Return on Ad Spend) chart | Chart renders with labeled axes. Shows ROAS data by channel or time period. Values are visible | | |
| H7 | Email Performance section | On /dashboard/cmo, scroll to "Email Performance" section | Shows email metrics: open rate, click rate, bounce rate, unsubscribes. Each with numeric values | | |
| H8 | Social Engagement section | On /dashboard/cmo, scroll to "Social Engagement" section | Shows engagement metrics across social platforms (e.g., likes, shares, comments, followers) | | |
| H9 | Website Traffic section | On /dashboard/cmo, scroll to "Website Traffic" section | Shows traffic metrics: sessions, page views, bounce rate, avg. session duration | | |
| H10 | Top Content section | On /dashboard/cmo, scroll to "Top Content" section | Lists top-performing content pieces with titles, views, and engagement scores | | |
| H11 | Brand Sentiment section | On /dashboard/cmo, scroll to "Brand Sentiment" section | Shows sentiment indicator (positive/neutral/negative) with a score or chart | | |
| H12 | Loading state | Navigate to /dashboard/cmo. Observe before data loads (throttle network to Slow 3G in DevTools) | Spinner or skeleton placeholders are visible while data is loading. No flash of empty content | | |
| H13 | Error state | Navigate to /dashboard/cmo. Disconnect network (DevTools > Network > Offline) after page load begins | Page shows a user-friendly error message. No white screen or crash | | |
| H14 | Empty state — new tenant | Login as CMO for a newly created tenant with no marketing data | Page shows "No data yet" or similar empty state message. No crash or broken layout | | |
| H15 | Responsive layout on mobile | Navigate to /dashboard/cmo. Resize browser to 375px width | KPI cards stack vertically. Charts resize to fit viewport. No horizontal scrollbar | | |

### SECTION I: NL Query Interface

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| I1 | Cmd+K opens search bar (Mac) | On any dashboard page (Mac), press Cmd+K | Search bar / command palette opens with a text input field focused and ready for typing | | |
| I2 | Ctrl+K opens search bar (Windows) | On any dashboard page (Windows), press Ctrl+K | Search bar / command palette opens with a text input field focused and ready for typing | | |
| I3 | Finance domain query | Open search bar. Type "What's my cash position?" and press Enter | Response appears showing a finance-domain answer. Agent name is displayed (e.g., "FPA Agent"). Response contains cash/balance data | | |
| I4 | Marketing domain query | Open search bar. Type "How did Google Ads perform?" and press Enter | Response appears showing a marketing-domain answer. Agent name is displayed (e.g., "Paid Ads Agent"). Response contains ad performance data | | |
| I5 | Empty query — no submit | Open search bar. Leave input empty and press Enter | Nothing is submitted. No API call is made. Search bar remains open or shows a hint to type a query | | |
| I6 | Long query (>1000 chars) | Open search bar. Paste a query exceeding 1000 characters and press Enter | Application does not crash. Query is either truncated gracefully or handled with an appropriate message | | |
| I7 | Special characters / XSS | Open search bar. Type `<script>alert('xss')</script>` and press Enter. Also try `'; DROP TABLE agents; --` | Input is sanitized. No script execution. No XSS alert. Query is treated as plain text. No SQL injection | | |
| I8 | "Open Chat" button | After receiving a query response, click the "Open Chat" button | A slide-out chat panel opens on the right side of the screen | | |
| I9 | Chat panel — multi-turn conversation | In the chat panel, send multiple messages in sequence | Each response shows agent attribution (agent name) and a confidence score. Conversation history is preserved | | |
| I10 | Chat panel — close button | In the open chat panel, click the X (close) button | Chat panel closes. Main dashboard content is fully visible again | | |
| I11 | Chat panel — Enter key sends message | In the chat panel, type a message and press Enter | Message is sent. Response appears below. No need to click a Send button | | |
| I12 | Escape key closes search dropdown | Open search bar with Ctrl+K / Cmd+K. Press Escape | Search bar / dropdown closes. Focus returns to the main page | | |

### SECTION J: Company Switcher (Multi-Company)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| J1 | Header shows company name | Login as any user. Look at the page header / top bar | Current company name is displayed (e.g., "Acme Corp"). If no company, shows "No company" or similar placeholder | | |
| J2 | Dropdown lists all companies | Click the company name in the header to open the dropdown | Dropdown shows all companies associated with the current tenant. Each listed with its name | | |
| J3 | Switch company — data refreshes | Select a different company from the dropdown | Page data refreshes to show KPIs and agents for the selected company. URL or context updates accordingly | | |
| J4 | Single company — no dropdown | Login as a user whose tenant has only one company | Company name is shown in the header but there is no dropdown arrow or switcher. Just a static label | | |
| J5 | Create new company via API | Send POST /companies with a valid company name via API (e.g., curl or Postman). Then refresh the dashboard | New company appears in the company switcher dropdown | | |
| J6 | Company ID persists after reload | Switch to a specific company. Reload the page (F5) | After reload, the same company is still selected. Check localStorage for persisted company_id | | |

### SECTION K: Report Scheduler (/dashboard/report-schedules)

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| K1 | Page loads with empty state | Navigate to /dashboard/report-schedules (no schedules created yet) | Page shows "No scheduled reports yet" or equivalent empty state message | | |
| K2 | "+ New Schedule" opens form | Click the "+ New Schedule" button | A create form or modal opens with fields for configuring a new report schedule | | |
| K3 | Report type dropdown | In the create form, click the report type dropdown | All 6 report types are listed and selectable | | |
| K4 | Schedule frequency presets | In the create form, click the schedule frequency dropdown | Options include: Daily, Weekly, Monthly presets. Each is selectable | | |
| K5 | Format selection | In the create form, select the output format | Options include: PDF, Excel, Both. Each is selectable | | |
| K6 | Email recipient field | In the create form, enter an email address in the recipient field | Email is accepted. Validation shows error for invalid email format | | |
| K7 | Slack channel field | In the create form, enter a Slack channel name (e.g., #finance-reports) | Channel name is accepted in the field | | |
| K8 | WhatsApp number field | In the create form, enter a WhatsApp number (e.g., +91-9876543210) | Phone number is accepted in the field | | |
| K9 | Create schedule — appears in list | Fill all required fields and click Create / Save | Schedule is created successfully. It appears in the schedule list with correct report type, frequency, format, and recipients | | |
| K10 | Toggle active/inactive | In the schedule list, toggle the active/inactive switch for a schedule | Badge changes between "Active" and "Inactive". Schedule status is updated | | |
| K11 | "Run Now" triggers generation | Click the "Run Now" button next to a schedule | Button shows loading state. Report generation is triggered immediately. Success confirmation shown | | |
| K12 | Delete schedule | Click the delete button next to a schedule | Confirmation prompt appears. After confirming, schedule is removed from the list | | |
| K13 | Multiple schedules display | Create 3+ schedules with different configurations | All schedules are listed with correct details (type, frequency, format, recipients, status) | | |
| K14 | Responsive layout | Navigate to /dashboard/report-schedules on a 375px viewport | Form fields stack vertically. Schedule list adapts to narrow width. No horizontal scrollbar | | |

### SECTION L: Updated Landing Page

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| L1 | Hero stats match product-facts endpoint | Open https://agenticorg.ai. Note the hero `agents`, `connectors`, `tools` numbers. Then `curl https://app.agenticorg.ai/api/v1/product-facts`. | Hero stats equal `agent_count` / `connector_count` / `tool_count` from the endpoint. Hardcoded numbers on the page are a drift bug — open a fix. | | |
| L2 | Role cards show live domain agent counts | Scroll to the role/domain cards section | Each domain card's count equals the number of registered agents for that domain in `GET /api/v1/agents?domain=<d>`. | | |
| L3 | CA Firm case study section visible | Scroll down the landing page to the section before the Final CTA | A CA Firm case study section is visible with heading, summary, and call-to-action link | | |
| L4 | Case study link works | Click "Read the full case study" link in the CA Firm section | Navigates to /blog/ca-firm-ai-agent-end-to-end. Blog post loads with full content | | |
| L5 | Blog nav link works | Click "Blog" in the top navigation bar | Navigates to /blog. Page shows 8+ blog posts listed with titles, dates, and category badges | | |
| L6 | New blog posts load correctly | On /blog, click each of the 4 newest blog posts | Each post loads fully with title, content, images (if any), and proper formatting. No 404 errors | | |
| L7 | Pricing page mentions Composio + native | Navigate to /pricing | Pricing page mentions Composio integrations alongside the native connector count. The native-connector number must match `connector_count` from `/product-facts`. | | |
| L8 | Meta description stays factual | View page source of landing page. Search for meta description tag | Meta description contains the product's current agent count (phrased as "pre-built agents" plus a number that agrees with `/product-facts`). No hardcoded "50+" style marketing placeholders. | | |

### SECTION M: SEO & Indexing

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| M1 | Sitemap includes new blog URLs | Open https://agenticorg.ai/sitemap.xml | Sitemap contains URLs for all 4 new blog posts. Each URL has a valid <lastmod> date | | |
| M2 | llms.txt counts match product-facts | Open https://agenticorg.ai/llms.txt and `curl https://app.agenticorg.ai/api/v1/product-facts` | Every count referenced in `llms.txt` (agents, connectors, tools) matches the `/product-facts` endpoint. `scripts/generate_llms_txt.py` should be the only writer — no hand-edited numbers. | | |
| M3 | llms-full.txt counts match product-facts | Open https://agenticorg.ai/llms-full.txt and compare to `/product-facts` | Same rule as M2 for the full variant. | | |
| M4 | robots.txt allows /blog/* | Open https://agenticorg.ai/robots.txt | robots.txt contains Allow rule for /blog/* or does not disallow /blog/ paths | | |
| M5 | New blog posts have proper meta tags | Open each of the 4 new blog posts. View page source | Each post has a unique <title> tag and a <meta name="description"> tag with relevant content | | |

### SECTION N: Cross-Feature Integration

| # | Test Case | Steps | Expected Result | Pass/Fail | Notes |
|---|-----------|-------|-----------------|-----------|-------|
| N1 | CFO report schedule end-to-end | Login as CFO. Navigate to /dashboard/report-schedules. Create a new schedule (e.g., Monthly P&L, PDF, email). Verify it appears in the list. Click "Run Now" | Schedule is created and listed. "Run Now" triggers report generation. Success confirmation appears. Report is generated (PDF) | | |
| N2 | CMO approves content via HITL | Login as CMO. Navigate to Approvals page. Find a pending content approval from a marketing agent. Approve it | Content approval status changes to "Approved". Agent execution continues with the approved content | | |
| N3 | NL query to chat from CFO dashboard | Login as CFO. Navigate to /dashboard/cfo. Press Ctrl+K. Type "What's my cash position?". View the response. Click "Open Chat". Send a follow-up message (e.g., "Break it down by account") | Finance agent responds to the initial query with cash data. Chat panel opens. Follow-up message receives a contextual multi-turn response with agent attribution and confidence score | | |
| N4 | Company switch updates all contexts | Login as CEO. Switch company via the header dropdown. Navigate to /dashboard/cfo. Then open NL query (Ctrl+K) and ask a question | CFO dashboard KPIs update to reflect the switched company's data. NL query response is contextualized to the new company | | |
| N5 | New company — empty state everywhere | Login as CEO. Create a new company via POST /companies API. Switch to the new company in the dropdown. Visit /dashboard/cfo, /dashboard/cmo, and /dashboard/report-schedules | CFO dashboard shows empty state ("No data yet"). CMO dashboard shows empty state. Report schedules page shows "No scheduled reports yet". No crashes or broken layouts | | |
