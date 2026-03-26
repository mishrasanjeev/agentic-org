# AgenticOrg — End-to-End Manual Test Plan

**Version:** 2.1.0
**Last Updated:** 2026-03-26
**Production URL:** https://app.agenticorg.ai
**Total Test Cases:** 195

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

---

## Test Execution Summary

| Module | Test Cases | Priority |
|--------|-----------|----------|
| Landing Page & Public Pages | TC-LP-001 to TC-LP-018 | High |
| Authentication | TC-AUTH-001 to TC-AUTH-015 | Critical |
| Dashboard | TC-DASH-001 to TC-DASH-005 | High |
| Agent Fleet | TC-AGT-001 to TC-AGT-013 | High |
| Agent Creation | TC-CRT-001 to TC-CRT-007 | Critical |
| Agent Execution | TC-EXEC-001 to TC-EXEC-008 | Critical |
| Workflows | TC-WF-001 to TC-WF-005 | Medium |
| Approvals (HITL) | TC-HITL-001 to TC-HITL-006 | Critical |
| Connectors | TC-CONN-001 to TC-CONN-004 | Medium |
| Prompt Templates | TC-TPL-001 to TC-TPL-007 | High |
| Sales Pipeline | TC-SALES-001 to TC-SALES-009 | High |
| Audit Log | TC-AUDIT-001 to TC-AUDIT-006 | Medium |
| Compliance (DSAR) | TC-COMP-001 to TC-COMP-004 | Medium |
| Organization | TC-ORG-001 to TC-ORG-006 | High |
| Settings | TC-SET-001 to TC-SET-005 | Medium |
| Email System | TC-EMAIL-001 to TC-EMAIL-006 | High |
| Demo Request | TC-DEMO-001 to TC-DEMO-004 | High |
| Schemas | TC-SCH-001 to TC-SCH-003 | Low |
| Health & API | TC-API-001 to TC-API-006 | Critical |
| Agent Teams | TC-TEAM-001 | Low |
| Config | TC-CFG-001 to TC-CFG-002 | Low |
| Cross-Cutting | TC-CC-001 to TC-CC-012 | Critical |
| Performance | TC-PERF-001 to TC-PERF-004 | High |
| Backward Compat | TC-BC-001 to TC-BC-006 | Critical |
| Onboarding | TC-ONB-001 to TC-ONB-002 | Medium |
| WebSocket | TC-WS-001 | Low |
| Negative/Edge | TC-NEG-001 to TC-NEG-010 | High |

**Total: 195 test cases**

### Recommended Execution Order:
1. **Critical first:** Authentication, Agent Execution, Cross-Cutting (security), API Health, Backward Compatibility
2. **High priority:** Landing Page, Agent Fleet, Agent Creation, Prompt Templates, Sales Pipeline, Email, Demo Request, Performance, Negative/Edge
3. **Medium:** Dashboard, Workflows, Approvals, Connectors, Audit, Compliance, Settings, Onboarding
4. **Low:** Schemas, Agent Teams, Config, WebSocket
