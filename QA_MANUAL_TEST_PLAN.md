# AgenticOrg — End-to-End Manual Test Plan

**Version:** 2.2.0
**Last Updated:** 2026-04-01
**Production URL:** https://app.agenticorg.ai
**Total Test Cases:** 574 (508 existing + 66 new regression)

## Demo Credentials

| Role | Email | Password |
|------|-------|----------|
| CEO/Admin | ceo@agenticorg.local | ceo123! |
| CFO | cfo@agenticorg.local | cfo123! |
| CHRO | chro@agenticorg.local | chro123! |
| CMO | cmo@agenticorg.local | cmo123! |
| COO | coo@agenticorg.local | coo123! |
| Auditor | auditor@agenticorg.local | audit123! |

---

## Module 1: Landing Page & Public Pages

### TC-LP-001: Landing page loads without errors
**Steps:**
1. Open https://app.agenticorg.ai/ in Chrome
2. Wait for page to fully load
3. Open browser DevTools → Console tab

**Expected Result:** Page loads within 3 seconds. No JavaScript errors in console. Hero section visible with headline, CTA buttons ("Book a Demo", "Try the Playground").

---

### TC-LP-002: Landing page — all sections render
**Steps:**
1. Open landing page
2. Scroll down slowly through the entire page
3. Verify each section is visible:
   - Hero section with headline
   - Logo bar (Oracle, SAP, Salesforce, Slack, GSTN, Darwinbox, Stripe, HubSpot)
   - Pain Points stats (3 colored cards)
   - Role cards (CFO, CHRO, CMO, COO) with metrics
   - India Connectors section (GSTN, EPFO, Darwinbox, etc.)
   - ROI Calculator
   - Agent Activity Ticker
   - Agents In Action
   - Workflow Animation
   - Interactive Demo
   - Social Proof / Testimonials

**Expected Result:** All sections render correctly with no layout breaks. Images load. Animations play smoothly.

---

### TC-LP-003: Book a Demo modal — happy path
**Steps:**
1. Open landing page
2. Click "Book a Demo" button
3. Fill in: Name = "Test User", Email = "test@gmail.com", Company = "Test Corp", Role = "CFO", Phone = "9876543210"
4. Click Submit

**Expected Result:** Modal opens with form fields. After submit, success message appears: "We'll be in touch within 2 minutes." Modal can be closed.

---

### TC-LP-004: Book a Demo modal — empty required fields
**Steps:**
1. Open landing page
2. Click "Book a Demo"
3. Leave Name and Email empty
4. Click Submit

**Expected Result:** Form validation prevents submission. Required field errors shown for Name and Email.

---

### TC-LP-005: Book a Demo — duplicate email
**Steps:**
1. Submit a demo request with email "duplicate@gmail.com"
2. Submit another demo request with the same email "duplicate@gmail.com"

**Expected Result:** Both submissions succeed (no error). Second submission returns the same lead_id as the first (duplicate detection). No duplicate lead created in pipeline.

---

### TC-LP-006: Landing page — responsive design (mobile)
**Steps:**
1. Open landing page on mobile device (or Chrome DevTools → 375x812 iPhone viewport)
2. Scroll through entire page
3. Check for horizontal overflow, cut-off text, overlapping elements

**Expected Result:** Page is fully responsive. No horizontal scrollbar. All text readable. CTAs tappable. Role cards stack vertically on mobile.

---

### TC-LP-007: Landing page — SEO meta tags
**Steps:**
1. Open landing page
2. View page source (Ctrl+U)
3. Check for: title tag, meta description, og:title, og:description, og:image, twitter:card, JSON-LD scripts

**Expected Result:** Title = "AgenticOrg — AI Virtual Employees for Enterprise | Create & Deploy AI Agents". Meta description present. At least 6 JSON-LD schemas (Organization, SoftwareApplication, WebSite, FAQPage, BreadcrumbList, Product). No static canonical tag in HTML (each page sets its own).

---

### TC-LP-008: Pricing page loads correctly
**Steps:**
1. Navigate to https://app.agenticorg.ai/pricing
2. Verify 3 pricing tiers display: Free ($0), Pro ($499/mo), Enterprise (Custom)
3. Scroll to feature comparison table
4. Scroll to FAQ section

**Expected Result:** 3 tier cards render with correct pricing. Feature comparison table shows checkmarks/X marks. FAQ accordion expands/collapses on click. Free plan shows "35 agents, 20 connectors, 500 tasks/day".

---

### TC-LP-009: Evals page loads without auth
**Steps:**
1. Open a private/incognito browser window (no login)
2. Navigate to https://app.agenticorg.ai/evals
3. Check page loads with evaluation data

**Expected Result:** Evals page loads without requiring login. Platform summary shows 4 metric cards (Auto-completion Rate, HITL Rate, Confidence, Uptime). Domain scores display (Finance, HR, Marketing, Ops). Per-agent table with scores and grades visible.

---

### TC-LP-010: Evals page — domain filter
**Steps:**
1. Open Evals page
2. Click "Finance" filter button
3. Verify only finance agents shown in table
4. Click "All" to reset

**Expected Result:** Table filters to show only finance domain agents. Count matches the Finance domain card agent count. "All" button resets to full list.

---

### TC-LP-011: Evals page — sort by column
**Steps:**
1. Open Evals page
2. Click "Composite" column header
3. Verify table sorts descending by composite score
4. Click again to toggle ascending

**Expected Result:** Table sorts correctly. Clicking same column toggles sort direction. Sort indicator visible on active column.

---

### TC-LP-012: Evals page — percentage display
**Steps:**
1. Open Evals page
2. Check all score values in table

**Expected Result:** All scores displayed as percentages (e.g., 87%, not 0.87). Color coding: green for >=90%, yellow for >=80%, red for <80%. Grade badges: A+/A (green), B+ (lime), B (yellow), C (orange), F (red).

---

### TC-LP-013: Playground page — run a use case
**Steps:**
1. Navigate to https://app.agenticorg.ai/playground
2. Select "Process Invoice" use case
3. Click Run
4. Watch the live execution trace

**Expected Result:** Execution trace renders in terminal-like display with color-coded lines (blue for agent startup, amber for LLM calls, green for results, red for HITL). After completion: status, confidence %, latency in ms shown. Agent output and reasoning trace displayed.

---

### TC-LP-014: Playground page — all 8 use cases
**Steps:**
1. Open Playground
2. Run each of the 8 use cases one by one:
   - Process Invoice
   - Reconcile Bank Transactions
   - Screen Resume
   - Compute Payroll
   - Score Lead
   - Analyze Brand Sentiment
   - Classify Support Ticket
   - Respond to P1 Incident

**Expected Result:** All 8 use cases execute successfully without errors. Each returns a result with confidence > 0 and latency < 30 seconds.

---

### TC-LP-015: Playground — no login required
**Steps:**
1. Open incognito browser
2. Navigate to /playground
3. Run any use case

**Expected Result:** Page loads and use case executes without requiring login. Demo token fetched automatically.

---

### TC-LP-016: Blog and Resources pages
**Steps:**
1. Navigate to /blog
2. Verify blog listing page loads
3. Click on a blog post
4. Navigate to /resources
5. Verify resources listing page loads

**Expected Result:** Blog and resources pages load with content. Individual blog post pages render correctly. Back navigation works.

---

### TC-LP-017: Solutions pages (Google Ads landing)
**Steps:**
1. Navigate to /solutions/ai-invoice-processing
2. Navigate to /solutions/automated-bank-reconciliation
3. Navigate to /solutions/payroll-automation

**Expected Result:** Each solution page loads with relevant content. CTA buttons present. SEO meta tags set per page.

---

### TC-LP-018: robots.txt and sitemap.xml
**Steps:**
1. Open https://app.agenticorg.ai/robots.txt
2. Verify crawlers allowed (Googlebot, Bingbot, ClaudeBot, OAI-SearchBot, etc.)
3. Open https://app.agenticorg.ai/sitemap.xml
4. Count URLs

**Expected Result:** robots.txt allows all major crawlers. Sitemap has 39 URLs covering all public pages (home, pricing, evals, playground, blog posts, resources, solutions).

---

## Module 2: Authentication

### TC-AUTH-001: Login with demo CEO credentials
**Steps:**
1. Navigate to /login
2. Click "Try the demo instead →"
3. Click "CEO/Admin" demo credential button
4. Verify email and password auto-fill
5. Click "Sign in with email"

**Expected Result:** Email field fills with "ceo@agenticorg.local", password with "ceo123!". After clicking sign in, redirects to /dashboard. User sees full dashboard with all agents across all domains.

---

### TC-AUTH-002: Login with all 6 demo roles
**Steps:**
1. For each role (CEO, CFO, CHRO, CMO, COO, Auditor):
   a. Navigate to /login
   b. Click the demo credential button for that role
   c. Sign in
   d. Note what's visible on the dashboard
   e. Logout

**Expected Result:**
- CEO: Sees all agents, all menu items, can create agents
- CFO: Sees only Finance domain agents
- CHRO: Sees only HR domain agents
- CMO: Sees only Marketing domain agents
- COO: Sees only Ops domain agents
- Auditor: Sees Audit Log only, read-only access, cannot modify anything

---

### TC-AUTH-003: Login with wrong password
**Steps:**
1. Navigate to /login
2. Enter email: ceo@agenticorg.local
3. Enter password: wrongpassword
4. Click Sign in

**Expected Result:** Error message "Invalid email or password" displayed. No redirect. User stays on login page.

---

### TC-AUTH-004: Login with non-existent email
**Steps:**
1. Navigate to /login
2. Enter email: nonexistent@example.com
3. Enter password: anything
4. Click Sign in

**Expected Result:** Error message "Invalid email or password" displayed. No information leakage about whether email exists.

---

### TC-AUTH-005: Signup — create new organization
**Steps:**
1. Navigate to /signup
2. Fill in: Organization = "QA Test Corp", Name = "QA Tester", Email = "qa-test-TIMESTAMP@gmail.com" (use unique email), Password = "TestPass123!", Confirm = "TestPass123!"
3. Click Create Account

**Expected Result:** Account created. Redirects to /onboarding page. User is logged in with role "admin". Organization created with slug derived from org name.

---

### TC-AUTH-006: Signup — duplicate email
**Steps:**
1. Create account with email "qa-dup@gmail.com"
2. Logout
3. Create another account with the same email "qa-dup@gmail.com"

**Expected Result:** Second signup attempt fails with error message indicating email already exists.

---

### TC-AUTH-007: Signup — password mismatch
**Steps:**
1. Navigate to /signup
2. Enter Password = "Pass123!"
3. Enter Confirm Password = "DifferentPass!"
4. Click Create Account

**Expected Result:** Client-side validation error: "Passwords do not match". Form not submitted.

---

### TC-AUTH-008: Signup — empty required fields
**Steps:**
1. Navigate to /signup
2. Leave all fields empty
3. Click Create Account

**Expected Result:** Validation errors shown for Organization Name, Name, Email, Password. Form not submitted.

---

### TC-AUTH-009: Google OAuth — button visibility
**Steps:**
1. Navigate to /login
2. Check if "Sign in with Google" button is visible

**Expected Result:** Google sign-in button visible if Google Client ID is configured. If not configured, button is hidden (no error).

---

### TC-AUTH-010: Logout
**Steps:**
1. Login as CEO
2. Click Logout (or navigate to logout action)
3. Try to access /dashboard directly

**Expected Result:** User logged out. Token cleared from localStorage. Redirected to /login when trying to access protected routes.

---

### TC-AUTH-011: Token expiry
**Steps:**
1. Login as CEO
2. Note the JWT token from localStorage
3. Wait for token to expire (default: 60 minutes) OR manually delete token from localStorage
4. Try to navigate to /dashboard/agents

**Expected Result:** Redirected to /login page. No access to protected routes without valid token.

---

### TC-AUTH-012: Protected route — direct URL access without login
**Steps:**
1. Open incognito browser (no login)
2. Navigate directly to:
   - /dashboard
   - /dashboard/agents
   - /dashboard/agents/new
   - /dashboard/approvals
   - /dashboard/sales
   - /dashboard/settings

**Expected Result:** All protected routes redirect to /login page. No dashboard content visible.

---

### TC-AUTH-013: Role-based route access — Auditor cannot access agent creation
**Steps:**
1. Login as Auditor (auditor@agenticorg.local / audit123!)
2. Try to navigate to /dashboard/agents/new
3. Try to navigate to /dashboard/settings
4. Try to navigate to /dashboard/connectors

**Expected Result:** Auditor cannot access agent creation, settings, or connectors pages. Redirected or shown "Access Denied". Auditor can only access /dashboard/audit.

---

### TC-AUTH-014: Role-based route access — CFO cannot create agents
**Steps:**
1. Login as CFO
2. Try to navigate to /dashboard/agents/new

**Expected Result:** CFO cannot access agent creation page. Only admin role can create agents.

---

### TC-AUTH-015: Rate limiting — brute force login
**Steps:**
1. Attempt to login 11 times with wrong password using the same IP
2. On the 11th attempt, try with the correct password

**Expected Result:** After 10 failed attempts within 60 seconds, all requests from that IP are blocked for 15 minutes (HTTP 429). Even correct credentials are rejected during block period.

---

## Module 3: Dashboard

### TC-DASH-001: Dashboard loads with metrics
**Steps:**
1. Login as CEO
2. Navigate to /dashboard
3. Check the 4 metric cards at the top

**Expected Result:** Dashboard loads with: Total Agents count, Active Agents count (green), Pending Approvals count (red if > 0), Shadow Agents count (yellow). Numbers match the actual agent fleet.

---

### TC-DASH-002: Dashboard — charts render
**Steps:**
1. Login as CEO
2. On dashboard, verify:
   - Agent Status pie chart (active/shadow/paused distribution)
   - Domain Distribution bar chart (Finance/HR/Marketing/Ops counts)
   - Agent Confidence Floors horizontal bar chart

**Expected Result:** All 3 charts render with correct data. Pie chart segments clickable/hoverable. Bar chart shows all domains. Confidence chart color-coded by domain.

---

### TC-DASH-003: Dashboard — recent activity feed
**Steps:**
1. Login as CEO
2. Scroll to Recent Activity section
3. Check entries

**Expected Result:** Last 10 audit entries displayed with: timestamp, event type, outcome badge (success=green, failure=red, pending=yellow). Entries sorted newest first.

---

### TC-DASH-004: Dashboard — pending approvals summary
**Steps:**
1. Login as CEO
2. Scroll to Pending Approvals section
3. Click on an approval item

**Expected Result:** Pending HITL items shown with priority badges. Clicking navigates to /dashboard/approvals page.

---

### TC-DASH-005: Dashboard — CFO sees only finance data
**Steps:**
1. Login as CFO
2. View dashboard charts and metrics

**Expected Result:** Agent counts reflect only finance domain agents. Domain distribution chart emphasizes finance. Activity feed shows only finance-related events.

---

## Module 4: Agent Fleet Management

### TC-AGT-001: Agent list — displays all agents
**Steps:**
1. Login as CEO
2. Navigate to /dashboard/agents
3. Check the stats row and agent grid

**Expected Result:** Stats row shows Total, Active, Shadow, Paused counts. Agent grid displays all agents as cards with: name, domain badge, status badge, confidence floor %, avatar/initial.

---

### TC-AGT-002: Agent list — filter by domain
**Steps:**
1. On Agents page, select "Finance" from domain dropdown
2. Verify only finance agents shown
3. Select "HR" — verify only HR agents
4. Select "All" — verify all agents return

**Expected Result:** Filter works correctly. Agent count updates to match filtered results. Cards show only agents of the selected domain.

---

### TC-AGT-003: Agent list — filter by status
**Steps:**
1. On Agents page, select "Active" from status dropdown
2. Verify only active agents shown
3. Select "Shadow" — verify only shadow agents
4. Select "Paused" — verify only paused agents

**Expected Result:** Status filter works correctly. Status badges on cards match the filter selection.

---

### TC-AGT-004: Agent list — search by name
**Steps:**
1. On Agents page, type "AP" in the search box
2. Verify agents with "AP" in name or type are shown
3. Clear search — all agents return

**Expected Result:** Search filters agent cards by name and agent_type. Case-insensitive matching. Clearing search restores full list.

---

### TC-AGT-005: Agent list — kill switch (pause active agent)
**Steps:**
1. On Agents page, find an active agent
2. Click the red Kill Switch button on the card
3. Confirm the action

**Expected Result:** Agent status changes from "active" to "paused". Card badge updates to "paused". Kill switch button disappears. Agent can be resumed later.

---

### TC-AGT-006: Agent detail — overview tab
**Steps:**
1. Click on any agent card to open detail page
2. Check the Overview tab content

**Expected Result:** Shows: Agent ID, domain, type, description, HITL condition, confidence floor, creation date, authorized tools list. Persona header shows employee name, designation, specialization (if available).

---

### TC-AGT-007: Agent detail — config tab
**Steps:**
1. Open any agent detail
2. Click Config tab

**Expected Result:** Shows: LLM model, max retries, retry backoff, HITL condition, confidence floor, authorized tools. Values match what was set during creation.

---

### TC-AGT-008: Agent detail — prompt tab
**Steps:**
1. Open any agent detail
2. Click Prompt tab

**Expected Result:** Shows system prompt text. If agent is active, prompt is locked (read-only) with message suggesting "clone to edit". Edit history shows previous prompt changes with timestamps, character count diffs, and change reasons.

---

### TC-AGT-009: Agent detail — promote shadow to active
**Steps:**
1. Find a shadow agent in the list
2. Open its detail page
3. Click "Promote" button

**Expected Result:** Agent status changes from "shadow" to "active". Promote button becomes disabled. Status badge updates. Audit log entry created.

---

### TC-AGT-010: Agent detail — rollback
**Steps:**
1. Open an active agent (version > 1.0.0 or previously promoted)
2. Click "Rollback" button

**Expected Result:** Agent reverts to previous version. Version number decrements. Status may change. Audit log entry created.

---

### TC-AGT-011: Agent detail — shadow tab
**Steps:**
1. Open a shadow agent's detail page
2. Click Shadow tab

**Expected Result:** Shows: sample progress bar (current/required), accuracy comparison chart (current vs floor), promotion readiness checklist. If samples insufficient, shows "Not ready for promotion".

---

### TC-AGT-012: Agent detail — cost tab
**Steps:**
1. Open any agent's detail page
2. Click Cost tab

**Expected Result:** Shows: monthly budget cap (if set), current month spend, utilization % bar chart. If no budget set, shows "No budget configured". Over-budget warning if spend >= cap.

---

### TC-AGT-013: Agent detail — prompt history
**Steps:**
1. Open an agent that has had prompt edits
2. Click Prompt tab
3. Scroll to edit history section

**Expected Result:** Shows chronological list of prompt changes with: editor info, timestamp, prompt before/after, change reason, character count diff (+/-).

---

## Module 5: Agent Creation Wizard

### TC-CRT-001: Full agent creation — happy path
**Steps:**
1. Login as CEO/Admin
2. Navigate to /dashboard/agents/new
3. **Step 1 (Persona):** Name = "Priya", Designation = "Senior AP Processor", Domain = "Finance"
4. Click Next
5. **Step 2 (Role):** Agent Type = "accounts_payable_processor", Specialization = "Domestic invoices, Mumbai region"
6. Add routing filter: key = "region", value = "Mumbai"
7. Click Next
8. **Step 3 (Prompt):** Select a template from dropdown OR write custom prompt (min 50 chars)
9. Click Next
10. **Step 4 (Behavior):** Confidence Floor = 0.85, HITL Condition = "invoice_amount > 500000", Max Retries = 3
11. Click Next
12. **Step 5 (Review):** Verify all settings in summary
13. Click "Create as Shadow"

**Expected Result:** Agent created successfully with status "shadow". Redirected to agent detail page. All fields match what was entered. Agent appears in agent list with shadow badge.

---

### TC-CRT-002: Agent creation — custom agent type
**Steps:**
1. Start agent creation wizard
2. In Step 2, check "Custom agent type"
3. Enter custom type: "custom_data_analyst"
4. Complete remaining steps with valid data
5. Create agent

**Expected Result:** Agent created with custom type "custom_data_analyst". Custom types fall back to BaseAgent for execution. Agent appears in list.

---

### TC-CRT-003: Agent creation — template variable substitution
**Steps:**
1. Start agent creation wizard
2. In Step 3, select a template that has {{variables}}
3. Verify variable input fields appear
4. Fill in all variable values
5. Check prompt preview shows substituted values

**Expected Result:** Template variables auto-detected from template text. Input fields generated for each {{variable}}. Preview shows the final prompt with all variables replaced.

---

### TC-CRT-004: Agent creation — step validation
**Steps:**
1. Start agent creation wizard
2. In Step 1, leave Employee Name empty
3. Click Next

**Expected Result:** Cannot proceed to Step 2 without required fields. Validation message shown for Employee Name and Domain.

---

### TC-CRT-005: Agent creation — cancel
**Steps:**
1. Start agent creation wizard
2. Fill in Step 1
3. Click Cancel

**Expected Result:** Wizard closes. No agent created. Redirected back to agent list.

---

### TC-CRT-006: Agent creation — back navigation
**Steps:**
1. Proceed to Step 3 of wizard
2. Click Back
3. Verify Step 2 data preserved
4. Click Back again
5. Verify Step 1 data preserved

**Expected Result:** All previously entered data retained when navigating back. No data loss between steps.

---

### TC-CRT-007: Agent creation — duplicate name+type
**Steps:**
1. Create agent with Name = "TestAgent", Type = "test_type", Domain = "finance"
2. Try to create another agent with same Name = "TestAgent", Type = "test_type"

**Expected Result:** Second creation fails with conflict error (unique constraint on tenant_id + agent_type + employee_name + version). Error message indicates duplicate.

---

## Module 6: Agent Execution

### TC-EXEC-001: Run agent — happy path
**Steps:**
1. Open an active agent's detail page
2. Go to Playground or use API: POST /api/v1/agents/{id}/run
3. Send task input: `{"inputs": {"text": "Process this invoice for ₹25,000 from Vendor ABC"}}`

**Expected Result:** Agent executes and returns: task_id, status (completed/hitl_required), output JSON, confidence score (0-1), reasoning_trace array, performance metrics (latency_ms, llm_tokens, llm_cost_usd).

---

### TC-EXEC-002: Run agent — HITL triggered (low confidence)
**Steps:**
1. Find or create an agent with confidence_floor = 0.95
2. Run agent with ambiguous input that produces low confidence
3. Check response

**Expected Result:** Response includes `hitl_request` object with: trigger_type = "confidence_below_floor", assignee_role, decision_options (approve/reject/defer). Status = "hitl_required". Approval item created in HITL queue.

---

### TC-EXEC-003: Run agent — LLM model fallback
**Steps:**
1. Create agent with llm_model = "claude-3-5-sonnet-20241022"
2. Verify no ANTHROPIC_API_KEY is set in production
3. Run the agent

**Expected Result:** Agent executes successfully using Gemini (default fallback), not Claude. No error about missing API key. Output is valid.

---

### TC-EXEC-004: Run agent — budget exceeded
**Steps:**
1. Create agent with cost_controls = {"monthly_cost_cap_usd": 0.01}
2. Run agent once (should succeed)
3. Run agent a second time

**Expected Result:** First run succeeds, cost tracked in AgentCostLedger. Second run returns error E1008 "budget_exceeded" with message indicating monthly cap reached. Agent may auto-pause.

---

### TC-EXEC-005: Run agent — confidence string handling
**Steps:**
1. Run an agent where LLM returns confidence as text (e.g., "high", "medium", "low")
2. Check response confidence value

**Expected Result:** String confidence mapped correctly: "high" → 0.95, "medium" → 0.75, "low" → 0.5. No crash or error.

---

### TC-EXEC-006: Run paused agent
**Steps:**
1. Pause an agent (kill switch)
2. Try to run the paused agent via API

**Expected Result:** Agent execution rejected with appropriate error. Paused agents should not execute tasks.

---

### TC-EXEC-007: Clone agent
**Steps:**
1. Open an active agent's detail page
2. Use API: POST /api/v1/agents/{id}/clone with body: `{"name": "Clone Test", "agent_type": "clone_test"}`

**Expected Result:** New agent created with all settings copied from original. Returns clone_id and parent_id. Clone starts in shadow status. Original agent unaffected.

---

### TC-EXEC-008: Agent budget endpoint
**Steps:**
1. Run an agent a few times
2. Call GET /api/v1/agents/{id}/budget

**Expected Result:** Returns: monthly_cap_usd, monthly_spent_usd, monthly_pct_used, monthly_tokens, monthly_tasks, daily_token_budget, warnings array. Numbers match actual usage.

---

## Module 7: Workflows

### TC-WF-001: Workflow list page
**Steps:**
1. Login as CEO
2. Navigate to /dashboard/workflows

**Expected Result:** Workflow list displays with cards showing: name, active/inactive badge, version, trigger type, created date. "Create Workflow" button visible.

---

### TC-WF-002: Create workflow
**Steps:**
1. Click "Create Workflow"
2. Fill in: Name = "Invoice Processing Pipeline", Version = "1.0.0", Domain = "finance"
3. Define steps (JSON definition)
4. Set trigger_type = "manual"
5. Submit

**Expected Result:** Workflow created. Appears in workflow list. Can be viewed by clicking on it.

---

### TC-WF-003: Run workflow manually
**Steps:**
1. Open a workflow
2. Click "Run Now"
3. Watch for execution progress

**Expected Result:** Workflow run created with status "running". Run ID returned. Steps execute sequentially. Status updates to "completed" or "failed" on finish.

---

### TC-WF-004: View workflow run details
**Steps:**
1. Trigger a workflow run
2. Navigate to /dashboard/workflows/{id}/runs/{runId}

**Expected Result:** Run detail page shows: status, started_at, steps_total, steps_completed, each step execution with input/output/status/latency. Context and result JSON visible.

---

### TC-WF-005: Workflow with HITL step
**Steps:**
1. Create workflow with an agent step that triggers HITL
2. Run the workflow
3. Check workflow status

**Expected Result:** Workflow pauses at the HITL step (status "running" but step pending). HITL item created in approval queue. After approval, workflow resumes.

---

## Module 8: Approval Queue (HITL)

### TC-HITL-001: View pending approvals
**Steps:**
1. Login as CEO
2. Navigate to /dashboard/approvals
3. Check "Pending" tab

**Expected Result:** Pending HITL items displayed with: priority badge, title, trigger type, assignee role, context details. Count shown in tab badge.

---

### TC-HITL-002: Approve an item
**Steps:**
1. Find a pending approval
2. Enter notes: "Approved after review"
3. Click "Approve"

**Expected Result:** Item status changes to "decided". Decision = "approve". Decision timestamp set. Item moves to "Decided" tab. Audit log entry created.

---

### TC-HITL-003: Reject an item
**Steps:**
1. Find a pending approval
2. Enter notes: "Rejected — data quality issue"
3. Click "Reject"

**Expected Result:** Item status changes to "decided". Decision = "reject". Notes saved. Item moves to "Decided" tab.

---

### TC-HITL-004: Filter by priority
**Steps:**
1. On Approvals page, select "Critical" from priority dropdown
2. Verify only critical items shown
3. Select "All" to reset

**Expected Result:** Filter works correctly. Only items matching selected priority displayed.

---

### TC-HITL-005: Decided tab shows history
**Steps:**
1. Approve or reject several items
2. Click "Decided" tab

**Expected Result:** All decided items shown with: decision (approve/reject), decision timestamp, notes, who decided. Items not editable (decision is final).

---

### TC-HITL-006: Role-based approval visibility — CFO
**Steps:**
1. Login as CFO
2. Navigate to /dashboard/approvals

**Expected Result:** CFO sees only HITL items assigned to finance domain. Items assigned to HR/Marketing/Ops not visible.

---

## Module 9: Connectors

### TC-CONN-001: Connector list page
**Steps:**
1. Login as CEO/Admin
2. Navigate to /dashboard/connectors

**Expected Result:** Connector grid displays all connectors with: name, category badge, status indicator. Stats row shows Total, Active, Unhealthy counts.

---

### TC-CONN-002: Filter by category
**Steps:**
1. On Connectors page, select "Finance" from category dropdown
2. Verify only finance connectors shown (Oracle, SAP, Tally, GSTN, etc.)
3. Try other categories: HR, Marketing, Ops, Comms

**Expected Result:** Each category filter shows only relevant connectors. Count updates accordingly.

---

### TC-CONN-003: Connector health check
**Steps:**
1. Click "Health Check" button on any connector
2. Wait for result

**Expected Result:** Health check modal/indicator shows: connector_id, name, status (healthy/unhealthy), health_check_at timestamp. Healthy connectors show green indicator, unhealthy show red.

---

### TC-CONN-004: Register new connector
**Steps:**
1. Click "Register Connector"
2. Fill in: Name = "Test API", Category = "ops", Base URL = "https://api.example.com", Auth Type = "api_key"
3. Submit

**Expected Result:** Connector registered and appears in list. Category badge shows "ops".

---

## Module 10: Prompt Templates

### TC-TPL-001: List prompt templates
**Steps:**
1. Login as CEO/Admin
2. Navigate to /dashboard/prompt-templates

**Expected Result:** Template list displays with: name, agent type, domain, built-in badge (for system templates), character count. Domain filter dropdown available.

---

### TC-TPL-002: View built-in template — read-only
**Steps:**
1. Click on a built-in template
2. Try to edit the template text

**Expected Result:** Template text is read-only (not editable). "Clone to Edit" button available. Edit/Delete buttons NOT shown for built-in templates.

---

### TC-TPL-003: Clone built-in template
**Steps:**
1. Click on a built-in template
2. Click "Clone to Edit"
3. Verify cloned template created

**Expected Result:** New template created with name "{original_name}_custom". Template text copied. New template is editable (not built-in).

---

### TC-TPL-004: Create custom template
**Steps:**
1. Click "Create Template"
2. Fill in: Name = "Custom Invoice Prompt", Agent Type = "accounts_payable_processor", Domain = "finance"
3. Enter template text with {{variables}}: "Process invoice from {{vendor_name}} for {{amount}}"
4. Click Create

**Expected Result:** Template created. Appears in list. Variables auto-detected: vendor_name, amount.

---

### TC-TPL-005: Edit custom template
**Steps:**
1. Click on a custom (non-built-in) template
2. Click Edit
3. Modify template text
4. Click "Save Changes"

**Expected Result:** Template text updated. Character count updates. Changes saved successfully.

---

### TC-TPL-006: Delete custom template
**Steps:**
1. Click on a custom template
2. Click Delete
3. Confirm deletion

**Expected Result:** Template removed from list (soft delete). No longer appears in template dropdown during agent creation.

---

### TC-TPL-007: Filter by domain
**Steps:**
1. Select "HR" from domain filter
2. Verify only HR templates shown

**Expected Result:** Filter works. Only templates with domain = "hr" displayed. Template count updates.

---

## Module 11: Sales Pipeline

### TC-SALES-001: Sales pipeline page loads
**Steps:**
1. Login as CEO/Admin
2. Navigate to /dashboard/sales

**Expected Result:** Page loads with: 6 metric cards (Total Leads, New This Week, Avg Score, Emails Sent, Stale Leads, Won), pipeline funnel bar chart, lead table. "Aarav (AI Sales Agent)" label visible.

---

### TC-SALES-002: Pipeline funnel visualization
**Steps:**
1. On Sales Pipeline page, check the funnel bar
2. Click on different stage segments

**Expected Result:** Funnel shows colored segments for each stage (New=slate, Contacted=blue, Qualified=indigo, etc.). Clicking a segment filters the lead table to that stage. Legend buttons below match.

---

### TC-SALES-003: View lead details
**Steps:**
1. Click on a lead row in the table
2. Check expanded detail view

**Expected Result:** Detail section shows: Email, Company, Role, Score, Stage, Follow-ups count, Created date, Deal Value. All fields populated.

---

### TC-SALES-004: Run Sales Agent on lead
**Steps:**
1. Find a lead in "new" or "contacted" stage
2. Click "Run Agent" button on that row
3. Wait for execution

**Expected Result:** Sales agent runs and returns: qualification output, next action recommendation, email content (if applicable). Lead score may update. Lead stage may advance.

---

### TC-SALES-005: Seed demo prospects
**Steps:**
1. Call API: POST /api/v1/sales/seed-prospects?auto_process=false
2. Check pipeline for new leads

**Expected Result:** 20 Indian enterprise prospects seeded. Returns: seeded count, skipped_duplicates. Second call skips duplicates (no double-seeding).

---

### TC-SALES-006: Import CSV leads
**Steps:**
1. Prepare CSV file with columns: name, email, company, role, phone
2. Call API: POST /api/v1/sales/import-csv with file upload
3. Check pipeline

**Expected Result:** Leads imported from CSV. Returns: imported count, skipped (duplicate) count, processed_by_agent count. Duplicate emails skipped.

---

### TC-SALES-007: Due follow-ups
**Steps:**
1. Call API: GET /api/v1/sales/pipeline/due-followups

**Expected Result:** Returns list of leads needing follow-up (next_followup_at <= now). Sorted by score (highest first).

---

### TC-SALES-008: Sales metrics
**Steps:**
1. Call API: GET /api/v1/sales/metrics

**Expected Result:** Returns: total_leads, new_this_week, funnel (stage counts), avg_score, emails_sent_this_week, stale_leads (contacted but no activity for 7+ days).

---

### TC-SALES-009: Lead score color coding
**Steps:**
1. View lead table with various scores

**Expected Result:** Score >= 70: green text. Score 40-69: amber text. Score < 40: slate text.

---

## Module 12: Audit Log

### TC-AUDIT-001: Audit log page loads
**Steps:**
1. Login as CEO or Auditor
2. Navigate to /dashboard/audit

**Expected Result:** Audit table shows entries with: Timestamp, Event Type (badge), Actor, Action, Outcome (success/failure/error badge). 50 entries per page. Pagination controls visible.

---

### TC-AUDIT-002: Filter by event type
**Steps:**
1. Enter "agent" in the event type filter
2. Verify filtered results

**Expected Result:** Only audit entries with event type containing "agent" displayed. Entries for other event types hidden.

---

### TC-AUDIT-003: Pagination
**Steps:**
1. If more than 50 audit entries exist, click "Next" page button
2. Verify page 2 loads with different entries
3. Click "Previous" to go back

**Expected Result:** Pagination works. Page 2 shows entries 51-100. Previous button returns to page 1. Current page number displayed.

---

### TC-AUDIT-004: Export as CSV
**Steps:**
1. Click "Download CSV" button
2. Open downloaded file

**Expected Result:** CSV file downloaded with columns: id, event_type, actor_type, action, outcome, created_at. All visible entries included.

---

### TC-AUDIT-005: Export evidence package (JSON)
**Steps:**
1. Click "Export Evidence Package"
2. Open downloaded JSON file

**Expected Result:** JSON file downloaded with all audit fields. Suitable for compliance review.

---

### TC-AUDIT-006: Auditor role — read-only access
**Steps:**
1. Login as Auditor
2. Navigate to /dashboard/audit
3. Try to access any write operation (edit agent, create workflow, etc.)

**Expected Result:** Auditor can view audit log with full history. Cannot access any create/edit/delete functionality on other pages. Menu shows only Audit Log option.

---

## Module 13: Compliance (DSAR)

### TC-COMP-001: DSAR access request
**Steps:**
1. Call API: POST /api/v1/dsar/access with body: `{"subject_email": "test@example.com"}`

**Expected Result:** Returns: request_id, type = "access", status = "processing", subject_email, created_at. Request logged for compliance.

---

### TC-COMP-002: DSAR erase request
**Steps:**
1. Call API: POST /api/v1/dsar/erase with body: `{"subject_email": "test@example.com"}`

**Expected Result:** Returns: request_id, type = "erase", status = "processing", deadline (30 days), deadline_days = 30.

---

### TC-COMP-003: DSAR export request
**Steps:**
1. Call API: POST /api/v1/dsar/export with body: `{"subject_email": "test@example.com"}`

**Expected Result:** Returns: request_id, type = "export", status = "processing", format = "json", estimated_records, estimated_size_mb.

---

### TC-COMP-004: SOC-2 evidence package
**Steps:**
1. Call API: GET /api/v1/compliance/evidence-package

**Expected Result:** Returns: package_id, generated_at, sections (access_controls, audit_logs, deployment_records, incident_history) each with event_count and status.

---

## Module 14: Organization Management

### TC-ORG-001: View organization profile
**Steps:**
1. Login as CEO
2. Call API: GET /api/v1/org/profile

**Expected Result:** Returns: id, name, slug, plan, data_region, settings (JSON), created_at. Values match the organization created during signup.

---

### TC-ORG-002: List organization members
**Steps:**
1. Login as CEO
2. Call API: GET /api/v1/org/members

**Expected Result:** Returns list of all members with: id, email, name, role, domain, status, created_at. Includes the CEO and all demo users.

---

### TC-ORG-003: Invite new member
**Steps:**
1. Login as CEO
2. Call API: POST /api/v1/org/invite with body: `{"email": "newmember@gmail.com", "name": "New Member", "role": "analyst", "domain": "finance"}`

**Expected Result:** Returns: status = "invited", user_id, email, invite_link. Invited user receives email with invite link (if email configured).

---

### TC-ORG-004: Accept invite
**Steps:**
1. Get invite token from step TC-ORG-003
2. Call API: POST /api/v1/org/accept-invite with body: `{"token": "{invite_token}", "password": "NewPass123!"}`

**Expected Result:** Returns: access_token, user object with role and domain matching the invite. User can now login with the new credentials.

---

### TC-ORG-005: Deactivate member
**Steps:**
1. Login as CEO
2. Call API: DELETE /api/v1/org/members/{user_id}

**Expected Result:** Returns: status = "deactivated". User can no longer login. User still appears in member list with inactive status.

---

### TC-ORG-006: Update onboarding progress
**Steps:**
1. After fresh signup, call API: PUT /api/v1/org/onboarding with body: `{"onboarding_step": "agents_created", "onboarding_complete": true}`

**Expected Result:** Returns: status = "updated", settings object with onboarding flags. User's onboarding_complete flag set to true.

---

## Module 15: Settings

### TC-SET-001: Settings page loads
**Steps:**
1. Login as CEO/Admin
2. Navigate to /dashboard/settings

**Expected Result:** Page shows Fleet Governance Limits section (Max Active Agents default 35, Max Shadow Agents, Max Replicas) and Compliance section (PII Masking toggle, Data Region dropdown, Audit Retention years).

---

### TC-SET-002: Update fleet limits
**Steps:**
1. Change Max Active Agents from 35 to 50
2. Click "Save Settings"

**Expected Result:** Success message shown. Settings saved. Refreshing page shows 50 as the new value.

---

### TC-SET-003: Toggle PII masking
**Steps:**
1. Toggle PII Masking from enabled to disabled
2. Save settings
3. Toggle back to enabled
4. Save again

**Expected Result:** Toggle state persists after save. Each save shows success message. Value correctly saved and retrieved.

---

### TC-SET-004: Change data region
**Steps:**
1. Change Data Region from "India" to "US"
2. Save settings

**Expected Result:** Data region updates. This affects where new data is stored. Value persists after page refresh.

---

### TC-SET-005: Non-admin cannot access settings
**Steps:**
1. Login as CFO
2. Navigate to /dashboard/settings

**Expected Result:** Settings page not accessible. Redirected or "Access Denied" shown. Only admin role can access settings.

---

## Module 16: Email System

### TC-EMAIL-001: Email domain validation — blocked domain
**Steps:**
1. Call API to trigger email to: test@mailinator.com
2. Check server logs

**Expected Result:** Email NOT sent. Log message: "Skipping email to test@mailinator.com: Blocked domain: mailinator.com". No SMTP connection attempted.

---

### TC-EMAIL-002: Email domain validation — fake domain (no MX)
**Steps:**
1. Call API to trigger email to: test@totally-fake-domain-xyz123.com
2. Check server logs

**Expected Result:** Email NOT sent. Log message: "Skipping email to test@totally-fake-domain-xyz123.com: No MX records for totally-fake-domain-xyz123.com — cannot receive email".

---

### TC-EMAIL-003: Email domain validation — real domain
**Steps:**
1. Call API to trigger email to: sanjeev@agenticorg.ai (or valid Gmail address)
2. Check server logs and inbox

**Expected Result:** Email sent successfully. Log message: "Email sent to sanjeev@agenticorg.ai from AgenticOrg...". Email received in inbox.

---

### TC-EMAIL-004: Email — test domain blocked
**Steps:**
1. Try to send email to: user@company.test
2. Try to send email to: user@local.local

**Expected Result:** Both emails blocked. Log shows "Test domain: company.test" and "Test domain: local.local".

---

### TC-EMAIL-005: Welcome email on signup
**Steps:**
1. Create new organization with a real email address
2. Check inbox for welcome email

**Expected Result:** Welcome email received with: personalized greeting (name), organization name, link to dashboard. From: "AgenticOrg <sanjeev@agenticorg.ai>".

---

### TC-EMAIL-006: Invite email
**Steps:**
1. Invite a user with a real email address
2. Check inbox for invite email

**Expected Result:** Invite email received with: organization name, inviter name, role, accept invitation link. From: "AgenticOrg <sanjeev@agenticorg.ai>".

---

## Module 17: Demo Request Flow

### TC-DEMO-001: Submit demo request — no auth required
**Steps:**
1. Open incognito browser
2. Call API: POST /api/v1/demo-request with body: `{"name": "Test Lead", "email": "testlead@gmail.com", "company": "Test Corp", "role": "CFO"}`

**Expected Result:** Returns 201 with: status = "received", message = "We'll be in touch within 2 minutes.", lead_id (UUID), agent_status. No authentication required.

---

### TC-DEMO-002: Demo request creates lead in pipeline
**Steps:**
1. Submit demo request with unique email
2. Login as CEO
3. Navigate to /dashboard/sales
4. Search for the email

**Expected Result:** Lead appears in sales pipeline with: name, email, company, role from the demo request. Stage = "new". Source = "website".

---

### TC-DEMO-003: Demo request — duplicate email handling
**Steps:**
1. Submit demo request with email "dup@gmail.com"
2. Submit another demo request with same email "dup@gmail.com"

**Expected Result:** Both return success. Same lead_id returned for both. No duplicate lead in pipeline.

---

### TC-DEMO-004: Demo request triggers sales agent
**Steps:**
1. Submit demo request
2. Check server logs for sales agent execution

**Expected Result:** Sales agent automatically triggered on the new lead. Lead may receive qualification and email sequence. Non-blocking — demo request returns even if agent takes time.

---

## Module 18: Schemas

### TC-SCH-001: List schemas
**Steps:**
1. Login as CEO
2. Call API: GET /api/v1/schemas

**Expected Result:** Returns paginated list of output schemas with: name, version, description, json_schema, is_default.

---

### TC-SCH-002: Create schema
**Steps:**
1. Call API: POST /api/v1/schemas with body: `{"name": "invoice_output", "version": "1.0.0", "description": "Invoice processing output", "json_schema": {"type": "object", "properties": {"amount": {"type": "number"}}}, "is_default": false}`

**Expected Result:** Schema created. Returns full schema object with id.

---

### TC-SCH-003: Upsert schema
**Steps:**
1. Create schema with name "test_schema"
2. Call PUT /api/v1/schemas/test_schema with updated version and json_schema

**Expected Result:** Schema updated (not duplicated). Returns updated schema with `created: false` (update, not create).

---

## Module 19: Health & API

### TC-API-001: Health check — liveness
**Steps:**
1. Call GET /api/v1/health/liveness (no auth required)

**Expected Result:** Returns `{"status": "alive"}` with HTTP 200. Response time < 500ms.

---

### TC-API-002: Health check — full
**Steps:**
1. Call GET /api/v1/health (no auth required)

**Expected Result:** Returns: status, version = "2.1.0", env, checks (DB connectivity, Redis connectivity). All checks pass.

---

### TC-API-003: API versioning
**Steps:**
1. All API calls use /api/v1/ prefix
2. Try calling an endpoint without the prefix

**Expected Result:** All endpoints correctly prefixed with /api/v1. Calls without prefix return 404.

---

### TC-API-004: CORS headers
**Steps:**
1. Make API call from browser on different origin
2. Check response headers for Access-Control-Allow-Origin

**Expected Result:** CORS headers present. In development: `Access-Control-Allow-Origin: *`. In production: restricted to configured origins.

---

### TC-API-005: Pagination — default parameters
**Steps:**
1. Call GET /api/v1/agents (no page/per_page params)
2. Check response structure

**Expected Result:** Returns paginated response with: items array, total count, page = 1, per_page = 20, pages (total pages). Default pagination applied.

---

### TC-API-006: Pagination — custom page size
**Steps:**
1. Call GET /api/v1/agents?page=2&per_page=5

**Expected Result:** Returns page 2 with up to 5 items. Total and pages calculated correctly. Items different from page 1.

---

## Module 20: Agent Teams

### TC-TEAM-001: Create agent team
**Steps:**
1. Call API: POST /api/v1/agent-teams with body: `{"name": "Finance Team", "domain": "finance", "routing_rules": [], "members": [{"agent_id": "{valid_id}", "role": "primary", "weight": 1.0}]}`

**Expected Result:** Team created with team_id, name, domain, members list, status, created_at.

---

## Module 21: Config Management

### TC-CFG-001: Get fleet limits
**Steps:**
1. Call API: GET /api/v1/config/fleet_limits

**Expected Result:** Returns FleetLimits object with all configured limits. Defaults applied for missing values.

---

### TC-CFG-002: Update fleet limits
**Steps:**
1. Call API: PUT /api/v1/config/fleet_limits with modified values
2. Call GET to verify changes persisted

**Expected Result:** Updated values returned. GET confirms persistence.

---

## Module 22: Cross-Cutting Concerns

### TC-CC-001: RBAC — domain segregation for CFO
**Steps:**
1. Login as CFO
2. Call GET /api/v1/agents
3. Check agent domains in response

**Expected Result:** Only finance domain agents returned. No HR, Marketing, or Ops agents visible. Total count matches finance-only count.

---

### TC-CC-002: RBAC — admin sees all domains
**Steps:**
1. Login as CEO
2. Call GET /api/v1/agents

**Expected Result:** All agents across all domains returned. Total count includes finance, HR, marketing, ops, backoffice.

---

### TC-CC-003: RBAC — auditor denied write operations
**Steps:**
1. Login as Auditor
2. Try POST /api/v1/agents (create agent)
3. Try PATCH /api/v1/agents/{id} (update agent)
4. Try POST /api/v1/agents/{id}/run (run agent)

**Expected Result:** All write operations return 403 Forbidden. Auditor can only read audit logs.

---

### TC-CC-004: Tenant isolation
**Steps:**
1. Create Org A (signup)
2. Create agent in Org A
3. Create Org B (separate signup)
4. Login as Org B admin
5. Try to access Org A's agent by ID

**Expected Result:** Org B cannot see or access Org A's agents. Returns 404 or 403. Complete data isolation between tenants.

---

### TC-CC-005: Concurrent requests
**Steps:**
1. Login as CEO
2. Open dashboard in two browser tabs simultaneously
3. Perform actions in both tabs (e.g., view agents in one, approvals in other)

**Expected Result:** Both tabs work independently. No data corruption or session conflicts. Token shared across tabs (same localStorage).

---

### TC-CC-006: Error handling — invalid JSON body
**Steps:**
1. Call POST /api/v1/agents with invalid JSON body: `{invalid}`

**Expected Result:** Returns 422 Unprocessable Entity with details about JSON parse error. No server crash.

---

### TC-CC-007: Error handling — missing required fields
**Steps:**
1. Call POST /api/v1/agents with empty body: `{}`

**Expected Result:** Returns 422 with validation errors listing all missing required fields (name, agent_type, domain, etc.).

---

### TC-CC-008: Error handling — non-existent resource
**Steps:**
1. Call GET /api/v1/agents/00000000-0000-0000-0000-000000000000

**Expected Result:** Returns 404 Not Found with appropriate error message.

---

### TC-CC-009: Prompt lock on active agents
**Steps:**
1. Find an active agent
2. Try to update its system_prompt_text via PATCH /api/v1/agents/{id}

**Expected Result:** Returns 409 Conflict with message: "Cannot edit prompt of an active agent. Clone the agent to make changes." Prompt remains unchanged.

---

### TC-CC-010: SQL injection prevention
**Steps:**
1. Call GET /api/v1/agents?domain=' OR 1=1 --
2. Call POST /api/v1/auth/login with email: "' OR 1=1 --@test.com"

**Expected Result:** Both return normal error responses (400/422). No SQL executed. No data leakage.

---

### TC-CC-011: XSS prevention
**Steps:**
1. Submit demo request with name: `<script>alert('xss')</script>`
2. View the lead in sales pipeline

**Expected Result:** Script tag rendered as text, not executed. No alert popup. HTML properly escaped.

---

### TC-CC-012: Large payload handling
**Steps:**
1. Call POST /api/v1/agents/{id}/run with very large input (>1MB JSON)

**Expected Result:** Server handles gracefully — either processes or returns 413 Payload Too Large. No server crash or timeout.

---

## Module 23: Performance & Reliability

### TC-PERF-001: Initial page load time
**Steps:**
1. Open https://app.agenticorg.ai/ in Chrome with DevTools Network tab
2. Clear cache (hard refresh)
3. Note total page load time and bundle size

**Expected Result:** Initial bundle < 200KB (code-split). First Contentful Paint < 2 seconds. Total page load < 4 seconds.

---

### TC-PERF-002: Code splitting verification
**Steps:**
1. Open landing page
2. Check Network tab for loaded JS chunks
3. Navigate to /dashboard (login first)
4. Check for additional chunks loaded on demand

**Expected Result:** Landing page loads only core bundle. Dashboard page loads additional chunks lazily. No single chunk > 300KB.

---

### TC-PERF-003: API response times
**Steps:**
1. Login and make these API calls, noting response times:
   - GET /api/v1/health/liveness
   - GET /api/v1/agents
   - GET /api/v1/approvals
   - GET /api/v1/audit

**Expected Result:** Liveness < 100ms. Agent list < 500ms. Approvals < 500ms. Audit log < 500ms.

---

### TC-PERF-004: Agent execution latency
**Steps:**
1. Run an agent via Playground
2. Note total execution time (from run to result)

**Expected Result:** Agent execution < 30 seconds for standard tasks. Performance metrics show latency_ms in response.

---

## Module 24: Backward Compatibility

### TC-BC-001: All 6 demo logins still work
**Steps:**
1. Login with each of the 6 demo credentials (CEO, CFO, CHRO, CMO, COO, Auditor)
2. Verify dashboard loads for each

**Expected Result:** All 6 logins succeed. Each role sees appropriate data based on RBAC rules.

---

### TC-BC-002: Existing agents with claude-3-5-sonnet model still work
**Steps:**
1. Login as CEO
2. Find agents with llm_model = "claude-3-5-sonnet-20241022"
3. Run one of these agents

**Expected Result:** Agent executes successfully using Gemini (safe fallback, since no Anthropic API key). No error about Claude model unavailability.

---

### TC-BC-003: Agents without cost_controls run unlimited
**Steps:**
1. Find an agent with no cost_controls (empty or null)
2. Run it multiple times

**Expected Result:** Agent runs successfully every time. No budget-exceeded errors. Cost not tracked (or tracked but no cap enforced).

---

### TC-BC-004: Agents without parent_agent_id work normally
**Steps:**
1. Find an agent with parent_agent_id = null
2. Run it

**Expected Result:** Agent works normally. No escalation chain errors. If HITL triggers, escalates to human (not another agent).

---

### TC-BC-005: CEO sees 28+ agents
**Steps:**
1. Login as CEO
2. Navigate to /dashboard/agents
3. Count total agents

**Expected Result:** Total agent count >= 28 (all built-in + any custom agents). All domains represented.

---

### TC-BC-006: Existing prompt templates intact
**Steps:**
1. Navigate to /dashboard/prompt-templates
2. Verify built-in templates present
3. Check template text is not corrupted

**Expected Result:** All built-in templates present with original text. Template count matches expected (26+ templates).

---

## Module 25: Onboarding Flow

### TC-ONB-001: New org onboarding
**Steps:**
1. Create new organization via /signup
2. After redirect to /onboarding, verify onboarding page loads
3. Complete onboarding steps

**Expected Result:** Onboarding page shows guided setup. After completion, redirects to /dashboard with onboarding_complete = true.

---

### TC-ONB-002: Skip onboarding
**Steps:**
1. After signup, manually navigate to /dashboard (skip onboarding)

**Expected Result:** Dashboard loads. Onboarding can be completed later. No features blocked due to incomplete onboarding.

---

## Module 26: WebSocket Feed

### TC-WS-001: Real-time agent activity feed
**Steps:**
1. Login as CEO
2. Open dashboard
3. Run an agent in another tab
4. Watch activity feed for real-time update

**Expected Result:** Activity feed updates in near-real-time when agents execute. No page refresh needed for new events.

---

## Module 27: Negative & Edge Cases

### TC-NEG-001: Access API with expired token
**Steps:**
1. Login and copy the JWT token
2. Wait for expiry or manually modify the token
3. Call any protected API endpoint

**Expected Result:** Returns 401 Unauthorized. Token recognized as expired/invalid.

---

### TC-NEG-002: Access API with tampered token
**Steps:**
1. Take a valid JWT token
2. Modify the payload (change tenant_id)
3. Call a protected API endpoint

**Expected Result:** Returns 401. JWT signature verification fails. No access granted.

---

### TC-NEG-003: Create agent with confidence floor out of range
**Steps:**
1. Try to create agent with confidence_floor = 1.5 (above max)
2. Try with confidence_floor = -0.5 (below min)

**Expected Result:** Validation error for out-of-range values. Valid range: 0.5 to 0.99.

---

### TC-NEG-004: Update non-existent agent
**Steps:**
1. Call PATCH /api/v1/agents/non-existent-uuid with valid body

**Expected Result:** Returns 404 Not Found.

---

### TC-NEG-005: Run agent with empty input
**Steps:**
1. Call POST /api/v1/agents/{id}/run with body: `{"inputs": {}}`

**Expected Result:** Agent executes (may produce low-quality output) or returns validation error. No server crash.

---

### TC-NEG-006: Extremely long agent name
**Steps:**
1. Create agent with name = 300 character string

**Expected Result:** Either truncated to 255 chars or rejected with validation error. No database error.

---

### TC-NEG-007: Special characters in inputs
**Steps:**
1. Create agent with name containing: `Test <Agent> "Special" & 'Chars'`
2. Run agent with input containing unicode: `Process invoice for ₹5,00,000 — vendor "日本語テスト"`

**Expected Result:** Special characters handled correctly. No encoding errors. Unicode preserved in output.

---

### TC-NEG-008: Concurrent agent creation race condition
**Steps:**
1. Send 2 simultaneous POST /api/v1/agents requests with same name + type

**Expected Result:** One succeeds, one fails with unique constraint violation (409 Conflict). No duplicate agents created.

---

### TC-NEG-009: Delete active agent with running workflows
**Steps:**
1. Start a workflow that uses an agent
2. While workflow is running, pause/deactivate the agent
3. Check workflow status

**Expected Result:** Workflow step using the paused agent fails gracefully with appropriate error. Workflow marked as failed (not hung).

---

### TC-NEG-010: Browser back button after logout
**Steps:**
1. Login as CEO, navigate to /dashboard/agents
2. Logout
3. Click browser Back button

**Expected Result:** Does not show cached dashboard data. Redirected to /login. No sensitive data visible.

---

## Module 28: Org Chart Hierarchy & Parent Escalation

### TC-ORG-CHART-001: Create agent with parent (reporting_to)
**Steps:**
1. Login as CEO/Admin
2. Create a parent agent: Name = "VP Finance", Type = "finance_manager", Domain = "finance", Status = shadow
3. Promote the parent agent to active
4. Create a child agent: Name = "Priya AP", Type = "accounts_payable_processor", Domain = "finance"
5. In Step 2 of the wizard, set "Reports to" dropdown to "VP Finance"
6. Complete creation

**Expected Result:** Child agent created with parent_agent_id pointing to VP Finance's UUID. reporting_to field populated. Agent detail page shows "Reports to: VP Finance" in the persona header.

---

### TC-ORG-CHART-002: View hierarchy in agent detail
**Steps:**
1. Open the child agent (Priya AP) from TC-ORG-CHART-001
2. Check the persona header section
3. Check the Overview tab

**Expected Result:** Persona header displays: employee name "Priya AP", designation, and "Reports to: VP Finance". Overview tab shows parent_agent_id field with the parent's UUID.

---

### TC-ORG-CHART-003: Escalation to parent agent on HITL
**Steps:**
1. Create parent agent "VP Finance" (active)
2. Create child agent "AP Processor" with parent_agent_id = VP Finance, confidence_floor = 0.99
3. Run child agent with a normal task (will produce confidence < 0.99)
4. Check HITL queue and escalation behavior

**Expected Result:** HITL triggered due to low confidence. Escalation chain uses parent_agent_id to identify VP Finance as the escalation target. HITL item's context includes the parent agent reference.

---

### TC-ORG-CHART-004: Escalation chain — 3 levels deep
**Steps:**
1. Create Agent C (grandchild) → parent = Agent B
2. Create Agent B (child) → parent = Agent A
3. Create Agent A (root) → no parent
4. Call escalate_to_parent on Agent C

**Expected Result:** Agent C escalates to Agent B. If Agent B also escalates, it goes to Agent A. If Agent A escalates, returns null (escalate to human). No infinite loop.

---

### TC-ORG-CHART-005: Escalation when parent is paused
**Steps:**
1. Create parent agent "VP Finance" (active)
2. Create child agent with parent_agent_id = VP Finance
3. Pause the parent agent (kill switch)
4. Trigger HITL on child agent

**Expected Result:** Escalation recognizes parent is paused. Returns null (escalate to human) since parent cannot handle tasks. No error thrown.

---

### TC-ORG-CHART-006: Agent without parent — escalation to human
**Steps:**
1. Create agent with no parent_agent_id (null)
2. Trigger HITL on this agent

**Expected Result:** Escalation returns null. HITL item created with standard human approval flow. No parent agent referenced.

---

### TC-ORG-CHART-007: Update parent_agent_id via PATCH
**Steps:**
1. Create two agents: Agent A (parent) and Agent B (child, no parent)
2. PATCH Agent B with: `{"parent_agent_id": "<Agent A UUID>", "reporting_to": "Agent A"}`
3. GET Agent B details

**Expected Result:** Agent B now shows parent_agent_id = Agent A's UUID. reporting_to = "Agent A". Relationship reflected in agent detail page.

---

### TC-ORG-CHART-008: Remove parent (set to null)
**Steps:**
1. Take an agent with a parent_agent_id set
2. PATCH with: `{"parent_agent_id": null, "reporting_to": null}`
3. GET agent details

**Expected Result:** parent_agent_id and reporting_to cleared. Agent now has no hierarchy. Escalation falls through to human.

---

## Module 29: Per-Agent LLM Model Selection

### TC-LLM-001: Create agent with Gemini model
**Steps:**
1. Create new agent via wizard
2. In Step 4 (Behavior), select LLM Model = "gemini-2.5-flash"
3. Complete creation
4. Run the agent

**Expected Result:** Agent created with llm_model = "gemini-2.5-flash". Agent executes using Gemini. No fallback needed. Output includes performance.model used.

---

### TC-LLM-002: Create agent with Claude model — no API key
**Steps:**
1. Verify ANTHROPIC_API_KEY is NOT set in production
2. Create agent with llm_model = "claude-3-5-sonnet-20241022"
3. Run the agent

**Expected Result:** Agent falls back to Gemini (global default) since no Anthropic API key. Agent executes successfully. No error about missing Claude key. Output valid.

---

### TC-LLM-003: Create agent with GPT model — no API key
**Steps:**
1. Verify OPENAI_API_KEY is NOT set in production
2. Create agent with llm_model = "gpt-4o"
3. Run the agent

**Expected Result:** Agent falls back to Gemini since no OpenAI API key. Agent executes successfully. No error.

---

### TC-LLM-004: Agent with Claude model — API key present
**Steps:**
1. Set ANTHROPIC_API_KEY in environment (if available for testing)
2. Create agent with llm_model = "claude-3-5-sonnet-20241022"
3. Run the agent

**Expected Result:** Agent executes using Claude (not Gemini). Output reflects Claude-style response. Performance metrics show Claude model used.

---

### TC-LLM-005: Agent with no llm_model set (null)
**Steps:**
1. Create agent without specifying llm_model
2. Run the agent

**Expected Result:** Agent uses global default (Gemini 2.5 Flash from settings.llm_primary). Executes normally.

---

### TC-LLM-006: Agent with unknown/invalid model name
**Steps:**
1. Create agent with llm_model = "nonexistent-model-xyz"
2. Run the agent

**Expected Result:** _resolve_llm_model returns None (unknown model). Falls back to global default Gemini. Agent executes without error.

---

### TC-LLM-007: LLM model dropdown in Agent Create wizard
**Steps:**
1. Open /dashboard/agents/new
2. Navigate to Step 4 (Behavior)
3. Check the LLM Model dropdown

**Expected Result:** Dropdown shows available models: Gemini 2.5 Flash (default/active), Claude 3.5 Sonnet (requires API key), GPT-4o (requires API key). Note text indicates which models require API keys.

---

### TC-LLM-008: Two agents with different models — back to back
**Steps:**
1. Create Agent A with llm_model = "gemini-2.5-flash"
2. Create Agent B with llm_model = "claude-3-5-sonnet-20241022" (falls back to Gemini if no key)
3. Run Agent A, note result
4. Run Agent B, note result

**Expected Result:** Both agents execute successfully. Agent A uses Gemini. Agent B uses Claude (if key) or Gemini (fallback). No cross-contamination between model settings.

---

## Module 30: Per-Agent Budget Enforcement

### TC-BUDGET-001: Create agent with monthly budget cap
**Steps:**
1. Create agent with cost_controls = {"monthly_cost_cap_usd": 10.00}
2. GET /api/v1/agents/{id}/budget

**Expected Result:** Budget endpoint returns: monthly_cap_usd = 10.00, monthly_spent_usd = 0.00, monthly_pct_used = 0.0, monthly_tasks = 0, warnings = [].

---

### TC-BUDGET-002: Cost tracking after execution
**Steps:**
1. Create agent with cost_controls = {"monthly_cost_cap_usd": 100.00}
2. Run agent once
3. GET /api/v1/agents/{id}/budget

**Expected Result:** monthly_spent_usd > 0 (reflects LLM cost). monthly_tasks = 1. monthly_pct_used = (spent/cap * 100). Cost recorded in AgentCostLedger.

---

### TC-BUDGET-003: Budget exceeded — execution blocked
**Steps:**
1. Create agent with cost_controls = {"monthly_cost_cap_usd": 0.001} (very low cap)
2. Run agent once (should succeed, cost likely exceeds 0.001)
3. Run agent a second time

**Expected Result:** Second run returns error with code E1008 "budget_exceeded". Message indicates monthly cap reached. Agent may auto-pause.

---

### TC-BUDGET-004: Agent without budget — unlimited execution
**Steps:**
1. Create agent with no cost_controls (null or empty {})
2. Run agent 5 times
3. GET /api/v1/agents/{id}/budget

**Expected Result:** All 5 runs succeed. Budget endpoint returns: monthly_cap_usd = 0 (or null), no warnings. No execution blocking.

---

### TC-BUDGET-005: Budget warning at 80% utilization
**Steps:**
1. Create agent with monthly_cost_cap_usd = 1.00
2. Run agent until ~80% of budget consumed
3. GET /api/v1/agents/{id}/budget

**Expected Result:** warnings array contains a warning about approaching budget limit (e.g., "80% of monthly budget consumed"). Agent still runs but flagged.

---

### TC-BUDGET-006: Budget resets monthly
**Steps:**
1. Create agent with budget cap
2. Run agent until budget partially consumed
3. Verify monthly_spent_usd > 0
4. Check that cost is tracked by period_date (current month)

**Expected Result:** Cost tracked per month. A new month would reset the counter. Budget endpoint shows current month spend only.

---

### TC-BUDGET-007: Cost tab in Agent Detail UI
**Steps:**
1. Open agent with budget set in /dashboard/agents/{id}
2. Click "Cost" tab

**Expected Result:** Shows: monthly budget cap (e.g., "$100.00"), current spend (e.g., "$3.47"), utilization bar (3.47%), tasks this month, tokens used. If near limit: amber warning. If over: red warning with "Budget Exceeded" message.

---

### TC-BUDGET-008: Cost tab — no budget configured
**Steps:**
1. Open agent with no cost_controls
2. Click "Cost" tab

**Expected Result:** Shows "No budget configured" or "Unlimited" message. No utilization bar. No warnings.

---

## Module 31: Smart Routing (Multi-Agent)

### TC-ROUTE-001: Routing by routing_filter match
**Steps:**
1. Create Agent A: type = "accounts_payable_processor", routing_filter = {"region": "Mumbai"}, status = active
2. Create Agent B: type = "accounts_payable_processor", routing_filter = {"region": "Delhi"}, status = active
3. Run task with routing_context = {"region": "Mumbai"}

**Expected Result:** Task routed to Agent A (Mumbai filter matches). Agent B not selected. Task result shows agent_id = Agent A's UUID.

---

### TC-ROUTE-002: Routing by specialization match
**Steps:**
1. Create Agent A: type = "accounts_payable_processor", specialization = "domestic invoices", status = active
2. Create Agent B: type = "accounts_payable_processor", specialization = "import invoices", status = active
3. Run task with routing_context = {"description": "Process this domestic invoice from Mumbai vendor"}

**Expected Result:** Task routed to Agent A (specialization "domestic invoices" matches description). Case-insensitive substring match.

---

### TC-ROUTE-003: Routing fallback — no filter match
**Steps:**
1. Create Agent A: routing_filter = {"region": "Mumbai"}
2. Create Agent B: routing_filter = {"region": "Delhi"}
3. Run task with routing_context = {"region": "Chennai"}

**Expected Result:** Neither filter matches. Falls back to first active agent of the type (ordered by created_at). Task executes on fallback agent.

---

### TC-ROUTE-004: Routing priority — filter > specialization > fallback
**Steps:**
1. Create Agent A: routing_filter = {"region": "APAC"} (exact match)
2. Create Agent B: specialization = "APAC invoices" (keyword match)
3. Create Agent C: no filter, no specialization (fallback)
4. Run task with routing_context = {"region": "APAC", "description": "APAC invoices"}

**Expected Result:** Agent A selected (routing_filter match takes priority over specialization match). Routing order is: filter match first → specialization second → fallback last.

---

### TC-ROUTE-005: Routing with single agent — no ambiguity
**Steps:**
1. Ensure only one active agent of type "payroll_processor"
2. Run task for that type

**Expected Result:** Single agent selected immediately. No routing logic needed. Task executes on the only available agent.

---

### TC-ROUTE-006: Routing filter — partial key match
**Steps:**
1. Create Agent with routing_filter = {"region": "APAC", "product": "enterprise"}
2. Run task with routing_context = {"region": "APAC"} (missing "product" key)

**Expected Result:** Partial match succeeds — all keys present in routing_context match the agent's filter. Missing keys in context don't disqualify.

---

### TC-ROUTE-007: Routing filter — case sensitivity
**Steps:**
1. Create Agent with routing_filter = {"region": "APAC"}
2. Run task with routing_context = {"region": "apac"} (lowercase)

**Expected Result:** Match fails (values compared with ==, case-sensitive). Falls through to specialization or fallback. QA should document this behavior.

---

## Module 32: Persona & Virtual Employee Fields

### TC-PERSONA-001: Create agent with full persona
**Steps:**
1. Create agent with: employee_name = "Priya Sharma", designation = "Senior AP Analyst", specialization = "Domestic invoices, high-value transactions", avatar_url = "https://example.com/avatar.png", routing_filter = {"region": "Mumbai"}

**Expected Result:** All persona fields saved. Agent detail shows: "Priya Sharma" as display name, "Senior AP Analyst" as designation, specialization text, avatar image rendered.

---

### TC-PERSONA-002: Update persona via PATCH
**Steps:**
1. PATCH /api/v1/agents/{id} with: `{"employee_name": "Priya K. Sharma", "designation": "Lead AP Analyst"}`
2. GET agent details

**Expected Result:** employee_name updated to "Priya K. Sharma". designation updated to "Lead AP Analyst". Other fields unchanged. Agent detail page reflects new values.

---

### TC-PERSONA-003: Agent list shows employee_name
**Steps:**
1. Navigate to /dashboard/agents
2. Find an agent with employee_name set

**Expected Result:** Agent card shows employee_name (e.g., "Priya Sharma") as the primary display name, not the system agent name. Avatar initial derived from employee_name.

---

### TC-PERSONA-004: Agent without persona fields
**Steps:**
1. Create agent without employee_name, designation, specialization
2. View in agent list and detail

**Expected Result:** Falls back to showing agent.name and agent_type. No empty fields or broken UI. Avatar shows initial from agent name.

---

### TC-PERSONA-005: Multiple agents same type — different personas
**Steps:**
1. Create Agent 1: type = "accounts_payable_processor", employee_name = "Priya", specialization = "Domestic, Mumbai"
2. Create Agent 2: type = "accounts_payable_processor", employee_name = "Arjun", specialization = "Import, Delhi"
3. View both in agent list

**Expected Result:** Both agents appear as separate cards with distinct names (Priya, Arjun), different specializations. Both are same agent_type but different personas.

---

## Module 33: Prompt Lock & Edit History

### TC-LOCK-001: Edit prompt on active agent — blocked
**Steps:**
1. Find an active agent (status = "active")
2. Call PATCH /api/v1/agents/{id} with body: `{"system_prompt_text": "new prompt text"}`

**Expected Result:** Returns 409 Conflict. Error message: "Cannot edit prompt of an active agent. Clone the agent to make changes." Prompt unchanged.

---

### TC-LOCK-002: Edit non-prompt fields on active agent — allowed
**Steps:**
1. Find an active agent
2. Call PATCH /api/v1/agents/{id} with body: `{"designation": "Updated Title", "max_retries": 5}`

**Expected Result:** Returns 200 OK. designation and max_retries updated. Prompt NOT affected. Active status maintained.

---

### TC-LOCK-003: Edit prompt on shadow agent — allowed
**Steps:**
1. Find a shadow agent
2. Call PATCH /api/v1/agents/{id} with body: `{"system_prompt_text": "updated shadow prompt", "change_reason": "Testing prompt update"}`

**Expected Result:** Prompt updated successfully. Prompt edit history entry created with: prompt_before, prompt_after, change_reason, edited_by, timestamp.

---

### TC-LOCK-004: Prompt edit history — full audit trail
**Steps:**
1. Create shadow agent with initial prompt
2. Update prompt 3 times with different change_reasons
3. GET /api/v1/agents/{id}/prompt-history

**Expected Result:** Returns 3 entries sorted newest first. Each entry has: prompt_before, prompt_after, change_reason, edited_by (user ID), created_at. Character count diff visible.

---

### TC-LOCK-005: Clone active agent to edit prompt
**Steps:**
1. Find an active agent with locked prompt
2. POST /api/v1/agents/{id}/clone with body: `{"name": "Clone for Edit", "agent_type": "clone_test"}`
3. Update clone's prompt (PATCH with system_prompt_text)

**Expected Result:** Clone created in shadow status. Clone's prompt is editable. Original active agent's prompt remains locked and unchanged.

---

### TC-LOCK-006: Prompt lock UI display
**Steps:**
1. Open active agent detail → Prompt tab
2. Check UI elements

**Expected Result:** Prompt text displayed as read-only (grey background or lock icon). "Prompt Locked" badge visible. "Clone to Edit" button available. Edit button disabled or hidden.

---

### TC-LOCK-007: Shadow agent prompt — editable UI
**Steps:**
1. Open shadow agent detail → Prompt tab
2. Check UI elements

**Expected Result:** Prompt text is editable (white background, cursor active). Edit/Save buttons visible. No lock badge.

---

## Module 34: Sales Pipeline — Advanced Flows

### TC-SALES-ADV-001: Update lead stage via PATCH
**Steps:**
1. Find a lead in "new" stage
2. PATCH /api/v1/sales/pipeline/{lead_id} with: `{"stage": "qualified", "score": 75}`
3. GET the lead

**Expected Result:** Lead stage updated to "qualified". Score updated to 75. Updated timestamp set. Lead appears in "Qualified" section of funnel.

---

### TC-SALES-ADV-002: Update lead BANT fields
**Steps:**
1. PATCH lead with: `{"budget": "$50K annual", "authority": "CTO", "need": "Invoice automation", "timeline": "Q2 2026"}`
2. GET lead details

**Expected Result:** All BANT fields saved. Visible in lead detail panel. Fields can be partially filled (some null, some set).

---

### TC-SALES-ADV-003: Update deal value and schedule demo
**Steps:**
1. PATCH lead with: `{"deal_value_usd": 25000.00, "demo_scheduled_at": "2026-04-01T10:00:00Z", "stage": "demo_scheduled"}`
2. GET lead details

**Expected Result:** deal_value_usd = 25000.00, demo_scheduled_at set, stage = "demo_scheduled". Lead moves to Demo Scheduled in funnel.

---

### TC-SALES-ADV-004: Mark lead as lost
**Steps:**
1. PATCH lead with: `{"stage": "lost", "lost_reason": "Chose competitor product"}`
2. GET lead details

**Expected Result:** Stage = "lost". lost_reason saved. Lead appears in "Closed Lost" section of funnel (red).

---

### TC-SALES-ADV-005: Run automated follow-ups
**Steps:**
1. Ensure there are leads with next_followup_at in the past
2. POST /api/v1/sales/run-followups

**Expected Result:** Returns: processed (number of leads checked), emailed (emails sent), skipped (not due or sequence complete), errors. Each processed lead's followup_count incremented. last_contacted_at updated.

---

### TC-SALES-ADV-006: Follow-up respects timing schedule
**Steps:**
1. Create lead, process it (step 0 sent)
2. Immediately call run-followups

**Expected Result:** Lead skipped — insufficient time since last contact (Day 1 follow-up requires 1 day gap). Returns in "skipped" count. No duplicate email sent.

---

### TC-SALES-ADV-007: Follow-up sequence completion
**Steps:**
1. Process a lead through all 5 follow-up steps (Day 0, 1, 3, 7, 14)
2. Call run-followups again after step 5

**Expected Result:** Lead skipped — sequence complete. No more emails sent. followup_count stays at 5.

---

### TC-SALES-ADV-008: Process inbox — Gmail reply detection
**Steps:**
1. Send a sales email to a real recipient
2. Have the recipient reply
3. POST /api/v1/sales/process-inbox

**Expected Result:** Returns: replies_found (>= 1), matched (matched to lead by email), responded (auto-response sent), details (per-reply breakdown). Reply detected and matched to correct lead.

---

### TC-SALES-ADV-009: Process lead with different actions
**Steps:**
1. POST /api/v1/sales/pipeline/process-lead with `{"lead_id": "...", "action": "qualify_and_respond"}`
2. POST with `{"lead_id": "...", "action": "followup", "sequence_step": 2}`

**Expected Result:** "qualify_and_respond" → agent qualifies lead and generates initial outreach email. "followup" with step 2 → sends Day 3 follow-up template. Different actions produce different email content and lead updates.

---

### TC-SALES-ADV-010: Import CSV — validation errors
**Steps:**
1. Create CSV with: missing email column, or invalid email format, or empty rows
2. POST /api/v1/sales/import-csv

**Expected Result:** Returns: imported = 0 (or partial), skipped with skip_details explaining each failure. No server crash on malformed CSV.

---

### TC-SALES-ADV-011: Import CSV — duplicate detection
**Steps:**
1. Import CSV with 5 leads (unique emails)
2. Import same CSV again

**Expected Result:** Second import: imported = 0, skipped = 5, skip_details shows "duplicate email" for each. No duplicate leads in pipeline.

---

### TC-SALES-ADV-012: Seed prospects — idempotent
**Steps:**
1. POST /api/v1/sales/seed-prospects
2. Note the seeded count
3. POST /api/v1/sales/seed-prospects again

**Expected Result:** First call: seeded = 20 (Indian enterprise prospects). Second call: seeded = 0, skipped_duplicates = 20. No duplicate leads.

---

### TC-SALES-ADV-013: Sales metrics accuracy
**Steps:**
1. GET /api/v1/sales/metrics
2. Manually count leads per stage from GET /api/v1/sales/pipeline

**Expected Result:** Metrics match actual data: total_leads = count of all leads, funnel breakdown matches stage counts, avg_score = average of all lead scores, emails_sent_this_week = count of EmailSequence records with sent_at this week.

---

## Module 35: Prompt Template — Advanced Flows

### TC-TPL-ADV-001: Edit built-in template — blocked (409)
**Steps:**
1. Find a built-in template (is_builtin = true)
2. PUT /api/v1/prompt-templates/{id} with modified template_text

**Expected Result:** Returns 409 Conflict. Message: "Built-in templates cannot be edited. Clone to create a custom version." Template unchanged.

---

### TC-TPL-ADV-002: Delete built-in template — blocked
**Steps:**
1. Find a built-in template
2. DELETE /api/v1/prompt-templates/{id}

**Expected Result:** Returns 409 Conflict. Built-in templates cannot be deleted.

---

### TC-TPL-ADV-003: RBAC — CFO sees only finance templates
**Steps:**
1. Login as CFO
2. GET /api/v1/prompt-templates

**Expected Result:** Only finance domain templates returned. HR, marketing, ops templates not visible.

---

### TC-TPL-ADV-004: RBAC — CEO sees all templates
**Steps:**
1. Login as CEO
2. GET /api/v1/prompt-templates

**Expected Result:** All templates returned across all domains (finance, hr, marketing, ops, backoffice). Both built-in and custom templates visible.

---

### TC-TPL-ADV-005: Template variable extraction
**Steps:**
1. Create template with text: "You are a {{role}} agent specializing in {{domain}}. Process {{document_type}} for {{company_name}}."
2. GET the template

**Expected Result:** variables field contains: ["role", "domain", "document_type", "company_name"]. Variables auto-extracted from {{placeholder}} syntax.

---

### TC-TPL-ADV-006: Template with no variables
**Steps:**
1. Create template with text: "You are a general purpose assistant." (no {{placeholders}})
2. GET the template

**Expected Result:** variables field empty or []. Template usable without variable substitution.

---

### TC-TPL-ADV-007: Use template in agent creation
**Steps:**
1. Create a template with variables
2. In Agent Create wizard Step 3, select this template
3. Fill in variable values
4. Check prompt preview

**Expected Result:** Template loaded. Variable input fields generated for each {{variable}}. Preview shows template with variables substituted. Created agent's system_prompt_text has the final resolved text.

---

## Module 36: Gmail Integration & Inbox Management

### TC-GMAIL-001: Process inbox — no replies
**Steps:**
1. Ensure no new replies in Gmail inbox
2. POST /api/v1/sales/process-inbox

**Expected Result:** Returns: replies_found = 0, matched = 0, responded = 0. No errors.

---

### TC-GMAIL-002: Gmail service account authentication
**Steps:**
1. Verify AGENTICORG_GMAIL_SA_KEY_JSON or AGENTICORG_GMAIL_SA_KEY is set
2. Verify AGENTICORG_GMAIL_USER = "sanjeev@agenticorg.ai"
3. POST /api/v1/sales/process-inbox

**Expected Result:** Gmail API authenticates via service account with domain-wide delegation. Accesses sanjeev@agenticorg.ai inbox. No auth errors.

---

### TC-GMAIL-003: Gmail service account — missing config
**Steps:**
1. If possible, test with missing SA key environment variable
2. POST /api/v1/sales/process-inbox

**Expected Result:** Graceful error returned. No server crash. Error message indicates Gmail configuration missing.

---

## Module 37: Agent Clone — Advanced Flows

### TC-CLONE-001: Clone inherits all settings
**Steps:**
1. Create agent with: custom prompt, routing_filter, specialization, persona fields, cost_controls, llm_model
2. POST /api/v1/agents/{id}/clone with name and type overrides
3. GET the clone's details

**Expected Result:** Clone has identical: system_prompt_text, routing_filter, specialization, confidence_floor, authorized_tools, cost_controls, llm_model. Clone has NEW: name, agent_type, id, version = "1.0.0", status = "shadow".

---

### TC-CLONE-002: Clone starts in shadow mode
**Steps:**
1. Clone an active agent
2. Check clone's status

**Expected Result:** Clone status = "shadow" regardless of original's status. Clone needs independent promotion to go active.

---

### TC-CLONE-003: Clone with overrides
**Steps:**
1. POST /api/v1/agents/{id}/clone with overrides: `{"overrides": {"system_prompt_text": "Customized clone prompt", "specialization": "Different specialization"}}`
2. GET clone details

**Expected Result:** Clone has the overridden prompt and specialization. Other fields inherited from original. parent_id in response points to original.

---

### TC-CLONE-004: Multiple clones from same original
**Steps:**
1. Clone Agent A → creates Clone B
2. Clone Agent A again → creates Clone C
3. Verify B and C are independent

**Expected Result:** Clone B and C have different IDs. Both reference Agent A as parent_id. Editing Clone B does not affect Clone C or Agent A.

---

## Module 38: Confidence & HITL — Advanced Flows

### TC-CONF-001: Confidence exactly at floor — no HITL
**Steps:**
1. Create agent with confidence_floor = 0.85
2. Run agent with input that produces confidence = 0.85 (exactly at floor)

**Expected Result:** HITL NOT triggered. Confidence >= floor means pass. Status = "completed".

---

### TC-CONF-002: Confidence just below floor — HITL triggered
**Steps:**
1. Create agent with confidence_floor = 0.85
2. Run agent with input that produces confidence = 0.84

**Expected Result:** HITL triggered. Status = "hitl_required". Approval item created in queue with trigger_type = "confidence_below_floor".

---

### TC-CONF-003: Confidence returned as "high" string
**Steps:**
1. Run agent where LLM returns `{"confidence": "high"}`
2. Check response confidence value

**Expected Result:** confidence mapped to 0.95. If confidence_floor < 0.95, no HITL. Processed correctly.

---

### TC-CONF-004: Confidence returned as "medium" string
**Steps:**
1. Run agent where LLM returns `{"confidence": "medium"}`

**Expected Result:** confidence mapped to 0.75. If confidence_floor > 0.75, HITL triggered.

---

### TC-CONF-005: Confidence returned as "low" string
**Steps:**
1. Run agent where LLM returns `{"confidence": "low"}`

**Expected Result:** confidence mapped to 0.5. HITL almost certainly triggered (most floors > 0.5).

---

### TC-CONF-006: Confidence field missing from LLM output
**Steps:**
1. Run agent where LLM returns output without "confidence" key

**Expected Result:** Default confidence = 0.85 used. HITL triggered if floor > 0.85.

---

### TC-CONF-007: HITL decision options
**Steps:**
1. Trigger HITL on any agent
2. Check the HITL item's decision_options

**Expected Result:** decision_options include: ["approve", "reject", "defer"]. Each has a label and action. Approve continues execution, reject fails it, defer keeps it pending.

---

### TC-CONF-008: HITL item expiry
**Steps:**
1. Trigger HITL
2. Check expires_at on the HITL item

**Expected Result:** expires_at set to ~4 hours from creation. After expiry, item should auto-expire (status changes to "expired").

---

## Module 39: Agent Create Wizard — UI Interactions

### TC-CRT-UI-001: Domain change cascades to parent agents and templates
**Steps:**
1. Open /dashboard/agents/new
2. In Step 1, select Domain = "Finance"
3. Click Next to Step 2
4. Note available agent types and parent agents in dropdown
5. Go Back to Step 1, change Domain to "HR"
6. Click Next to Step 2

**Expected Result:** Agent types update from finance types (ap_processor, recon_agent…) to HR types (talent_acquisition, onboarding_agent…). "Reports To" dropdown refreshes to show only active HR agents. Templates in Step 3 will also change to HR domain.

---

### TC-CRT-UI-002: Custom agent type toggle
**Steps:**
1. In Step 2, uncheck "Create custom agent type" — verify dropdown shown
2. Check "Create custom agent type" — verify text input appears
3. Type "custom_data_analyst" in the text input
4. Uncheck again — verify dropdown returns

**Expected Result:** Checking the box replaces the dropdown with a free-text input. Unchecking restores the dropdown. The custom type text persists if you toggle back.

---

### TC-CRT-UI-003: Routing filter add/remove
**Steps:**
1. In Step 2, click "+ Add Filter"
2. Enter Key = "region", Value = "APAC"
3. Click "+ Add Filter" again
4. Enter Key = "product", Value = "enterprise"
5. Click "Remove" on the first filter

**Expected Result:** Each click adds a new key-value row. Remove button deletes that specific row. Remaining filters preserved. Review step shows only the surviving filter(s).

---

### TC-CRT-UI-004: Reports To dropdown — populated with active agents
**Steps:**
1. In Step 1, select Domain = "Finance"
2. In Step 2, click the "Reports To" dropdown

**Expected Result:** Dropdown shows "— No parent (escalates to human) —" as default. Below that, lists all active finance agents with format: "{employee_name} ({Agent Type}) — Finance". Paused/shadow agents NOT shown.

---

### TC-CRT-UI-005: Reports To dropdown — select and deselect parent
**Steps:**
1. Select a parent agent from the dropdown
2. Verify "Reports To" shows in Step 5 Review
3. Go back and change to "— No parent —"
4. Verify Step 5 Review no longer shows Reports To

**Expected Result:** Selecting a parent populates both parentAgentId and reportingTo. Deselecting clears both. Review step reflects the current selection.

---

### TC-CRT-UI-006: LLM model selector
**Steps:**
1. Navigate to Step 4 (Behavior)
2. Check the LLM Model dropdown options
3. Select "Claude 3.5 Sonnet"
4. Read the helper text below
5. Select "Gemini 2.5 Flash"
6. Read the helper text

**Expected Result:** Dropdown shows 6 options: Gemini 2.5 Flash (default), Gemini 2.5 Pro, Claude 3.5 Sonnet, Claude Opus 4, GPT-4o, GPT-4o Mini. Selecting Claude/GPT shows: "This model requires an API key. If not configured, the agent will fall back to Gemini." Selecting Gemini shows: "Gemini is always available — no additional API key needed."

---

### TC-CRT-UI-007: LLM model shown in Review step
**Steps:**
1. Select LLM Model = "GPT-4o" in Step 4
2. Click Next to Step 5 Review

**Expected Result:** Review grid shows "LLM Model: gpt-4o" alongside other settings.

---

### TC-CRT-UI-008: Confidence floor slider boundary values
**Steps:**
1. In Step 4, drag confidence floor slider to minimum (0.5)
2. Verify display shows "50%"
3. Drag to maximum (0.99)
4. Verify display shows "99%"
5. Set to 0.88 (default)
6. Verify display shows "88%"

**Expected Result:** Slider moves between 50% and 99%. Label updates in real-time as slider moves. Value is reflected in Review step.

---

### TC-CRT-UI-009: Max Retries input validation
**Steps:**
1. In Step 4, set Max Retries = 1 (minimum)
2. Try to type 0 — verify clamped to 1
3. Set to 10 (maximum)
4. Try to type 11 — verify clamped to 10

**Expected Result:** Input enforces min=1, max=10 range. Values outside range are clamped or rejected.

---

### TC-CRT-UI-010: Step validation — disabled Next button
**Steps:**
1. Step 1: Clear Employee Name field, try clicking Next
2. Step 2: If using custom type, clear it, try clicking Next
3. Step 3: Clear prompt text, try clicking Next

**Expected Result:** Next button is disabled (greyed out) when required fields are empty. Step 1 requires employee_name. Step 2 requires agent type. Step 3 requires prompt text.

---

### TC-CRT-UI-011: Cancel and Back button labels
**Steps:**
1. On Step 1, check left button label
2. Click Next to Step 2, check left button label
3. Click Next to Step 3, check left button label

**Expected Result:** Step 1 left button shows "Cancel" (navigates to agent list). Steps 2-5 left button shows "Back" (goes to previous step).

---

### TC-CRT-UI-012: Avatar URL preview in Review
**Steps:**
1. In Step 1, enter Avatar URL = a valid image URL
2. Go to Step 5 Review

**Expected Result:** Review shows the avatar image (16x16 rounded circle). If no URL entered, shows initial letter circle instead.

---

### TC-CRT-UI-013: Creation failure error message
**Steps:**
1. Fill all wizard steps with valid data
2. Disconnect network (or simulate API failure)
3. Click "Create as Shadow"

**Expected Result:** Error message displayed: "Failed to create agent. Please try again." Button re-enables after failure. User stays on Review step.

---

### TC-CRT-UI-014: Submitting state — button disabled
**Steps:**
1. Fill all wizard steps
2. Click "Create as Shadow"
3. Observe button during API call

**Expected Result:** Button text changes to "Creating..." and is disabled during submission. Prevents double-click. Re-enables on success (redirects) or failure (shows error).

---

## Module 40: Agent Detail — UI Interactions

### TC-DET-UI-001: Persona header — employee name display
**Steps:**
1. Open an agent with employee_name set (e.g., "Priya Sharma")
2. Check the header section

**Expected Result:** Header shows "Priya Sharma" (employee_name) as primary name, NOT the system agent name. Designation shown below. Domain badge visible.

---

### TC-DET-UI-002: Persona header — avatar rendering
**Steps:**
1. Open agent with avatar_url set
2. Open agent without avatar_url

**Expected Result:** Agent with URL shows rounded image. Agent without URL shows colored initial circle (first letter of name, uppercase).

---

### TC-DET-UI-003: Persona header — Reports To display
**Steps:**
1. Open agent with reporting_to = "VP Finance"
2. Open agent without reporting_to

**Expected Result:** First agent shows "Reports to: VP Finance" in muted text below specialization. Second agent does not show the Reports To line at all.

---

### TC-DET-UI-004: Overview tab — LLM Model and Reports To fields
**Steps:**
1. Open agent detail → Overview tab
2. Check for LLM Model and Reports To rows

**Expected Result:** LLM Model shows the configured model (e.g., "gemini-2.5-flash") or "Default (Gemini)". Reports To shows parent name or "None (escalates to human)".

---

### TC-DET-UI-005: Promote button — disabled when active
**Steps:**
1. Open an active agent
2. Check Promote button state

**Expected Result:** Promote button is disabled (greyed out) with no click handler. Cannot promote an already-active agent.

---

### TC-DET-UI-006: Promote button — loading state
**Steps:**
1. Open a shadow agent
2. Click Promote
3. Observe button during API call

**Expected Result:** Button text changes to "Promoting..." and is disabled. After success, agent status updates to "active" and page refreshes.

---

### TC-DET-UI-007: Rollback button — loading and error state
**Steps:**
1. Click Rollback on an agent
2. If rollback fails, check error display

**Expected Result:** Button shows "Rolling back..." during API call. On failure, red error text appears below the action buttons. Error auto-clears on next action attempt.

---

### TC-DET-UI-008: Agent not found
**Steps:**
1. Navigate to /dashboard/agents/nonexistent-uuid-here

**Expected Result:** Page shows "Agent not found." message. No crash or blank page.

---

### TC-DET-UI-009: Kill switch on detail page
**Steps:**
1. Open an active agent
2. Click the red Kill Switch button
3. Confirm the action

**Expected Result:** Agent paused. Status badge changes from "active" to "paused". Page refreshes to reflect new status. Kill switch button disappears (already paused).

---

### TC-DET-UI-010: Tab switching preserves state
**Steps:**
1. Open agent detail, view Overview tab
2. Switch to Config tab
3. Switch to Prompt tab
4. Switch back to Overview

**Expected Result:** Each tab renders its content. Switching tabs does not trigger additional API calls (data loaded once). Active tab has underline indicator.

---

### TC-DET-UI-011: Authorized tools display
**Steps:**
1. Open agent with authorized_tools = ["slack_send", "pdf_extract"]
2. Check Overview and Config tabs

**Expected Result:** Both tabs show tool badges: "slack_send", "pdf_extract". If no tools: shows "No tools configured".

---

### TC-DET-UI-012: Shadow tab — progress bar colors
**Steps:**
1. Open shadow agent with sample_count < min_samples
2. Check progress bar color
3. Open shadow agent with sample_count >= min_samples
4. Check progress bar color

**Expected Result:** Below requirement: yellow bar. Met requirement: green bar. Bar width proportional to count/min ratio.

---

### TC-DET-UI-013: Shadow tab — promotion readiness checklist
**Steps:**
1. Open shadow agent not ready for promotion

**Expected Result:** Shows two items with checkmark/X indicators: "Sample count (X/100)" and "Accuracy (X% / 95.0%)". Badge shows "Not yet ready". When both met: badge shows "Ready to promote" in green.

---

### TC-DET-UI-014: Cost tab — utilization bar colors
**Steps:**
1. Open agent with spend < 80% of cap → green bar
2. Open agent with spend 80-99% of cap → yellow bar with "Approaching budget limit"
3. Open agent with spend > cap → red bar with "Over budget! Agent may be throttled."

**Expected Result:** Bar color matches budget status. Percentage label shows actual value. Text warning matches the color state.

---

### TC-DET-UI-015: Built-in badge rendering
**Steps:**
1. Open a built-in agent (is_builtin = true)
2. Check persona header

**Expected Result:** "Built-in" outline badge shown below specialization line.

---

## Module 41: Login Page — UI Interactions

### TC-LOGIN-UI-001: Demo credentials toggle
**Steps:**
1. Navigate to /login
2. Click "Try the demo instead →"
3. Verify demo credentials section expands
4. Click "Hide demo logins"

**Expected Result:** Toggle button text changes between "Try the demo instead →" and "Hide demo logins". Clicking reveals/hides grid of 6 demo role buttons.

---

### TC-LOGIN-UI-002: Demo credential button — auto-fill
**Steps:**
1. Expand demo credentials
2. Click "CFO" button

**Expected Result:** Email field fills with "cfo@agenticorg.local". Password field fills with "cfo123!". User can then click "Sign in with email" to login.

---

### TC-LOGIN-UI-003: Loading state during login
**Steps:**
1. Enter valid credentials
2. Click "Sign in with email"
3. Observe button during API call

**Expected Result:** Button text changes to "Signing in..." and is disabled. Prevents double-click.

---

### TC-LOGIN-UI-004: Error message display
**Steps:**
1. Enter wrong password
2. Click Sign in
3. Observe error area

**Expected Result:** Red error message displayed below the form. Message persists until next login attempt. No redirect.

---

### TC-LOGIN-UI-005: Navigation links
**Steps:**
1. Click "Create a new organization →" link
2. Verify navigation to /signup
3. Go back, click back-to-home link
4. Verify navigation to /

**Expected Result:** Both links navigate to correct pages.

---

### TC-LOGIN-UI-006: Google OAuth button conditional rendering
**Steps:**
1. Check if Google sign-in button is visible on login page

**Expected Result:** Button visible only if Google Client ID is configured (fetched from /api/v1/auth/config). If not configured, no Google button shown — no error.

---

## Module 42: Signup Page — UI Interactions

### TC-SIGNUP-UI-001: All fields required
**Steps:**
1. Leave all fields empty
2. Click "Create Account"

**Expected Result:** Browser HTML5 validation prevents submission. Required indicators on: Organization Name, Your Name, Email, Password, Confirm Password.

---

### TC-SIGNUP-UI-002: Loading state during signup
**Steps:**
1. Fill all fields correctly
2. Click Create Account
3. Observe button

**Expected Result:** Button text changes to "Creating account..." and is disabled during API call.

---

### TC-SIGNUP-UI-003: Error message display
**Steps:**
1. Try to sign up with an email that already exists
2. Observe error

**Expected Result:** Error message displayed in red text above or below the form. Form remains filled for correction.

---

### TC-SIGNUP-UI-004: Sign in link
**Steps:**
1. On signup page, click "Sign in" link

**Expected Result:** Navigates to /login page.

---

## Module 43: Dashboard — UI Interactions

### TC-DASH-UI-001: Metric card color coding
**Steps:**
1. Login as CEO, view dashboard
2. Check the 4 metric cards

**Expected Result:** Total Agents: default text. Active Agents: green text (text-green-600). Pending Approvals: red text (text-red-600). Shadow Agents: yellow text (text-yellow-600).

---

### TC-DASH-UI-002: Pie chart rendering and interaction
**Steps:**
1. Check the Agent Status pie chart
2. Hover over a segment

**Expected Result:** Pie chart shows distribution of active/shadow/paused agents with colored segments. Hovering shows tooltip with count and label.

---

### TC-DASH-UI-003: Domain distribution bar chart
**Steps:**
1. Check the Domain Distribution bar chart

**Expected Result:** Bar chart shows 4-5 bars (Finance, HR, Marketing, Ops, Backoffice) with agent counts. Labels angled for readability. Colors match domain scheme.

---

### TC-DASH-UI-004: Activity feed — badge colors
**Steps:**
1. Scroll to Recent Activity section
2. Check outcome badges

**Expected Result:** "success" = green badge, "failure"/"error" = red badge, other = grey/secondary badge.

---

### TC-DASH-UI-005: Pending approvals click-through
**Steps:**
1. Click on a pending approval item in dashboard summary

**Expected Result:** Navigates to /dashboard/approvals page.

---

### TC-DASH-UI-006: Loading state
**Steps:**
1. Login and navigate to /dashboard
2. Observe initial render

**Expected Result:** Shows "Loading dashboard data..." message while APIs fetch. Replaced by charts and data once loaded.

---

### TC-DASH-UI-007: Empty states
**Steps:**
1. Login as a new org with no agents or activity

**Expected Result:** Charts show empty state messages (e.g., "No agents found"). No JavaScript errors. Dashboard still renders structural layout.

---

## Module 44: Approvals Page — UI Interactions

### TC-APPR-UI-001: Tab switching — Pending vs Decided
**Steps:**
1. Navigate to /dashboard/approvals
2. Click "Decided" tab
3. Click "Pending" tab

**Expected Result:** Tabs switch content. Active tab has visual indicator. Pending tab shows count badge (e.g., "Pending (3)"). Decided tab shows decided count.

---

### TC-APPR-UI-002: Priority filter — live filtering
**Steps:**
1. Select "Critical" from priority dropdown
2. Verify list updates
3. Select "All" to reset

**Expected Result:** List filters to show only items matching selected priority. Changing filter triggers re-fetch.

---

### TC-APPR-UI-003: Feedback message after decision
**Steps:**
1. Approve an item with notes
2. Check for success feedback

**Expected Result:** Green success message appears: "Decision recorded successfully" (or similar). Message auto-dismisses or persists until next action.

---

### TC-APPR-UI-004: Empty approval queue
**Steps:**
1. Approve/reject all pending items
2. Check Pending tab

**Expected Result:** Shows "No pending approvals" message. No broken layout.

---

## Module 45: Connectors Page — UI Interactions

### TC-CONN-UI-001: Stats cards accuracy
**Steps:**
1. Navigate to /dashboard/connectors
2. Compare stats cards with connector list

**Expected Result:** Total count matches number of connector cards. Active count matches green-status connectors. Unhealthy count matches red-status connectors.

---

### TC-CONN-UI-002: Health check button and result
**Steps:**
1. Click "Health Check" on a connector
2. Wait for result

**Expected Result:** Button triggers API call. Result shown in alert/modal: "Healthy" (green) or "Unhealthy" (red) with timestamp.

---

### TC-CONN-UI-003: Register Connector navigation
**Steps:**
1. Click "Register Connector" button

**Expected Result:** Navigates to /dashboard/connectors/new page.

---

### TC-CONN-UI-004: Loading and empty states
**Steps:**
1. Navigate to connectors page, observe initial load
2. Filter to a category with no connectors

**Expected Result:** Loading: shows "Loading connectors..." text. Empty filter: shows "No connectors found" message.

---

## Module 46: Workflows Page — UI Interactions

### TC-WF-UI-001: Workflow card rendering
**Steps:**
1. Navigate to /dashboard/workflows
2. Check each workflow card

**Expected Result:** Each card shows: name, Active/Inactive badge, version (e.g., "v1.0.0"), trigger type, created date. Hover effect (shadow increase).

---

### TC-WF-UI-002: Run Now button
**Steps:**
1. Click "Run Now" on a workflow
2. Observe result

**Expected Result:** Workflow run triggered. Success message or redirect to run detail page. Run appears in workflow run history.

---

### TC-WF-UI-003: View button navigation
**Steps:**
1. Click "View" on a workflow card

**Expected Result:** Navigates to /dashboard/workflows/{id} detail page.

---

### TC-WF-UI-004: Create Workflow navigation
**Steps:**
1. Click "Create Workflow" button

**Expected Result:** Navigates to /dashboard/workflows/new page.

---

### TC-WF-UI-005: Loading and empty states
**Steps:**
1. Observe initial page load
2. Check page with no workflows

**Expected Result:** Loading: "Loading workflows...". Empty: "No workflows configured yet." with Create Workflow button.

---

## Module 47: Settings Page — UI Interactions

### TC-SET-UI-001: Fleet limits input changes
**Steps:**
1. Navigate to /dashboard/settings
2. Change Max Active Agents to 50
3. Change Max Shadow Agents to 15
4. Change Max Replicas Per Type to 25

**Expected Result:** All number inputs accept new values. No validation errors for reasonable values.

---

### TC-SET-UI-002: Per-domain max agents
**Steps:**
1. Change each domain's max agents: finance=25, hr=20, marketing=15, ops=20, backoffice=10
2. Save settings
3. Refresh page

**Expected Result:** All 5 domain-specific inputs accept values. Values persist after save and page refresh.

---

### TC-SET-UI-003: PII masking toggle
**Steps:**
1. Set PII Masking to "Disabled"
2. Save
3. Refresh and verify value

**Expected Result:** Dropdown switches between "Enabled" and "Disabled". Value persists.

---

### TC-SET-UI-004: Data region dropdown
**Steps:**
1. Select "US" from data region
2. Save
3. Refresh

**Expected Result:** Dropdown shows India/EU/US options with GCP regions. Selected value persists.

---

### TC-SET-UI-005: Save success feedback
**Steps:**
1. Make any change
2. Click "Save Settings"

**Expected Result:** Button shows "Saving..." during API call. Green success message: "Settings saved successfully" appears after save. Message auto-hides after ~3 seconds.

---

## Module 48: Playground Page — UI Interactions

### TC-PLAY-UI-001: Use case card selection
**Steps:**
1. Navigate to /playground
2. Click "Process Invoice" use case card
3. Click "Screen Resume" use case card

**Expected Result:** Selected card has highlighted border/background. Previous selection deselected. Run button enabled.

---

### TC-PLAY-UI-002: Run button disabled during execution
**Steps:**
1. Select a use case
2. Click Run
3. Try clicking Run again during execution

**Expected Result:** Button disabled during execution. Shows loading indicator. Re-enables after completion.

---

### TC-PLAY-UI-003: Execution trace — terminal display
**Steps:**
1. Run any use case
2. Watch the terminal output area

**Expected Result:** Color-coded lines appear sequentially: blue (agent startup), amber (LLM calls), green (results), red (HITL if triggered). Terminal auto-scrolls to bottom. Monospace font.

---

### TC-PLAY-UI-004: Summary card after execution
**Steps:**
1. Run a use case to completion
2. Check summary section

**Expected Result:** Shows: Status (success/failure badge), Confidence (percentage), Latency (milliseconds), HITL Triggered (Yes/No). All values populated from API response.

---

### TC-PLAY-UI-005: Error state display
**Steps:**
1. Run a use case that fails (e.g., if agent is unavailable)
2. Check error display

**Expected Result:** Error message displayed in red. Terminal shows error trace. Summary card shows failure status.

---

### TC-PLAY-UI-006: User agents section
**Steps:**
1. Login as CEO (or user with custom agents)
2. Navigate to /playground
3. Scroll to "Your Agents" section

**Expected Result:** Section shows custom agents created by the user. Each listed with name, type, domain. Can be selected and run like pre-built use cases.

---

## Module 49: Pricing Page — UI Interactions

### TC-PRICE-UI-001: Three tier cards rendering
**Steps:**
1. Navigate to /pricing
2. Check the 3 pricing cards

**Expected Result:** Free ($0/month), Pro ($499/month, highlighted with "Popular" badge), Enterprise (Custom). Pro card has visual emphasis (scale, ring border).

---

### TC-PRICE-UI-002: Feature comparison table
**Steps:**
1. Scroll to comparison table
2. Check all rows

**Expected Result:** Table has 15+ feature rows. Each cell shows: checkmark (included), X (not included), or text value (e.g., "7 days", "Unlimited"). All 3 columns (Free, Pro, Enterprise) populated.

---

### TC-PRICE-UI-003: FAQ accordion
**Steps:**
1. Scroll to FAQ section
2. Click first question to expand
3. Click again to collapse
4. Click a different question

**Expected Result:** Questions expand/collapse on click. Only one question open at a time (or multiple, depending on implementation). Answers fully visible when expanded.

---

### TC-PRICE-UI-004: CTA buttons open demo modal
**Steps:**
1. Click "Start Pro" button on Pro tier card
2. Close modal
3. Click "Contact Sales" on Enterprise tier card

**Expected Result:** Both buttons open the Book a Demo modal. Modal has form fields (Name, Email, Company, Role). Submit shows success message.

---

### TC-PRICE-UI-005: Navbar navigation
**Steps:**
1. On pricing page, click logo/home link
2. Navigate back, click "Evaluations" link
3. Navigate back, click "Sign In" button

**Expected Result:** Logo → home (/). Evaluations → /evals. Sign In → /login. All navigations work.

---

### TC-PRICE-UI-006: Mobile menu toggle
**Steps:**
1. Open pricing page on mobile viewport (375px width)
2. Click hamburger menu icon
3. Click a nav link

**Expected Result:** Menu icon opens mobile navigation overlay. Links work. Menu closes after navigation.

---

## Module 50: Landing Page — UI Interactions

### TC-LAND-UI-001: Hero CTA buttons
**Steps:**
1. Click "Get Started Free" button in hero section
2. Navigate back
3. Click "Play in Playground" button

**Expected Result:** "Get Started Free" → /signup. "Play in Playground" → /playground.

---

### TC-LAND-UI-002: Book a Demo modal — from button
**Steps:**
1. Click "Book a Demo" button in navbar or hero
2. Fill form: Name, Email, Company, Role dropdown, Phone
3. Submit

**Expected Result:** Modal opens. All fields editable. Role dropdown includes CEO/CFO/CHRO/CMO/COO/CTO/Other. Submit shows success: "We'll be in touch within 2 minutes."

---

### TC-LAND-UI-003: Mobile menu toggle and navigation
**Steps:**
1. Open landing on mobile viewport
2. Tap hamburger icon
3. Tap "Pricing" link

**Expected Result:** Mobile menu slides open. Tapping link navigates to /pricing and closes menu.

---

### TC-LAND-UI-004: Navbar sign-in button
**Steps:**
1. Click "Sign In" in navbar

**Expected Result:** Navigates to /login page.

---

### TC-LAND-UI-005: Logo bar renders
**Steps:**
1. Scroll to logo bar section

**Expected Result:** Shows partner logos: Oracle, SAP, Salesforce, Slack, GSTN, Darwinbox, Stripe, HubSpot. All logos render (no broken images).

---

### TC-LAND-UI-006: Role cards — content accuracy
**Steps:**
1. Scroll to role cards section
2. Check each card: CFO, CHRO, CMO, COO

**Expected Result:** Each card shows: role title, list of agents for that role, key metric. CFO: "₹69,800/month saved". CHRO: "Zero payroll errors". CMO: "3.2x ROI". COO: "42 tickets/day auto-triaged".

---

### TC-LAND-UI-007: India Connectors section
**Steps:**
1. Scroll to India connectors section

**Expected Result:** Shows: GSTN, EPFO, Darwinbox, Pine Labs Plural, Tally, DigiLocker. Each with icon/name.

---

### TC-LAND-UI-008: Scroll animations
**Steps:**
1. Scroll down slowly through the page
2. Watch sections as they enter viewport

**Expected Result:** Sections fade in smoothly as they enter the viewport (useInView animations). No janky jumps.

---

## Module 51: Prompt Templates Page — UI Interactions

### TC-TPL-UI-001: Create template form toggle
**Steps:**
1. Navigate to /dashboard/prompt-templates
2. Click "Create Template" button
3. Verify form appears
4. Click Cancel or close

**Expected Result:** Create form slides open with fields: Name, Agent Type, Domain, Description, Template Text. Closing/cancelling hides the form.

---

### TC-TPL-UI-002: Create form validation
**Steps:**
1. Open create form
2. Leave Name, Agent Type, and Template Text empty
3. Check "Create Template" submit button

**Expected Result:** Submit button is disabled when required fields (Name, Agent Type, Template Text) are empty. Enabled when all filled.

---

### TC-TPL-UI-003: Template card — click to expand
**Steps:**
1. Click on a template card in the list
2. Verify detail section appears below/beside
3. Click another template

**Expected Result:** First click expands template detail (shows full text, variables, edit/clone buttons). Clicking another template switches detail to the new one.

---

### TC-TPL-UI-004: Clone button for built-in template
**Steps:**
1. Click a built-in template
2. Verify "Clone to Edit" button is shown (not Edit/Delete)
3. Click "Clone to Edit"

**Expected Result:** Clone creates new template named "{original}_custom". New template appears in list as non-built-in. New template is editable.

---

### TC-TPL-UI-005: Edit and Save for custom template
**Steps:**
1. Click a custom (non-built-in) template
2. Click Edit button
3. Modify template text
4. Click "Save Changes"

**Expected Result:** Edit mode: textarea becomes editable. Save triggers API update. Success: template text updated, view returns to read mode.

---

### TC-TPL-UI-006: Delete custom template
**Steps:**
1. Click a custom template
2. Click Delete button
3. Confirm deletion

**Expected Result:** Template removed from list. API call to DELETE endpoint. Template no longer available in agent creation dropdown.

---

### TC-TPL-UI-007: Variables display
**Steps:**
1. Click a template with {{variables}}
2. Check variables section

**Expected Result:** Variables shown as badges: e.g., "{{company_name}}", "{{domain}}", "{{role}}". Each extracted from template text.

---

### TC-TPL-UI-008: Domain filter
**Steps:**
1. Select "Finance" from domain filter dropdown
2. Verify only finance templates shown
3. Check template count label updates

**Expected Result:** List filters to finance domain only. Count label (e.g., "12 templates") updates to match filtered count.

---

## Module 52: Sales Pipeline — UI Interactions

### TC-SALES-UI-001: Funnel bar — click to filter
**Steps:**
1. Navigate to /dashboard/sales
2. Click on "Qualified" segment in the funnel bar
3. Verify table filters
4. Click "Qualified" again to deselect

**Expected Result:** Clicking a segment filters the lead table to that stage only. Clicking again (or clicking "All" legend) resets to show all leads.

---

### TC-SALES-UI-002: Stage legend buttons
**Steps:**
1. Click "Demo Scheduled" in the stage legend below the funnel

**Expected Result:** Lead table filters to show only demo_scheduled leads. Button appears "selected" (highlighted).

---

### TC-SALES-UI-003: Lead row expand/collapse
**Steps:**
1. Click a lead row in the table
2. Verify detail panel expands
3. Click the same row again

**Expected Result:** First click: detail panel shows below (or beside) with Email, Company, Role, Score, Stage, Follow-ups, Created, Deal Value. Second click: collapses detail.

---

### TC-SALES-UI-004: Run Agent button — loading state
**Steps:**
1. Click "Run Agent" on a lead
2. Observe button during execution

**Expected Result:** Button text changes to "Running..." and is disabled. After completion: button re-enables, lead data may update (score, stage).

---

### TC-SALES-UI-005: Refresh button
**Steps:**
1. Click the Refresh button on the pipeline page
2. Observe data reload

**Expected Result:** Metrics cards and lead table refresh with latest data from API. Brief loading state may appear.

---

### TC-SALES-UI-006: Metrics card accuracy
**Steps:**
1. Compare the 6 metrics cards with actual pipeline data

**Expected Result:** Total Leads matches lead count. New This Week matches leads created in last 7 days. Avg Score = average of all lead scores. Emails Sent = EmailSequence records this week. Stale = leads contacted but inactive for 7+ days. Won = leads with stage "won".

---

### TC-SALES-UI-007: Empty pipeline state
**Steps:**
1. Login as a new org with no leads
2. Navigate to /dashboard/sales

**Expected Result:** Shows "No leads yet" or empty table message. Funnel bar is empty. Metrics show zeros. No JavaScript errors.

---

### TC-SALES-UI-008: Deal value and follow-up date display
**Steps:**
1. Find a lead with deal_value_usd and next_followup_at set

**Expected Result:** Deal value shows as "$25,000" (formatted). Follow-up date shows as formatted date. If no value: shows "—" dash.

---

---

## Module 53: Org Chart Tree Visualization

### TC-ORGTREE-001: Org chart page loads
**Steps:**
1. Login as CEO (ceo@agenticorg.local / ceo123!)
2. Navigate to /dashboard/org-chart
3. Open browser DevTools → Console tab

**Expected Result:** Page loads within 3 seconds. Title reads "Organization Chart". Subtitle shows agent count, department head count, and hierarchy count (e.g., "24 agents | 4 department heads | 20 in hierarchy"). No JavaScript errors in console.

---

### TC-ORGTREE-002: Tree view shows hierarchy with connector lines
**Steps:**
1. Navigate to /dashboard/org-chart
2. Ensure "Tree" view is selected (default)
3. Verify the visual tree structure

**Expected Result:** Root nodes (department heads) display at the top. Vertical connector lines (w-px h-6 bg-border) connect parent cards to child cards. Horizontal connector bars join sibling children. Each node renders as a card with avatar/initial, name, designation, domain badge, and status badge.

---

### TC-ORGTREE-003: List view shows indented flat list
**Steps:**
1. Navigate to /dashboard/org-chart
2. Click the "List" toggle button
3. Observe the flat list rendering

**Expected Result:** View switches to a flat indented list inside a Card component. Each row shows a status dot, avatar/initial, agent name, designation/agent_type, domain badge, and status badge. Child agents are indented with increasing left padding (28px per level). Level > 0 rows show a "└" connector character.

---

### TC-ORGTREE-004: Domain filter (All, Finance, HR, Marketing, Ops, Backoffice)
**Steps:**
1. Navigate to /dashboard/org-chart
2. Click the domain dropdown (default "All Departments")
3. Select "Finance"
4. Observe the tree reload
5. Select "Hr"
6. Select "All Departments" again

**Expected Result:** When "Finance" is selected, only agents with domain=finance appear in the tree. The API call includes ?domain=finance. When "Hr" is selected, only HR agents appear. When "All Departments" is re-selected, all agents appear and no domain param is sent to the API. Agent counts in the subtitle update accordingly for each filter.

---

### TC-ORGTREE-005: View toggle (Tree/List)
**Steps:**
1. Navigate to /dashboard/org-chart
2. Confirm "Tree" button is highlighted (bg-primary text-primary-foreground)
3. Click "List" button
4. Confirm "List" button is now highlighted and "Tree" is not
5. Click "Tree" again

**Expected Result:** Toggle buttons switch between tree and list rendering. The active button has the primary background color. The inactive button has the default background with hover:bg-muted. Switching views preserves the current domain filter selection and does not re-fetch data.

---

### TC-ORGTREE-006: Click node navigates to agent detail
**Steps:**
1. Navigate to /dashboard/org-chart in Tree view
2. Click on any agent node card
3. Observe the URL change

**Expected Result:** Browser navigates to /dashboard/agents/{agent-uuid} where the UUID matches the clicked agent's ID. The agent detail page loads with correct agent information matching the card that was clicked.

---

### TC-ORGTREE-007: Expand/collapse on deep branches
**Steps:**
1. Navigate to /dashboard/org-chart in Tree view
2. Find a node with children at depth >= 3 (auto-collapsed)
3. Click the circular expand button (shows child count number)
4. Observe children appear
5. Click the collapse button (shows minus sign)

**Expected Result:** Nodes at depth >= 3 are collapsed by default with a circular button showing the child count (e.g., "3"). Clicking expands the branch to show children with connector lines. The button changes to show a minus sign. Clicking again collapses the branch back. No layout jump or console errors during expand/collapse.

---

### TC-ORGTREE-008: Empty state — no hierarchy
**Steps:**
1. Login as a new org that has no agents with parent_agent_id set
2. Navigate to /dashboard/org-chart

**Expected Result:** A centered card displays: "No agents with hierarchy found." with helper text: "Create agents and set 'Reports To' to build your org chart, or import a hierarchy via CSV." Two buttons appear: "Create Agent" (navigates to /dashboard/agents/new) and "Go to Agent Fleet" (navigates to /dashboard/agents).

---

### TC-ORGTREE-009: Status dot colors (green/yellow/red/blue)
**Steps:**
1. Navigate to /dashboard/org-chart
2. Identify agents with different statuses: active, shadow, paused, staging
3. Verify the status indicator dot color for each

**Expected Result:** Active agents show a green dot (bg-green-500). Shadow agents show a yellow dot (bg-yellow-500). Paused agents show a red dot (bg-red-500). Staging agents show a blue dot (bg-blue-500). Any unknown status shows a gray dot (bg-gray-400). Dots appear in both Tree view (inside NodeCard) and List view.

---

### TC-ORGTREE-010: Domain color coding (emerald/purple/amber/blue borders)
**Steps:**
1. Navigate to /dashboard/org-chart in Tree view
2. Identify agent cards from different domains
3. Compare border/background colors

**Expected Result:** Finance domain cards have emerald border and subtle emerald background (border-emerald-500/50 bg-emerald-500/5). HR cards have purple styling. Marketing cards have amber styling. Ops cards have blue styling. Backoffice cards have slate styling. Any unknown domain uses the default border-border bg-card.

---

### TC-ORGTREE-011: Legend displays correctly
**Steps:**
1. Navigate to /dashboard/org-chart
2. Locate the legend row between the header and the tree content

**Expected Result:** Legend shows three status indicators: a green dot labeled "Active", a yellow dot labeled "Shadow", and a red dot labeled "Paused". After a vertical separator, it shows four domain color swatches: emerald for "Finance", purple for "HR", amber for "Marketing", and blue for "Ops". Legend is visible in both Tree and List view modes.

---

### TC-ORGTREE-012: Sidebar nav shows "Org Chart" link
**Steps:**
1. Login to the dashboard
2. Look at the left sidebar navigation

**Expected Result:** The sidebar contains an "Org Chart" link/item. Clicking it navigates to /dashboard/org-chart. The link is highlighted/active when on the org chart page. It appears alongside other nav items like "Agent Fleet", "Workflows", etc.

---

### TC-ORGTREE-013: CFO sees only finance tree when domain-scoped
**Steps:**
1. Login as CFO (cfo@agenticorg.local / cfo123!)
2. Navigate to /dashboard/org-chart
3. Check the domain filter dropdown

**Expected Result:** If the CFO role is RBAC-restricted to the finance domain, the org-tree API is called with the user's domain scope. Only finance-domain agents appear in the tree. The domain filter may be pre-set or the API backend enforces the domain restriction. No agents from HR, Marketing, or Ops domains appear.

---

### TC-ORGTREE-014: Horizontal scroll on wide trees
**Steps:**
1. Navigate to /dashboard/org-chart in Tree view
2. Ensure there is a root node with 5+ direct children (wide tree)
3. Check for horizontal scrolling

**Expected Result:** The tree container has overflow-x-auto applied. When the tree is wider than the viewport, a horizontal scrollbar appears. Users can scroll left/right to see all branches. The tree content has min-w-max to prevent wrapping. No content is clipped or hidden without scroll access.

---

---

## Module 54: CSV Bulk Import

### TC-CSV-001: Import CSV button visible on Agents page
**Steps:**
1. Login as CEO (ceo@agenticorg.local / ceo123!)
2. Navigate to /dashboard/agents
3. Locate the top-right action buttons

**Expected Result:** An "Import CSV" button (variant="outline") is visible next to the "Create Agent" button. The button text reads "Import CSV".

---

### TC-CSV-002: Import panel toggle (show/hide)
**Steps:**
1. Navigate to /dashboard/agents
2. Click the "Import CSV" button
3. Observe the import panel appear
4. Click the "Import CSV" button again
5. Observe the import panel disappear

**Expected Result:** First click: an import Card panel appears below the header with title "Import Agents from CSV", description text, a "Download Template" button, a file input, and an "Upload & Import" button. Second click: the panel hides. The toggle is controlled by the showImport state variable.

---

### TC-CSV-003: Download template CSV
**Steps:**
1. Navigate to /dashboard/agents
2. Click "Import CSV" to open the panel
3. Click "Download Template"
4. Check the downloaded file

**Expected Result:** A file named "agent_import_template.csv" downloads. The CSV contains a header row with columns: name, agent_type, domain, designation, specialization, reporting_to_name, org_level, llm_model, confidence_floor. It contains one example data row with "Priya Sharma" as a sample entry showing all field formats.

---

### TC-CSV-004: Upload and import valid CSV — happy path
**Steps:**
1. Navigate to /dashboard/agents and open the import panel
2. Create a CSV file with valid columns: name, agent_type, domain (all required filled)
3. Select the CSV file via the file input
4. Click "Upload & Import"
5. Wait for the import to complete

**Expected Result:** The button shows "Importing..." while processing and is disabled. After completion, a green success panel appears showing: "{N} agents imported | {M} parent links set | {S} skipped". The agent list below refreshes automatically (fetchAgents called). Newly imported agents appear in the list.

---

### TC-CSV-005: CSV with reporting_to_name — parent links set
**Steps:**
1. Prepare a CSV with 3 rows: a manager (e.g., "VP Finance", domain=finance, no reporting_to_name) and 2 subordinates with reporting_to_name="VP Finance"
2. Import the CSV via the import panel

**Expected Result:** All 3 agents are imported. The result shows "2 parent links set". The two subordinates have their parent_agent_id set to the VP Finance agent. On the org chart page, the VP Finance appears as a parent node with the two subordinates as children.

---

### TC-CSV-006: CSV with missing required fields — skip rows
**Steps:**
1. Prepare a CSV where row 1 has name, agent_type, domain (valid) and row 2 is missing the "name" field and row 3 is missing "domain"
2. Import the CSV

**Expected Result:** Row 1 is imported successfully. Rows 2 and 3 are skipped. The result shows "1 agents imported | 2 skipped". The skip_details section is visible and lists "missing required field" as the reason for each skipped row.

---

### TC-CSV-007: CSV with self-reference — skip and report
**Steps:**
1. Prepare a CSV with a row where name="Alice" and reporting_to_name="Alice" (self-reference)
2. Import the CSV

**Expected Result:** The agent "Alice" is imported (agent creation succeeds). However, the parent link is skipped because the reporting_to_name matches the agent's own name. The skip_details show reason "self-reference" for that row. parent_links_set is 0.

---

### TC-CSV-008: CSV with unknown parent name — skip and report
**Steps:**
1. Prepare a CSV with a row where name="Bob" and reporting_to_name="NonExistentManager" (a name not present in any agent in the same domain)
2. Import the CSV

**Expected Result:** Agent "Bob" is imported successfully. The parent link is skipped because "NonExistentManager" does not match any existing agent in the same domain. The skip_details show reason "parent 'NonExistentManager' not found in {domain}". parent_links_set is 0.

---

### TC-CSV-009: Duplicate import — second run skips existing agents
**Steps:**
1. Import a CSV with 3 agents (e.g., "Agent A", "Agent B", "Agent C")
2. Confirm all 3 are imported
3. Import the same CSV file again

**Expected Result:** On second import, the system creates new agent records (since the import endpoint does not check for duplicates by name). The result shows 3 imported again. To verify idempotency expectations, check the agent list — there will be 6 agents total (3 pairs). Note: the current implementation does not deduplicate by name; this is the expected behavior to document.

---

### TC-CSV-010: Import result display (imported count, skipped, parent links)
**Steps:**
1. Import a CSV with a mix of valid and invalid rows
2. Observe the result panel

**Expected Result:** A green panel (bg-green-50) shows three bold metrics: imported count, parent links set count, and skipped count. Format: "{N} agents imported | {M} parent links set | {S} skipped". If all rows fail, the skipped count matches total rows and imported is 0.

---

### TC-CSV-011: Skip details expandable section
**Steps:**
1. Import a CSV that has some skipped rows (e.g., missing fields or unknown parent)
2. Observe the result panel
3. Click "Skipped rows" summary text to expand

**Expected Result:** Below the counts, a collapsible details element appears with a "Skipped rows" summary. Clicking it expands to show a list of skipped items, each displaying the reason and the agent name. Up to 10 skip details are shown (API returns skip_details[:10]).

---

### TC-CSV-012: Agents appear in org chart after import
**Steps:**
1. Import a CSV with 3 agents where 2 have reporting_to_name linking them to the first
2. Navigate to /dashboard/org-chart
3. Search for the imported agents in the tree

**Expected Result:** The root agent (no reporting_to_name) appears as a top-level node. The two subordinate agents appear as children under the root agent. Connector lines are drawn. All three agents show status "shadow" (yellow dot) since CSV import creates agents in shadow mode.

---

### TC-CSV-013: All imported agents start in shadow mode
**Steps:**
1. Import a CSV with 5 valid agents
2. Navigate to /dashboard/agents
3. Filter by status "Shadow"

**Expected Result:** All 5 newly imported agents appear with status "shadow". The CSV import endpoint hardcodes status="shadow" for all created agents. No imported agent is active, paused, or any other status.

---

### TC-CSV-014: org_level column respected from CSV
**Steps:**
1. Prepare a CSV where one agent has org_level=1 and another has org_level=3
2. Import the CSV
3. Navigate to /dashboard/agents/{id} for each agent

**Expected Result:** The agent with org_level=1 is stored with org_level=1 in the database. The agent with org_level=3 is stored with org_level=3. If org_level is empty or non-numeric in the CSV, it defaults to 0. The org_level value is visible on the agent detail page and affects ordering in the org tree (sorted by org_level ascending).

---

---

## Module 55: Smart Escalation

### TC-ESC-001: Escalation walks to active parent
**Steps:**
1. Set up a chain: Agent C (active) → reports to Agent B (active) → reports to Agent A (active)
2. Trigger escalation from Agent C via the TaskRouter.escalate() method
3. Inspect the returned escalation result

**Expected Result:** Escalation returns escalated_to = Agent B's UUID (the immediate active parent). escalation_type = "parent_agent". The chain list contains [C, B]. Reason states "Escalated to active parent {B.id} after 1 hop(s)". Agent A is not visited because B is already active.

---

### TC-ESC-002: Escalation skips paused parent, continues to grandparent
**Steps:**
1. Set up a chain: Agent C (active) → reports to Agent B (paused) → reports to Agent A (active)
2. Trigger escalation from Agent C
3. Inspect the result

**Expected Result:** Agent B is skipped because its status is "paused" (in _INACTIVE_STATUSES). Escalation continues to Agent A. Result: escalated_to = Agent A's UUID, escalation_type = "parent_agent", chain = [C, B, A]. Reason states "Escalated to active parent {A.id} after 2 hop(s)". A log message "Skipping inactive parent {B.id} (status=paused)" is emitted.

---

### TC-ESC-003: Escalation max depth (5 hops) — no infinite loop
**Steps:**
1. Set up a chain of 7 agents: G → F → E → D → C → B → A, all with status "shadow" (non-active, non-paused)
2. Trigger escalation from Agent G with max_depth=5

**Expected Result:** Escalation walks 5 hops (F, E, D, C, B) then stops (does not reach A). Since none are active, the parent chain is exhausted. The method falls back to domain-head lookup. The chain list has 6 entries [G, F, E, D, C, B]. No infinite loop or stack overflow occurs regardless of chain length.

---

### TC-ESC-004: Cycle detection — A to B to A stops gracefully
**Steps:**
1. Set up a cycle: Agent A (parent_agent_id = B) and Agent B (parent_agent_id = A), both active
2. Trigger escalation from Agent A
3. Inspect the result

**Expected Result:** The escalation walks from A to B (active, returns). If B were inactive: walks to A again, detects A is already in the visited set, logs "Escalation cycle detected: {A.id} already visited", breaks the loop, and falls back to domain-head. No infinite loop. The chain reflects only unique nodes visited before cycle detection.

---

### TC-ESC-005: Domain head fallback when no parent found
**Steps:**
1. Create Agent X (active, domain=finance, parent_agent_id=NULL — no parent)
2. Create Agent Y (active, domain=finance, parent_agent_id=NULL — this is the domain head, created first)
3. Trigger escalation from Agent X

**Expected Result:** Agent X has no parent_agent_id, so the parent chain walk completes immediately with 0 hops. The method calls resolve_domain_head(tenant_id, "finance", session). It finds Agent Y (active, no parent, same domain, oldest). Result: escalated_to = Y.id, escalation_type = "domain_head". Reason includes "fell back to domain head".

---

### TC-ESC-006: Human fallback when no domain head exists
**Steps:**
1. Create Agent X (active, domain=finance) with no parent
2. Ensure Agent X is the only agent in the finance domain (so it is visited, not eligible as domain_head fallback)
3. Trigger escalation from Agent X

**Expected Result:** Parent chain walk finds no parent. resolve_domain_head returns X itself, but X is already in the visited set so it is excluded. No other domain head exists. Result: escalated_to = None, escalation_type = "human". Reason states "No active parent or domain head found for agent {X.id} (domain=finance); escalating to human operator".

---

### TC-ESC-007: DOMAIN_TO_ROLE mapping (finance to cfo, hr to chro, etc.)
**Steps:**
1. Inspect the DOMAIN_TO_ROLE constant in task_router.py
2. Verify all domain-to-role mappings

**Expected Result:** The DOMAIN_TO_ROLE dict contains exactly: finance → "cfo", hr → "chro", marketing → "cmo", ops → "coo", backoffice → "admin". These five entries cover all domains used in the platform. The mapping is used for identifying executive-level escalation targets by domain.

---

### TC-ESC-008: Backward compatibility — escalate_to_parent still returns UUID or None
**Steps:**
1. Call TaskRouter.escalate_to_parent(agent_id, session) using the same agent chain as TC-ESC-001
2. Inspect the return value type

**Expected Result:** The method returns a UUID object (the escalated_to agent ID) when an escalation target is found, or None when the escalation falls through to human. It does not return the full dict — it extracts result["escalated_to"] from the inner escalate() call. This preserves the original API contract for existing callers.

---

### TC-ESC-009: Escalation from nonexistent agent
**Steps:**
1. Generate a random UUID that does not correspond to any agent
2. Call TaskRouter.escalate(random_uuid, session)
3. Inspect the result

**Expected Result:** The method returns escalated_to = None, escalation_type = "human", chain = [] (empty), reason = "Starting agent {random_uuid} not found". No exception is thrown. The method handles the missing agent gracefully.

---

### TC-ESC-010: resolve_domain_head picks oldest active root agent
**Steps:**
1. Create Agent A (active, domain=finance, no parent, created_at = 2026-01-01)
2. Create Agent B (active, domain=finance, no parent, created_at = 2026-03-01)
3. Call TaskRouter.resolve_domain_head(tenant_id, "finance", session)

**Expected Result:** Returns Agent A's UUID because the query orders by created_at ascending and limits to 1. Agent B is a valid candidate but is newer. The domain head is deterministically the oldest active root agent in the given domain.

---

## Module 56: Org Chart — End-to-End Integration Flows

### TC-E2E-ORG-001: Full flow — create hierarchy via wizard then view in org chart
**Steps:**
1. Login as CEO
2. Create parent agent "VP Finance" via Agent Create wizard (Step 2: no Reports To)
3. Promote VP Finance to active
4. Create child agent "AP Analyst - Priya" via wizard (Step 2: Reports To = VP Finance)
5. Create child agent "AP Analyst - Arjun" via wizard (Step 2: Reports To = VP Finance)
6. Navigate to /dashboard/org-chart
7. Filter by Finance

**Expected Result:** Tree shows VP Finance as root with 2 children (Priya and Arjun). Connector lines link parent to children. All 3 agents visible. Clicking any node navigates to agent detail.

---

### TC-E2E-ORG-002: Full flow — CSV import then view in org chart
**Steps:**
1. Prepare CSV:
```
name,agent_type,domain,designation,reporting_to_name,org_level
VP Tax,tax_compliance,finance,VP Tax & Compliance,,0
GST Officer,tax_compliance,finance,GST Filing Officer,VP Tax,1
TDS Analyst,tax_compliance,finance,TDS Analyst,VP Tax,1
```
2. Navigate to /dashboard/agents → Import CSV
3. Upload the file
4. Check results panel
5. Navigate to /dashboard/org-chart → Filter Finance

**Expected Result:** 3 agents imported, 2 parent links set, 0 skipped. Org chart shows VP Tax as root with GST Officer and TDS Analyst as children. All in shadow mode (yellow dots).

---

### TC-E2E-ORG-003: Full flow — edit parent via Agent Detail then verify org chart
**Steps:**
1. Open an agent detail page (one without a parent)
2. In Overview tab, click Edit next to "Reports To"
3. Select a parent from the dropdown
4. Click Save
5. Navigate to /dashboard/org-chart

**Expected Result:** Agent now appears as a child of the selected parent in the org chart tree. The persona header shows "Reports to: {parent name}".

---

### TC-E2E-ORG-004: Full flow — remove parent via Agent Detail then verify org chart
**Steps:**
1. Open an agent that has a parent set
2. In Overview tab, click Edit next to "Reports To"
3. Select "— No parent (escalates to human) —"
4. Click Save
5. Navigate to /dashboard/org-chart

**Expected Result:** Agent now appears as a root node (no parent). Previous parent no longer shows this agent as a child.

---

### TC-E2E-ORG-005: Full flow — CSV import then promote agents level by level
**Steps:**
1. Import a 3-level hierarchy via CSV (Head → Managers → Analysts)
2. All agents start as shadow
3. Promote Analysts first (bottom level)
4. Then promote Managers
5. Then promote Head

**Expected Result:** Each promotion changes status from shadow to active. Org chart shows progressive green dots as agents are promoted. Bottom-up promotion ensures children are active before parents.

---

### TC-E2E-ORG-006: Full flow — escalation triggers when child agent has low confidence
**Steps:**
1. Create parent agent (active, confidence_floor = 0.5)
2. Create child agent (active, confidence_floor = 0.99, parent = parent agent)
3. Run the child agent with a normal task

**Expected Result:** Child agent executes, produces confidence < 0.99, triggers HITL. Escalation chain: child → parent (active, returned). Parent agent referenced in the HITL item context. If parent agent also fails, escalates to human CFO.

---

### TC-E2E-ORG-007: Full flow — multi-department org chart
**Steps:**
1. Import CSV with agents for ALL 4 departments:
   - Finance: VP Finance → 2 analysts
   - HR: VP HR → 2 analysts
   - Marketing: VP Marketing → 2 analysts
   - Ops: VP Ops → 2 analysts
2. Navigate to /dashboard/org-chart (All Departments)

**Expected Result:** 4 root nodes (one per department VP). Each with 2 children. Different domain colors: emerald (finance), purple (HR), amber (marketing), blue (ops). 12 agents total. Filter by Finance shows only 3, by HR shows only 3, etc.

---

### TC-E2E-ORG-008: CSV import — large file (20+ agents)
**Steps:**
1. Prepare a CSV with 20+ agents across multiple levels (Head → Directors → Managers → Analysts)
2. Import via /dashboard/agents
3. Check result
4. View in org chart

**Expected Result:** All agents imported within 30 seconds. Parent links correctly set for all levels. Org chart renders the full tree without performance issues. Expand/collapse works on deep branches.

---

### TC-E2E-ORG-009: CSV import — BOM-encoded file from Excel
**Steps:**
1. Open a CSV in Microsoft Excel and save as "CSV UTF-8"
2. Import this file (Excel adds BOM: EF BB BF at file start)

**Expected Result:** Import handles BOM correctly (utf-8-sig decoding). No "missing required field" errors caused by BOM corrupting the first column header. All rows imported successfully.

---

### TC-E2E-ORG-010: Org chart — API response structure
**Steps:**
1. Call GET /api/v1/agents/org-tree directly with auth token
2. Inspect JSON response

**Expected Result:** Response has `{"tree": [...], "flat_count": N}`. Each tree node has: id, name, employee_name, designation, domain, agent_type, status, avatar_url, org_level, parent_agent_id, specialization, children (array). Children recursively have the same structure. flat_count matches total agents (not just roots).

---

### TC-E2E-ORG-011: Org chart — domain filter via API
**Steps:**
1. Call GET /api/v1/agents/org-tree?domain=finance
2. Call GET /api/v1/agents/org-tree?domain=hr
3. Call GET /api/v1/agents/org-tree (no filter)

**Expected Result:** finance filter returns only finance agents in tree. hr filter returns only HR agents. No filter returns all agents. flat_count differs per filter.

---

### TC-E2E-ORG-012: Import CSV — error state UI
**Steps:**
1. Open import panel
2. Try to import without selecting a file (button should be disabled)
3. Upload a non-CSV file (e.g., .jpg)
4. Check error handling

**Expected Result:** Upload button disabled when no file selected. Non-CSV file may fail parsing — error displayed in red panel: "Import failed" or specific error message. No server crash.

---

### TC-E2E-ORG-013: Import CSV — confidence_floor and llm_model from CSV
**Steps:**
1. Prepare CSV with confidence_floor=0.95 and llm_model=claude-3-5-sonnet-20241022 for one agent
2. Import
3. Check agent detail

**Expected Result:** Agent created with confidence_floor=0.95 (not default 0.88). llm_model="claude-3-5-sonnet-20241022" stored. Agent falls back to Gemini at runtime (no Claude key) but model preference saved.

---

### TC-E2E-ORG-014: Org chart loading state
**Steps:**
1. Navigate to /dashboard/org-chart
2. Observe initial render before API returns

**Expected Result:** Shows "Loading org chart..." text while API fetches. Replaced by tree/list once data arrives.

---

### TC-E2E-ORG-015: Org chart — agent count stats in header
**Steps:**
1. Navigate to /dashboard/org-chart
2. Check the subtitle stats

**Expected Result:** Shows "X agents | Y department heads | Z in hierarchy". X = flat_count from API. Y = number of root nodes (tree.length). Z = agents with parent_agent_id set or with children.

---

### TC-E2E-ORG-016: List view — click to navigate
**Steps:**
1. Switch to List view on org chart
2. Click any row

**Expected Result:** Navigates to /dashboard/agents/{id}. Same behavior as tree view click.

---

### TC-E2E-ORG-017: Org chart accessible to CFO, CHRO, CMO, COO roles
**Steps:**
1. Login as CFO → navigate to /dashboard/org-chart → page loads
2. Login as CHRO → navigate to /dashboard/org-chart → page loads
3. Login as CMO → navigate to /dashboard/org-chart → page loads
4. Login as COO → navigate to /dashboard/org-chart → page loads
5. Login as Auditor → try /dashboard/org-chart

**Expected Result:** CFO, CHRO, CMO, COO, and Admin can all access the org chart. Auditor cannot access it (not in allowedRoles).

---

### TC-E2E-ORG-018: Sidebar "Org Chart" appears between "Agents" and "Workflows"
**Steps:**
1. Login as CEO
2. Check sidebar navigation order

**Expected Result:** Navigation order includes: Dashboard, Observatory, Agents, **Org Chart**, Workflows, Approvals... The "Org Chart" link is between "Agents" and "Workflows" in the sidebar.

---

---

## Test Execution Summary

| # | Module | Test Cases | Count | Priority |
|---|--------|-----------|-------|----------|
| | **FUNCTIONAL FLOWS** | | | |
| 1 | Landing Page & Public Pages | TC-LP-001 to TC-LP-018 | 18 | High |
| 2 | Authentication | TC-AUTH-001 to TC-AUTH-015 | 15 | Critical |
| 3 | Dashboard | TC-DASH-001 to TC-DASH-005 | 5 | High |
| 4 | Agent Fleet | TC-AGT-001 to TC-AGT-013 | 13 | High |
| 5 | Agent Creation | TC-CRT-001 to TC-CRT-007 | 7 | Critical |
| 6 | Agent Execution | TC-EXEC-001 to TC-EXEC-008 | 8 | Critical |
| 7 | Workflows | TC-WF-001 to TC-WF-005 | 5 | Medium |
| 8 | Approvals (HITL) | TC-HITL-001 to TC-HITL-006 | 6 | Critical |
| 9 | Connectors | TC-CONN-001 to TC-CONN-004 | 4 | Medium |
| 10 | Prompt Templates | TC-TPL-001 to TC-TPL-007 | 7 | High |
| 11 | Sales Pipeline | TC-SALES-001 to TC-SALES-009 | 9 | High |
| 12 | Audit Log | TC-AUDIT-001 to TC-AUDIT-006 | 6 | Medium |
| 13 | Compliance (DSAR) | TC-COMP-001 to TC-COMP-004 | 4 | Medium |
| 14 | Organization | TC-ORG-001 to TC-ORG-006 | 6 | High |
| 15 | Settings | TC-SET-001 to TC-SET-005 | 5 | Medium |
| 16 | Email System | TC-EMAIL-001 to TC-EMAIL-006 | 6 | High |
| 17 | Demo Request | TC-DEMO-001 to TC-DEMO-004 | 4 | High |
| 18 | Schemas | TC-SCH-001 to TC-SCH-003 | 3 | Low |
| 19 | Health & API | TC-API-001 to TC-API-006 | 6 | Critical |
| 20 | Agent Teams | TC-TEAM-001 | 1 | Low |
| 21 | Config | TC-CFG-001 to TC-CFG-002 | 2 | Low |
| 22 | Cross-Cutting (RBAC, Security) | TC-CC-001 to TC-CC-012 | 12 | Critical |
| 23 | Performance | TC-PERF-001 to TC-PERF-004 | 4 | High |
| 24 | Backward Compat | TC-BC-001 to TC-BC-006 | 6 | Critical |
| 25 | Onboarding | TC-ONB-001 to TC-ONB-002 | 2 | Medium |
| 26 | WebSocket | TC-WS-001 | 1 | Low |
| 27 | Negative/Edge | TC-NEG-001 to TC-NEG-010 | 10 | High |
| | **FEATURE-SPECIFIC FLOWS** | | | |
| 28 | Org Chart Hierarchy | TC-ORG-CHART-001 to TC-ORG-CHART-008 | 8 | Critical |
| 29 | Per-Agent LLM Selection | TC-LLM-001 to TC-LLM-008 | 8 | Critical |
| 30 | Per-Agent Budget | TC-BUDGET-001 to TC-BUDGET-008 | 8 | Critical |
| 31 | Smart Routing (Multi-Agent) | TC-ROUTE-001 to TC-ROUTE-007 | 7 | Critical |
| 32 | Persona & Virtual Employee | TC-PERSONA-001 to TC-PERSONA-005 | 5 | High |
| 33 | Prompt Lock & Edit History | TC-LOCK-001 to TC-LOCK-007 | 7 | Critical |
| 34 | Sales Pipeline — Advanced | TC-SALES-ADV-001 to TC-SALES-ADV-013 | 13 | High |
| 35 | Prompt Template — Advanced | TC-TPL-ADV-001 to TC-TPL-ADV-007 | 7 | High |
| 36 | Gmail Integration | TC-GMAIL-001 to TC-GMAIL-003 | 3 | High |
| 37 | Agent Clone — Advanced | TC-CLONE-001 to TC-CLONE-004 | 4 | High |
| 38 | Confidence & HITL — Advanced | TC-CONF-001 to TC-CONF-008 | 8 | Critical |
| | **UI INTERACTION TESTING** | | | |
| 39 | Agent Create Wizard — UI | TC-CRT-UI-001 to TC-CRT-UI-014 | 14 | High |
| 40 | Agent Detail — UI | TC-DET-UI-001 to TC-DET-UI-015 | 15 | High |
| 41 | Login Page — UI | TC-LOGIN-UI-001 to TC-LOGIN-UI-006 | 6 | High |
| 42 | Signup Page — UI | TC-SIGNUP-UI-001 to TC-SIGNUP-UI-004 | 4 | Medium |
| 43 | Dashboard — UI | TC-DASH-UI-001 to TC-DASH-UI-007 | 7 | Medium |
| 44 | Approvals Page — UI | TC-APPR-UI-001 to TC-APPR-UI-004 | 4 | Medium |
| 45 | Connectors Page — UI | TC-CONN-UI-001 to TC-CONN-UI-004 | 4 | Medium |
| 46 | Workflows Page — UI | TC-WF-UI-001 to TC-WF-UI-005 | 5 | Medium |
| 47 | Settings Page — UI | TC-SET-UI-001 to TC-SET-UI-005 | 5 | Medium |
| 48 | Playground Page — UI | TC-PLAY-UI-001 to TC-PLAY-UI-006 | 6 | High |
| 49 | Pricing Page — UI | TC-PRICE-UI-001 to TC-PRICE-UI-006 | 6 | Medium |
| 50 | Landing Page — UI | TC-LAND-UI-001 to TC-LAND-UI-008 | 8 | Medium |
| 51 | Prompt Templates Page — UI | TC-TPL-UI-001 to TC-TPL-UI-008 | 8 | High |
| 52 | Sales Pipeline — UI | TC-SALES-UI-001 to TC-SALES-UI-008 | 8 | High |
| | **NEW FEATURE MODULES** | | | |
| 53 | Org Chart Tree Visualization | TC-ORGTREE-001 to TC-ORGTREE-014 | 14 | Critical |
| 54 | CSV Bulk Import | TC-CSV-001 to TC-CSV-014 | 14 | Critical |
| 55 | Smart Escalation | TC-ESC-001 to TC-ESC-010 | 10 | Critical |
| 56 | **Org Chart E2E Integration** | TC-E2E-ORG-001 to TC-E2E-ORG-018 | **18** | **Critical** |

**Total: 574 test cases**

### Breakdown by Category:
- **Functional Flows (Modules 1-27):** 195 cases
- **Feature-Specific Flows (Modules 28-38):** 96 cases
- **UI Interaction Testing (Modules 39-52):** 179 cases
- **Org Chart / CSV / Escalation (Modules 53-55):** 38 cases
- **Org Chart E2E Integration (Module 56):** 18 cases
- **March 2026 Bug Regression (Modules 57-63):** 66 cases

### Recommended Execution Order:
1. **Critical first (210 cases):** Authentication, Agent Execution, Cross-Cutting (RBAC/security), API Health, Backward Compat, Org Chart Hierarchy, LLM Selection, Budget Enforcement, Smart Routing, Prompt Lock, Confidence & HITL, Org Chart Tree/CSV/Escalation, **March 2026 Regression (ALL)**
2. **High priority (222 cases):** Landing Page, Agent Fleet, Agent Creation + UI, Agent Detail UI, Prompt Templates + UI, Sales Pipeline (all), Email, Demo Request, Performance, Negative/Edge, Persona, Gmail, Agent Clone, Playground UI, Login UI
3. **Medium (142 cases):** Dashboard + UI, Workflows + UI, Approvals + UI, Connectors + UI, Audit, Compliance, Settings + UI, Onboarding, Signup UI, Pricing UI, Landing UI

---

## Module 57: March 2026 Bug Regression — Login & Signup UI

### TC-REG-LOGIN-001: Login page divider is visible and styled
**Bug Ref:** UI-LOGIN-001
**Steps:**
1. Open https://agenticorg.ai/login
2. Observe the divider between Google sign-in and email form

**Expected Result:** Divider shows "OR SIGN IN WITH EMAIL" in uppercase, centered, with visible 2px border line on both sides. Font is medium weight with letter spacing.

---

### TC-REG-SIGNUP-002: Signup page OR divider styled consistently
**Bug Ref:** UI-REG-002
**Steps:**
1. Open https://agenticorg.ai/signup
2. Scroll to the OR divider between form and Google signup

**Expected Result:** "OR" text is uppercase, centered, font-medium, with 2px border line. Visually identical to login page divider.

---

### TC-REG-SIGNUP-003: Signup fields do not auto-fill
**Bug Ref:** UI-REG-003
**Steps:**
1. Open https://agenticorg.ai/signup in a fresh incognito window
2. Observe all form fields

**Expected Result:** All fields are empty. Email field has `autoComplete="off"`. Password fields have `autoComplete="new-password"`. Browser should not pre-fill any values.

---

### TC-REG-SIGNUP-004: Password show/hide toggle on Signup
**Bug Ref:** UI-AUTH-004
**Steps:**
1. Open https://agenticorg.ai/signup
2. Type a password in the Password field
3. Click the eye icon next to the password field
4. Type in the Confirm Password field
5. Click the eye icon next to it

**Expected Result:** Both password fields have eye icons. Clicking toggles between showing and hiding the password text. Icon changes between open-eye and crossed-eye.

---

### TC-REG-SIGNUP-005: Terms & Conditions checkbox blocks signup
**Bug Ref:** UI-REG-006
**Steps:**
1. Open https://agenticorg.ai/signup
2. Fill in all fields (org name, name, email, password, confirm password)
3. Do NOT check the "I agree to Terms of Service and Privacy Policy" checkbox
4. Try to click "Create account"

**Expected Result:** Submit button is disabled (grayed out) when checkbox is unchecked. After checking the checkbox, button becomes active. Links to Terms and Privacy Policy are clickable.

---

### TC-REG-SIGNUP-006: Signup end-to-end with terms consent
**Steps:**
1. Open https://agenticorg.ai/signup
2. Fill: Org = "QA Test", Name = "QA Tester", Email = unique email, Password = "Test@1234", Confirm = "Test@1234"
3. Check the Terms checkbox
4. Click "Create account"

**Expected Result:** Account created successfully. Redirected to onboarding. Terms checkbox was required before submit.

---

## Module 58: March 2026 Bug Regression — Agent Management

### TC-REG-AGENT-007: Comms domain in agent creation
**Bug Ref:** UI-CONFIG-009
**Steps:**
1. Login as CEO/Admin
2. Go to Agents > Create Agent
3. Open the Domain dropdown

**Expected Result:** Dropdown shows 6 options: Finance, HR, Marketing, Ops, Backoffice, **Comms**. Selecting "Comms" shows agent types: email_agent, notification_agent, chat_agent.

---

### TC-REG-AGENT-008: Shadow agent resumes to shadow (not active)
**Bug Ref:** TC_AGENT-007
**Steps:**
1. Create an agent (starts in shadow mode)
2. Click Kill Switch / Pause button
3. Verify agent status = "paused"
4. Click Resume / Unpause
5. Check agent status

**Expected Result:** Agent returns to "shadow" status, NOT "active". To go active, user must use the Promote button which validates accuracy.

---

### TC-REG-AGENT-009: Shadow agent retest resets counters
**Bug Ref:** TC_AGENT-008
**Steps:**
1. Navigate to a shadow agent that has run some samples
2. Note the current sample count and accuracy
3. Click Retest (or call POST /agents/{id}/retest via API)
4. Check sample count and accuracy

**Expected Result:** sample_count resets to 0. accuracy resets to null. Agent remains in shadow mode. A lifecycle event is recorded.

---

### TC-REG-AGENT-010: Authorized tools auto-populated and validated
**Bug Ref:** AGENT-CONFIG-005, INT-CONN-017
**Steps:**
1. Create a new agent with domain = "finance", type = "ap_processor"
2. Leave authorized_tools empty
3. Submit
4. Open the agent's Configure tab

**Expected Result:** Tools are auto-populated based on agent type (e.g., fetch_bank_statement, create_charge). Invalid tool names are rejected with 422 error.

---

## Module 59: March 2026 Bug Regression — Connector Architecture

### TC-REG-CONN-011: Connector base_url override
**Bug Ref:** INT-CONN-010
**Steps:**
1. Register a connector with a custom base_url
2. Verify the connector config shows the custom URL
3. Execute an agent that uses this connector

**Expected Result:** Runtime uses the configured base_url, not the hardcoded default.

---

### TC-REG-CONN-012: Gmail connector available
**Bug Ref:** INT-CONN-012
**Steps:**
1. Go to Connectors page
2. Search for "Gmail" or filter by Comms category
3. Check available tools

**Expected Result:** Gmail connector exists with 4 tools: send_email, read_inbox, search_emails, get_thread. Total connectors = 43.

---

### TC-REG-CONN-013: Multi-auth connector UI
**Bug Ref:** INT-CONN-014
**Steps:**
1. Go to Register Connector
2. Select Auth Type = "OAuth2"
3. Observe credential fields

**Expected Result:** Three fields appear: Client ID, Client Secret, Refresh Token. Switching to "API Key" shows only API Key field. Switching to "Basic" shows Username/Password.

---

### TC-REG-CONN-014: Health check includes connectors
**Bug Ref:** INT-CONN-016
**Steps:**
1. Call GET /api/v1/health

**Expected Result:** Response includes `connectors` section with `registered`, `healthy`, `unhealthy` counts and per-connector `details`. Overall status reflects connector health.

---

### TC-REG-CONN-015: Prompt tool reference validation
**Bug Ref:** INT-CONN-018
**Steps:**
1. Create a prompt template with text containing `{{tool:fake_nonexistent_tool}}`
2. Submit

**Expected Result:** Returns 422 error with message listing "fake_nonexistent_tool" as invalid. Templates with valid or no tool references save successfully.

---

## Module 60: March 2026 Bug Regression — API Key System (NEW)

### TC-REG-APIKEY-001: Generate API key from Settings
**Steps:**
1. Login as admin
2. Go to Settings
3. In API Keys section, enter name "Test Key"
4. Click "Generate Key"

**Expected Result:** Key is generated with `ao_sk_` prefix. Full key shown ONCE. Key appears in the table with "active" status.

---

### TC-REG-APIKEY-002: Authenticate with API key
**Steps:**
1. Copy the generated API key
2. Call GET /api/v1/agents with header `Authorization: Bearer ao_sk_...`

**Expected Result:** Returns agent list (200 OK). Same response as JWT auth.

---

### TC-REG-APIKEY-003: Revoke API key
**Steps:**
1. In Settings > API Keys, click "Revoke" on a key
2. Try to use the revoked key for API calls

**Expected Result:** Key status changes to "revoked". API calls with revoked key return 401.

---

## Module 61: March 2026 Bug Regression — SDK & MCP Integration (NEW)

### TC-REG-SDK-001: Developers section on landing page
**Steps:**
1. Open https://agenticorg.ai
2. Click "Developers" in the navbar
3. Scroll to the Developers section

**Expected Result:** Section shows 4 SDK cards (Python, TypeScript, CLI, MCP Server) with install commands and code examples. 3 protocol cards (A2A, MCP, Grantex). "View Full Workflow" link to /integration-workflow.

---

### TC-REG-SDK-002: Integration workflow page
**Steps:**
1. Open https://agenticorg.ai/integration-workflow
2. Click through the 8 workflow steps

**Expected Result:** Page shows architecture stack, 8 interactive steps (User -> ChatGPT -> MCP -> Auth -> Agent -> HITL -> Approve -> Result), sequence diagram, and key takeaways.

---

### TC-REG-SDK-003: MCP server on npm
**Steps:**
1. Visit https://www.npmjs.com/package/agenticorg-mcp-server
2. Check version and description

**Expected Result:** Package exists, v0.1.1, description mentions AI agents and MCP.

---

### TC-REG-SDK-004: MCP Registry listing
**Steps:**
1. Search for "agenticorg" on https://registry.modelcontextprotocol.io

**Expected Result:** Server "io.github.mishrasanjeev/agenticorg" is listed with 10 tools.

---

## Module 62: March 2026 Bug Regression — SEO & Content (NEW)

### TC-REG-SEO-001: Sitemap includes new pages
**Steps:**
1. Open https://agenticorg.ai/sitemap.xml

**Expected Result:** Contains /integration-workflow URL. Total 40+ URLs.

---

### TC-REG-SEO-002: llms.txt includes SDK section
**Steps:**
1. Open https://agenticorg.ai/llms.txt

**Expected Result:** Contains "Developer SDKs & Integration" section with Python SDK, TypeScript SDK, MCP Server, CLI, API Keys documentation.

---

## Module 63: March 2026 Bug Regression — LLM Cost Tracking (NEW)

### TC-REG-LLM-001: Agent run returns performance metrics
**Steps:**
1. Run an agent via POST /agents/{id}/run
2. Check the response

**Expected Result:** Response includes `performance` object with `total_latency_ms` (non-zero), `llm_tokens_used`, and `llm_cost_usd`. Previously returned zeros.

---

| # | Module | Test IDs | Count | Priority |
|---|--------|----------|-------|----------|
| 57 | Login & Signup UI Regression | TC-REG-LOGIN-001 to TC-REG-SIGNUP-006 | 6 | Critical |
| 58 | Agent Management Regression | TC-REG-AGENT-007 to TC-REG-AGENT-010 | 4 | Critical |
| 59 | Connector Architecture Regression | TC-REG-CONN-011 to TC-REG-CONN-015 | 5 | Critical |
| 60 | API Key System | TC-REG-APIKEY-001 to TC-REG-APIKEY-003 | 3 | Critical |
| 61 | SDK & MCP Integration | TC-REG-SDK-001 to TC-REG-SDK-004 | 4 | High |
| 62 | SEO & Content | TC-REG-SEO-001 to TC-REG-SEO-002 | 2 | Medium |
| 63 | LLM Cost Tracking | TC-REG-LLM-001 | 1 | High |
4. **Low (7 cases):** Schemas, Agent Teams, Config, WebSocket
