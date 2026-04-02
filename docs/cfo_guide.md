# CFO Guide — AgenticOrg Finance Platform

## Overview

AgenticOrg gives CFOs a unified command center for enterprise finance operations. Instead of juggling spreadsheets, portals, and manual workflows, you get 10 specialized AI agents that handle accounts payable, receivables, reconciliation, tax compliance, month-end close, planning, treasury, expenses, revenue recognition, and fixed assets — all with human-in-the-loop approval on every critical decision.

This guide covers everything a CFO needs to operate the platform: the finance dashboard, each finance agent, natural language querying, report scheduling, workflow templates, multi-company management, and HITL approval gates.

---

## Finance Dashboard (`/dashboard/cfo`)

The CFO Dashboard is your real-time financial cockpit. It aggregates data from all 10 finance agents and connected systems into a single view. Access it at `/dashboard/cfo` or by selecting "CFO Dashboard" from the sidebar.

### KPI Cards

| KPI | What It Shows | Data Source |
|-----|---------------|-------------|
| **Cash Runway** | Months of cash remaining at current burn rate | Treasury Agent (bank balances via AA + burn rate calculation) |
| **Burn Rate** | Monthly cash outflow trend (3-month rolling average) | Treasury Agent (aggregated outflows from connected accounts) |
| **DSO** | Days Sales Outstanding — average days to collect receivables | AR Collections Agent (invoice dates vs. payment dates) |
| **DPO** | Days Payable Outstanding — average days to pay vendors | AP Processor (invoice receipt date vs. payment date) |
| **AR Aging** | Receivables bucketed by 0-30, 31-60, 61-90, 91-120, 120+ days | AR Collections Agent |
| **AP Aging** | Payables bucketed by the same aging buckets | AP Processor |
| **P&L Summary** | Revenue, COGS, Gross Margin, Operating Expenses, EBITDA | FP&A Agent (aggregated from ERP/accounting software) |
| **Bank Balances** | Current balances across all connected bank accounts | Banking AA connector (RBI-compliant Account Aggregator) |
| **Tax Calendar** | Upcoming GST, TDS, advance tax deadlines with filing status | Tax Compliance Agent |

### How Data Flows

Each KPI card refreshes automatically. The underlying agents query connected systems (Tally, Zoho Books, Banking AA, GSTN) on a configurable schedule — typically every 15 minutes for cash data and hourly for aging buckets. You can also force-refresh any card by clicking the refresh icon.

The dashboard respects RBAC: only users with CFO or CEO roles can access `/dashboard/cfo`. Other roles see a 403 if they attempt to navigate there directly.

---

## Finance Agents (10)

### 1. AP Processor
**What it does**: End-to-end accounts payable automation — from invoice receipt to payment execution.

**Pipeline**: OCR extraction --> GSTIN validation (via GSTN connector) --> duplicate detection --> 3-way match (invoice vs. PO vs. GRN) --> payment scheduling (early-payment discount optimization) --> GL posting (idempotent) --> remittance advice --> payment execution (via PineLabs Plural).

**HITL triggers**: Invoice amount above configured threshold (default: 5 lakhs), 3-way match delta above tolerance (default: 2%), first-time vendor, GSTIN validation failure.

**Connected systems**: Oracle Fusion, SAP, Tally, GSTN, PineLabs Plural, Zoho Books.

### 2. AR Collections
**What it does**: Manages accounts receivable — invoice creation, aging tracking, collection follow-ups, and cash application.

**Key capabilities**: Creates invoices in accounting software with GSTIN, HSN codes, and tax breakup. Tracks aging and automatically escalates overdue items. Sends collection reminders at configurable intervals (30/45/60/90 days). Applies incoming payments to open invoices.

**Connected systems**: Zoho Books, Oracle Fusion, Banking AA.

### 3. Reconciliation Agent
**What it does**: Automated bank reconciliation — fetches bank statements, matches against GL, and analyzes breaks.

**Matching engine**: Round 1: exact match by amount + date + reference (~96% match rate). Round 2: fuzzy match by amount + date with reference similarity scoring (~3.5% additional). Unmatched items categorized as bank charges, timing differences, partial payments, or unexplained.

**HITL triggers**: Breaks above configured amount (default: 50,000), unexplained items, percentage of unmatched above threshold.

**Connected systems**: Banking AA (for bank statements), Tally/Zoho Books/Oracle (for GL entries).

### 4. Tax Compliance Agent
**What it does**: GST filing (GSTR-1, GSTR-3B, GSTR-9), TDS computation and filing, ITC reconciliation, and tax calendar management.

**Key capabilities**: Pushes GSTR-1 outbound supply data to GSTN via Adaequare GSP. 2-step authentication (session token + API calls). DSC signing for returns (PKCS#1 v1.5 RSA-SHA256). Automatic ITC reconciliation (GSTR-2A vs. books). Tax deadline tracking with proactive alerts.

**HITL triggers**: All GST filings require CFO approval before submission. ITC mismatches above threshold. DSC certificate nearing expiry.

**Connected systems**: GSTN (via Adaequare), Income Tax India, Tally.

### 5. Month-End Close Agent
**What it does**: Orchestrates the entire month-end close process — from trial balance extraction to final close.

**Close checklist**: Extract trial balance --> post adjusting entries --> run reconciliations --> review aging --> compute accruals --> generate close package --> obtain approvals --> lock period.

**HITL triggers**: All adjusting entries above threshold. Period lock requires CFO sign-off. Any item flagged by other agents during the close window.

**Connected systems**: Tally, Oracle Fusion, SAP (trial balance and journal entries).

### 6. FP&A Agent
**What it does**: Financial planning and analysis — budget vs. actual variance, forecasting, and scenario modeling.

**Key capabilities**: Monthly budget vs. actual comparison with variance analysis. Rolling 12-month forecast based on historical trends. Scenario modeling (best/base/worst case). Department-level P&L drill-down.

**Connected systems**: ERP (actuals), Google Sheets/Excel (budgets), internal data warehouse.

### 7. Treasury Agent
**What it does**: Cash management — daily cash position, sweep management, cash flow forecasting, and bank balance aggregation.

**Key capabilities**: Real-time cash position across all bank accounts (via AA). Automatic sweep recommendations (move excess cash to higher-yield accounts). 13-week cash flow forecast. Daily treasury report generation.

**HITL triggers**: Sweep amounts above threshold. Cash runway below configured minimum (e.g., 3 months). Forecast variance exceeding tolerance.

**Connected systems**: Banking AA, Tally/Zoho Books (payables/receivables forecast).

### 8. Expense Manager
**What it does**: Employee expense management — receipt OCR, policy enforcement, approval routing, and reimbursement processing.

**Key capabilities**: Receipt OCR extraction (amount, vendor, category, date). Automatic policy enforcement (per-diem limits, category restrictions, duplicate detection). Multi-level approval routing based on amount. Reimbursement batch processing.

**HITL triggers**: Expenses above per-diem limits. Missing receipts. Suspected duplicates. Category violations.

**Connected systems**: Darwinbox (employee data), Banking (reimbursement), Tally/Zoho Books (GL posting).

### 9. Revenue Recognition Agent (ASC 606)
**What it does**: Automates the 5-step ASC 606 revenue recognition model.

**Pipeline**: Identify contract --> identify performance obligations --> determine transaction price --> allocate price to obligations --> recognize revenue as obligations are satisfied.

**Key capabilities**: Contract analysis for embedded performance obligations. Standalone selling price estimation. Revenue schedule generation. Journal entry creation for recognized revenue.

**HITL triggers**: Multi-element arrangements. Variable consideration estimates. Contract modifications. Revenue above materiality threshold.

**Connected systems**: CRM (contracts), ERP (revenue schedules and journal entries).

### 10. Fixed Assets Agent
**What it does**: Fixed asset lifecycle management — acquisition, depreciation, impairment, and disposal.

**Key capabilities**: Asset capitalization from PO/invoice. Depreciation schedule computation (SLM, WDV, units-of-production). Impairment testing per IND-AS 36. Disposal and retirement processing. Asset register maintenance.

**HITL triggers**: Asset acquisitions above capitalization threshold. Impairment indicators. Disposals requiring write-off.

**Connected systems**: ERP (asset register), Tally (depreciation entries).

---

## NL Query for Finance

The NL Query interface lets you ask questions in plain English. Press **Cmd+K** (or **Ctrl+K** on Windows) from any page to open the search bar, or click the chat icon to open the slide-out chat panel.

### Example Finance Queries

| Query | What You Get |
|-------|-------------|
| "What's my cash position?" | Current bank balances across all connected accounts, aggregated by bank |
| "Show me AP aging over 90 days" | List of vendor invoices outstanding beyond 90 days, sorted by amount |
| "What's our DSO this quarter?" | Days Sales Outstanding computed for the current quarter with trend vs. previous |
| "How much did we pay vendor XYZ this year?" | Total payments to a specific vendor with invoice-level breakdown |
| "What's the GSTR-1 filing status for March?" | Filing status, deadline, and any pending items |
| "Show me the P&L for February" | Revenue, COGS, gross margin, operating expenses, EBITDA for the month |
| "Which invoices are pending 3-way match?" | List of invoices awaiting PO/GRN matching with match status |
| "What's our cash runway?" | Months of cash remaining at current burn rate |
| "List all adjusting entries this month" | Journal entries posted during the current close cycle |
| "What fixed assets are fully depreciated?" | Assets with zero net book value |

Every answer includes **agent attribution** — you can see which agent (AP Processor, Treasury, Recon, etc.) provided the data, giving you confidence in the source.

---

## Report Scheduling

Set up automated reports that generate and deliver on a schedule. Navigate to **Reports > Scheduled Reports** in the sidebar, or use the API.

### Setting Up Common Finance Reports

#### Daily Cash Report
- **Schedule**: Every weekday at 6:00 AM
- **Content**: Bank balances, cash inflows/outflows, net position, cash runway update
- **Format**: PDF
- **Delivery**: Email to CFO + Slack #finance channel

#### Weekly P&L
- **Schedule**: Every Monday at 8:00 AM
- **Content**: Revenue, COGS, gross margin, opex by department, EBITDA, budget variance
- **Format**: Excel (with drill-down tabs)
- **Delivery**: Email to CFO + CEO

#### Monthly Close Package
- **Schedule**: 3rd business day of each month
- **Content**: Trial balance, P&L, balance sheet, cash flow statement, aging reports, reconciliation summary, adjusting entries log
- **Format**: PDF (consolidated) + Excel (detail tabs)
- **Delivery**: Email to CFO, CEO, and external auditor

### Report Scheduler UI

From the Report Scheduler page, you can:
1. **Create** a new scheduled report — select report type, configure schedule (cron expression or friendly picker), choose format (PDF/Excel), add delivery channels (email addresses, Slack channels, WhatsApp numbers).
2. **Manage** existing schedules — view upcoming runs, last run status, and delivery confirmations.
3. **Toggle** schedules on/off — pause a report without deleting its configuration.
4. **Run Now** — trigger an immediate run of any scheduled report for testing or ad-hoc needs.

---

## Workflow Templates for Finance

### Month-End Close (`month_end_close`)
**Agents involved**: Recon, AP Processor, AR Collections, Tax Compliance, Month-End Close, FP&A

**Steps**:
1. Recon Agent runs bank reconciliation for all accounts
2. AP Processor completes pending invoice processing
3. AR Collections updates receivables aging
4. Tax Compliance runs ITC reconciliation
5. Month-End Close Agent extracts trial balance and posts adjusting entries
6. FP&A Agent generates budget vs. actual variance report
7. HITL: CFO reviews close package and approves period lock
8. Period locked — no further postings allowed

### Daily Treasury (`daily_treasury`)
**Agents involved**: Treasury, Recon

**Steps**:
1. Treasury Agent fetches bank balances via AA
2. Recon Agent matches overnight transactions
3. Treasury Agent computes net cash position
4. Treasury Agent generates sweep recommendations
5. HITL: CFO approves sweeps above threshold
6. Treasury Agent generates daily cash report
7. Report delivered via email/Slack

### Tax Calendar (`tax_calendar`)
**Agents involved**: Tax Compliance

**Steps**:
1. Check upcoming filing deadlines (next 7 days)
2. Verify prerequisite data is ready (GSTR-1 data, TDS computations)
3. Prepare filing payloads
4. HITL: CFO reviews and approves filing
5. DSC signing and submission
6. Confirmation and receipt logging

### Invoice to Pay v3 (`invoice_to_pay_v3`)
**Agents involved**: AP Processor, Tax Compliance, Recon

**Steps**:
1. AP Processor: OCR extraction from invoice
2. Tax Compliance: GSTIN validation
3. AP Processor: Duplicate detection
4. AP Processor: 3-way match (invoice vs. PO vs. GRN)
5. Conditional: Amount > threshold? --> HITL approval
6. AP Processor: Schedule payment (early-payment discount optimization)
7. AP Processor: GL posting (idempotent)
8. AP Processor: Payment execution via PineLabs Plural
9. AP Processor: Send remittance advice

---

## Multi-Company Support

### For CA Firms
If you manage multiple client entities, AgenticOrg's multi-company support lets you operate all clients from a single login.

**Company Switcher**: A dropdown in the top navigation bar shows all companies you have access to. Select a company to switch context — all dashboards, agents, connectors, workflows, and reports instantly reflect that company's data.

**Setting Up Companies**:
1. Navigate to **Settings > Companies**
2. Click **Add Company**
3. Enter company name, GSTIN, PAN, and primary contact
4. Configure connectors for that company (Tally, banking, GSTN credentials)
5. Assign users and roles per company

**Isolated Data**: Each company has completely isolated data — agents, workflows, audit trails, and reports are separated. There is no data leakage between companies.

**Cross-Company Reporting**: For consolidated views (e.g., total AR across all clients, combined cash position), use the "All Companies" view in the company switcher. This aggregates data from all entities you have access to.

**RBAC per Company**: You can assign different roles per company. For example, you might be CFO for Client A but only have Auditor (read-only) access to Client B.

---

## HITL Approvals — When and Why Agents Escalate

Finance agents never make critical decisions autonomously. Here is when each agent escalates to the CFO:

| Agent | Escalation Trigger | What CFO Sees |
|-------|-------------------|---------------|
| AP Processor | Invoice > 5L, match delta > 2%, first-time vendor, GSTIN failure | Invoice, PO, GRN, match details, GSTIN validation, recommendation |
| AR Collections | Write-off request, payment plan modification | Customer history, aging, recommended action |
| Recon Agent | Break > 50K, unexplained items, match rate below threshold | Break details, categorization, recommended resolution |
| Tax Compliance | All GST filings, ITC mismatches, DSC expiry | Filing payload, deadline, compliance impact |
| Month-End Close | Adjusting entries > threshold, period lock | Close package, adjustments list, sign-off request |
| FP&A | Budget variance > tolerance, forecast revision | Variance analysis, revised forecast, impact assessment |
| Treasury | Sweep > threshold, runway < 3 months | Cash position, sweep details, runway projection |
| Expense Manager | Policy violations, missing receipts, suspected duplicates | Expense details, policy rule violated, employee history |
| Rev Rec | Multi-element arrangements, variable consideration | Contract analysis, allocation methodology, journal entries |
| Fixed Assets | Acquisitions > capitalization threshold, impairment | Asset details, valuation, depreciation schedule |

### Approval Workflow
1. Agent creates an approval request with full context
2. CFO receives notification (in-app + email/Slack)
3. CFO reviews the request in the Approvals queue (`/approvals`)
4. CFO clicks **Approve**, **Reject**, or **Override** (with notes)
5. Agent proceeds, stops, or adjusts based on decision
6. Every decision is logged in the WORM-compliant audit trail (7-year retention)

### Timeout Rules
If the CFO does not respond within the configured timeout (default: 4 hours), the request automatically escalates to the next person in the escalation chain (e.g., CEO). If no one responds within 24 hours, the task is marked as expired and flagged for review.

---

## Getting Started

1. **Log in** as CFO: `cfo@agenticorg.local` / `cfo123!` (demo) or your enterprise credentials
2. **Navigate** to `/dashboard/cfo` to see your financial KPIs
3. **Try NL Query**: Press Cmd+K and ask "What's my cash position?"
4. **Review Approvals**: Check `/approvals` for any pending HITL requests
5. **Set Up Reports**: Go to Reports > Scheduled Reports to configure your daily cash report
6. **Explore Workflows**: Navigate to Workflows to see the month-end close template

For questions or pilot setup, contact sales@agenticorg.ai.
