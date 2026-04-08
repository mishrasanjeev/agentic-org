# AgenticOrg CxO Platform PRD v5.0

**Document Version:** 5.0.0
**Date:** 2026-04-08
**Author:** Sanjeev Kumar, CEO, Edumatica
**Status:** Draft for Engineering Review
**Classification:** Internal - Confidential

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [CxO Role Definitions & Job Scope](#2-cxo-role-definitions--job-scope)
3. [Technical Architecture](#3-technical-architecture)
4. [Database Migrations](#4-database-migrations)
5. [API Endpoints](#5-api-endpoints)
6. [UI/UX Specifications](#6-uiux-specifications)
7. [Testing Plan](#7-testing-plan)
8. [Documentation Updates](#8-documentation-updates)
9. [Rollout Plan](#9-rollout-plan)
10. [Success Metrics](#10-success-metrics)
11. [Risk Register](#11-risk-register)
12. [Appendix](#12-appendix)
13. [Edge Cases & Negative Scenarios](#13-edge-cases--negative-scenarios)
14. [Non-Functional Requirements](#14-non-functional-requirements)
15. [Cross-Reference & Consistency Matrix](#15-cross-reference--consistency-matrix)

---

## 1. Executive Summary

### 1.1 Vision

Every CxO in a mid-to-large Indian enterprise gets a dedicated AI-powered command center with real-time data from their enterprise systems, powered by domain-specific AI agents that execute real tasks -- not demo screens with hardcoded data.

### 1.2 Current State (As-Is)

The platform has real infrastructure but demo product surface:

| Component | Status | Detail |
|-----------|--------|--------|
| Connectors | 54 native + 1000+ Composio | All connector classes exist with real API integrations (Tally TDL/XML, Darwinbox POST-based, GSTN GSP auth, AA consent flow, etc.) |
| Agents | 35 registered types | All 35 agent classes are empty shells that call `super().execute()` with no domain logic. The BaseAgent handles LLM reasoning, tool calling, and HITL but individual agents add nothing. |
| CFO Dashboard | Exists | Renders hardcoded demo data from `/kpis/cfo` endpoint. Shows AR/AP aging charts, P&L table, bank balances, tax calendar -- all static numbers. |
| CMO Dashboard | Exists | Renders hardcoded demo data from `/kpis/cmo` endpoint. Shows CAC, MQLs, ROAS, email metrics, social engagement -- all static numbers. |
| CHRO Dashboard | Does NOT exist | No page, no route, no API endpoint. |
| COO Dashboard | Does NOT exist | No page, no route, no API endpoint. |
| CBO Dashboard | Does NOT exist | No page, no route, no API endpoint. |
| CEO Dashboard | Partial | General `Dashboard.tsx` shows agent counts, domain distribution, approvals. No cross-CxO KPI aggregation. |
| Workflows | 23 YAML definitions | Real workflow engine (LangGraph-based) with scheduling, conditions, HITL. Workflow definitions exist but execution depends on agent logic that is empty. |
| Auth & RBAC | Complete | JWT auth, role-based route protection (admin, cfo, chro, cmo, coo, auditor), multi-tenant RLS. |
| KPI Caching | Does NOT exist | No Redis cache layer. KPI endpoints return hardcoded dicts. |
| Connector Config UI | Partial | Connector listing and creation pages exist. No per-tenant credential configuration flow. |

### 1.3 Target State (To-Be)

| Component | Target |
|-----------|--------|
| 6 CxO Dashboards | CEO, CFO, CHRO, CMO, COO, CBO -- all rendering real data from connected systems |
| 35+ Agents | Every agent has domain-specific logic: pre-processing, tool selection rules, validation, confidence computation, HITL conditions |
| 54+ Connectors | End-to-end wired: configured per tenant, health-monitored, credential-rotated |
| KPI Pipeline | Agent results -> PostgreSQL (historical) -> Redis (current) -> API -> Dashboard |
| Connector Config | UI flow for entering credentials, testing connection, auto-discovering data |
| Board Reporting | Automated MIS pack generation (PDF) from aggregated KPIs |
| Workflow Scheduling | All 23+ workflows runnable on cron with real agent execution |

### 1.4 Target Customers

| Company | Industry | Size | Primary CxO Need |
|---------|----------|------|-------------------|
| Pine Labs | Fintech / Payments | 2000+ employees | CFO (treasury, reconciliation), COO (merchant ops) |
| Cred | Consumer Fintech | 800+ employees | CMO (growth marketing), CFO (credit ops) |
| PolicyBazaar | Insurtech | 5000+ employees | COO (customer support), CHRO (large workforce) |
| Zerodha | Broking / Fintech | 1500+ employees | CFO (regulatory compliance), CBO (SEBI filings) |
| Razorpay | Payments | 3000+ employees | COO (merchant support), CFO (reconciliation at scale) |
| PhonePe | Digital Payments | 5000+ employees | CHRO (rapid hiring), CMO (user acquisition) |
| Freshworks | SaaS | 5000+ employees | CMO (PLG marketing), COO (global support) |
| Zoho | SaaS | 12000+ employees | CHRO (large org management), COO (multi-product ops) |

### 1.5 Timeline

| Phase | Duration | Focus |
|-------|----------|-------|
| Phase 1 | Weeks 1-4 | Replace hardcoded KPIs (CFO, CMO), build CHRO + COO dashboards, KPI cache, top 10 agent logic |
| Phase 2 | Weeks 5-8 | Build CEO + CBO dashboards, all workflows with scheduling, remaining 25 agent logic, connector health monitoring |
| Phase 3 | Weeks 9-12 | Performance optimization, security hardening, documentation, customer pilots, production monitoring |

### 1.6 Key Principles

1. **No hardcoded data anywhere.** Every number on every dashboard must trace back to a connector call or agent computation.
2. **Graceful degradation.** When a connector is down, the dashboard must show the last-known value with a staleness indicator, not crash.
3. **HITL on everything risky.** Any financial posting, employee action, or compliance filing must require human approval above configurable thresholds.
4. **India-first.** GST, TDS, EPFO, ESI, PT, MCA, DPDPA, Account Aggregator -- these are not optional add-ons, they are core features.
5. **Open source only.** No LangSmith, no proprietary SaaS dependencies, no AGPL-licensed components.

---

## 2. CxO Role Definitions & Job Scope

### 2.1 CEO / Admin

The CEO/Admin role serves as the cross-departmental command center with visibility into every function and the authority to configure the entire platform.

#### 2.1.1 Job Functions

**A. Company-Wide KPI Overview**

- **Description:** Single-pane view of the top 4-5 KPIs from each department (Finance, HR, Marketing, Operations, Back Office).
- **Agents Responsible:**
  - `fpa_agent` (Finance KPIs: revenue, burn rate, runway)
  - `talent_acquisition` (HR KPIs: headcount, attrition)
  - `campaign_pilot` (Marketing KPIs: CAC, pipeline)
  - `support_triage` (Ops KPIs: ticket volume, MTTR)
  - `compliance_guard` (Back Office KPIs: compliance score)
- **Connectors Required:**
  - `tally.get_trial_balance` -- Revenue and P&L data
  - `darwinbox.get_org_chart` -- Headcount data
  - `hubspot.list_deals` -- Pipeline value
  - `zendesk.get_sla_status` -- Support metrics
  - `mca_portal.fetch_company_master_data` -- Compliance status
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Monthly Revenue | tally.get_trial_balance -> Revenue group | Daily | INR |
  | Cash Runway | banking_aa.check_account_balance / fpa_agent burn rate | Daily | Months |
  | Total Headcount | darwinbox.get_org_chart -> count | Daily | Number |
  | Monthly Attrition Rate | darwinbox -> terminations / headcount | Monthly | Percentage |
  | Pipeline Value | hubspot.list_deals -> sum(amount) | Hourly | INR |
  | CAC | campaign_pilot -> total_spend / new_customers | Weekly | INR |
  | Open Support Tickets | zendesk -> open ticket count | Real-time | Number |
  | MTTR (Mean Time To Resolve) | pagerduty.list_incidents -> avg resolution time | Hourly | Minutes |
  | Compliance Score | compliance_guard -> weighted compliance checklist | Weekly | 0-100 |
  | Pending Approvals | internal DB -> pending HITL count | Real-time | Number |
- **HITL Conditions:**
  - Any invoice > INR 5,00,000 requires CEO escalation
  - Employee termination requires CEO approval (if C-level or VP)
  - Budget reallocation > 10% of department budget
  - Security incident classified as P1
- **Workflows:**
  - `daily_ceo_briefing`: Runs at 8:00 AM IST. Aggregates top KPIs from all departments. Generates a 1-page summary. Delivers via email and Slack.
  - `weekly_board_prep`: Runs every Friday at 5:00 PM IST. Compiles weekly metrics into a board-ready MIS pack. Generates PDF via report engine.
  - `escalation_router`: Event-driven. Routes HITL requests from any department to the appropriate CxO. If unresolved in 4 hours, escalates to CEO.
- **Error Handling:**
  - If any connector fails during daily briefing: include "Data unavailable" for that section with last-known timestamp. Do not block the entire briefing.
  - If all connectors fail: send alert to admin, retry in 15 minutes, max 3 retries.
- **Test Cases:**
  - UNIT: `test_ceo_kpi_aggregation_with_all_sources` -- All connectors return valid data, verify aggregation math.
  - UNIT: `test_ceo_kpi_aggregation_partial_failure` -- 2 of 5 connectors fail, verify remaining KPIs render with fallback.
  - UNIT: `test_ceo_escalation_threshold_invoice` -- Invoice of INR 6,00,000 triggers CEO HITL.
  - UNIT: `test_ceo_escalation_threshold_below` -- Invoice of INR 4,00,000 does not trigger CEO HITL.
  - INTEGRATION: `test_ceo_dashboard_api_to_agent_pipeline` -- API call triggers agent execution, agent calls connectors, results cached, API returns real data.
  - E2E: `test_ceo_dashboard_loads_all_quadrants` -- Page loads, all 4 quadrants render, clicking each quadrant navigates to respective CxO dashboard.
  - E2E: `test_ceo_alert_banner_shows_critical_items` -- Create a pending HITL item, verify alert banner appears.

**B. Cross-Departmental Agent Observatory**

- **Description:** Real-time feed of all agent actions across the organization. Shows agent name, task type, status, confidence, duration, cost.
- **Agents Responsible:** None (reads from `agent_task_results` table)
- **Connectors Required:** None (internal database query)
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Total Agent Executions (24h) | agent_task_results.count(last 24h) | Real-time | Number |
  | Average Confidence | agent_task_results.avg(confidence) | Hourly | 0-1.0 |
  | HITL Trigger Rate | agent_task_results.count(hitl) / total | Hourly | Percentage |
  | Average Latency | agent_task_results.avg(duration_ms) | Hourly | Milliseconds |
  | Total LLM Cost (24h) | agent_task_results.sum(cost) | Hourly | USD |
- **Test Cases:**
  - UNIT: `test_observatory_query_filters_by_tenant` -- Verify tenant isolation in observatory queries.
  - E2E: `test_observatory_real_time_feed` -- Trigger an agent, verify the feed updates within 5 seconds.

**C. Approval Escalations**

- **Description:** Unified inbox for all HITL requests across all departments. CEO sees only items that have been escalated past the department CxO.
- **Test Cases:**
  - E2E: `test_ceo_approval_inbox_shows_escalated_items` -- Create an approval that exceeded the 4-hour CxO timeout, verify it appears in CEO inbox.
  - E2E: `test_ceo_approval_approve_flow` -- Approve an item, verify status updates to "approved" and downstream workflow resumes.
  - E2E: `test_ceo_approval_reject_flow` -- Reject an item, verify status updates to "rejected" and notification sent to originating agent.

**D. Board Reporting (Automated MIS Pack)**

- **Description:** Generate a monthly MIS pack containing P&L, Balance Sheet, headcount summary, marketing pipeline, operational metrics, and compliance status.
- **Agents Responsible:** `fpa_agent` (financial compilation), `close_agent` (month-end data)
- **Connectors Required:**
  - `tally.get_trial_balance`, `tally.get_ledger_balance` -- Financial data
  - `darwinbox.get_org_chart` -- HR data
  - `hubspot.list_deals`, `hubspot.get_campaign_analytics` -- Marketing data
- **Workflow:** `monthly_board_pack`
  1. Trigger: 1st business day of each month, 10:00 AM IST
  2. Step 1: `fpa_agent` generates P&L and Balance Sheet from Tally data
  3. Step 2: `talent_acquisition` generates headcount summary from Darwinbox
  4. Step 3: `campaign_pilot` generates marketing summary from HubSpot
  5. Step 4: `support_triage` generates ops summary from Zendesk/PagerDuty
  6. Step 5: `compliance_guard` generates compliance summary from MCA/EPFO
  7. Step 6: Report engine compiles all sections into a single PDF
  8. Step 7: HITL -- CEO reviews and approves the pack
  9. Step 8: Deliver via email to board members and archive in S3
- **Test Cases:**
  - INTEGRATION: `test_board_pack_generation_all_sections` -- All 5 sections generate successfully, PDF is valid.
  - INTEGRATION: `test_board_pack_partial_failure` -- 1 section fails, pack generates with "Data unavailable" placeholder.
  - E2E: `test_board_pack_approval_and_delivery` -- Generate pack, approve, verify email delivery.

**E. Strategic Planning Support**

- **Description:** On-demand competitive intelligence and market analysis powered by LLM reasoning over connected data.
- **Agents Responsible:** `fpa_agent` (financial modeling), `crm_intelligence` (market data)
- **Test Cases:**
  - UNIT: `test_strategic_analysis_prompt_construction` -- Verify the prompt includes relevant financial context.

**F. Organization Chart with Agent-to-Human Mapping**

- **Description:** Visual org chart showing departments, reporting hierarchy, and which AI agents are assigned to each function.
- **Connectors Required:** `darwinbox.get_org_chart`
- **Test Cases:**
  - E2E: `test_org_chart_renders_departments` -- Org chart page loads, departments are visible.
  - E2E: `test_org_chart_shows_agent_mapping` -- Each department node shows assigned agents.

**G. Cost Center Management**

- **Description:** Per-department AI spend tracking (LLM tokens, connector API calls, compute).
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | LLM Cost per Department | agent_task_results grouped by domain | Daily | USD |
  | Connector API Calls per Department | audit_log grouped by domain | Daily | Number |
  | Total Platform Cost (MTD) | sum of all costs | Daily | USD |
- **Test Cases:**
  - UNIT: `test_cost_center_aggregation_by_domain` -- Verify correct grouping and summation.

**H. Risk Dashboard**

- **Description:** Aggregated risk view covering compliance gaps, security incidents, financial exposures, and audit findings.
- **Agents Responsible:** `risk_sentinel`, `compliance_guard`
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Overall Risk Score | risk_sentinel -> weighted_risk | Weekly | 0-100 |
  | Open Compliance Gaps | compliance_guard -> open_items | Daily | Number |
  | Security Incidents (30d) | pagerduty -> P1/P2 incidents | Real-time | Number |
  | Overdue Tax Filings | gstn.check_filing_status | Daily | Number |
- **Test Cases:**
  - UNIT: `test_risk_score_computation` -- Verify weighted risk calculation from multiple sources.
  - E2E: `test_risk_dashboard_renders_all_categories` -- All 4 risk categories display on the dashboard.

---

### 2.2 CFO (Chief Financial Officer)

#### 2.2.1 Treasury Management

- **Description:** Real-time bank balances via Account Aggregator, cash flow forecasting, FD/investment tracking, and liquidity management.
- **Agents Responsible:**
  - `fpa_agent` -- Cash flow forecasting, runway calculation
  - `recon_agent` -- Balance verification
- **Connectors Required:**
  - `banking_aa.check_account_balance` -- Real-time balances (RBI-compliant AA flow)
  - `banking_aa.fetch_bank_statement` -- Statement for forecasting
  - `banking_aa.get_transaction_list` -- Transaction details
  - `tally.get_ledger_balance` -- Book balance for verification
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Total Cash Balance | banking_aa.check_account_balance -> sum(all accounts) | Real-time | INR | SUM(account.balance for all configured accounts) |
  | Cash Runway | Total Cash / Monthly Burn Rate | Daily | Months | total_cash / avg_monthly_opex(last_3_months) |
  | Monthly Burn Rate | tally -> Total OPEX for current month | Daily | INR | SUM(opex_ledger_groups for current month) |
  | FD Maturity Calendar | tally.get_ledger_balance -> FD accounts | Weekly | INR + Date | List of FDs with maturity dates and amounts |
  | Net Cash Position | Cash + FDs - Outstanding Payables | Daily | INR | total_cash + total_fd - total_payables |
  | Cash Flow Forecast (12mo) | fpa_agent -> time series forecast | Weekly | INR[] | Rolling 12-month forecast based on 6-month actuals trend |
- **HITL Conditions:**
  - Cash balance drops below 3 months runway -> Alert CFO
  - FD maturity within 7 days without renewal instructions -> Alert CFO
  - Discrepancy > INR 10,000 between AA balance and book balance -> Escalate
- **Workflows:**
  - `daily_treasury` (existing): Runs at 9:30 AM IST. Fetches all bank balances via AA. Compares with Tally book balances. Flags discrepancies. Updates cash flow forecast.
    1. `banking_aa.check_account_balance` for each configured account
    2. `tally.get_ledger_balance` for bank ledger groups
    3. `recon_agent` compares and flags breaks
    4. `fpa_agent` updates 12-month cash flow forecast
    5. Notify CFO via Slack with daily treasury summary
- **Error Handling:**
  - If AA consent has expired: Show last-known balance with "Consent expired" badge. Trigger consent renewal flow.
  - If AA API is down: Fallback to Tally book balance with "Estimated" badge. Retry AA every 30 minutes.
  - If Tally bridge is disconnected: Show only AA balance. Flag "Book balance unavailable" in treasury tab.
- **Test Cases:**
  - UNIT: `test_treasury_balance_aggregation` -- 4 accounts with different currencies, verify correct INR conversion and summation.
  - UNIT: `test_treasury_runway_calculation` -- Given cash=1.5Cr and burn=18.5L/mo, verify runway = 8.1 months.
  - UNIT: `test_treasury_runway_low_alert` -- Runway < 3 months triggers HITL.
  - UNIT: `test_treasury_balance_discrepancy` -- AA shows 1,45,00,000 and Tally shows 1,44,85,000 (delta 15,000 > threshold 10,000) -> escalate.
  - INTEGRATION: `test_treasury_aa_to_dashboard` -- Configure AA connector, fetch balance, verify it appears on CFO dashboard.
  - E2E: `test_treasury_tab_renders_balances` -- CFO dashboard loads, Treasury tab shows bank balance cards.
  - E2E: `test_treasury_tab_cash_flow_chart` -- 12-month forecast chart renders with correct data points.

#### 2.2.2 Accounts Payable

- **Description:** End-to-end invoice processing from receipt to payment, including OCR extraction, GSTIN validation, 3-way matching (PO-GRN-Invoice), vendor payment scheduling, and early payment discount capture.
- **Agents Responsible:**
  - `ap_processor` -- Full AP pipeline (extract, validate, match, schedule, post, notify)
- **Connectors Required:**
  - `tally.post_voucher` -- Post journal entry
  - `tally.get_ledger_balance` -- Verify GL balances
  - `gstn.generate_einvoice_irn` -- E-invoice validation
  - `banking_aa.fetch_bank_statement` -- Payment verification
  - `sendgrid.send_email` -- Remittance advice
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | DPO (Days Payable Outstanding) | tally -> AP balance / (COGS/365) | Daily | Days | avg_ap_balance / (annual_cogs / 365) |
  | AP Aging Buckets | tally -> AP ledger by date | Daily | INR | Grouped by 0-30, 31-60, 61-90, 90+ days |
  | Invoices Processed (MTD) | agent_task_results where agent=ap_processor | Real-time | Number | COUNT(tasks where status=completed) |
  | Auto-Match Rate | ap_processor -> matched / total | Daily | Percentage | matched_invoices / total_invoices * 100 |
  | Pending Invoices | ap_processor -> status=pending | Real-time | Number | COUNT(tasks where status=pending) |
  | Early Payment Savings | ap_processor -> discount_captured | Monthly | INR | SUM(discount_amount where payment_date < discount_deadline) |
- **HITL Conditions:**
  - Invoice amount > INR 5,00,000
  - 3-way match delta > 2% of PO amount
  - Vendor risk score > 7 (out of 10)
  - GSTIN validation returns ambiguous result
  - Duplicate invoice detected
  - Confidence < 0.88
- **Workflows:**
  - `invoice_to_pay_v3` (existing):
    1. Invoice received (email attachment or upload)
    2. `ap_processor` STEP 1: OCR extract invoice fields
    3. `ap_processor` STEP 2: Validate GSTIN via `gstn.generate_einvoice_irn`
    4. `ap_processor` STEP 3: 3-way match (PO from Tally, GRN from Tally, Invoice)
    5. Condition: If match_delta > 2% -> HITL (CFO reviews mismatch)
    6. `ap_processor` STEP 4: Schedule payment with discount optimization
    7. `ap_processor` STEP 5: Post journal entry via `tally.post_voucher`
    8. `ap_processor` STEP 6: Send remittance advice via `sendgrid.send_email`
- **Error Handling:**
  - OCR extraction confidence < 70%: Flag for manual entry, do not proceed.
  - Tally bridge disconnected: Queue invoice for later processing. Notify CFO.
  - GSTN API timeout: Retry 3 times with exponential backoff. If still fails, HITL with "GSTIN validation failed" context.
  - Duplicate detected: Auto-reject, notify AP team with original invoice reference.
- **Test Cases:**
  - UNIT: `test_ap_ocr_extraction_all_fields` -- Mock OCR returns all required fields, verify extraction.
  - UNIT: `test_ap_ocr_extraction_missing_field` -- OCR misses invoice_date, verify status=incomplete.
  - UNIT: `test_ap_gstin_validation_valid` -- GSTIN is valid, verify proceed to matching.
  - UNIT: `test_ap_gstin_validation_invalid` -- GSTIN is invalid, verify status=gstin_invalid and STOP.
  - UNIT: `test_ap_three_way_match_within_tolerance` -- PO=100000, GRN=100000, Invoice=101500 (1.5% < 2%) -> matched.
  - UNIT: `test_ap_three_way_match_over_tolerance` -- PO=100000, Invoice=103000 (3% > 2%) -> HITL triggered.
  - UNIT: `test_ap_duplicate_detection` -- Same invoice_id+vendor_id submitted twice -> status=duplicate.
  - UNIT: `test_ap_hitl_amount_threshold` -- Invoice=600000 (> 500000 threshold) -> HITL triggered.
  - UNIT: `test_ap_early_payment_discount` -- Invoice with 2/10 net 30 terms, verify discount date calculation.
  - INTEGRATION: `test_ap_full_pipeline_happy_path` -- Invoice -> OCR -> validate -> match -> schedule -> post -> notify. All steps complete.
  - INTEGRATION: `test_ap_pipeline_with_hitl` -- Invoice over threshold triggers HITL, approve, verify pipeline resumes.
  - E2E: `test_ap_tab_shows_aging_chart` -- CFO dashboard AP/AR tab renders aging buckets chart.
  - E2E: `test_ap_tab_shows_pending_invoices` -- Pending invoices table shows correct count and details.

#### 2.2.3 Accounts Receivable

- **Description:** Customer invoice generation, aging analysis, collection automation with escalating reminders (Day 30/60/90), and bad debt provisioning.
- **Agents Responsible:**
  - `ar_collections` -- Collection automation, aging analysis, bad debt provisioning
- **Connectors Required:**
  - `tally.get_ledger_balance` -- AR ledger balances
  - `tally.post_voucher` -- Write-off journal entries
  - `sendgrid.send_email` -- Collection reminder emails
  - `whatsapp.send_message` -- WhatsApp reminders for Indian customers
  - `hubspot.update_contact` -- Update customer payment status in CRM
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | DSO (Days Sales Outstanding) | tally -> AR balance / (Revenue/365) | Daily | Days | avg_ar_balance / (annual_revenue / 365) |
  | AR Aging Buckets | tally -> AR ledger by date | Daily | INR | Grouped by 0-30, 31-60, 61-90, 90+ days |
  | Collection Efficiency | ar_collections -> collected / due | Monthly | Percentage | total_collected / total_due * 100 |
  | Overdue Amount | tally -> AR entries past due date | Daily | INR | SUM(amount where due_date < today) |
  | Bad Debt Provision | ar_collections -> provision estimate | Monthly | INR | SUM(amount * provision_rate by aging bucket) |
- **HITL Conditions:**
  - Customer overdue > 90 days and amount > INR 1,00,000 -> CFO decides: pursue, write-off, or escalate to legal
  - Write-off amount > INR 50,000 -> CFO approval required
  - Customer disputes an invoice -> CFO reviews before proceeding
- **Workflows:**
  - `ar_collection_cycle`:
    1. Daily: Scan AR ledger for newly overdue invoices
    2. Day 1 overdue: Send polite reminder email via SendGrid
    3. Day 30 overdue: Send firm reminder + WhatsApp follow-up
    4. Day 60 overdue: Escalate to AR manager, send final notice
    5. Day 90 overdue: HITL -> CFO decides next action
    6. If CFO approves write-off: Post journal entry via `tally.post_voucher`
    7. Update CRM record via `hubspot.update_contact`
- **Test Cases:**
  - UNIT: `test_ar_aging_bucket_calculation` -- Given invoices with various due dates, verify correct bucket assignment.
  - UNIT: `test_ar_collection_reminder_day30` -- Invoice 30 days overdue triggers firm reminder.
  - UNIT: `test_ar_bad_debt_provision_calculation` -- 0-30d: 0%, 31-60d: 5%, 61-90d: 25%, 90+d: 50% provision rates.
  - UNIT: `test_ar_write_off_hitl_threshold` -- Write-off of 60,000 (> 50,000) triggers HITL.
  - INTEGRATION: `test_ar_collection_email_delivery` -- Overdue invoice triggers SendGrid email, verify delivery.
  - E2E: `test_ar_tab_shows_aging_chart` -- CFO dashboard shows AR aging chart with correct buckets.
  - E2E: `test_ar_tab_shows_collection_trend` -- Collection efficiency trend line renders.

#### 2.2.4 Bank Reconciliation

- **Description:** Automated bank-to-book matching via Account Aggregator, exception flagging, break escalation, and BRS report generation.
- **Agents Responsible:**
  - `recon_agent` -- Auto-matching, exception flagging, BRS report
- **Connectors Required:**
  - `banking_aa.fetch_bank_statement` -- Bank transactions
  - `banking_aa.get_transaction_list` -- Detailed transaction data
  - `tally.get_ledger_balance` -- Book entries (bank ledger)
  - `tally.get_trial_balance` -- Trial balance verification
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Auto-Match Rate | recon_agent -> matched / total | Daily | Percentage | auto_matched_txns / total_bank_txns * 100 |
  | Unmatched Items | recon_agent -> unmatched count | Daily | Number | COUNT(bank_txns where match_status != matched) |
  | Break Amount | recon_agent -> sum of breaks | Daily | INR | SUM(abs(bank_balance - book_balance)) per account |
  | Items Outstanding > 30 Days | recon_agent -> old items count | Daily | Number | COUNT(unmatched where age > 30 days) |
  | Reconciliation Status | recon_agent -> overall status | Daily | Enum | reconciled / partial / unreconciled |
- **HITL Conditions:**
  - Break amount > INR 50,000 or > 0.01% of daily volume
  - Items outstanding > 30 days exceeding 5 items
  - Confidence < 0.95 for any match
- **Workflows:**
  - `bank_recon_daily` (existing): See workflow YAML definition. Runs weekdays at 9 AM IST.
- **Test Cases:**
  - UNIT: `test_recon_exact_match` -- Bank txn of 50,000 on 2026-04-01 with ref "INV-001" matches book entry exactly.
  - UNIT: `test_recon_fuzzy_match` -- Bank txn of 49,950 matches book entry of 50,000 (within tolerance).
  - UNIT: `test_recon_no_match` -- Bank txn with no corresponding book entry -> flagged as unmatched.
  - UNIT: `test_recon_break_escalation` -- Break of 60,000 (> 50,000 threshold) triggers HITL.
  - UNIT: `test_recon_old_outstanding_flag` -- Item unmatched for 35 days -> flagged.
  - INTEGRATION: `test_recon_daily_workflow_execution` -- Full workflow runs with mock AA and Tally data.
  - E2E: `test_recon_brs_report_download` -- BRS report generates and is downloadable from dashboard.

#### 2.2.5 Tax Compliance

- **Description:** Complete India tax compliance: GST (GSTR-1, GSTR-3B, GSTR-9, ITC reconciliation, e-invoice, e-way bill), TDS (26Q, 24Q, Form 16/16A, 26AS reconciliation), advance tax estimation, and income tax return preparation.
- **Agents Responsible:**
  - `tax_compliance` -- All tax filing operations, ITC reconciliation, TDS computation
- **Connectors Required:**
  - `gstn.fetch_gstr2a` -- Inbound supplies for ITC reconciliation
  - `gstn.push_gstr1_data` -- Outbound supplies filing
  - `gstn.file_gstr3b` -- Monthly summary return (DSC-signed)
  - `gstn.file_gstr9` -- Annual return (DSC-signed)
  - `gstn.generate_eway_bill` -- E-way bill generation
  - `gstn.generate_einvoice_irn` -- E-invoice IRN generation
  - `gstn.check_filing_status` -- Filing status verification
  - `gstn.get_compliance_notice` -- Compliance notices
  - `income_tax_india.file_tds_return` -- TDS return filing
  - `income_tax_india.get_26as` -- 26AS data for reconciliation
  - `income_tax_india.compute_advance_tax` -- Advance tax estimation
  - `tally.generate_gst_report` -- GST data from books
  - `tally.get_trial_balance` -- Financial data for ITR
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | GST Filing Calendar | gstn.check_filing_status | Daily | Status[] | 12-month calendar with filed/pending/overdue per return type |
  | ITC Mismatch Amount | gstn.fetch_gstr2a vs tally.generate_gst_report | Monthly | INR | ABS(itc_claimed - itc_available_per_2a) |
  | TDS Quarterly Status | income_tax_india -> quarterly filing status | Quarterly | Status | filed / pending / overdue per quarter |
  | Advance Tax Due | income_tax_india.compute_advance_tax | Quarterly | INR | Estimated tax - tax already paid |
  | Compliance Risk Score | tax_compliance -> weighted compliance index | Weekly | 0-100 | Weighted score based on filing status, penalties, notices |
- **HITL Conditions:**
  - Any GST filing (GSTR-3B, GSTR-9) requires CFO approval before DSC signing
  - ITC mismatch > INR 10,000 requires review
  - TDS computation variance > 5% from previous quarter
  - Any compliance notice received -> immediate CFO alert
  - Advance tax installment due within 7 days -> CFO reminder
- **Workflows:**
  - `gstr_filing_monthly` (existing):
    1. Day 1-5: `tax_compliance` extracts GST data from Tally
    2. Day 5-10: `tax_compliance` reconciles with GSTR-2A from GSTN
    3. Day 10-15: `tax_compliance` prepares GSTR-3B draft
    4. Day 15: HITL -> CFO reviews and approves
    5. Day 15-20: `tax_compliance` files GSTR-3B via `gstn.file_gstr3b` (DSC-signed)
    6. Post-filing: Verify filing status, archive return
  - `tds_quarterly_filing` (existing):
    1. End of quarter: `tax_compliance` computes TDS from payroll and vendor payments
    2. Prepare Form 26Q/24Q
    3. HITL -> CFO reviews
    4. File via `income_tax_india.file_tds_return`
    5. Generate Form 16/16A for employees
  - `tax_calendar`:
    1. Daily: Check upcoming filing deadlines
    2. 7 days before due: Alert CFO via Slack
    3. 3 days before due: Send email reminder with preparation checklist
    4. On due date: If not filed, send urgent alert
- **Test Cases:**
  - UNIT: `test_gst_itc_reconciliation` -- Given Tally ITC claims and GSTR-2A data, verify mismatch calculation.
  - UNIT: `test_gst_itc_mismatch_hitl` -- ITC mismatch of 15,000 (> 10,000) triggers HITL.
  - UNIT: `test_tds_computation_salary` -- Given salary of 12,00,000/year, verify TDS = correct slab amount.
  - UNIT: `test_tds_computation_vendor` -- Given vendor payment of 50,000 for professional services, verify TDS @ 10%.
  - UNIT: `test_advance_tax_estimation` -- Given YTD income, verify advance tax installment calculation.
  - UNIT: `test_tax_calendar_upcoming_alert` -- Filing due in 5 days triggers alert.
  - INTEGRATION: `test_gstr3b_filing_workflow` -- Full GSTR-3B workflow with mock GSTN responses.
  - E2E: `test_tax_tab_shows_calendar` -- CFO dashboard Tax tab shows 12-month filing calendar with correct statuses.
  - E2E: `test_tax_tab_shows_itc_mismatch` -- ITC reconciliation section shows mismatch amount.

#### 2.2.6 Financial Close

- **Description:** Month-end close automation including journal entries, accruals, provisions, depreciation, intercompany reconciliation, trial balance validation, and P&L/Balance Sheet generation.
- **Agents Responsible:**
  - `close_agent` -- Month-end close orchestration, checklist management
  - `fpa_agent` -- P&L and Balance Sheet generation
- **Connectors Required:**
  - `tally.post_voucher` -- Journal entries
  - `tally.get_trial_balance` -- Trial balance
  - `tally.get_ledger_balance` -- Ledger verifications
  - `tally.export_tally_xml_data` -- Full data export
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Close Completion % | close_agent -> completed_items / total_items | Real-time | Percentage | Checklist completion |
  | Pending Close Items | close_agent -> incomplete items | Real-time | Number | COUNT(checklist items where status != done) |
  | Estimated Close Date | close_agent -> projection | Daily | Date | Based on completion velocity |
  | Trial Balance Variance | tally.get_trial_balance -> debit vs credit | Daily | INR | ABS(total_debit - total_credit) |
  | Close Duration Trend | close_agent -> historical close days | Monthly | Days | Number of days from month-end to close completion |
- **HITL Conditions:**
  - Trial balance does not balance (debit != credit) -> Escalate immediately
  - Any manual journal entry > INR 1,00,000 requires CFO approval
  - Depreciation variance > 5% from previous month
  - Intercompany balance mismatch > INR 0
- **Workflows:**
  - `month_end_close` (existing):
    1. Day 1: `close_agent` creates month-end checklist (12-15 items)
    2. Day 1-2: Post accrual entries (salary, rent, utilities)
    3. Day 2-3: Post depreciation entries
    4. Day 3: Post provision entries (bad debt, warranty, tax)
    5. Day 3-4: Intercompany reconciliation
    6. Day 4: Trial balance validation
    7. Day 4: P&L and Balance Sheet generation
    8. Day 5: HITL -> CFO reviews and signs off
    9. Day 5: Lock period in Tally, archive close package
- **Test Cases:**
  - UNIT: `test_close_checklist_generation` -- Verify all 12 items are created.
  - UNIT: `test_close_accrual_entries` -- Given salary accrual of 15L, verify correct journal entry format.
  - UNIT: `test_close_depreciation_calculation` -- Given asset of 10L with 10% SLM, verify monthly depreciation = 8,333.
  - UNIT: `test_close_trial_balance_validation_pass` -- Debit = Credit -> pass.
  - UNIT: `test_close_trial_balance_validation_fail` -- Debit != Credit -> HITL triggered.
  - INTEGRATION: `test_close_full_workflow` -- All close steps execute in sequence with mock Tally.
  - E2E: `test_close_tab_shows_checklist` -- CFO dashboard Close tab shows checklist with completion percentage.

#### 2.2.7 Budgeting & FP&A

- **Description:** Budget vs actual variance analysis, rolling forecasts, department-wise cost tracking, revenue forecasting, and unit economics calculation.
- **Agents Responsible:**
  - `fpa_agent` -- Budget analysis, forecasting, variance computation
- **Connectors Required:**
  - `tally.get_trial_balance` -- Actual figures
  - `tally.get_ledger_balance` -- Department-wise costs
  - `hubspot.list_deals` -- Revenue pipeline for forecasting
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Budget Variance (Total) | fpa_agent -> actual - budget | Monthly | INR | total_actual_spend - total_budget |
  | Budget Variance by Dept | fpa_agent -> dept breakdown | Monthly | INR% | (dept_actual - dept_budget) / dept_budget * 100 |
  | Revenue Forecast (Q+1) | fpa_agent -> forecast model | Weekly | INR | Based on pipeline + historical trend |
  | Forecast Accuracy | fpa_agent -> previous forecast vs actual | Monthly | Percentage | 1 - ABS(forecast - actual) / actual |
  | Unit Economics (CAC, LTV, LTV/CAC) | fpa_agent -> computed metrics | Monthly | INR, Ratio | LTV = avg_revenue_per_customer * avg_lifetime; CAC = total_sales_marketing / new_customers |
- **HITL Conditions:**
  - Department budget variance > 15% -> Alert CFO
  - Revenue forecast accuracy < 80% -> Review model assumptions
  - CAC increase > 20% MoM -> Review with CMO
- **Test Cases:**
  - UNIT: `test_budget_variance_calculation` -- Budget=50L, Actual=58L, Variance=+16% -> alert.
  - UNIT: `test_revenue_forecast_model` -- Given 6 months of actuals, verify forecast produces 12-month projection.
  - UNIT: `test_unit_economics_ltv_cac` -- Given LTV=50000, CAC=15000, verify LTV/CAC=3.33.
  - E2E: `test_budget_tab_shows_waterfall_chart` -- Budget tab shows budget vs actual waterfall chart.

#### 2.2.8 Audit Support

- **Description:** Automated audit trail retrieval, document compilation, and auditor query response.
- **Agents Responsible:**
  - `fpa_agent` -- Document compilation, query response
- **Connectors Required:**
  - `tally.export_tally_xml_data` -- Full data export for auditors
  - `s3.upload_file` -- Archive documents
- **Test Cases:**
  - UNIT: `test_audit_trail_retrieval` -- Given a date range, verify complete transaction log is retrieved.
  - INTEGRATION: `test_audit_document_compilation` -- Compile all closing documents for a month into a single archive.

#### 2.2.9 Payroll Accounting

- **Description:** Payroll journal posting, PF/ESI/PT computation and posting, TDS on salary.
- **Agents Responsible:**
  - `payroll_engine` (shared with CHRO) -- Payroll computation
  - `ap_processor` -- Journal entry posting
- **Connectors Required:**
  - `darwinbox.run_payroll` or `keka.run_payroll` -- Payroll data
  - `tally.post_voucher` -- Payroll journal entry
  - `epfo.file_ecr` -- PF filing
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Payroll Cost (Monthly) | darwinbox.run_payroll -> total CTC | Monthly | INR |
  | Statutory Dues (PF+ESI+PT) | payroll_engine -> statutory computation | Monthly | INR |
  | Payroll Accuracy | payroll_engine -> variance from previous month | Monthly | Percentage |
- **HITL Conditions:**
  - Payroll total variance > 5% from previous month -> CFO review
  - Any payroll correction > INR 10,000 -> CFO approval
- **Test Cases:**
  - UNIT: `test_payroll_journal_entry_format` -- Given payroll summary, verify correct journal entry (Salary Dr, Bank Cr, PF Payable Cr, TDS Payable Cr).
  - UNIT: `test_pf_computation` -- Given basic salary of 15,000, verify employee PF = 1,800 (12%), employer PF = 1,800.
  - UNIT: `test_esi_computation` -- Given gross salary of 18,000, verify ESI = 135 (0.75%), employer ESI = 585 (3.25%).

#### 2.2.10 Board Reporting

- **Description:** Monthly MIS pack generation, investor updates, and financial model updates.
- **Agents Responsible:**
  - `fpa_agent` -- Financial model and MIS compilation
- **Connectors Required:**
  - All finance connectors for data aggregation
  - `s3.upload_file` -- Archive reports
  - `sendgrid.send_email` -- Deliver to board members
- **Test Cases:**
  - INTEGRATION: `test_mis_pack_generation` -- Full MIS pack with P&L, BS, cash flow, KPIs.
  - E2E: `test_mis_pack_download` -- Generate and download MIS pack PDF from dashboard.

---

### 2.3 CHRO (Chief Human Resources Officer)

#### 2.3.1 Recruitment

- **Description:** End-to-end recruitment from job posting to offer letter generation, including resume screening, interview scheduling, candidate scoring, and background verification.
- **Agents Responsible:**
  - `talent_acquisition` -- Recruitment pipeline management, candidate scoring
- **Connectors Required:**
  - `linkedin_talent.post_job` -- Post job on LinkedIn
  - `linkedin_talent.search_candidates` -- Source candidates
  - `greenhouse.create_candidate` -- ATS candidate creation
  - `greenhouse.advance_candidate` -- Move candidate through pipeline
  - `greenhouse.schedule_interview` -- Interview scheduling
  - `google_calendar.create_event` -- Calendar booking for interviews
  - `zoom.create_meeting` -- Video interview setup
  - `docusign.send_envelope` -- Offer letter signing
  - `sendgrid.send_email` -- Candidate communications
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Open Positions | greenhouse -> open requisitions | Daily | Number | COUNT(requisitions where status=open) |
  | Pipeline by Stage | greenhouse -> candidates per stage | Daily | Number[] | COUNT(candidates) GROUP BY stage |
  | Time-to-Hire | greenhouse -> avg days from open to accept | Weekly | Days | AVG(offer_accept_date - requisition_open_date) |
  | Offer Acceptance Rate | greenhouse -> accepted / offered | Monthly | Percentage | offers_accepted / offers_extended * 100 |
  | Source Effectiveness | greenhouse -> hires by source | Monthly | Number[] | COUNT(hires) GROUP BY source (LinkedIn, Naukri, referral, etc.) |
  | Cost per Hire | talent_acquisition -> total recruitment spend / hires | Monthly | INR | total_spend / total_hires |
- **HITL Conditions:**
  - Candidate with salary expectation > INR 30,00,000/year -> CHRO approval
  - Offer letter for VP/C-level -> CEO + CHRO approval
  - Background check returns discrepancy -> CHRO review
  - Candidate rejection with > 3 rounds completed -> CHRO confirmation
- **Workflows:**
  - `recruitment_pipeline`:
    1. Trigger: New requisition created in Greenhouse
    2. `talent_acquisition` posts job on LinkedIn + internal portal
    3. Resume screening: `talent_acquisition` scores candidates (0-100) using LLM
    4. Shortlisted candidates (score > 70): Schedule interviews via `google_calendar`
    5. After interviews: Aggregate interviewer scores
    6. If score > 80: Generate offer letter draft
    7. HITL -> CHRO approves offer letter
    8. Send offer via `docusign.send_envelope`
    9. Track acceptance/rejection
    10. On acceptance: Trigger onboarding workflow
- **Test Cases:**
  - UNIT: `test_candidate_scoring_model` -- Given resume text and JD, verify score between 0-100.
  - UNIT: `test_candidate_scoring_minimum_threshold` -- Score < 70 -> not shortlisted.
  - UNIT: `test_offer_letter_generation` -- Given candidate details + compensation, verify letter template population.
  - UNIT: `test_salary_hitl_threshold` -- Salary > 30L -> HITL triggered.
  - INTEGRATION: `test_recruitment_pipeline_end_to_end` -- Requisition -> screening -> interview -> offer -> accept.
  - E2E: `test_recruitment_tab_shows_pipeline` -- CHRO dashboard shows pipeline funnel chart.
  - E2E: `test_recruitment_tab_shows_open_positions` -- Open positions table renders with correct data.

#### 2.3.2 Onboarding

- **Description:** Complete employee onboarding from account provisioning to training enrollment, including document collection, policy acknowledgment, buddy assignment, and equipment provisioning.
- **Agents Responsible:**
  - `onboarding_agent` -- Full onboarding orchestration
- **Connectors Required:**
  - `darwinbox.create_employee` -- Create employee record
  - `okta.create_user` -- Provision email and SSO
  - `slack.invite_user` -- Invite to Slack workspace
  - `jira.create_issue` -- Create IT ticket for equipment
  - `google_calendar.create_event` -- Schedule orientation
  - `sendgrid.send_email` -- Welcome email with docs checklist
  - `docusign.send_envelope` -- Send policy documents for signature
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Onboarding Completion Rate | onboarding_agent -> completed / total | Monthly | Percentage | completed_onboardings / new_joiners * 100 |
  | Average Onboarding Time | onboarding_agent -> avg duration | Monthly | Days | AVG(onboarding_complete_date - joining_date) |
  | Pending Document Submissions | onboarding_agent -> pending docs | Daily | Number | COUNT(employees where docs_complete=false) |
  | Equipment Provision Lead Time | jira -> avg ticket resolution time | Monthly | Days | AVG(equipment_delivered_date - joining_date) |
- **HITL Conditions:**
  - Employee documents incomplete after 7 days -> CHRO alert
  - Equipment provision delayed > 3 days -> Escalate to IT
  - Background verification discrepancy -> Hold onboarding, CHRO review
- **Workflows:**
  - `employee_onboarding` (existing):
    1. Trigger: Offer accepted (from recruitment workflow)
    2. Day -5: `onboarding_agent` creates employee in Darwinbox
    3. Day -3: Provision email, Slack, SSO via Okta
    4. Day -2: Create IT ticket for laptop/equipment
    5. Day -1: Send welcome email with document checklist (Aadhaar, PAN, bank details)
    6. Day 1: Schedule orientation, assign buddy
    7. Day 1: Send policy documents via DocuSign (NDA, code of conduct, leave policy)
    8. Day 7: Check document completion
    9. Day 7: If incomplete -> HITL -> CHRO alert
    10. Day 14: Enroll in mandatory training
    11. Day 30: Probation review reminder to manager
- **Test Cases:**
  - UNIT: `test_onboarding_employee_creation` -- Verify Darwinbox create_employee called with correct fields.
  - UNIT: `test_onboarding_account_provisioning` -- Verify Okta, Slack, and Jira tickets created in parallel.
  - UNIT: `test_onboarding_document_checklist` -- Verify all 5 required documents are listed.
  - UNIT: `test_onboarding_incomplete_docs_hitl` -- After 7 days with missing docs -> HITL triggered.
  - INTEGRATION: `test_onboarding_full_workflow` -- Complete onboarding from offer acceptance to 30-day check.
  - E2E: `test_onboarding_section_in_workforce_tab` -- New joiners appear in workforce tab.

#### 2.3.3 Payroll

- **Description:** Complete payroll processing including salary computation, statutory deductions (PF, ESI, PT, TDS), reimbursement processing, variable pay calculation, and payslip generation.
- **Agents Responsible:**
  - `payroll_engine` -- Full payroll orchestration
- **Connectors Required:**
  - `darwinbox.run_payroll` -- Trigger payroll processing
  - `darwinbox.get_payslip` -- Fetch payslip data
  - `darwinbox.get_attendance` -- Attendance for pay computation
  - `keka.run_payroll` -- Alternative HRMS payroll
  - `keka.get_leave_balance` -- Leave balance check
  - `epfo.file_ecr` -- PF ECR filing
  - `epfo.generate_trrn` -- TRRN for PF payment
  - `tally.post_voucher` -- Payroll journal entry
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Total Payroll Cost (Monthly) | darwinbox.run_payroll -> gross pay | Monthly | INR | SUM(gross_pay for all employees) |
  | PF Liability | payroll_engine -> PF computation | Monthly | INR | SUM(employee_pf + employer_pf) |
  | ESI Liability | payroll_engine -> ESI computation | Monthly | INR | SUM(employee_esi + employer_esi) for eligible employees |
  | PT Liability | payroll_engine -> PT computation | Monthly | INR | SUM(pt_amount by state) |
  | TDS on Salary | payroll_engine -> TDS computation | Monthly | INR | SUM(tds_deducted for all employees) |
  | Payroll Accuracy Rate | payroll_engine -> corrections / total | Monthly | Percentage | 1 - (corrections / total_payslips) * 100 |
  | Average CTC | darwinbox -> sum(ctc) / headcount | Monthly | INR | total_ctc / total_employees |
  | CTC Distribution | darwinbox -> CTC by band | Monthly | INR[] | Histogram by CTC bands |
- **HITL Conditions:**
  - Payroll processing for entire company -> CHRO final approval before disbursement
  - Individual salary correction > INR 5,000 -> CHRO approval
  - New employee's first payroll -> Verify manually
  - Full & final settlement -> CHRO approval
- **Workflows:**
  - `monthly_payroll`:
    1. Day 25: `payroll_engine` computes attendance from Darwinbox
    2. Day 26: `payroll_engine` computes gross pay, deductions, net pay
    3. Day 26: Compute statutory: PF (12% of basic up to 15,000), ESI (0.75%+3.25% if gross <= 21,000), PT (state-wise slab), TDS (based on annual projection)
    4. Day 27: Generate payslip drafts
    5. Day 27: HITL -> CHRO reviews payroll summary
    6. Day 28: Disburse via banking
    7. Day 28: Post payroll journal to Tally
    8. Day 5 (next month): File PF ECR via EPFO
    9. Day 7: File ESI contribution
    10. Day 15: File TDS deposit (challan 281)
- **Test Cases:**
  - UNIT: `test_pf_computation_below_ceiling` -- Basic=12,000 (< 15,000), PF=1,440 (12%).
  - UNIT: `test_pf_computation_above_ceiling` -- Basic=25,000, PF=1,800 (12% of 15,000 ceiling).
  - UNIT: `test_esi_eligible` -- Gross=18,000 (< 21,000), employee ESI=135 (0.75%).
  - UNIT: `test_esi_not_eligible` -- Gross=25,000 (> 21,000), ESI=0.
  - UNIT: `test_pt_karnataka` -- Gross=25,000, PT=200 (Karnataka slab).
  - UNIT: `test_pt_maharashtra` -- Gross=15,000, PT=175 (Maharashtra slab).
  - UNIT: `test_tds_old_regime` -- Annual income=12,00,000, verify TDS as per old regime slabs.
  - UNIT: `test_tds_new_regime` -- Annual income=12,00,000, verify TDS as per new regime slabs.
  - UNIT: `test_payroll_journal_entry` -- Verify Tally voucher format: Salary Expense Dr, Bank Cr, PF Payable Cr, ESI Payable Cr, PT Payable Cr, TDS Payable Cr.
  - INTEGRATION: `test_payroll_full_cycle` -- Attendance -> computation -> approval -> disbursement -> journal.
  - E2E: `test_payroll_tab_shows_status` -- CHRO dashboard Payroll tab shows current month status.
  - E2E: `test_payroll_tab_shows_statutory_dues` -- Statutory dues section shows PF, ESI, PT amounts.

#### 2.3.4 Attendance & Leave

- **Description:** Leave balance tracking, attendance regularization, WFH policy enforcement, overtime calculation, and shift management.
- **Agents Responsible:**
  - `payroll_engine` -- Attendance-to-payroll integration
  - `onboarding_agent` -- Leave policy setup for new joiners
- **Connectors Required:**
  - `darwinbox.get_attendance` -- Attendance records
  - `darwinbox.apply_leave` -- Leave application
  - `keka.get_leave_balance` -- Leave balance
  - `keka.get_attendance_summary` -- Attendance summary
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Average Attendance Rate | darwinbox -> present_days / working_days | Daily | Percentage |
  | Leave Utilization Rate | darwinbox -> leaves_taken / leaves_entitled | Monthly | Percentage |
  | WFH Percentage | darwinbox -> wfh_days / total_days | Monthly | Percentage |
  | Absenteeism Rate | darwinbox -> unplanned_leaves / total_days | Monthly | Percentage |
- **Test Cases:**
  - UNIT: `test_attendance_regularization` -- Missing punch handled correctly.
  - UNIT: `test_leave_balance_deduction` -- 2-day leave deducted from balance correctly.
  - E2E: `test_attendance_section_in_workforce_tab` -- Attendance rate widget renders.

#### 2.3.5 Performance Management

- **Description:** Goal setting, quarterly reviews, 360-degree feedback aggregation, PIP management, and promotion recommendations.
- **Agents Responsible:**
  - `performance_coach` -- Performance analysis, review preparation, PIP tracking
- **Connectors Required:**
  - `darwinbox.update_performance` -- Update goals and ratings
  - `darwinbox.get_employee` -- Employee details for context
  - `sendgrid.send_email` -- Review reminders and notifications
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Goal Completion Rate | darwinbox -> completed_goals / total_goals | Quarterly | Percentage |
  | Average Performance Rating | darwinbox -> avg(rating) | Quarterly | 1-5 Scale |
  | Employees on PIP | performance_coach -> PIP count | Monthly | Number |
  | Review Completion Rate | performance_coach -> reviews_completed / reviews_due | Quarterly | Percentage |
- **HITL Conditions:**
  - PIP initiation -> CHRO approval
  - Promotion recommendation -> CHRO + CEO approval for VP+
  - Performance rating < 2.0 -> CHRO review
- **Test Cases:**
  - UNIT: `test_performance_review_aggregation` -- Aggregate 5 reviewer scores, verify weighted average.
  - UNIT: `test_pip_initiation_hitl` -- PIP creation triggers CHRO HITL.
  - E2E: `test_engagement_tab_shows_performance_data` -- Engagement tab renders performance metrics.

#### 2.3.6 Learning & Development

- **Description:** Training needs identification, course enrollment, completion tracking, skill matrix updates, and certification management.
- **Agents Responsible:**
  - `ld_coordinator` -- L&D orchestration, skill gap analysis
- **Connectors Required:**
  - `darwinbox.get_employee` -- Employee skills and certifications
  - `google_calendar.create_event` -- Training session scheduling
  - `sendgrid.send_email` -- Enrollment confirmations and reminders
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Training Hours per Employee | ld_coordinator -> total_hours / headcount | Monthly | Hours |
  | Course Completion Rate | ld_coordinator -> completed / enrolled | Monthly | Percentage |
  | Skill Gap Score | ld_coordinator -> required_skills - current_skills | Quarterly | 0-100 |
  | Certification Expiry Count | ld_coordinator -> expiring within 90 days | Daily | Number |
- **HITL Conditions:**
  - Mandatory training not completed within 30 days of joining -> CHRO alert
  - Certification expiring within 30 days -> Alert employee and manager
- **Test Cases:**
  - UNIT: `test_skill_gap_analysis` -- Given required skills and employee skills, verify gap calculation.
  - UNIT: `test_certification_expiry_alert` -- Cert expiring in 25 days triggers alert.
  - E2E: `test_engagement_tab_shows_ld_metrics` -- L&D metrics render on engagement tab.

#### 2.3.7 Employee Engagement

- **Description:** Pulse surveys, eNPS calculation, attrition risk prediction, and retention intervention triggers.
- **Agents Responsible:**
  - `performance_coach` -- Engagement analysis, attrition prediction
- **Connectors Required:**
  - `darwinbox.get_employee` -- Employee data for risk modeling
  - `slack.send_message` -- Survey distribution via Slack
  - `sendgrid.send_email` -- Survey emails
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | eNPS Score | performance_coach -> (promoters - detractors) / total * 100 | Monthly | -100 to 100 | (promoters% - detractors%) |
  | Pulse Survey Response Rate | performance_coach -> responses / sent | Monthly | Percentage | responses_received / surveys_sent * 100 |
  | Attrition Risk (High) | performance_coach -> employees with risk > 0.7 | Weekly | Number | COUNT(employees where attrition_risk_score > 0.7) |
  | Retention Rate (Monthly) | darwinbox -> (headcount_start - exits) / headcount_start | Monthly | Percentage | (start_count - exit_count) / start_count * 100 |
- **HITL Conditions:**
  - eNPS drops below 0 -> Urgent CHRO review
  - Attrition risk > 0.8 for any employee with tenure > 2 years -> Manager + CHRO alert
  - 3+ exits from same team in 30 days -> CHRO investigation
- **Test Cases:**
  - UNIT: `test_enps_calculation` -- Given 40 promoters, 30 passives, 30 detractors: eNPS = 10.
  - UNIT: `test_attrition_risk_scoring` -- Employee with low engagement + missed reviews + peer exits -> high risk.
  - UNIT: `test_attrition_risk_hitl` -- Risk > 0.8 triggers HITL.
  - E2E: `test_engagement_tab_shows_enps` -- eNPS gauge renders with correct score.
  - E2E: `test_engagement_tab_shows_risk_heatmap` -- Attrition risk heatmap by department renders.

#### 2.3.8 Compliance

- **Description:** EPFO filing (ECR), ESI filing, Professional Tax filing, labor law compliance, sexual harassment committee reporting, and diversity metrics.
- **Agents Responsible:**
  - `payroll_engine` -- Statutory computation and filing
  - `compliance_guard` -- Compliance monitoring
- **Connectors Required:**
  - `epfo.file_ecr` -- PF ECR filing
  - `epfo.generate_trrn` -- TRRN for PF payment
  - `epfo.get_uan` -- UAN verification
  - `epfo.verify_member` -- Member verification
  - `darwinbox.get_employee` -- Employee statutory details
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | EPFO Filing Status | epfo -> ECR filing status by month | Monthly | Status |
  | ESI Filing Status | compliance_guard -> ESI status | Monthly | Status |
  | PT Filing Status | compliance_guard -> PT status by state | Monthly | Status |
  | Compliance Score | compliance_guard -> weighted index | Monthly | 0-100 |
  | Overdue Filings | compliance_guard -> overdue items | Daily | Number |
- **HITL Conditions:**
  - EPFO ECR filing -> CHRO approval before submission
  - ESI return filing -> CHRO approval
  - Any compliance notice received -> Immediate CHRO alert
  - PT filing for new state -> CHRO review for correct slab application
- **Test Cases:**
  - UNIT: `test_ecr_file_generation` -- Given 50 employees, verify ECR format with correct PF amounts.
  - UNIT: `test_ecr_uan_verification` -- Verify UAN lookup before ECR generation.
  - UNIT: `test_compliance_score_calculation` -- 8 of 10 items filed -> score = 80.
  - INTEGRATION: `test_epfo_ecr_filing_workflow` -- Generate ECR -> CHRO approves -> file via EPFO -> verify TRRN.
  - E2E: `test_compliance_tab_shows_filing_status` -- CHRO dashboard Compliance tab shows filing status grid.

#### 2.3.9 Offboarding

- **Description:** Exit interview management, knowledge transfer documentation, access revocation, full & final settlement, experience letter generation, and EPFO transfer.
- **Agents Responsible:**
  - `offboarding_agent` -- Full offboarding orchestration
- **Connectors Required:**
  - `darwinbox.terminate_employee` -- Mark employee as terminated
  - `okta.deactivate_user` -- Revoke SSO and email access
  - `slack.remove_user` -- Remove from Slack
  - `jira.create_issue` -- IT ticket for asset recovery
  - `epfo.check_claim_status` -- PF transfer/withdrawal status
  - `docusign.send_envelope` -- Experience letter signing
  - `sendgrid.send_email` -- Offboarding communications
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Offboarding Completion Rate | offboarding_agent -> completed / total | Monthly | Percentage |
  | Average F&F Settlement Time | offboarding_agent -> avg days | Monthly | Days |
  | Pending Access Revocations | offboarding_agent -> pending items | Daily | Number |
  | Exit Interview Completion Rate | offboarding_agent -> interviews / exits | Monthly | Percentage |
- **HITL Conditions:**
  - F&F settlement amount > INR 5,00,000 -> CHRO + CFO approval
  - Access revocation not completed within 24 hours of LWD -> Security alert
  - Employee disputes F&F -> CHRO review
- **Test Cases:**
  - UNIT: `test_offboarding_access_revocation` -- Verify Okta, Slack, and Jira tickets created on LWD.
  - UNIT: `test_ff_settlement_calculation` -- Given salary, leaves, gratuity: verify F&F amount.
  - UNIT: `test_ff_hitl_threshold` -- F&F > 5L -> HITL triggered.
  - INTEGRATION: `test_offboarding_full_workflow` -- Resignation -> notice period -> LWD -> access revocation -> F&F -> experience letter.
  - E2E: `test_workforce_tab_shows_exits` -- Exits count and trend visible on workforce tab.

#### 2.3.10 Org Design

- **Description:** Headcount planning, org chart updates, reporting hierarchy management, and span of control analysis.
- **Agents Responsible:**
  - `talent_acquisition` -- Headcount planning support
- **Connectors Required:**
  - `darwinbox.get_org_chart` -- Current org structure
  - `darwinbox.transfer_employee` -- Execute org changes
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Total Headcount | darwinbox -> employee count | Daily | Number |
  | Headcount by Department | darwinbox -> grouped count | Daily | Number[] |
  | Average Span of Control | darwinbox -> avg direct reports per manager | Monthly | Number |
  | Open vs Filled Positions | greenhouse + darwinbox -> comparison | Weekly | Number/Number |
- **Test Cases:**
  - UNIT: `test_span_of_control_calculation` -- Manager with 8 reports, avg span = 8.
  - E2E: `test_workforce_tab_shows_department_breakdown` -- Department headcount chart renders.

---

### 2.4 CMO (Chief Marketing Officer)

#### 2.4.1 Demand Generation

- **Description:** Lead generation campaign management across Google Ads, Meta Ads, and LinkedIn, including landing page optimization and webinar management.
- **Agents Responsible:**
  - `campaign_pilot` -- Campaign creation, budget allocation, performance monitoring
- **Connectors Required:**
  - `google_ads.search_campaigns` -- Campaign listing
  - `google_ads.get_campaign_performance` -- Performance metrics
  - `google_ads.mutate_campaign_budget` -- Budget adjustment
  - `meta_ads.get_campaign_insights` -- Meta campaign data
  - `linkedin_ads.get_campaign_analytics` -- LinkedIn campaign data
  - `hubspot.create_contact` -- Lead capture
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | MQLs (Marketing Qualified Leads) | hubspot -> lifecycle_stage=MQL count | Daily | Number | COUNT(contacts where lifecycle=MQL and created this month) |
  | SQLs (Sales Qualified Leads) | hubspot -> lifecycle_stage=SQL count | Daily | Number | COUNT(contacts where lifecycle=SQL and created this month) |
  | MQL-to-SQL Conversion Rate | hubspot -> SQL/MQL | Weekly | Percentage | sql_count / mql_count * 100 |
  | CAC (Customer Acquisition Cost) | campaign_pilot -> total_spend / customers | Monthly | INR | (google_spend + meta_spend + linkedin_spend + content_cost) / new_customers |
  | Pipeline Value | hubspot.list_deals -> sum(amount) | Daily | INR | SUM(deal.amount for open deals) |
  | ROAS by Channel | google_ads + meta_ads + linkedin_ads | Weekly | Ratio | revenue_attributed / ad_spend per channel |
- **HITL Conditions:**
  - Campaign daily spend exceeds budget by > 20% -> Pause and alert CMO
  - ROAS drops below 1.5x for any channel -> CMO review
  - New campaign launch > INR 1,00,000 budget -> CMO approval
- **Workflows:**
  - `campaign_launch` (existing):
    1. CMO creates campaign brief
    2. `campaign_pilot` generates ad copy variants (3-5 options)
    3. HITL -> CMO selects preferred variants
    4. `campaign_pilot` creates campaigns via Google Ads / Meta Ads
    5. Set budget and bidding strategy
    6. Daily: Monitor performance, pause underperforming ads
    7. Weekly: Report ROAS, CPC, conversions to CMO
- **Test Cases:**
  - UNIT: `test_mql_count_from_hubspot` -- Given 50 contacts with lifecycle=MQL, verify count=50.
  - UNIT: `test_cac_calculation` -- Spend=300000, new_customers=100, CAC=3000.
  - UNIT: `test_roas_calculation` -- Revenue=420000, spend=100000, ROAS=4.2x.
  - UNIT: `test_campaign_overspend_hitl` -- Daily spend=25000, budget=20000 (125% > 120%) -> HITL.
  - INTEGRATION: `test_campaign_launch_workflow` -- Brief -> ad copy -> launch -> monitoring.
  - E2E: `test_pipeline_tab_shows_funnel` -- CMO dashboard Pipeline tab shows MQL/SQL funnel.
  - E2E: `test_pipeline_tab_shows_cac_trend` -- CAC trend chart renders with monthly data points.

#### 2.4.2 Content Marketing

- **Description:** Blog creation, social media scheduling, video script generation, email newsletter, brand voice enforcement, and content calendar management.
- **Agents Responsible:**
  - `content_factory` -- Content creation, scheduling, performance tracking
- **Connectors Required:**
  - `wordpress.create_post` -- Blog publishing
  - `wordpress.get_posts` -- Blog analytics
  - `buffer.create_post` -- Social media scheduling
  - `buffer.get_analytics` -- Social media metrics
  - `youtube.upload_video` -- Video publishing
  - `mailchimp.create_campaign` -- Newsletter creation
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Blog Posts Published (MTD) | wordpress.get_posts -> count | Daily | Number |
  | Average Blog Views | wordpress -> avg(views) per post | Weekly | Number |
  | Social Posts Scheduled | buffer -> scheduled count | Daily | Number |
  | Email Newsletter Open Rate | mailchimp -> open_rate | Per send | Percentage |
  | Content Calendar Adherence | content_factory -> published / planned | Weekly | Percentage |
- **HITL Conditions:**
  - Blog post mentioning competitor -> CMO review before publish
  - Email to > 10,000 recipients -> CMO approval
  - Video script for product launch -> CMO approval
- **Test Cases:**
  - UNIT: `test_content_calendar_generation` -- Given topics and frequency, verify calendar.
  - UNIT: `test_brand_voice_check` -- Content with off-brand language flagged.
  - E2E: `test_content_tab_shows_calendar` -- Content tab renders content calendar.

#### 2.4.3 SEO

- **Description:** Keyword research, on-page optimization, technical SEO audit, backlink monitoring, competitor analysis, and search console monitoring.
- **Agents Responsible:**
  - `seo_strategist` -- SEO analysis, keyword research, audit
- **Connectors Required:**
  - `ahrefs.get_backlinks` -- Backlink monitoring
  - `ahrefs.get_keywords` -- Keyword research
  - `ahrefs.get_site_audit` -- Technical SEO audit
  - `ga4.get_report` -- Organic traffic data
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Organic Traffic (MTD) | ga4 -> organic sessions | Daily | Number |
  | Top 10 Keyword Count | ahrefs -> keywords ranking 1-10 | Weekly | Number |
  | Domain Authority | ahrefs -> domain rating | Weekly | 0-100 |
  | Backlink Count | ahrefs -> referring domains | Weekly | Number |
  | Technical SEO Score | ahrefs -> site_audit_score | Weekly | 0-100 |
- **Test Cases:**
  - UNIT: `test_keyword_ranking_trend` -- Given weekly ranking data, verify trend calculation.
  - E2E: `test_content_tab_shows_seo_rankings` -- SEO section renders keyword rankings table.

#### 2.4.4 Email Marketing

- **Description:** Campaign creation, audience segmentation, A/B testing, drip sequences, deliverability monitoring, and unsubscribe management.
- **Agents Responsible:**
  - `content_factory` -- Email creation
  - `campaign_pilot` -- A/B test management
- **Connectors Required:**
  - `mailchimp.create_campaign` -- Create email campaign
  - `mailchimp.send_campaign` -- Send campaign
  - `mailchimp.get_campaign_report` -- Campaign metrics
  - `hubspot.create_contact` -- Contact management
  - `sendgrid.send_email` -- Transactional emails
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Email Open Rate | mailchimp -> open_rate | Per send | Percentage |
  | Email Click Rate | mailchimp -> click_rate | Per send | Percentage |
  | Unsubscribe Rate | mailchimp -> unsubscribe_rate | Per send | Percentage |
  | Deliverability Rate | mailchimp -> delivered / sent | Per send | Percentage |
  | Drip Sequence Completion | campaign_pilot -> completed / enrolled | Weekly | Percentage |
- **HITL Conditions:**
  - Unsubscribe rate > 1% on any campaign -> CMO review
  - Email to entire list (> 50,000 recipients) -> CMO approval
  - A/B test winner selection when confidence < 95% -> CMO decides
- **Test Cases:**
  - UNIT: `test_email_ab_test_winner_selection` -- Given variant A: 32% open, variant B: 35% open, verify B selected.
  - UNIT: `test_unsubscribe_rate_hitl` -- Unsubscribe rate 1.2% (> 1%) -> HITL.
  - E2E: `test_campaigns_tab_shows_email_metrics` -- Email performance metrics render.

#### 2.4.5 Social Media

- **Description:** Post scheduling, engagement monitoring, influencer identification, crisis monitoring, and community management.
- **Agents Responsible:**
  - `content_factory` -- Post creation and scheduling
  - `brand_monitor` -- Social listening, crisis detection
- **Connectors Required:**
  - `buffer.create_post` -- Schedule posts
  - `buffer.get_analytics` -- Post metrics
  - `twitter.post_tweet` -- Direct Twitter posting
  - `twitter.search_mentions` -- Mention monitoring
  - `brandwatch.get_mentions` -- Brand monitoring
  - `brandwatch.get_sentiment` -- Sentiment analysis
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Social Engagement (Total) | buffer + twitter -> sum(likes, comments, shares) | Daily | Number |
  | Follower Growth | twitter + linkedin -> delta followers | Weekly | Number |
  | Social Share of Voice | brandwatch -> brand mentions / total mentions | Weekly | Percentage |
  | Crisis Alert Count | brand_monitor -> negative sentiment spikes | Real-time | Number |
- **Test Cases:**
  - UNIT: `test_social_engagement_aggregation` -- Sum across platforms.
  - UNIT: `test_crisis_detection` -- Negative sentiment spike > 3x baseline -> alert.
  - E2E: `test_brand_tab_shows_sentiment` -- Brand tab renders sentiment gauge.

#### 2.4.6 ABM (Account-Based Marketing)

- **Description:** Target account identification, intent scoring via Bombora/G2/TrustRadius, personalized outreach, and multi-touch attribution.
- **Agents Responsible:**
  - `crm_intelligence` -- ABM orchestration, intent analysis
- **Connectors Required:**
  - `bombora.get_surge_scores` -- Intent data
  - `bombora.search_companies` -- Company search by intent topic
  - `g2.get_buyer_intent` -- G2 buyer intent signals
  - `trustradius.get_intent` -- TrustRadius intent signals
  - `hubspot.list_companies` -- Target account list
  - `hubspot.update_contact` -- Update intent scores in CRM
  - `linkedin_ads.get_campaign_analytics` -- ABM campaign metrics
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Target Accounts | hubspot -> tagged accounts | Daily | Number |
  | Accounts with Intent Signal | bombora + g2 -> surging accounts | Weekly | Number |
  | Average Intent Score | crm_intelligence -> avg(intent_score) | Weekly | 0-100 |
  | ABM Pipeline Generated | hubspot -> deals from ABM accounts | Monthly | INR |
  | Multi-Touch Attribution | crm_intelligence -> attribution model | Monthly | Percentage[] |
- **HITL Conditions:**
  - Target account list change > 20% -> CMO review
  - ABM campaign budget > INR 50,000/month -> CMO approval
- **Test Cases:**
  - UNIT: `test_intent_score_aggregation` -- Combine Bombora + G2 + TrustRadius scores.
  - UNIT: `test_target_account_identification` -- Intent > 70 -> target.
  - E2E: `test_abm_tab_shows_target_accounts` -- ABM tab renders target accounts with intent scores.

#### 2.4.7 Marketing Analytics

- **Description:** Campaign ROI, CAC, LTV, funnel conversion rates, channel attribution, and MQL tracking.
- **Agents Responsible:**
  - `campaign_pilot` -- Performance analysis
  - `crm_intelligence` -- Attribution modeling
- **Connectors Required:**
  - `ga4.get_report` -- Web analytics
  - `mixpanel.get_funnel` -- Product analytics
  - `hubspot.get_campaign_analytics` -- CRM campaign data
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | LTV (Lifetime Value) | crm_intelligence -> avg_revenue * avg_lifetime | Monthly | INR |
  | LTV/CAC Ratio | crm_intelligence -> ltv / cac | Monthly | Ratio |
  | Funnel Conversion Rates | hubspot -> stage-to-stage conversion | Weekly | Percentage[] |
  | Channel Attribution | crm_intelligence -> multi-touch model | Monthly | Percentage[] |
  | Marketing ROI | campaign_pilot -> revenue_attributed / total_spend | Monthly | Ratio |
- **Test Cases:**
  - UNIT: `test_ltv_calculation` -- Given ARPU=5000, lifetime=24mo, LTV=120000.
  - UNIT: `test_ltv_cac_ratio` -- LTV=120000, CAC=35000, ratio=3.43.
  - E2E: `test_pipeline_tab_shows_funnel_conversion` -- Funnel chart with conversion rates renders.

#### 2.4.8 Brand Management

- **Description:** Brand sentiment monitoring, competitive positioning, PR monitoring, and brand guidelines enforcement.
- **Agents Responsible:**
  - `brand_monitor` -- Sentiment tracking, competitive intelligence
- **Connectors Required:**
  - `brandwatch.get_mentions` -- Brand mentions
  - `brandwatch.get_sentiment` -- Sentiment analysis
  - `brandwatch.get_competitors` -- Competitive monitoring
  - `twitter.search_mentions` -- Twitter brand mentions
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Brand Sentiment Score | brandwatch -> weighted sentiment | Daily | 0-100 |
  | Share of Voice | brandwatch -> brand / total | Weekly | Percentage |
  | Media Mentions (30d) | brandwatch -> mention count | Daily | Number |
  | Competitor Share of Voice | brandwatch -> competitor mentions | Weekly | Percentage[] |
- **Test Cases:**
  - UNIT: `test_sentiment_score_calculation` -- Given 80 positive, 15 neutral, 5 negative: score = 78.
  - E2E: `test_brand_tab_shows_sentiment_gauge` -- Sentiment gauge renders with correct score.

#### 2.4.9 Product Marketing

- **Description:** Feature launch campaigns, competitive battle cards, sales enablement content, and customer case studies.
- **Agents Responsible:**
  - `content_factory` -- Battle card generation, case study creation
  - `campaign_pilot` -- Launch campaign management
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Battle Cards Updated | content_factory -> last_update per card | Monthly | Number |
  | Case Studies Published | content_factory -> published count | Quarterly | Number |
  | Sales Enablement Downloads | s3 -> download count | Monthly | Number |
- **Test Cases:**
  - UNIT: `test_battle_card_generation` -- Given competitor data, verify card sections populated.

#### 2.4.10 Events

- **Description:** Event registration management, attendee engagement tracking, post-event follow-up, and ROI calculation.
- **Agents Responsible:**
  - `campaign_pilot` -- Event ROI calculation
- **Connectors Required:**
  - `hubspot.create_contact` -- Register attendees
  - `zoom.create_meeting` -- Virtual event setup
  - `sendgrid.send_email` -- Event communications
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Event Registrations | hubspot -> event list count | Per event | Number |
  | Attendance Rate | zoom -> attendees / registered | Per event | Percentage |
  | Post-Event Pipeline | hubspot -> deals from event contacts | Monthly | INR |
  | Event ROI | campaign_pilot -> pipeline / event_cost | Per event | Ratio |
- **Test Cases:**
  - UNIT: `test_event_roi_calculation` -- Pipeline=500000, cost=50000, ROI=10x.
  - E2E: `test_campaigns_tab_shows_events` -- Active events render in campaigns tab.

---

### 2.5 COO (Chief Operating Officer)

#### 2.5.1 IT Operations

- **Description:** Incident management via PagerDuty, change management via ServiceNow, infrastructure monitoring, capacity planning, SLA management, and runbook automation.
- **Agents Responsible:**
  - `it_operations` -- Incident triage, runbook execution, capacity analysis
- **Connectors Required:**
  - `pagerduty.create_incident` -- Create incidents
  - `pagerduty.acknowledge_incident` -- Acknowledge
  - `pagerduty.resolve_incident` -- Resolve
  - `pagerduty.get_on_call` -- On-call schedule
  - `pagerduty.list_incidents` -- Incident list
  - `pagerduty.create_postmortem` -- Postmortem creation
  - `servicenow.create_incident` -- ServiceNow incident
  - `servicenow.submit_change_request` -- Change requests
  - `servicenow.check_sla_status` -- SLA monitoring
  - `servicenow.get_cmdb_ci` -- CMDB queries
  - `jira.create_issue` -- Bug tracking
  - `jira.search_issues` -- Issue search
  - `jira.get_project_metrics` -- Project health
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Active Incidents (P1/P2) | pagerduty.list_incidents -> filter(triggered+acknowledged) | Real-time | Number | COUNT(incidents where status IN (triggered, acknowledged) AND urgency=high) |
  | MTTR (Mean Time To Resolve) | pagerduty -> avg(resolved_at - created_at) | Hourly | Minutes | AVG(resolution_time) for last 30 days |
  | MTTR Trend | pagerduty -> weekly MTTR | Weekly | Minutes[] | 12-week trend |
  | Change Success Rate | servicenow -> successful / total changes | Weekly | Percentage | successful_changes / total_changes * 100 |
  | Uptime SLA | servicenow.check_sla_status -> uptime | Daily | Percentage | uptime_minutes / total_minutes * 100 |
  | Incident by Severity | pagerduty -> group by severity | Daily | Number[] | COUNT(incidents) GROUP BY severity |
  | On-Call Coverage | pagerduty.get_on_call -> coverage % | Daily | Percentage | shifts_covered / total_shifts * 100 |
- **HITL Conditions:**
  - P1 incident not acknowledged within 5 minutes -> Auto-escalate to COO
  - Change request affecting production -> COO approval
  - Uptime drops below 99.5% SLA -> COO alert
  - MTTR exceeds 60 minutes for P1 -> COO review
- **Workflows:**
  - `it_incident_escalation` (existing):
    1. Trigger: PagerDuty webhook for new incident
    2. `it_operations` classifies severity (P1/P2/P3/P4)
    3. P1: Page on-call engineer + notify COO
    4. P2: Page on-call engineer
    5. P3/P4: Create Jira ticket
    6. If not acknowledged in 5 min (P1) or 15 min (P2): Escalate to next on-call
    7. On resolution: `it_operations` generates postmortem draft
    8. HITL -> COO reviews postmortem
    9. Create follow-up Jira tickets for remediation
- **Test Cases:**
  - UNIT: `test_incident_classification_p1` -- Production outage with > 100 users affected -> P1.
  - UNIT: `test_incident_classification_p3` -- UI cosmetic issue -> P3.
  - UNIT: `test_mttr_calculation` -- Given 10 incidents with resolution times, verify MTTR.
  - UNIT: `test_p1_escalation_timeout` -- P1 not acknowledged in 5 min -> escalation.
  - INTEGRATION: `test_incident_lifecycle` -- Create -> acknowledge -> resolve -> postmortem.
  - E2E: `test_it_ops_tab_shows_active_incidents` -- COO dashboard IT Ops tab shows active incidents.
  - E2E: `test_it_ops_tab_shows_mttr_trend` -- MTTR trend chart renders with weekly data.

#### 2.5.2 Customer Support

- **Description:** Ticket triage with AI classification (88% target accuracy), SLA enforcement, CSAT/NPS tracking, knowledge base management, escalation management, and auto-response for common queries.
- **Agents Responsible:**
  - `support_triage` -- Ticket classification, routing, priority assignment
  - `support_deflector` -- Auto-response for FAQ-type tickets, knowledge base search
- **Connectors Required:**
  - `zendesk.create_ticket` -- Ticket creation
  - `zendesk.update_ticket` -- Status/priority updates
  - `zendesk.get_ticket` -- Ticket details
  - `zendesk.apply_macro` -- Auto-response macros
  - `zendesk.get_csat_score` -- CSAT ratings
  - `zendesk.escalate_ticket` -- Escalation
  - `zendesk.merge_tickets` -- Duplicate merging
  - `zendesk.get_sla_status` -- SLA tracking
  - `confluence.search_pages` -- Knowledge base search
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Ticket Volume (Daily) | zendesk -> new tickets today | Real-time | Number | COUNT(tickets created today) |
  | First Response Time | zendesk -> avg first response time | Hourly | Minutes | AVG(first_response_at - created_at) |
  | Resolution Time | zendesk -> avg resolution time | Hourly | Hours | AVG(solved_at - created_at) |
  | Deflection Rate | support_deflector -> auto_resolved / total | Daily | Percentage | auto_resolved_tickets / total_tickets * 100 |
  | CSAT Score | zendesk.get_csat_score -> avg rating | Daily | 0-5 | AVG(satisfaction_rating) |
  | NPS Score | zendesk -> promoters - detractors | Monthly | -100 to 100 | (promoters% - detractors%) |
  | SLA Compliance | zendesk.get_sla_status -> within_sla / total | Daily | Percentage | tickets_within_sla / total_tickets * 100 |
  | Auto-Classification Accuracy | support_triage -> correct / total | Weekly | Percentage | correctly_classified / total_classified * 100 |
- **HITL Conditions:**
  - Classification confidence < 0.85 -> Human agent classifies
  - Customer sentiment "very negative" -> Escalate to support lead
  - SLA breach imminent (< 1 hour remaining) -> Alert COO
  - Customer request involving refund > INR 10,000 -> COO approval
- **Workflows:**
  - `support_triage` (existing):
    1. Trigger: New Zendesk ticket created
    2. `support_triage` classifies ticket (category, priority, sentiment)
    3. If FAQ-type: `support_deflector` searches knowledge base
    4. If KB match confidence > 0.90: Auto-respond with KB article
    5. If not FAQ: Route to appropriate team based on category
    6. Monitor SLA: Send reminder at 75% SLA time elapsed
    7. If SLA breached: Escalate to support lead
    8. On resolution: Request CSAT rating
- **Test Cases:**
  - UNIT: `test_ticket_classification_billing` -- Ticket about invoice -> category=billing.
  - UNIT: `test_ticket_classification_technical` -- Ticket about API error -> category=technical.
  - UNIT: `test_deflection_kb_match` -- FAQ about password reset -> KB article matched -> auto-response.
  - UNIT: `test_deflection_no_match` -- Novel issue -> no auto-response, route to agent.
  - UNIT: `test_sla_breach_alert` -- SLA at 80% elapsed -> reminder sent.
  - UNIT: `test_classification_accuracy_below_target` -- Accuracy 82% (< 88%) -> flag for model retraining.
  - INTEGRATION: `test_support_lifecycle` -- Ticket creation -> classification -> deflection attempt -> routing -> resolution -> CSAT.
  - E2E: `test_support_tab_shows_ticket_volume` -- COO dashboard Support tab shows daily ticket volume chart.
  - E2E: `test_support_tab_shows_sla_compliance` -- SLA compliance percentage renders.
  - E2E: `test_support_tab_shows_csat_trend` -- CSAT trend line renders with daily data points.

#### 2.5.3 Vendor Management

- **Description:** Vendor onboarding, PO management, contract lifecycle management, SLA monitoring, spend analysis, and vendor performance scoring.
- **Agents Responsible:**
  - `vendor_manager` -- Vendor scoring, SLA monitoring, spend analysis
- **Connectors Required:**
  - `tally.get_ledger_balance` -- Vendor payment data
  - `docusign.send_envelope` -- Contract signing
  - `docusign.get_envelope_status` -- Contract status
  - `jira.create_issue` -- Vendor onboarding tasks
  - `hubspot.create_company` -- Vendor record in CRM
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Active Vendors | tally -> vendor ledger count | Monthly | Number |
  | Contract Renewals Due (90d) | vendor_manager -> expiring contracts | Daily | Number |
  | Spend by Category | tally -> vendor payments grouped | Monthly | INR[] |
  | Vendor Performance Score | vendor_manager -> weighted scoring | Quarterly | 0-100 |
  | Pending POs | tally -> open PO count | Daily | Number |
- **HITL Conditions:**
  - New vendor onboarding with contract > INR 10,00,000 -> COO approval
  - Vendor performance score < 50 -> COO review for replacement
  - Contract renewal without competitive bidding -> COO approval
- **Test Cases:**
  - UNIT: `test_vendor_performance_scoring` -- Given delivery, quality, cost metrics: verify weighted score.
  - UNIT: `test_contract_renewal_alert` -- Contract expiring in 60 days -> alert.
  - E2E: `test_vendors_tab_shows_active_vendors` -- Vendors tab renders vendor list with scores.

#### 2.5.4 Facilities

- **Description:** Office management, asset tracking, maintenance scheduling, security access management, and travel & expense management.
- **Agents Responsible:**
  - `facilities_agent` -- Asset tracking, maintenance scheduling
- **Connectors Required:**
  - `jira.create_issue` -- Maintenance requests
  - `jira.search_issues` -- Track maintenance status
  - `tally.get_ledger_balance` -- Facilities expense
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Asset Utilization Rate | facilities_agent -> in_use / total | Monthly | Percentage |
  | Open Maintenance Requests | jira -> open facilities tickets | Daily | Number |
  | Facilities Expense (MTD) | tally -> facilities ledger | Monthly | INR |
  | Travel Expense (MTD) | tally -> travel ledger | Monthly | INR |
- **HITL Conditions:**
  - Facilities expense > 110% of budget -> COO review
  - Asset purchase > INR 50,000 -> COO approval
- **Test Cases:**
  - UNIT: `test_asset_utilization_calculation` -- 80 laptops in use, 100 total: 80%.
  - E2E: `test_facilities_tab_shows_maintenance_requests` -- Facilities tab renders maintenance request list.

#### 2.5.5 Supply Chain (If Applicable)

- **Description:** Inventory management, procurement automation, logistics tracking, and demand forecasting for companies with physical goods.
- **Agents Responsible:**
  - `vendor_manager` -- Procurement orchestration
- **Connectors Required:**
  - `tally.get_stock_summary` -- Inventory levels
  - `tally.get_ledger_balance` -- Procurement data
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Inventory Turnover | tally -> COGS / avg_inventory | Monthly | Ratio |
  | Stock-Out Events | vendor_manager -> stock_out count | Daily | Number |
  | Procurement Lead Time | vendor_manager -> avg PO-to-delivery | Monthly | Days |
- **Test Cases:**
  - UNIT: `test_inventory_turnover_calculation` -- COGS=1Cr, avg_inventory=25L: turnover=4.

#### 2.5.6 Quality Assurance & Process Excellence

- **Description:** Process audit, compliance monitoring, SOP management, and workflow optimization.
- **Agents Responsible:**
  - `compliance_guard` -- Process compliance monitoring
- **Connectors Required:**
  - `confluence.search_pages` -- SOP lookup
  - `jira.search_issues` -- Process tickets
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | SOP Compliance Rate | compliance_guard -> compliant / total | Monthly | Percentage |
  | Process Cycle Time | jira -> avg issue cycle time | Weekly | Hours |
  | Bottleneck Count | compliance_guard -> identified bottlenecks | Monthly | Number |
- **Test Cases:**
  - UNIT: `test_sop_compliance_scoring` -- 9 of 10 processes compliant: 90%.

#### 2.5.7 Business Continuity

- **Description:** DR planning, incident response, risk assessment, and business impact analysis.
- **Agents Responsible:**
  - `risk_sentinel` -- Risk assessment, BIA
  - `it_operations` -- DR readiness
- **Test Cases:**
  - UNIT: `test_business_impact_calculation` -- Given system downtime costs, verify BIA score.

---

### 2.6 CBO (Chief Business Officer) / Back Office

#### 2.6.1 Legal

- **Description:** Contract review with clause extraction and risk flagging, NDA management, regulatory filing, IP management, and litigation tracking.
- **Agents Responsible:**
  - `legal_ops` -- Contract analysis, clause extraction, risk assessment
  - `contract_intelligence` -- Contract lifecycle management
- **Connectors Required:**
  - `docusign.send_envelope` -- Send contracts for signature
  - `docusign.get_envelope_status` -- Track signing status
  - `docusign.download_document` -- Retrieve signed copies
  - `s3.upload_file` -- Archive contracts
  - `confluence.search_pages` -- Template lookup
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Active Contracts | legal_ops -> contract count by status | Daily | Number |
  | Contracts Pending Review | legal_ops -> status=pending_review | Daily | Number |
  | Average Review Time | legal_ops -> avg(review_complete - received) | Weekly | Days |
  | NDA Status (Active/Expired) | legal_ops -> NDA lifecycle | Daily | Number/Number |
  | Risk Clauses Flagged | legal_ops -> flagged clause count | Per review | Number |
  | Litigation Cases (Active) | legal_ops -> active_cases | Monthly | Number |
- **HITL Conditions:**
  - Contract value > INR 50,00,000 -> CBO + CEO approval
  - High-risk clauses detected (unlimited liability, broad IP assignment) -> CBO review
  - NDA with competitor -> CBO + CEO approval
  - Litigation settlement offer -> CBO + CEO + CFO approval
- **Workflows:**
  - `contract_review`:
    1. Contract uploaded or received via email
    2. `legal_ops` extracts key clauses (payment terms, liability, IP, termination, indemnity)
    3. `contract_intelligence` flags risk clauses against company policy
    4. If risk score > 0.7: HITL -> CBO reviews flagged clauses
    5. If approved: Route to counterparty via DocuSign
    6. Track signing progress
    7. On completion: Archive in S3, update contract registry
  - `compliance_review` (existing):
    1. Monthly: Scan all active contracts for expiry within 90 days
    2. Flag contracts needing renewal
    3. Notify CBO and relevant department heads
- **Test Cases:**
  - UNIT: `test_clause_extraction_payment_terms` -- Contract text contains "Net 45" -> extract payment_terms=45.
  - UNIT: `test_clause_extraction_liability` -- "Unlimited liability" clause -> risk flagged.
  - UNIT: `test_clause_extraction_ip` -- "All IP transfers to Client" -> risk flagged.
  - UNIT: `test_contract_value_hitl` -- Value > 50L -> HITL.
  - UNIT: `test_nda_expiry_alert` -- NDA expiring in 30 days -> alert.
  - INTEGRATION: `test_contract_review_workflow` -- Upload -> extract -> flag -> review -> sign -> archive.
  - E2E: `test_legal_tab_shows_active_contracts` -- CBO dashboard Legal tab renders contract table.
  - E2E: `test_legal_tab_shows_pending_reviews` -- Pending review count visible.

#### 2.6.2 Risk & Compliance

- **Description:** Fraud detection, AML/KYC screening, regulatory monitoring, audit management, policy enforcement, and sanctions screening.
- **Agents Responsible:**
  - `risk_sentinel` -- Risk assessment, fraud detection, compliance scoring
  - `compliance_guard` -- Regulatory monitoring, policy enforcement
- **Connectors Required:**
  - `sanctions_api.screen_entity` -- Entity screening against sanctions lists
  - `sanctions_api.screen_transaction` -- Transaction party screening
  - `sanctions_api.batch_screen` -- Bulk screening
  - `sanctions_api.get_alert` -- Alert management
  - `sanctions_api.generate_report` -- Compliance reports
  - `mca_portal.fetch_company_master_data` -- Company verification
- **KPIs:**
  | Metric | Source | Refresh | Unit | Formula |
  |--------|--------|---------|------|---------|
  | Compliance Score | compliance_guard -> weighted compliance index | Weekly | 0-100 | Weighted average of all compliance areas |
  | Sanctions Screening Rate | sanctions_api -> screened / total transactions | Daily | Percentage | screened_transactions / total_transactions * 100 |
  | Screening Alerts (Unresolved) | sanctions_api.get_alert -> pending count | Real-time | Number | COUNT(alerts where status=pending) |
  | Audit Findings (Open) | risk_sentinel -> open findings | Monthly | Number | COUNT(findings where status != closed) |
  | Risk Register Items | risk_sentinel -> total risks by severity | Monthly | Number[] | GROUP BY severity (critical, high, medium, low) |
  | Policy Violations (MTD) | compliance_guard -> violations count | Monthly | Number | COUNT(violations this month) |
- **HITL Conditions:**
  - Sanctions screening match (any confidence) -> CBO reviews immediately
  - Fraud detection alert -> CBO + CFO immediate review
  - Compliance score drops below 70 -> CBO urgent review
  - New regulatory requirement identified -> CBO review for policy update
- **Workflows:**
  - `transaction_screening`:
    1. Trigger: New vendor payment or customer onboarding
    2. `risk_sentinel` screens entity via `sanctions_api.screen_entity`
    3. If match found (confidence > 0.5): HITL -> CBO reviews match
    4. If CBO confirms false positive: Mark as cleared, proceed
    5. If CBO confirms true positive: Block transaction, escalate to CEO
    6. Generate compliance report
  - `quarterly_audit`:
    1. Trigger: First day of each quarter
    2. `compliance_guard` generates compliance checklist
    3. Review each compliance area (financial, HR, IT, legal)
    4. Flag non-compliant items
    5. HITL -> CBO reviews and assigns remediation
    6. Track remediation progress
- **Test Cases:**
  - UNIT: `test_sanctions_screening_no_match` -- Entity "Infosys" -> no match -> clear.
  - UNIT: `test_sanctions_screening_match` -- Entity matching sanctions list -> alert created.
  - UNIT: `test_compliance_score_calculation` -- Given 8 areas scored, verify weighted average.
  - UNIT: `test_fraud_detection_alert` -- Unusual pattern detected -> CBO + CFO alert.
  - INTEGRATION: `test_transaction_screening_workflow` -- Payment -> screen -> alert -> review -> clear.
  - E2E: `test_risk_tab_shows_compliance_score` -- CBO dashboard Risk tab renders compliance score.
  - E2E: `test_risk_tab_shows_screening_results` -- Screening results table renders.

#### 2.6.3 Corporate Secretary

- **Description:** Board meeting management, minutes preparation, statutory filing with MCA, annual return filing, share transfer management.
- **Agents Responsible:**
  - `compliance_guard` -- Statutory filing management
  - `legal_ops` -- Board documentation
- **Connectors Required:**
  - `mca_portal.file_annual_return` -- Annual return filing
  - `mca_portal.complete_director_kyc` -- Director KYC
  - `mca_portal.fetch_company_master_data` -- Company data verification
  - `mca_portal.file_charge_satisfaction` -- Charge management
  - `docusign.send_envelope` -- Board resolution signing
  - `google_calendar.create_event` -- Board meeting scheduling
  - `sendgrid.send_email` -- Board meeting notices
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Board Meetings (YTD) | google_calendar -> board events | Monthly | Number |
  | Statutory Filings (Upcoming 90d) | mca_portal -> pending filings | Daily | Number |
  | Director KYC Status | mca_portal -> KYC completion | Annual | Status[] |
  | Annual Return Status | mca_portal -> filing status | Annual | Status |
  | Pending Board Resolutions | legal_ops -> unsigned resolutions | Daily | Number |
- **HITL Conditions:**
  - Any MCA filing -> CBO approval before submission
  - Board resolution -> All directors must sign
  - Director KYC reminder -> 30 days before deadline
- **Workflows:**
  - `board_meeting_prep`:
    1. 14 days before meeting: Create agenda draft
    2. 7 days before: Send board pack (agenda + MIS + reports)
    3. Meeting day: Record attendance
    4. 7 days after: Draft minutes
    5. HITL -> Directors review and approve minutes
    6. Sign minutes via DocuSign
    7. Archive in S3
- **Test Cases:**
  - UNIT: `test_statutory_filing_deadline_alert` -- Annual return due in 60 days -> alert.
  - UNIT: `test_director_kyc_reminder` -- KYC due in 25 days -> reminder.
  - INTEGRATION: `test_annual_return_filing` -- Prepare -> CBO approves -> file via MCA -> verify status.
  - E2E: `test_corporate_tab_shows_filing_status` -- Corporate tab renders filing status grid.

#### 2.6.4 Communications

- **Description:** Internal communications (all-hands, newsletters), external communications (press releases, media queries), crisis communication, and investor relations.
- **Agents Responsible:**
  - `content_factory` -- Content creation (shared with CMO)
  - `brand_monitor` -- Media monitoring (shared with CMO)
- **Connectors Required:**
  - `slack.send_message` -- Internal announcements
  - `sendgrid.send_email` -- Newsletter delivery
  - `twitter.post_tweet` -- Public communications
  - `brandwatch.get_mentions` -- Media monitoring
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | Internal Comms Reach | slack -> message read rate | Per send | Percentage |
  | Newsletter Open Rate | sendgrid -> open rate | Per send | Percentage |
  | Media Coverage (30d) | brandwatch -> media mentions | Daily | Number |
  | Investor Query Response Time | legal_ops -> avg response time | Monthly | Days |
- **HITL Conditions:**
  - External press release -> CBO + CEO approval
  - Crisis communication -> CBO + CEO immediate review
  - Investor update -> CBO + CFO approval
- **Test Cases:**
  - UNIT: `test_internal_comms_reach_calculation` -- 80 of 100 employees read -> 80%.
  - E2E: `test_comms_tab_shows_media_coverage` -- Comms tab renders media mentions chart.

#### 2.6.5 Data & Analytics

- **Description:** Data governance, privacy compliance (DPDPA), data breach management, analytics reporting, and dashboard maintenance.
- **Agents Responsible:**
  - `risk_sentinel` -- Data governance, breach detection
  - `compliance_guard` -- DPDPA compliance
- **Connectors Required:**
  - `s3.list_objects` -- Data inventory
  - `sanctions_api.generate_report` -- Data processing report
- **KPIs:**
  | Metric | Source | Refresh | Unit |
  |--------|--------|---------|------|
  | DPDPA Compliance Status | compliance_guard -> compliance checklist | Monthly | 0-100 |
  | Data Breach Incidents (YTD) | risk_sentinel -> breach count | Monthly | Number |
  | PII Data Locations | risk_sentinel -> PII scan results | Weekly | Number |
  | Data Retention Compliance | compliance_guard -> retention policy adherence | Monthly | Percentage |
- **HITL Conditions:**
  - Data breach detected -> CBO + CEO immediate alert
  - DPDPA compliance score < 80 -> CBO review
- **Test Cases:**
  - UNIT: `test_dpdpa_compliance_scoring` -- 7 of 8 requirements met -> 87.5.
  - E2E: `test_risk_tab_shows_data_governance` -- Data governance section renders.

---

## 3. Technical Architecture

### 3.1 Dashboard Architecture

#### 3.1.1 Component Structure Per CxO Role

| Role | Page Component | API Endpoint | Route Path | RBAC Roles |
|------|---------------|--------------|------------|------------|
| CEO | `CEODashboard.tsx` | `GET /kpis/ceo` | `/dashboard/ceo` | admin |
| CFO | `CFODashboard.tsx` (existing) | `GET /kpis/cfo` (existing, needs real data) | `/dashboard/cfo` | admin, cfo |
| CHRO | `CHRODashboard.tsx` (NEW) | `GET /kpis/chro` (NEW) | `/dashboard/chro` | admin, chro |
| CMO | `CMODashboard.tsx` (existing) | `GET /kpis/cmo` (existing, needs real data) | `/dashboard/cmo` | admin, cmo |
| COO | `COODashboard.tsx` (NEW) | `GET /kpis/coo` (NEW) | `/dashboard/coo` | admin, coo |
| CBO | `CBODashboard.tsx` (NEW) | `GET /kpis/cbo` (NEW) | `/dashboard/cbo` | admin, cbo |

#### 3.1.2 Data Flow

```
Dashboard (React) --> API Endpoint (FastAPI)
    --> KPI Cache Layer (Redis, TTL per metric)
        --> HIT: Return cached value + computed_at timestamp
        --> MISS: Trigger Agent Execution
            --> Agent (domain logic) --> Connector(s) --> External System(s)
            --> Agent returns TaskResult
            --> Store in agent_task_results (PostgreSQL)
            --> Extract KPI values from TaskResult.output
            --> Write to kpi_cache (Redis)
            --> Return to Dashboard
```

#### 3.1.3 Refresh Strategy Per Metric Type

| Category | Strategy | TTL | Examples |
|----------|----------|-----|---------|
| Real-time | WebSocket push or polling (10s) | 30 seconds | Active incidents, open tickets, pending approvals |
| Near-real-time | Polling (60s) | 5 minutes | Cash balance, ticket volume |
| Hourly | Background cron job | 1 hour | MTTR, email metrics, SLA compliance |
| Daily | Background cron job (midnight IST) | 24 hours | Revenue, headcount, attrition, DSO, DPO |
| Weekly | Background cron job (Sunday midnight) | 7 days | Budget variance, campaign ROI, compliance score |
| Monthly | Background cron job (1st of month) | 30 days | P&L, Balance Sheet, board pack |

#### 3.1.4 Caching Implementation

```python
# Redis key format: kpi:{tenant_id}:{role}:{metric_name}
# Example: kpi:tenant_abc:cfo:cash_runway_months

CACHE_TTL = {
    "real_time": 30,        # 30 seconds
    "near_real_time": 300,  # 5 minutes
    "hourly": 3600,         # 1 hour
    "daily": 86400,         # 24 hours
    "weekly": 604800,       # 7 days
    "monthly": 2592000,     # 30 days
}

async def get_kpi(tenant_id: str, role: str, metric: str) -> dict:
    cache_key = f"kpi:{tenant_id}:{role}:{metric}"
    cached = await redis.get(cache_key)
    if cached:
        data = json.loads(cached)
        data["cached"] = True
        data["cache_age_seconds"] = time.time() - data["computed_at"]
        return data
    # Cache miss: compute from agent/connector
    result = await compute_kpi(tenant_id, role, metric)
    await redis.setex(cache_key, CACHE_TTL[result["refresh_type"]], json.dumps(result))
    return result
```

#### 3.1.5 Fallback Behavior (Connector Down)

When a connector fails, the dashboard must:

1. **Show last-known value** from PostgreSQL `kpi_cache` table (historical storage)
2. **Display staleness indicator**: Orange badge with "Last updated 3h ago"
3. **Show connector health icon**: Red dot next to the metric card
4. **Log the failure**: Write to `connector_health_log` table
5. **Retry in background**: Schedule retry based on connector type (30s for real-time, 5min for hourly)
6. **Alert if persistent**: If connector fails > 3 consecutive times, alert admin via Slack

### 3.2 Agent Architecture

#### 3.2.1 Domain Logic Requirements

Every agent class must implement domain-specific logic INSTEAD of just calling `super().execute()`. The required override pattern:

```python
@AgentRegistry.register
class ApProcessorAgent(BaseAgent):
    agent_type = "ap_processor"
    domain = "finance"
    confidence_floor = 0.88
    prompt_file = "ap_processor.prompt.txt"

    async def execute(self, task: TaskAssignment) -> TaskResult:
        """AP-specific execution with pre-processing and validation."""
        # 1. PRE-PROCESS: Extract and validate input fields
        invoice_data = self._extract_invoice_fields(task)
        if not invoice_data:
            return self._make_error_result(task, "E2010", "Missing required invoice fields")

        # 2. DOMAIN RULES: Apply AP-specific business logic BEFORE LLM
        if self._is_duplicate(invoice_data):
            return self._make_result(task, ..., status="duplicate", confidence=0.99)

        # 3. TOOL SELECTION: Determine which tools to call based on invoice type
        tools_needed = self._determine_tools(invoice_data)
        task.task.metadata["selected_tools"] = tools_needed

        # 4. EXECUTE BASE: Let LLM reason + call tools
        result = await super().execute(task)

        # 5. POST-PROCESS: Validate the LLM's output against domain rules
        if result.status == "completed":
            result = self._validate_ap_output(result, invoice_data)

        return result

    def _extract_invoice_fields(self, task) -> dict | None:
        """Extract required fields from task input."""
        data = task.task.metadata or {}
        required = ["invoice_id", "vendor_id", "amount"]
        if all(k in data for k in required):
            return data
        return None

    def _is_duplicate(self, invoice_data: dict) -> bool:
        """Check for duplicate invoice (same invoice_id + vendor_id)."""
        # Query agent_task_results for existing invoice
        ...

    def _determine_tools(self, invoice_data: dict) -> list[str]:
        """Select tools based on invoice characteristics."""
        tools = ["tally.get_ledger_balance"]
        if invoice_data.get("gstin"):
            tools.append("gstn.generate_einvoice_irn")
        if invoice_data["amount"] > 50000:
            tools.append("tally.post_voucher")
        return tools

    def _validate_ap_output(self, result, invoice_data) -> TaskResult:
        """Post-LLM validation: ensure output matches AP rules."""
        # Verify match_delta is within tolerance
        # Verify GL codes are valid
        # Verify payment date is logical
        ...
```

#### 3.2.2 Agent-by-Agent Domain Logic Specification

**Finance Domain Agents:**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `ap_processor` | Extract invoice fields, validate format | Duplicate check, GSTIN validation, 3-way match tolerance (2%) | GSTN for validation, Tally for posting, Banking for payment | Verify GL codes, match delta, payment date | 0.88 |
| `ar_collections` | Identify overdue invoices, calculate aging | Day 30/60/90 escalation rules, write-off thresholds | Tally for AR data, SendGrid for reminders, WhatsApp for follow-up | Verify reminder sent, collection tracked | 0.88 |
| `recon_agent` | Fetch bank + book data for date range | Match by amount+date+ref, tolerance 0.5%, break threshold 50K | Banking AA for bank data, Tally for book data | Verify all transactions accounted, breaks flagged | 0.95 |
| `close_agent` | Create month-end checklist from template | Checklist sequence enforcement, TB must balance | Tally for all entries, journals, TB | Verify TB balances, all items checked | 0.92 |
| `fpa_agent` | Gather actuals from multiple sources | Budget vs actual variance rules (>15% flag), forecast model | Tally for actuals, HubSpot for pipeline | Verify forecast reasonableness, variance flags | 0.88 |
| `tax_compliance` | Identify applicable filings for period | GST filing rules, TDS computation slabs, advance tax dates | GSTN for filing, Income Tax for TDS, Tally for data | Verify filing acknowledgment, amounts correct | 0.92 |

**HR Domain Agents:**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `talent_acquisition` | Parse requisition, extract JD keywords | Candidate scoring model (skills 40%, experience 30%, culture 30%) | LinkedIn for sourcing, Greenhouse for ATS, Google Calendar for scheduling | Verify shortlist count, scores calculated | 0.85 |
| `onboarding_agent` | Extract employee details from offer acceptance | Account provisioning sequence, document checklist enforcement | Darwinbox for record, Okta for SSO, Slack for invite, Jira for equipment | Verify all accounts created, docs sent | 0.95 |
| `payroll_engine` | Gather attendance, leave, deductions | PF ceiling (15K), ESI threshold (21K), PT slabs by state, TDS new/old regime | Darwinbox/Keka for payroll, EPFO for PF, Tally for journal | Verify statutory computations, payslip accuracy | 0.95 |
| `performance_coach` | Aggregate review scores from multiple reviewers | Weighted scoring (self 10%, peer 30%, manager 60%), PIP criteria (< 2.0 rating) | Darwinbox for performance data, SendGrid for notifications | Verify score aggregation, PIP recommendation | 0.88 |
| `ld_coordinator` | Identify skill gaps from performance data | Mandatory training rules (new joiners: 30 days), certification expiry (90-day alert) | Darwinbox for skills, Google Calendar for scheduling | Verify training enrollment, cert alerts | 0.85 |
| `offboarding_agent` | Extract LWD, calculate notice period | Access revocation within 24h of LWD, F&F within 30 days | Darwinbox for termination, Okta for access, EPFO for PF | Verify access revoked, F&F calculated | 0.95 |

**Marketing Domain Agents:**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `campaign_pilot` | Parse campaign brief, extract objectives | Budget allocation rules, ROAS minimum (1.5x), overspend alert (>120%) | Google Ads, Meta Ads, LinkedIn Ads for campaigns, HubSpot for leads | Verify ROAS calculated, budget tracked | 0.85 |
| `content_factory` | Identify content type and audience | Brand voice check, competitor mention review, SEO keyword inclusion | WordPress for blog, Buffer for social, Mailchimp for email | Verify readability score, SEO score | 0.82 |
| `seo_strategist` | Analyze current rankings and gaps | Keyword difficulty threshold (< 70 for targeting), content gap rules | Ahrefs for SEO data, GA4 for traffic | Verify keyword recommendations, audit score | 0.85 |
| `brand_monitor` | Ingest social mentions and news | Sentiment baseline comparison (>3x negative = crisis), competitive tracking | Brandwatch for monitoring, Twitter for mentions | Verify sentiment calculation, crisis detection | 0.82 |
| `crm_intelligence` | Aggregate CRM and intent data | Intent threshold (>70 = target), ABM engagement scoring | HubSpot for CRM, Bombora + G2 + TrustRadius for intent | Verify intent scores, target list updates | 0.85 |

**Ops Domain Agents:**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `it_operations` | Parse incident from PagerDuty webhook | Severity classification (P1: production down, P2: degraded, P3: minor, P4: cosmetic) | PagerDuty for incidents, ServiceNow for ITSM, Jira for tracking | Verify classification, escalation timing | 0.88 |
| `support_triage` | Extract ticket subject, description, customer info | Category classification (billing, technical, feature, general), sentiment analysis | Zendesk for tickets, Confluence for KB | Verify classification accuracy > 88% | 0.85 |
| `support_deflector` | Search knowledge base for matching articles | KB match threshold (>0.90 confidence for auto-response) | Zendesk for auto-response, Confluence for KB search | Verify response relevance, customer satisfaction | 0.90 |
| `vendor_manager` | Gather vendor data from Tally + contracts | Performance scoring (delivery 30%, quality 30%, cost 20%, communication 20%) | Tally for payments, DocuSign for contracts, Jira for tasks | Verify scoring accuracy, renewal alerts | 0.85 |
| `compliance_guard` | Scan all compliance areas | Weighted compliance scoring, regulatory deadline tracking | MCA for statutory, EPFO for PF, Sanctions API for screening | Verify compliance score, overdue alerts | 0.92 |
| `contract_intelligence` | Extract contract metadata | Risk clause identification (unlimited liability, broad IP, non-compete >2yr) | DocuSign for contracts, S3 for archive | Verify clause extraction, risk flags | 0.88 |

**Back Office Domain Agents:**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `legal_ops` | Parse contract text, identify document type | Clause extraction rules, risk scoring (0-1 per clause type) | DocuSign for signing, S3 for storage, Confluence for templates | Verify all clauses extracted, risk scored | 0.90 |
| `risk_sentinel` | Aggregate risk signals from all domains | Weighted risk model (financial 30%, compliance 25%, operational 25%, security 20%) | Sanctions API for screening, PagerDuty for security incidents | Verify risk score calculation, alert thresholds | 0.95 |
| `facilities_agent` | Parse maintenance requests, asset data | Asset lifecycle rules, maintenance scheduling rules | Jira for tracking, Tally for expense | Verify request routing, expense tracking | 0.82 |

### 3.3 KPI Data Pipeline

#### 3.3.1 Computation Flow

```
1. Cron / Event / API Request
       |
       v
2. KPI Compute Service (Python)
   - Determines which KPIs need refresh based on TTL
   - For each stale KPI:
     a. Instantiate appropriate agent(s)
     b. Create TaskAssignment with KPI computation instruction
     c. Execute agent (which calls connectors)
     d. Extract metric values from TaskResult.output
       |
       v
3. Store Results
   - agent_task_results (PostgreSQL) -- full execution record
   - kpi_cache (PostgreSQL) -- historical KPI values with timestamps
   - Redis -- current value with TTL for fast dashboard access
       |
       v
4. Dashboard API
   - Reads from Redis (fast path)
   - Falls back to kpi_cache (PostgreSQL) on cache miss
   - Returns value + computed_at + source + staleness indicator
```

#### 3.3.2 KPI Refresh Schedule

| Time | Action |
|------|--------|
| Every 30 seconds | Real-time KPIs: pending approvals, active incidents, open tickets |
| Every 5 minutes | Near-real-time KPIs: cash balance (AA), ticket volume |
| Every hour | Hourly KPIs: MTTR, email metrics, SLA compliance, agent execution stats |
| 00:00 IST daily | Daily KPIs: revenue, headcount, attrition, DSO, DPO, aging buckets |
| Sunday 00:00 IST | Weekly KPIs: budget variance, campaign ROI, compliance score, SEO metrics |
| 1st of month 00:00 IST | Monthly KPIs: P&L, Balance Sheet, payroll totals, board pack |

#### 3.3.3 Storage Schema

```sql
-- Historical KPI storage (PostgreSQL)
CREATE TABLE kpi_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    role VARCHAR(20) NOT NULL,           -- ceo, cfo, chro, cmo, coo, cbo
    metric_name VARCHAR(100) NOT NULL,
    metric_value JSONB NOT NULL,         -- supports scalar, array, object
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_agent VARCHAR(50),
    source_connectors TEXT[],
    refresh_type VARCHAR(20) NOT NULL,   -- real_time, hourly, daily, weekly, monthly
    ttl_seconds INTEGER NOT NULL,
    is_stale BOOLEAN DEFAULT FALSE,
    error TEXT,
    CONSTRAINT uq_kpi_tenant_role_metric UNIQUE (tenant_id, role, metric_name)
);

CREATE INDEX idx_kpi_cache_tenant_role ON kpi_cache (tenant_id, role);
CREATE INDEX idx_kpi_cache_computed_at ON kpi_cache (computed_at DESC);

-- Partitioned by month for historical queries
CREATE TABLE kpi_history (
    id UUID DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_agent VARCHAR(50)
) PARTITION BY RANGE (computed_at);
```

### 3.4 Connector Configuration Flow

#### 3.4.1 UI Flow

1. **Admin navigates to Connectors page** -> Lists all 54 connectors with status (configured/not configured)
2. **Clicks "Configure" on a connector** -> Opens configuration modal
3. **Auth type determines fields:**
   - `api_key`: Single API key input
   - `oauth2`: Client ID + Client Secret + Redirect URI -> "Authorize" button initiates OAuth flow
   - `api_key_oauth2`: API key + OAuth flow
   - `gsp_dsc`: API key + GSTIN + Username + Password + DSC upload
   - `dsc`: DSC certificate upload + API key
   - `tdl_xml`: Bridge URL + Bridge ID + Bridge Token (for Tally)
   - `jwt`: Integration key + RSA private key upload + Account ID (for DocuSign)
   - `rest_oauth2`: Client ID + Client Secret + Instance URL (for ServiceNow)
   - `aa_oauth2`: Client ID + Client Secret + FIU ID + Callback URL (for AA)
4. **Test Connection**: Button that calls `connector.health_check()` and shows result
5. **Save**: Encrypts credentials, stores in `connector_configs` table
6. **Auto-Discovery**: After successful connection, auto-populate basic data (e.g., Darwinbox -> employee count, HubSpot -> contact count)

#### 3.4.2 Credential Encryption

- All credentials encrypted at rest using AES-256-GCM
- Encryption key stored in GCP Secret Manager (not in DB or env vars)
- Credentials never logged, never returned in API responses (masked with `****`)
- Key rotation: Support re-encryption with new key without downtime

#### 3.4.3 Health Monitoring

- **Health check frequency:** Every 5 minutes for configured connectors
- **Status levels:**
  - Green: Health check passed in last 5 minutes
  - Yellow: Last health check failed but < 3 consecutive failures
  - Red: 3+ consecutive failures
  - Gray: Not configured
- **Dashboard display:** Connector health panel on CEO/Admin dashboard showing all 54 connectors with status dots
- **Alerting:** Red status -> Slack alert to admin channel

---

## 4. Database Migrations

### 4.1 New Tables

#### 4.1.1 `kpi_cache`

```sql
CREATE TABLE kpi_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    role VARCHAR(20) NOT NULL CHECK (role IN ('ceo', 'cfo', 'chro', 'cmo', 'coo', 'cbo')),
    metric_name VARCHAR(100) NOT NULL,
    metric_value JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_agent VARCHAR(50),
    source_connectors TEXT[],
    refresh_type VARCHAR(20) NOT NULL CHECK (refresh_type IN ('real_time', 'near_real_time', 'hourly', 'daily', 'weekly', 'monthly')),
    ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    is_stale BOOLEAN DEFAULT FALSE,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_kpi_cache_tenant_role_metric UNIQUE (tenant_id, role, metric_name)
);

CREATE INDEX idx_kpi_cache_lookup ON kpi_cache (tenant_id, role);
CREATE INDEX idx_kpi_cache_freshness ON kpi_cache (computed_at DESC);
ALTER TABLE kpi_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY kpi_cache_tenant_isolation ON kpi_cache
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.2 `kpi_history`

```sql
CREATE TABLE kpi_history (
    id UUID DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    metric_name VARCHAR(100) NOT NULL,
    metric_value JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (computed_at);

-- Create monthly partitions
CREATE TABLE kpi_history_2026_04 PARTITION OF kpi_history
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE kpi_history_2026_05 PARTITION OF kpi_history
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
-- Continue for 12 months...

CREATE INDEX idx_kpi_history_lookup ON kpi_history (tenant_id, role, metric_name, computed_at DESC);
ALTER TABLE kpi_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY kpi_history_tenant_isolation ON kpi_history
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.3 `agent_task_results`

```sql
CREATE TABLE agent_task_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    agent_id UUID NOT NULL,
    agent_type VARCHAR(50) NOT NULL,
    domain VARCHAR(20) NOT NULL,
    task_type VARCHAR(100),
    input JSONB,
    output JSONB,
    status VARCHAR(30) NOT NULL CHECK (status IN ('completed', 'failed', 'hitl_triggered', 'pending', 'cancelled')),
    confidence NUMERIC(4,3),
    tool_calls JSONB,
    reasoning_trace TEXT[],
    hitl_id UUID,
    error JSONB,
    llm_model VARCHAR(50),
    llm_tokens_used INTEGER,
    llm_cost_usd NUMERIC(10,6),
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    workflow_run_id UUID,
    step_id VARCHAR(50)
);

CREATE INDEX idx_atr_tenant_agent ON agent_task_results (tenant_id, agent_type);
CREATE INDEX idx_atr_tenant_created ON agent_task_results (tenant_id, created_at DESC);
CREATE INDEX idx_atr_tenant_domain ON agent_task_results (tenant_id, domain);
CREATE INDEX idx_atr_status ON agent_task_results (status);
ALTER TABLE agent_task_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY atr_tenant_isolation ON agent_task_results
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.4 `connector_configs`

```sql
CREATE TABLE connector_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    connector_name VARCHAR(50) NOT NULL,
    auth_type VARCHAR(30) NOT NULL,
    credentials_encrypted BYTEA NOT NULL,
    encryption_key_version INTEGER NOT NULL DEFAULT 1,
    config_metadata JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'configured' CHECK (status IN ('configured', 'active', 'error', 'disabled')),
    last_health_check TIMESTAMPTZ,
    health_status VARCHAR(10) DEFAULT 'gray' CHECK (health_status IN ('green', 'yellow', 'red', 'gray')),
    consecutive_failures INTEGER DEFAULT 0,
    last_sync_at TIMESTAMPTZ,
    auto_discovered_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_connector_config_tenant_name UNIQUE (tenant_id, connector_name)
);

CREATE INDEX idx_cc_tenant ON connector_configs (tenant_id);
CREATE INDEX idx_cc_health ON connector_configs (health_status);
ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY cc_tenant_isolation ON connector_configs
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.5 `connector_health_log`

```sql
CREATE TABLE connector_health_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    connector_name VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    response_time_ms INTEGER,
    error TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chl_tenant_connector ON connector_health_log (tenant_id, connector_name, checked_at DESC);
ALTER TABLE connector_health_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY chl_tenant_isolation ON connector_health_log
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.6 `workflow_schedules`

```sql
CREATE TABLE workflow_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    workflow_id UUID NOT NULL,
    workflow_name VARCHAR(100) NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    timezone VARCHAR(50) NOT NULL DEFAULT 'Asia/Kolkata',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    last_run_status VARCHAR(20),
    next_run_at TIMESTAMPTZ,
    run_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    timeout_minutes INTEGER DEFAULT 120,
    inputs JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ws_tenant ON workflow_schedules (tenant_id);
CREATE INDEX idx_ws_next_run ON workflow_schedules (next_run_at) WHERE is_active = TRUE;
ALTER TABLE workflow_schedules ENABLE ROW LEVEL SECURITY;
CREATE POLICY ws_tenant_isolation ON workflow_schedules
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.7 `board_reports`

```sql
CREATE TABLE board_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    report_type VARCHAR(50) NOT NULL CHECK (report_type IN ('mis_pack', 'board_pack', 'investor_update', 'compliance_report', 'audit_report')),
    report_period VARCHAR(20) NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'pending_review', 'approved', 'delivered', 'archived')),
    reviewed_by UUID,
    reviewed_at TIMESTAMPTZ,
    delivered_to TEXT[],
    delivered_at TIMESTAMPTZ,
    sections JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_br_tenant ON board_reports (tenant_id);
CREATE INDEX idx_br_type_period ON board_reports (tenant_id, report_type, report_period);
ALTER TABLE board_reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY br_tenant_isolation ON board_reports
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.8 `dashboard_preferences`

```sql
CREATE TABLE dashboard_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    user_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    visible_tabs TEXT[] NOT NULL DEFAULT '{}',
    tab_order TEXT[] DEFAULT '{}',
    default_date_range VARCHAR(20) DEFAULT '30d',
    refresh_interval_seconds INTEGER DEFAULT 60,
    pinned_metrics TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_dash_pref_user UNIQUE (tenant_id, user_id, role)
);

CREATE INDEX idx_dash_pref_tenant_user ON dashboard_preferences (tenant_id, user_id);
ALTER TABLE dashboard_preferences ENABLE ROW LEVEL SECURITY;
CREATE POLICY dash_pref_tenant_isolation ON dashboard_preferences
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.9 `hitl_requests`

This table is referenced by agents, workflows, and the CEO approval inbox but was not explicitly defined.

```sql
CREATE TABLE hitl_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    workflow_run_id UUID,
    step_id VARCHAR(50),
    agent_type VARCHAR(50) NOT NULL,
    agent_task_result_id UUID REFERENCES agent_task_results(id),
    request_type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    context JSONB NOT NULL DEFAULT '{}',
    required_role VARCHAR(20) NOT NULL,
    assigned_to UUID,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'escalated', 'expired')),
    decision_reason TEXT,
    decided_by UUID,
    decided_at TIMESTAMPTZ,
    escalated_to VARCHAR(20),
    escalated_at TIMESTAMPTZ,
    escalation_timeout_hours INTEGER NOT NULL DEFAULT 4,
    priority VARCHAR(10) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_hitl_tenant_status ON hitl_requests (tenant_id, status);
CREATE INDEX idx_hitl_tenant_role ON hitl_requests (tenant_id, required_role, status);
CREATE INDEX idx_hitl_assigned ON hitl_requests (assigned_to, status);
CREATE INDEX idx_hitl_created ON hitl_requests (created_at DESC);
ALTER TABLE hitl_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY hitl_tenant_isolation ON hitl_requests
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

#### 4.1.10 `audit_log`

Referenced by cost center management and multiple agents but not defined.

```sql
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID,
    agent_type VARCHAR(50),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    detail JSONB DEFAULT '{}',
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_audit_tenant_action ON audit_log (tenant_id, action, created_at DESC);
CREATE INDEX idx_audit_resource ON audit_log (tenant_id, resource_type, resource_id);
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_tenant_isolation ON audit_log
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Create partitions (monthly, retain 7 years per compliance requirements)
CREATE TABLE audit_log_2026_04 PARTITION OF audit_log
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
-- Continue for all months...
```

### 4.2 Partitioning Strategy

| Table | Partition Key | Partition Size | Retention |
|-------|-------------|----------------|-----------|
| `kpi_history` | `computed_at` | Monthly | 2 years (24 partitions) |
| `audit_log` | `created_at` | Monthly | 7 years (84 partitions) |
| `agent_task_results` | Not partitioned (indexed) | N/A | 1 year (cron job purges older records) |
| `connector_health_log` | Not partitioned | N/A | 90 days (cron job purges older records) |

**Partition maintenance cron:** Weekly job to create next month's partition and drop partitions beyond retention period.

### 4.3 Migration File

Migration file: `migrations/016_cxo_dashboards.sql`

Alembic version: `migrations/versions/v5_0_0_cxo_dashboards.py`

---

## 5. API Endpoints

### 5.0 API Versioning & Common Standards

#### 5.0.1 Versioning Strategy

All endpoints are prefixed with `/api/v1/`. When breaking changes are introduced, a new version (`/api/v2/`) is created while the old version is maintained for 6 months with deprecation warnings in the `Deprecation` response header.

#### 5.0.2 Common Error Response Schema

All endpoints return errors in this format:

```json
{
  "error": {
    "code": "ERR_4010",
    "message": "Human-readable error message",
    "detail": "Technical detail for debugging (omitted in production)",
    "request_id": "uuid-for-tracing"
  }
}
```

**Standard Error Codes:**

| HTTP Status | Code | Description |
|-------------|------|-------------|
| 400 | ERR_4000 | Malformed request body or invalid query parameter |
| 401 | ERR_4010 | Missing or expired JWT token |
| 403 | ERR_4030 | Valid token but insufficient RBAC role for this endpoint |
| 404 | ERR_4040 | Resource not found (agent, connector, report, workflow) |
| 409 | ERR_4090 | Conflict (e.g., duplicate connector config, concurrent workflow run) |
| 422 | ERR_4220 | Validation error (e.g., invalid cron expression, unsupported date range) |
| 429 | ERR_4290 | Rate limit exceeded (see `Retry-After` header) |
| 500 | ERR_5000 | Internal server error (agent execution failure, DB error) |
| 502 | ERR_5020 | Upstream connector error (external API unreachable) |
| 503 | ERR_5030 | Service temporarily unavailable (Redis down, DB failover in progress) |

#### 5.0.3 Rate Limiting

| Endpoint Category | Rate Limit | Window | Burst |
|-------------------|-----------|--------|-------|
| KPI read endpoints (`GET /kpis/*`) | 120 requests | per minute per tenant | 20 |
| KPI drill-down (`GET /kpis/*/detail/*`) | 60 requests | per minute per tenant | 10 |
| Agent execution (`POST /agents/*/run`) | 30 requests | per minute per tenant | 5 |
| Connector config (`POST /connectors/*/configure`) | 10 requests | per minute per tenant | 2 |
| Report generation (`POST /reports/*`) | 5 requests | per minute per tenant | 1 |
| Workflow scheduling (`POST /workflows/*`) | 20 requests | per minute per tenant | 5 |
| Health check (`GET /connectors/*/health`) | 200 requests | per minute per tenant | 30 |

Rate limit headers returned on every response:
- `X-RateLimit-Limit`: Total allowed in window
- `X-RateLimit-Remaining`: Remaining in current window
- `X-RateLimit-Reset`: Seconds until window resets
- `Retry-After`: Seconds to wait (only on 429 responses)

#### 5.0.4 Pagination

All list endpoints support cursor-based pagination:

```
GET /agents/{id}/results?limit=25&cursor=eyJjcmVhdGVkX2F0IjoiMjAyNi0wNC0wOFQxMDowMDowMFoifQ==
```

Response includes:
```json
{
  "data": [...],
  "pagination": {
    "total": 1250,
    "limit": 25,
    "has_more": true,
    "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wNC0wOFQwOTozMDowMFoifQ==",
    "prev_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wNC0wOFQxMDozMDowMFoifQ=="
  }
}
```

### 5.1 KPI Endpoints

#### `GET /kpis/ceo`

- **Description:** CEO overview KPIs aggregated from all departments
- **Auth:** JWT Bearer token
- **RBAC:** `admin` only
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string, default "default"): Multi-company selector
  - `date_range` (optional, string, default "30d"): 7d, 30d, 90d, 1y
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 422: Invalid `date_range` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure during KPI computation
  - 502: Upstream connector unreachable (partial data returned with `errors` array)
- **Response Schema:**
  ```json
  {
    "cached": true,
    "cache_age_seconds": 45,
    "company_id": "default",
    "finance": {
      "monthly_revenue": 7800000,
      "cash_runway_months": 14.2,
      "pending_approvals": 7
    },
    "hr": {
      "total_headcount": 145,
      "monthly_attrition_rate": 2.1,
      "open_positions": 12
    },
    "marketing": {
      "pipeline_value": 14200000,
      "cac": 3200,
      "mqls": 284
    },
    "ops": {
      "active_incidents": 2,
      "mttr_minutes": 42,
      "open_tickets": 156
    },
    "backoffice": {
      "compliance_score": 87,
      "active_contracts": 34,
      "pending_reviews": 5
    },
    "alerts": [
      {"type": "invoice_approval", "amount": 650000, "vendor": "XYZ Corp"},
      {"type": "p1_incident", "title": "API gateway latency spike"}
    ]
  }
  ```

#### `GET /kpis/cfo`

- **Description:** CFO finance KPIs (REPLACE existing hardcoded endpoint)
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `cfo`
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string)
  - `tab` (optional, string): `treasury`, `ap_ar`, `tax`, `close`, `budget` -- returns only KPIs for that tab
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin` or `cfo`
  - 422: Invalid `tab` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure
  - 502: Upstream connector unreachable (partial data returned)
- **Response Schema:** Same structure as current but with `"demo": false` and real values from connectors

#### `GET /kpis/chro` (NEW)

- **Description:** CHRO HR KPIs
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `chro`
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string)
  - `tab` (optional, string): `workforce`, `payroll`, `recruitment`, `engagement`, `compliance`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin` or `chro`
  - 422: Invalid `tab` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure
  - 502: Upstream connector unreachable (partial data returned)
- **Response Schema:**
  ```json
  {
    "cached": true,
    "cache_age_seconds": 120,
    "workforce": {
      "total_headcount": 145,
      "headcount_trend": [{"month": "2026-01", "count": 138}, ...],
      "department_breakdown": [{"dept": "Engineering", "count": 52}, ...],
      "monthly_attrition_rate": 2.1,
      "new_joiners_mtd": 8
    },
    "payroll": {
      "monthly_cost": 18500000,
      "pf_liability": 1620000,
      "esi_liability": 85000,
      "pt_liability": 42000,
      "tds_liability": 2150000,
      "payroll_status": "processed",
      "ctc_distribution": [{"band": "5-10L", "count": 45}, ...]
    },
    "recruitment": {
      "open_positions": 12,
      "pipeline_by_stage": [{"stage": "Screening", "count": 34}, ...],
      "avg_time_to_hire_days": 28,
      "offer_acceptance_rate": 72.5
    },
    "engagement": {
      "enps_score": 32,
      "pulse_response_rate": 78.5,
      "attrition_risk_high": 5,
      "retention_rate": 97.9
    },
    "compliance": {
      "epfo_status": "filed",
      "esi_status": "filed",
      "pt_status": "filed",
      "compliance_score": 92,
      "overdue_filings": 0
    }
  }
  ```

#### `GET /kpis/cmo`

- **Description:** CMO marketing KPIs (REPLACE existing hardcoded endpoint)
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `cmo`
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string)
  - `tab` (optional, string): `pipeline`, `campaigns`, `content`, `abm`, `brand`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin` or `cmo`
  - 422: Invalid `tab` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure
  - 502: Upstream connector unreachable (partial data returned)
- **Response:** Same structure as current but with real data

#### `GET /kpis/coo` (NEW)

- **Description:** COO operations KPIs
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `coo`
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string)
  - `tab` (optional, string): `it_ops`, `support`, `vendors`, `facilities`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin` or `coo`
  - 422: Invalid `tab` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure
  - 502: Upstream connector unreachable (partial data returned)
- **Response Schema:**
  ```json
  {
    "cached": true,
    "it_ops": {
      "active_incidents_p1p2": 2,
      "mttr_minutes": 42,
      "mttr_trend": [{"week": "W14", "mttr": 45}, ...],
      "change_success_rate": 94.2,
      "uptime_sla": 99.87,
      "incidents_by_severity": {"P1": 1, "P2": 3, "P3": 12, "P4": 28}
    },
    "support": {
      "ticket_volume_today": 156,
      "first_response_time_min": 8.5,
      "resolution_time_hours": 4.2,
      "deflection_rate": 73.0,
      "csat_score": 4.2,
      "sla_compliance": 96.5,
      "auto_classification_accuracy": 88.3
    },
    "vendors": {
      "active_vendors": 45,
      "contract_renewals_90d": 7,
      "spend_by_category": [{"category": "Software", "amount": 4500000}, ...],
      "avg_vendor_score": 72
    },
    "facilities": {
      "asset_utilization": 82.5,
      "open_maintenance_requests": 8,
      "facilities_expense_mtd": 850000,
      "travel_expense_mtd": 320000
    }
  }
  ```

#### `GET /kpis/cbo` (NEW)

- **Description:** CBO back-office KPIs
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `cbo`
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `company_id` (optional, string)
  - `tab` (optional, string): `legal`, `risk`, `corporate`, `comms`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin` or `cbo`
  - 422: Invalid `tab` value
  - 429: Rate limit exceeded
  - 500: Agent execution failure
  - 502: Upstream connector unreachable (partial data returned)
- **Response Schema:**
  ```json
  {
    "cached": true,
    "legal": {
      "active_contracts": 34,
      "pending_reviews": 5,
      "avg_review_time_days": 3.2,
      "nda_active": 28,
      "nda_expired": 4,
      "risk_clauses_flagged": 12,
      "active_litigation": 1
    },
    "risk": {
      "compliance_score": 87,
      "screening_alerts_unresolved": 3,
      "audit_findings_open": 8,
      "risk_register": {"critical": 1, "high": 4, "medium": 12, "low": 23},
      "policy_violations_mtd": 2
    },
    "corporate": {
      "board_meetings_ytd": 3,
      "statutory_filings_upcoming_90d": 2,
      "director_kyc_status": [{"name": "Dir A", "status": "completed"}, ...],
      "pending_board_resolutions": 1
    },
    "comms": {
      "internal_comms_reach": 85.0,
      "newsletter_open_rate": 42.5,
      "media_coverage_30d": 18,
      "investor_query_response_days": 1.5
    }
  }
  ```

#### `GET /kpis/{role}/detail/{metric}`

- **Description:** Drill-down for any specific metric (time series, breakdown, details)
- **Auth:** JWT Bearer token
- **RBAC:** Corresponding role + admin
- **Rate Limit:** 60/min per tenant
- **Path Params:**
  - `role` (required, string): ceo, cfo, chro, cmo, coo, cbo
  - `metric` (required, string): metric_name (e.g., "cash_runway_months", "total_headcount")
- **Query Params:**
  - `date_from` (optional, ISO date string): Start date for historical data
  - `date_to` (optional, ISO date string): End date for historical data
  - `granularity` (optional, string): daily, weekly, monthly
- **Error Responses:**
  - 400: Invalid date format in `date_from` or `date_to`
  - 401: Token missing or expired
  - 403: User role does not match the `role` path param
  - 404: Metric not found for the specified role
  - 422: Invalid `granularity` value or `date_from` > `date_to`
  - 429: Rate limit exceeded
  - 500: Agent execution failure
- **Response Schema:**
  ```json
  {
    "metric": "cash_runway_months",
    "current_value": 14.2,
    "computed_at": "2026-04-08T09:30:00Z",
    "source": "fpa_agent",
    "history": [
      {"date": "2026-03-01", "value": 12.8},
      {"date": "2026-03-15", "value": 13.5},
      {"date": "2026-04-01", "value": 14.2}
    ],
    "breakdown": {
      "total_cash": 25700000,
      "monthly_burn": 1850000,
      "formula": "total_cash / monthly_burn"
    }
  }
  ```

### 5.2 Agent Endpoints

#### `POST /agents/{id}/run`

- **Description:** Trigger an agent execution manually
- **Auth:** JWT Bearer token
- **RBAC:** admin + corresponding CxO role
- **Rate Limit:** 30/min per tenant
- **Path Params:**
  - `id` (required, UUID): Agent ID
- **Request Body:**
  ```json
  {
    "task_type": "compute_kpi",
    "inputs": {"metric": "cash_runway_months"},
    "priority": "normal"
  }
  ```
  - `task_type` (required, string): Type of task to execute
  - `inputs` (required, object): Task-specific input data
  - `priority` (optional, string, default "normal"): "low", "normal", "high", "critical"
- **Response:** `{"task_id": "uuid", "status": "queued"}`
- **Error Responses:**
  - 400: Missing `task_type` or `inputs`
  - 401: Token missing or expired
  - 403: User role does not authorize this agent's domain
  - 404: Agent ID not found
  - 409: Agent is already executing a task for this tenant (concurrent limit reached)
  - 422: Invalid `priority` value
  - 429: Rate limit exceeded
  - 500: Internal error queuing the task

#### `GET /agents/{id}/results`

- **Description:** Get agent execution history
- **Auth:** JWT Bearer token
- **RBAC:** admin + corresponding CxO role
- **Rate Limit:** 120/min per tenant
- **Path Params:**
  - `id` (required, UUID): Agent ID
- **Query Params:**
  - `limit` (optional, integer, default 25, max 100): Number of results per page
  - `cursor` (optional, string): Pagination cursor from previous response
  - `status` (optional, string): Filter by status -- `completed`, `failed`, `hitl_triggered`, `pending`, `cancelled`
  - `date_from` (optional, ISO datetime): Filter by created_at >= date_from
  - `date_to` (optional, ISO datetime): Filter by created_at <= date_to
- **Response:** Paginated list of `agent_task_results` (see pagination schema in 5.0.4)
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role does not authorize this agent's domain
  - 404: Agent ID not found
  - 422: Invalid cursor, date format, or status value
  - 429: Rate limit exceeded

### 5.3 Connector Endpoints

#### `POST /connectors/{name}/configure`

- **Description:** Configure connector credentials for current tenant
- **Auth:** JWT Bearer token
- **RBAC:** `admin` only
- **Rate Limit:** 10/min per tenant
- **Path Params:**
  - `name` (required, string): Connector name from the 54-connector registry (e.g., "hubspot", "tally", "gstn")
- **Request Body:**
  ```json
  {
    "auth_type": "oauth2",
    "credentials": {
      "client_id": "abc",
      "client_secret": "xyz",
      "refresh_token": "token123"
    },
    "config_metadata": {
      "domain": "mycompany"
    }
  }
  ```
  - `auth_type` (required, string): Must match the connector's expected auth type
  - `credentials` (required, object): Auth-type-specific credential fields
  - `config_metadata` (optional, object): Additional connector-specific configuration
- **Response:** `{"status": "configured", "connector": "hubspot"}`
- **Error Responses:**
  - 400: Missing required credential fields for this auth type
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: Connector name not found in registry
  - 409: Connector already configured for this tenant (use PUT to update)
  - 422: `auth_type` does not match expected type for this connector
  - 429: Rate limit exceeded
  - 500: Encryption failure or DB error
- **Security:** Credentials encrypted with AES-256-GCM before storage. Never returned in any API response.

#### `PUT /connectors/{name}/configure`

- **Description:** Update existing connector credentials for current tenant
- **Auth:** JWT Bearer token
- **RBAC:** `admin` only
- **Rate Limit:** 10/min per tenant
- **Path Params:**
  - `name` (required, string): Connector name
- **Request Body:** Same as POST
- **Response:** `{"status": "updated", "connector": "hubspot"}`
- **Error Responses:**
  - 400: Missing required credential fields
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: Connector not configured for this tenant (use POST to create)
  - 429: Rate limit exceeded

#### `DELETE /connectors/{name}/configure`

- **Description:** Remove connector configuration and revoke stored credentials
- **Auth:** JWT Bearer token
- **RBAC:** `admin` only
- **Rate Limit:** 10/min per tenant
- **Path Params:**
  - `name` (required, string): Connector name
- **Response:** `{"status": "removed", "connector": "hubspot"}`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: Connector not configured for this tenant
  - 409: Connector is in use by active workflows (must disable workflows first)
  - 429: Rate limit exceeded

#### `GET /connectors/{name}/health`

- **Description:** Check connector health status
- **Auth:** JWT Bearer token
- **Rate Limit:** 200/min per tenant
- **Path Params:**
  - `name` (required, string): Connector name
- **Response:**
  ```json
  {
    "connector": "hubspot",
    "status": "green",
    "last_check": "2026-04-08T09:45:00Z",
    "response_time_ms": 245,
    "consecutive_failures": 0,
    "last_sync": "2026-04-08T09:30:00Z"
  }
  ```
- **Error Responses:**
  - 401: Token missing or expired
  - 404: Connector name not found or not configured for this tenant
  - 429: Rate limit exceeded

#### `GET /connectors/health/all`

- **Description:** All connector health statuses for current tenant
- **Auth:** JWT Bearer token
- **RBAC:** `admin`
- **Rate Limit:** 60/min per tenant
- **Response:** Array of health objects for all 54 connectors (gray status for unconfigured)
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 429: Rate limit exceeded

#### `POST /connectors/{name}/test`

- **Description:** Test connector connection with provided (unsaved) credentials
- **Auth:** JWT Bearer token
- **RBAC:** `admin`
- **Rate Limit:** 10/min per tenant
- **Path Params:**
  - `name` (required, string): Connector name
- **Request Body:** Same as configure
- **Response:** `{"status": "healthy", "details": {...}}` or `{"status": "unhealthy", "error": "..."}`
- **Error Responses:**
  - 400: Missing required credential fields
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: Connector name not found in registry
  - 422: Invalid credential format
  - 429: Rate limit exceeded
  - 502: External API unreachable during test
  - 504: External API timeout during test (> 30 seconds)

### 5.4 Report Endpoints

#### `POST /reports/board-pack`

- **Description:** Generate board reporting pack
- **Auth:** JWT Bearer token
- **RBAC:** `admin`, `cfo`
- **Rate Limit:** 5/min per tenant
- **Request Body:**
  ```json
  {
    "report_type": "mis_pack",
    "period": "2026-03",
    "sections": ["pl", "bs", "cashflow", "hr", "marketing", "ops", "compliance"],
    "format": "pdf"
  }
  ```
  - `report_type` (required, string): `mis_pack`, `board_pack`, `investor_update`, `compliance_report`, `audit_report`
  - `period` (required, string): Year-month in YYYY-MM format
  - `sections` (optional, string[]): Sections to include (default: all). Valid values: `pl`, `bs`, `cashflow`, `hr`, `marketing`, `ops`, `compliance`
  - `format` (optional, string, default "pdf"): `pdf` or `xlsx`
- **Response:** `{"report_id": "uuid", "status": "generating", "estimated_time_seconds": 120}`
- **Error Responses:**
  - 400: Missing `report_type` or `period`
  - 401: Token missing or expired
  - 403: User role is not `admin` or `cfo`
  - 409: Report for this period and type already being generated
  - 422: Invalid `period` format, unknown `report_type`, or invalid `sections` value
  - 429: Rate limit exceeded
  - 500: Report generation infrastructure failure

#### `GET /reports/{id}`

- **Description:** Get report status and download URL
- **Auth:** JWT Bearer token
- **Rate Limit:** 120/min per tenant
- **Path Params:**
  - `id` (required, UUID): Report ID
- **Response:**
  ```json
  {
    "report_id": "uuid",
    "status": "ready",
    "download_url": "/reports/uuid/download",
    "generated_at": "2026-04-08T10:00:00Z",
    "file_size_bytes": 2457600
  }
  ```
  - `status` values: `generating`, `ready`, `failed`, `expired`
- **Error Responses:**
  - 401: Token missing or expired
  - 404: Report ID not found for this tenant
  - 429: Rate limit exceeded

#### `GET /reports/{id}/download`

- **Description:** Download generated report (PDF)
- **Auth:** JWT Bearer token
- **Rate Limit:** 30/min per tenant
- **Path Params:**
  - `id` (required, UUID): Report ID
- **Response:** Binary PDF or XLSX file with `Content-Type: application/pdf` or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- **Error Responses:**
  - 401: Token missing or expired
  - 404: Report ID not found or report not yet ready
  - 410: Report file has been purged (expired after 30 days)
  - 429: Rate limit exceeded

### 5.5 Workflow Scheduling Endpoints

#### `GET /workflows/scheduled`

- **Description:** List all scheduled workflows for current tenant
- **Auth:** JWT Bearer token
- **RBAC:** `admin` + corresponding CxO
- **Rate Limit:** 120/min per tenant
- **Query Params:**
  - `limit` (optional, integer, default 25, max 100): Page size
  - `cursor` (optional, string): Pagination cursor
  - `domain` (optional, string): Filter by domain -- `finance`, `hr`, `marketing`, `ops`, `backoffice`
  - `is_active` (optional, boolean): Filter by active/inactive status
- **Response:** Paginated list of `workflow_schedules` (see pagination schema in 5.0.4)
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role does not match any workflow domain
  - 422: Invalid `domain` value or cursor
  - 429: Rate limit exceeded

#### `POST /workflows/{id}/schedule`

- **Description:** Create or update a workflow schedule
- **Auth:** JWT Bearer token
- **RBAC:** `admin`
- **Rate Limit:** 20/min per tenant
- **Path Params:**
  - `id` (required, UUID): Workflow ID
- **Request Body:**
  ```json
  {
    "cron_expression": "0 9 * * 1-5",
    "timezone": "Asia/Kolkata",
    "is_active": true,
    "inputs": {"company_id": "default"},
    "timeout_minutes": 120,
    "max_retries": 3
  }
  ```
  - `cron_expression` (required, string): Valid cron expression (5-field)
  - `timezone` (optional, string, default "Asia/Kolkata"): IANA timezone string
  - `is_active` (optional, boolean, default true): Enable or disable the schedule
  - `inputs` (optional, object): Workflow input parameters
  - `timeout_minutes` (optional, integer, default 120, max 1440): Max execution time
  - `max_retries` (optional, integer, default 3, max 10): Retry count on failure
- **Response:** `{"schedule_id": "uuid", "next_run_at": "2026-04-09T09:00:00+05:30"}`
- **Error Responses:**
  - 400: Missing `cron_expression`
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: Workflow ID not found
  - 422: Invalid cron expression, unknown timezone, or `timeout_minutes` out of range
  - 429: Rate limit exceeded

#### `DELETE /workflows/{id}/schedule`

- **Description:** Remove a workflow schedule
- **Auth:** JWT Bearer token
- **RBAC:** `admin`
- **Rate Limit:** 20/min per tenant
- **Path Params:**
  - `id` (required, UUID): Workflow ID
- **Response:** `{"status": "deleted", "workflow_id": "uuid"}`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role is not `admin`
  - 404: No schedule exists for this workflow
  - 409: Workflow is currently executing (wait for completion or cancel first)
  - 429: Rate limit exceeded

#### `POST /workflows/{id}/schedule/run-now`

- **Description:** Manually trigger a scheduled workflow immediately
- **Auth:** JWT Bearer token
- **RBAC:** `admin` + corresponding CxO
- **Rate Limit:** 20/min per tenant
- **Path Params:**
  - `id` (required, UUID): Workflow ID
- **Request Body (optional):**
  ```json
  {
    "inputs": {"company_id": "default"},
    "override_timeout_minutes": 60
  }
  ```
- **Response:** `{"run_id": "uuid", "status": "started", "started_at": "2026-04-08T10:00:00Z"}`
- **Error Responses:**
  - 401: Token missing or expired
  - 403: User role does not match workflow domain
  - 404: Workflow ID not found or no schedule exists
  - 409: Workflow is already running for this tenant
  - 429: Rate limit exceeded
  - 500: Failed to start workflow execution

---

## 6. UI/UX Specifications

### 6.1 CEO Dashboard (`CEODashboard.tsx`)

**Route:** `/dashboard/ceo`

**Layout:** 4-quadrant overview with alert banner at top

**Components:**

1. **Alert Banner** (top, full-width)
   - Red banner for critical items (P1 incidents, invoices > INR 5L pending approval, security alerts)
   - Each alert: icon + title + action button ("Review")
   - Clicking alert navigates to relevant CxO dashboard
   - Max 3 alerts shown, "+N more" link to approvals page

2. **Finance Quadrant** (top-left)
   - Monthly Revenue: Large number + MoM trend arrow
   - Cash Runway: Large number (months) + trend
   - Burn Rate: Large number (INR) + trend
   - Click: Navigates to `/dashboard/cfo`

3. **HR Quadrant** (top-right)
   - Headcount: Large number + department mini-bar
   - Attrition Rate: Large number (%) + trend
   - Open Positions: Large number
   - Click: Navigates to `/dashboard/chro`

4. **Marketing Quadrant** (bottom-left)
   - Pipeline Value: Large number (INR) + trend
   - CAC: Large number (INR) + trend (inverted -- lower is better)
   - MQLs: Large number + trend
   - Click: Navigates to `/dashboard/cmo`

5. **Operations Quadrant** (bottom-right)
   - Active Incidents: Large number (color-coded by severity)
   - MTTR: Large number (min) + trend
   - Open Tickets: Large number + SLA compliance badge
   - Click: Navigates to `/dashboard/coo`

6. **Agent Observatory** (below quadrants)
   - Real-time scrolling feed of agent actions
   - Each row: timestamp, agent name, task type, status (green/yellow/red dot), confidence, duration
   - Filter by domain (finance, hr, marketing, ops, backoffice)
   - Max 20 recent entries, "View All" link to Observatory page

7. **Cost Center Overview** (bottom section)
   - Per-department LLM cost bar chart
   - Total platform cost (MTD) number
   - Cost trend line (last 30 days)

**Interactions:**
- Each quadrant is a clickable card that navigates to the full CxO dashboard
- Alert banner items have "Approve" / "Review" buttons for quick actions
- Observatory feed auto-refreshes every 10 seconds
- Date range selector (top-right): 7d / 30d / 90d / 1y

### 6.2 CFO Dashboard (UPDATE existing `CFODashboard.tsx`)

**Route:** `/dashboard/cfo` (existing)

**Changes from current:**
- Remove `{data.demo && <Badge>Demo Data</Badge>}` -- replaced with `"demo": false` from API
- Add tab navigation: Treasury | AP/AR | Tax | Close | Budget
- Replace hardcoded number rendering with live data + staleness indicators
- Add connector health indicators next to each data section

**Tab: Treasury** (default)
- Bank balance cards: One card per account. Account name, balance (large font), currency. Green/yellow/red connector health dot.
- Cash flow forecast chart: 12-month line chart. Historical (solid line) + forecast (dashed line).
- FD maturity calendar: Table with FD name, amount, maturity date, days remaining. Sorted by maturity date.
- Net cash position: Single large number with breakdown tooltip.

**Tab: AP/AR**
- AR Aging chart: Stacked bar chart (0-30, 31-60, 61-90, 90+). Existing chart enhanced with click-through.
- AP Aging chart: Same format. Existing chart enhanced.
- DSO and DPO cards: Large numbers with trend arrows.
- Pending invoices table: Invoice ID, vendor, amount, status, due date. Sortable columns. Click to view details.
- Collection efficiency trend: Line chart (12 months).

**Tab: Tax**
- 12-month GST filing calendar: Grid layout. Each month: GSTR-1, GSTR-3B, GSTR-9 status (filed/pending/overdue). Color-coded cells.
- TDS quarterly status: 4-quarter view with filing status.
- ITC reconciliation summary: Claimed vs Available. Mismatch amount highlighted in red if > threshold.
- Advance tax dues: Next installment date and amount.
- Compliance notices: List of any notices with status.

**Tab: Close**
- Month-end checklist: Vertical checklist with checkboxes. Completed items in green. Pending in yellow. Blocked in red.
- Completion percentage: Large circular progress indicator.
- Estimated close date: Calculated from completion velocity.
- Trial balance status: Balance/Imbalance indicator.
- P&L summary: Mini table with revenue, COGS, gross margin, OPEX, net income.

**Tab: Budget**
- Budget vs Actual waterfall chart: Budget as baseline, +/- by department.
- Department-wise spend table: Department, Budget, Actual, Variance %, Status badge.
- Revenue forecast vs actual: Dual-axis chart.
- Unit economics: CAC, LTV, LTV/CAC displayed as cards.
- Forecast accuracy: Percentage with trend.

### 6.3 CHRO Dashboard (NEW `CHRODashboard.tsx`)

**Route:** `/dashboard/chro`

**Tab Navigation:** Workforce | Payroll | Recruitment | Engagement | Compliance

**Tab: Workforce** (default)
- Headcount trend: Line chart (12 months). Total + new joiners + exits.
- Department breakdown: Horizontal bar chart. Click department to see team members.
- Attrition rate: Monthly line chart with target line (< 2% / month).
- New joiners this month: List with name, department, joining date, onboarding status.

**Tab: Payroll**
- Current month payroll status: Badge (not started / in progress / processed / disbursed).
- Payroll cost: Large number with MoM trend.
- Statutory dues summary: PF, ESI, PT, TDS -- each as a card with amount and filing status.
- CTC distribution: Histogram by CTC band (0-5L, 5-10L, 10-20L, 20-50L, 50L+).

**Tab: Recruitment**
- Open positions table: Position title, department, posted date, applicants, status. Sortable.
- Pipeline funnel chart: Applied -> Screened -> Interviewed -> Offered -> Accepted. With conversion rates.
- Time-to-hire: Average days with trend.
- Source effectiveness: Bar chart by source (LinkedIn, Naukri, Referral, Direct).
- Offer acceptance rate: Percentage with trend.

**Tab: Engagement**
- eNPS gauge: Semi-circle gauge (-100 to 100). Color-coded (red < 0, yellow 0-30, green > 30).
- Pulse survey results: Latest survey scores by question category.
- Attrition risk heatmap: Department x Risk Level matrix. Color intensity = number of at-risk employees.
- Retention rate: Monthly percentage with trend.

**Tab: Compliance**
- Filing status grid: EPFO, ESI, PT rows x 12-month columns. Color-coded (green=filed, yellow=pending, red=overdue).
- Compliance score: Large number (0-100) with gauge.
- Overdue items: List with filing type, due date, days overdue.
- Upcoming deadlines: Next 30 days of compliance items.

### 6.4 CMO Dashboard (UPDATE existing `CMODashboard.tsx`)

**Route:** `/dashboard/cmo` (existing)

**Changes:** Add tab navigation, replace hardcoded data.

**Tab Navigation:** Pipeline | Campaigns | Content | ABM | Brand

**Tab: Pipeline** (default)
- MQL/SQL funnel: Vertical funnel chart with numbers and conversion rates at each stage.
- CAC trend: Line chart (12 months). Target line at company CAC goal.
- LTV/CAC ratio: Large number with trend arrow.
- Pipeline value: Large number with stage breakdown.

**Tab: Campaigns**
- Active campaigns table: Campaign name, channel, budget, spend, impressions, clicks, conversions, ROAS. Sortable.
- Spend vs Budget: Stacked bar chart by channel.
- ROAS by channel: Existing bar chart with real data.
- Campaign performance trend: Line chart of daily conversions.

**Tab: Content**
- Content calendar: Monthly view with planned vs published content.
- Top performing content: Existing table with real data from GA4 / WordPress.
- Social engagement: Existing chart with real data from Buffer / Twitter.
- Email metrics: Open rate, click rate, unsubscribe rate with trends.

**Tab: ABM**
- Target accounts table: Company name, intent score (Bombora), engagement score, pipeline stage. Sortable.
- Intent heatmap: Topic x Company matrix with surge scores.
- ABM pipeline: Pipeline value from ABM-tagged accounts.
- Multi-touch attribution: Pie chart showing channel contributions.

**Tab: Brand**
- Brand sentiment gauge: Existing gauge with real data from Brandwatch.
- Share of voice: Donut chart (brand vs competitors).
- Media mentions: Timeline chart with daily mention count.
- Competitor tracking: Table with competitor name, sentiment, SOV, recent activity.

### 6.5 COO Dashboard (NEW `COODashboard.tsx`)

**Route:** `/dashboard/coo`

**Tab Navigation:** IT Ops | Support | Vendors | Facilities

**Tab: IT Ops** (default)
- Active incidents: List with severity badge (P1 red, P2 orange, P3 yellow, P4 gray), title, time since creation, assigned to.
- MTTR trend: Line chart (12 weeks). Target line at 30 minutes for P1, 4 hours for P2.
- Change success rate: Donut chart with percentage.
- Uptime SLA: Large number with 99.5% target line.
- Incident by severity: Stacked bar chart (30 days, weekly grouping).

**Tab: Support**
- Ticket volume: Area chart (30 days, daily). Split by category (billing, technical, feature, general).
- Resolution time: Box plot or average with P95.
- CSAT trend: Line chart (30 days).
- Deflection rate: Large percentage with trend.
- SLA compliance: Donut chart with percentage.
- Top issues: Table with issue category, count, avg resolution time.

**Tab: Vendors**
- Active vendors table: Vendor name, category, contract value, expiry date, performance score. Sortable.
- Contract renewals calendar: Next 90 days with renewal dates.
- Spend by category: Donut chart.
- Vendor scorecards: Selected vendor detail with performance dimensions.

**Tab: Facilities**
- Asset utilization: Donut chart.
- Open maintenance requests: List with priority, description, assigned to, age.
- Expense tracking: Facilities + Travel + Equipment expense trend.

### 6.6 CBO Dashboard (NEW `CBODashboard.tsx`)

**Route:** `/dashboard/cbo`

**Tab Navigation:** Legal | Risk | Corporate | Comms

**Tab: Legal** (default)
- Active contracts: Table with contract name, counterparty, value, start date, expiry date, status.
- Pending reviews: Count badge + list with document name, received date, priority.
- NDA tracker: Active / Expired / Expiring-soon counts.
- Litigation tracker: Active cases with case name, type, status, next hearing date.
- Risk clauses flagged: Recent flagged clauses with contract name and clause type.

**Tab: Risk**
- Compliance score gauge: Large circular gauge (0-100). Color bands at 70 (yellow) and 90 (green).
- Screening results: Recent sanctions screening with entity name, match status, resolution.
- Audit findings: Open findings table with finding, severity, due date, owner.
- Risk register: Summary by severity (critical, high, medium, low) with counts.
- Policy violations: MTD count with trend.

**Tab: Corporate**
- Board meeting calendar: Next meeting date, agenda items count.
- Statutory filing status: Grid similar to CHRO compliance tab.
- Director KYC status: Table with director name, KYC status, expiry date.
- Pending resolutions: List with resolution title, date, signature status.

**Tab: Comms**
- Internal comms reach: Percentage with trend.
- Media coverage: Timeline chart (30 days).
- Newsletter metrics: Open rate, click rate.
- Investor query tracker: Pending queries with response time.

### 6.7 Common UI Elements

**Date Range Selector:** Global component in dashboard header. Options: 7d, 30d, 90d, 1y, custom. Persists per session.

**Connector Health Strip:** Thin horizontal bar below the tab navigation showing configured connectors for that role with green/yellow/red/gray dots. Hovering shows connector name and last check time.

**Export Button:** Every data table and chart has an "Export" dropdown: CSV, PDF, PNG (for charts).

**Refresh Button:** Manual refresh button next to date range selector. Shows spinner during refresh.

**Stale Data Indicator:** Orange badge on any card/chart where data is older than 2x the expected TTL.

### 6.8 State Definitions

#### 6.8.1 Empty States

Every dashboard tab must handle the "no data" case:

| State | Visual | Message | CTA |
|-------|--------|---------|-----|
| No connectors configured for this role | Ghost icon + illustration | "Connect your [Tally/Darwinbox/etc.] to see real data here" | "Configure Connectors" button -> `/dashboard/connectors` |
| Connector configured but no data yet | Spinner + text | "Fetching data from [connector name]... This may take up to 2 minutes on first load" | None (auto-refresh) |
| No data for selected date range | Calendar icon | "No data available for the selected period. Try expanding the date range." | "Reset to 30 days" link |
| No pending approvals | Checkmark icon | "All caught up! No pending approvals." | None |
| No active incidents (COO) | Green shield icon | "All clear -- no active incidents." | None |
| No open positions (CHRO) | Party icon | "All positions filled!" | "Create Requisition" button |
| No active campaigns (CMO) | Rocket icon | "No active campaigns. Launch one to start tracking." | "Create Campaign" button |

#### 6.8.2 Loading States

All dashboard components use consistent skeleton screens:

- **KPI Cards:** Gray animated shimmer rectangles matching card dimensions (height 80px, rounded corners)
- **Charts:** Gray shimmer rectangle matching chart area, with faint grid lines placeholder
- **Tables:** 5 rows of gray shimmer bars with column header placeholders
- **Gauges:** Gray circular shimmer
- **Loading timeout:** If data does not load within 10 seconds, show error state with retry button
- **Progressive loading:** KPI cards load first (fastest), then charts, then tables (heaviest)

#### 6.8.3 Error States

| Error Type | Visual | Message | Actions |
|------------|--------|---------|---------|
| Connector down (single) | Red dot on affected card + orange banner | "[Connector] is unreachable. Showing last known data from [timestamp]." | "Retry" button, "View Health" link |
| Connector down (all) | Full-width red banner | "All data sources are unreachable. Showing cached data." | "Retry All" button, "Contact Support" link |
| API error (500) | Error illustration | "Something went wrong loading your dashboard. Our team has been notified." | "Retry" button, "View Status Page" link |
| Auth expired (401) | Lock icon | "Your session has expired. Please log in again." | "Log In" button -> login page |
| Forbidden (403) | Shield icon | "You don't have permission to view this dashboard." | "Request Access" button, "Go Home" link |
| Rate limited (429) | Clock icon | "Too many requests. Please wait a moment." | Auto-retry after `Retry-After` header value |

#### 6.8.4 Responsive Behavior

| Breakpoint | Layout | Behavior |
|------------|--------|----------|
| Desktop (1440px+) | Full multi-column layout | All tabs visible, side-by-side charts |
| Laptop (1024px - 1439px) | Reduced column layout | Charts stack to single column per row, tables scroll horizontally |
| Tablet (768px - 1023px) | Single column | Tabs collapse to dropdown, all cards full-width, charts full-width, tables with horizontal scroll |
| Mobile (< 768px) | Not officially supported | Redirect to "Use tablet or desktop" notice with read-only KPI summary cards |

**Specific responsive rules:**
- CEO 4-quadrant layout: 2x2 grid on desktop, 2x1 on laptop, 1x1 stack on tablet
- Tab navigation: Horizontal tabs on desktop/laptop, dropdown selector on tablet
- Data tables: Fixed first column (metric name), horizontal scroll for remaining columns on narrow screens
- Chart minimum width: 320px. If container < 320px, show summary numbers instead of chart
- Export buttons: Full text ("Export CSV") on desktop, icon-only on tablet

---

## 7. Testing Plan

### 7.1 Unit Tests

#### 7.1.1 Agent Unit Tests

For each of the 35 agents, test:

| Test Category | Test Count per Agent | Total |
|---------------|---------------------|-------|
| Domain logic (pre-processing) | 3-5 | ~140 |
| Tool selection rules | 2-3 | ~85 |
| Prompt construction | 1-2 | ~50 |
| Confidence scoring | 2-3 | ~85 |
| HITL conditions | 3-5 | ~140 |
| Output validation | 2-3 | ~85 |
| Error handling | 2-3 | ~85 |
| **Subtotal** | | **~670** |

Example tests (per agent):

**`ap_processor` tests:**
- `test_ap_extract_invoice_fields_complete` -- All required fields present
- `test_ap_extract_invoice_fields_missing_vendor` -- Missing vendor_id returns None
- `test_ap_duplicate_detection_same_invoice` -- Exact match returns True
- `test_ap_gstin_validation_correct_format` -- Valid GSTIN format check
- `test_ap_three_way_match_exact` -- PO=Invoice -> matched
- `test_ap_three_way_match_within_tolerance` -- Delta < 2% -> matched
- `test_ap_three_way_match_over_tolerance` -- Delta > 2% -> mismatch + HITL
- `test_ap_hitl_amount_threshold_triggered` -- Amount > 500000 -> HITL
- `test_ap_hitl_amount_threshold_not_triggered` -- Amount < 500000 -> no HITL
- `test_ap_confidence_scoring_high_match` -- Exact match -> confidence 0.95
- `test_ap_confidence_scoring_low_match` -- Poor match -> confidence 0.72
- `test_ap_tool_selection_with_gstin` -- Has GSTIN -> include gstn tool
- `test_ap_tool_selection_without_gstin` -- No GSTIN -> skip gstn tool
- `test_ap_output_validation_valid` -- All fields present -> valid
- `test_ap_output_validation_invalid` -- Missing status -> invalid

#### 7.1.2 KPI Endpoint Unit Tests

For each of the 6 KPI endpoints, test:

| Test Category | Test Count per Endpoint | Total |
|---------------|------------------------|-------|
| Happy path (all connectors return data) | 1 | 6 |
| Partial connector failure | 2 | 12 |
| All connectors fail | 1 | 6 |
| Cache hit (return cached) | 1 | 6 |
| Cache miss (compute fresh) | 1 | 6 |
| Stale data fallback | 1 | 6 |
| Tab filtering | 2-5 | ~24 |
| RBAC enforcement | 2 | 12 |
| Multi-tenant isolation | 1 | 6 |
| **Subtotal** | | **~84** |

#### 7.1.3 Connector Unit Tests

For each of the 54 connectors, test:

| Test Category | Test Count per Connector | Total |
|---------------|-------------------------|-------|
| Auth flow (mock) | 1 | 54 |
| Each tool function (mock external API) | 2-8 (varies) | ~300 |
| Error handling (timeout, 401, 500) | 3 | 162 |
| Rate limiting | 1 | 54 |
| Health check | 1 | 54 |
| **Subtotal** | | **~624** |

### 7.2 Integration Tests

| Test | Description | Components Involved |
|------|-------------|-------------------|
| `test_cfo_dashboard_real_data_pipeline` | CFO API -> FPA agent -> Tally connector -> Mock Tally -> KPI cache -> API response | API, agent, connector, cache |
| `test_chro_dashboard_real_data_pipeline` | CHRO API -> Payroll agent -> Darwinbox connector -> Mock Darwinbox -> KPI cache -> API | API, agent, connector, cache |
| `test_cmo_dashboard_real_data_pipeline` | CMO API -> Campaign agent -> HubSpot connector -> Mock HubSpot -> KPI cache -> API | API, agent, connector, cache |
| `test_coo_dashboard_real_data_pipeline` | COO API -> IT Ops agent -> PagerDuty connector -> Mock PagerDuty -> KPI cache -> API | API, agent, connector, cache |
| `test_cbo_dashboard_real_data_pipeline` | CBO API -> Legal Ops agent -> DocuSign connector -> Mock DocuSign -> KPI cache -> API | API, agent, connector, cache |
| `test_ceo_dashboard_aggregation` | CEO API -> All 5 CxO agent results -> Aggregation -> API response | API, 5 agents, cache |
| `test_invoice_processing_workflow` | Invoice upload -> AP agent -> GSTN + Tally -> Match -> Post -> Notify | Workflow engine, agent, 3 connectors |
| `test_bank_recon_workflow` | Daily trigger -> Recon agent -> AA + Tally -> Match -> Report | Workflow engine, agent, 2 connectors |
| `test_payroll_workflow` | Monthly trigger -> Payroll agent -> Darwinbox + EPFO + Tally -> Process | Workflow engine, agent, 3 connectors |
| `test_support_triage_workflow` | Ticket webhook -> Triage agent -> Zendesk + Confluence -> Route | Workflow engine, agent, 2 connectors |
| `test_contract_review_workflow` | Upload -> Legal agent -> DocuSign + S3 -> Review -> Sign | Workflow engine, agent, 2 connectors |
| `test_multi_tenant_isolation` | Tenant A creates KPI, Tenant B cannot read it | API, DB, RLS |
| `test_role_based_access_cfo_cannot_see_chro` | CFO token used to access /kpis/chro -> 403 | API, RBAC |
| `test_role_based_access_admin_sees_all` | Admin token used to access all 6 endpoints -> 200 | API, RBAC |
| `test_connector_config_encryption` | Configure connector -> Read DB -> Credentials are encrypted | API, DB |
| `test_connector_health_monitoring` | Configure connector -> Wait 5 min -> Health check runs -> Status updated | Scheduler, connector, DB |
| `test_kpi_cache_hit` | Compute KPI -> Read again within TTL -> Returns cached | Cache, API |
| `test_kpi_cache_miss` | Read KPI after TTL -> Recomputes from agent | Cache, agent, API |
| `test_stale_data_fallback` | Connector down -> Dashboard shows last-known with staleness indicator | API, cache, fallback |
| `test_board_pack_generation` | Trigger board pack -> All sections generate -> PDF created | Report engine, 5 agents |

**Total integration tests: ~20**

### 7.3 E2E Tests (Playwright)

#### 7.3.1 Dashboard E2E Tests

| Test | Steps | Assertions |
|------|-------|-----------|
| `test_ceo_dashboard_loads` | Navigate to /dashboard/ceo | Page title, 4 quadrants visible, alert banner |
| `test_ceo_quadrant_navigation` | Click Finance quadrant | Navigates to /dashboard/cfo |
| `test_cfo_dashboard_tabs` | Navigate to /dashboard/cfo, click each tab | All 5 tabs render without error |
| `test_cfo_treasury_tab_balances` | Navigate to CFO, Treasury tab | Bank balance cards visible |
| `test_cfo_treasury_tab_forecast` | Navigate to CFO, Treasury tab | Cash flow chart renders |
| `test_cfo_ap_ar_tab_aging` | Navigate to CFO, AP/AR tab | AR and AP aging charts render |
| `test_cfo_tax_tab_calendar` | Navigate to CFO, Tax tab | Filing calendar grid renders |
| `test_cfo_close_tab_checklist` | Navigate to CFO, Close tab | Checklist with completion % |
| `test_cfo_budget_tab_waterfall` | Navigate to CFO, Budget tab | Waterfall chart renders |
| `test_chro_dashboard_loads` | Navigate to /dashboard/chro | Page title, tab navigation visible |
| `test_chro_workforce_tab` | CHRO, Workforce tab | Headcount chart, dept breakdown |
| `test_chro_payroll_tab` | CHRO, Payroll tab | Payroll cost, statutory dues |
| `test_chro_recruitment_tab` | CHRO, Recruitment tab | Pipeline funnel, open positions |
| `test_chro_engagement_tab` | CHRO, Engagement tab | eNPS gauge, risk heatmap |
| `test_chro_compliance_tab` | CHRO, Compliance tab | Filing status grid |
| `test_cmo_dashboard_tabs` | Navigate to /dashboard/cmo, click each tab | All 5 tabs render |
| `test_cmo_pipeline_tab_funnel` | CMO, Pipeline tab | MQL/SQL funnel visible |
| `test_cmo_campaigns_tab` | CMO, Campaigns tab | Active campaigns table |
| `test_cmo_abm_tab` | CMO, ABM tab | Target accounts table |
| `test_cmo_brand_tab_sentiment` | CMO, Brand tab | Sentiment gauge renders |
| `test_coo_dashboard_loads` | Navigate to /dashboard/coo | Page title, tab navigation |
| `test_coo_it_ops_tab` | COO, IT Ops tab | Active incidents, MTTR chart |
| `test_coo_support_tab` | COO, Support tab | Ticket volume chart, CSAT |
| `test_coo_vendors_tab` | COO, Vendors tab | Vendor table, renewals |
| `test_coo_facilities_tab` | COO, Facilities tab | Asset utilization, maintenance |
| `test_cbo_dashboard_loads` | Navigate to /dashboard/cbo | Page title, tab navigation |
| `test_cbo_legal_tab` | CBO, Legal tab | Contracts table, NDA tracker |
| `test_cbo_risk_tab` | CBO, Risk tab | Compliance gauge, screening |
| `test_cbo_corporate_tab` | CBO, Corporate tab | Filing status, meetings |
| `test_cbo_comms_tab` | CBO, Comms tab | Media coverage chart |

#### 7.3.2 Interaction E2E Tests

| Test | Steps | Assertions |
|------|-------|-----------|
| `test_date_range_selector` | Change date range from 30d to 7d | Charts update, API called with new range |
| `test_export_csv` | Click Export -> CSV on any table | CSV file downloaded with correct data |
| `test_export_pdf` | Click Export -> PDF on any table | PDF file downloaded |
| `test_manual_refresh` | Click Refresh button | Loading spinner, data refreshes |
| `test_tab_persistence` | Switch to Tax tab, navigate away, come back | Tax tab still selected |
| `test_connector_health_strip` | Hover over connector dot | Tooltip shows connector name and status |
| `test_stale_data_badge` | Make data stale (exceed TTL) | Orange "stale" badge appears |
| `test_drill_down_metric` | Click on a KPI card | Drill-down modal with historical chart |

#### 7.3.3 Workflow E2E Tests

| Test | Steps | Assertions |
|------|-------|-----------|
| `test_workflow_trigger_manual` | Navigate to Workflows, click Run on bank_recon | Workflow starts, steps progress |
| `test_workflow_hitl_appears` | Trigger workflow that has HITL | Approval item appears in Approvals page |
| `test_workflow_hitl_approve` | Click Approve on HITL item | Workflow resumes, completes |
| `test_workflow_hitl_reject` | Click Reject on HITL item | Workflow records rejection |
| `test_workflow_schedule_create` | Create a new schedule for a workflow | Schedule appears in scheduled list |
| `test_workflow_schedule_run_now` | Click "Run Now" on scheduled workflow | Workflow executes immediately |

#### 7.3.4 Auth & RBAC E2E Tests

| Test | Steps | Assertions |
|------|-------|-----------|
| `test_cfo_role_sees_cfo_dashboard` | Login as CFO user, navigate to /dashboard/cfo | Dashboard loads |
| `test_cfo_role_cannot_see_chro` | Login as CFO, navigate to /dashboard/chro | Redirected or 403 |
| `test_admin_sees_all_dashboards` | Login as admin, navigate to each dashboard | All 6 load |
| `test_coo_role_limited_nav` | Login as COO | Sidebar only shows COO-relevant items |

**Total E2E tests: ~50**

### 7.4 Performance Tests

| Test | Target | Tool |
|------|--------|------|
| Dashboard load time (warm cache) | < 2 seconds | Playwright + performance API |
| Dashboard load time (cold cache) | < 5 seconds | Playwright + performance API |
| KPI API response time (cache hit) | < 100ms | k6 / locust |
| KPI API response time (cache miss) | < 3 seconds | k6 / locust |
| Agent execution time (including LLM) | < 30 seconds | pytest + timing |
| Connector API call time | < 10 seconds per call | pytest + timing |
| 50 concurrent users same tenant | All responses < 5s, no errors | k6 |
| 10 concurrent workflow executions | All complete within timeout | pytest |
| Redis cache throughput | > 10,000 reads/second | redis-benchmark |
| DB query time for KPI history | < 500ms for 90-day range | EXPLAIN ANALYZE |

### 7.5 Negative & Edge Case Tests

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_invalid_json_body` | Send malformed JSON to any POST endpoint | 400 with parse error message |
| `test_sql_injection_in_query_params` | Send `'; DROP TABLE kpi_cache; --` as `date_from` | 422 (invalid date format), no SQL execution |
| `test_xss_in_metric_name` | Create metric with `<script>alert(1)</script>` name | Value sanitized, no script execution in UI |
| `test_concurrent_workflow_same_tenant` | Start same workflow twice simultaneously | Second returns 409, first completes normally |
| `test_concurrent_kpi_write` | Two agents compute same KPI simultaneously | Last-write-wins with optimistic locking, no data corruption |
| `test_extremely_large_payload` | Send 10MB JSON body to any endpoint | 413 (Payload Too Large) |
| `test_unicode_in_all_fields` | Hindi characters in metric names, agent inputs | Stored and retrieved correctly (UTF-8) |
| `test_empty_connector_response` | Connector returns 200 but empty body | Agent treats as error, returns fallback, logs warning |
| `test_connector_returns_html_instead_of_json` | Connector returns HTML (e.g., login page) | Agent detects non-JSON, treats as auth failure, triggers health check |
| `test_hitl_approval_after_timeout` | Approve an HITL item after the 4-hour window | Approval rejected, workflow already escalated to CEO |
| `test_hitl_double_approval` | Two users approve same HITL item concurrently | First approval wins, second gets 409 |
| `test_workflow_step_timeout` | Agent takes > 5 minutes on one step | Step marked as timed out, workflow triggers failure path |
| `test_workflow_all_steps_fail` | Every step in a workflow fails | Workflow status = failed, notification sent, no partial data left in inconsistent state |
| `test_negative_kpi_value` | KPI computation returns negative number (e.g., negative runway) | Displayed correctly with warning indicator |
| `test_zero_division_in_kpi` | Denominator is zero (e.g., 0 employees for attrition rate) | Returns null with `"error": "Division by zero: no employees for attrition calculation"` |
| `test_future_date_in_query` | `date_from` is in the future | 422 with "date_from cannot be in the future" |
| `test_tenant_id_mismatch_in_token` | Token tenant_id differs from request tenant_id | 403 |
| `test_expired_oauth_token_refresh` | Connector OAuth token expired, refresh token valid | Auto-refresh, retry request, return data |
| `test_expired_oauth_token_no_refresh` | Both access and refresh tokens expired | Connector status -> red, HITL alert to admin to re-authorize |
| `test_data_migration_rollback` | Migration 016 partially fails | Rolled back cleanly, no orphaned tables or constraints |
| `test_zero_downtime_migration` | Apply migration 016 while API is serving requests | All requests continue, no 500 errors during migration window |

**Total negative/edge case tests: ~21**

### 7.6 Data Migration Tests

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_migration_016_creates_all_tables` | Run migration 016 on fresh DB | All 8 tables created with correct schema |
| `test_migration_016_idempotent` | Run migration 016 twice | Second run is a no-op (no errors) |
| `test_migration_016_rls_enabled` | Check RLS after migration | All 7 tenant-scoped tables have RLS policies |
| `test_migration_016_indexes_exist` | Query pg_indexes after migration | All 15 specified indexes exist |
| `test_migration_016_partitions_created` | Check kpi_history partitions | 12 monthly partitions exist |
| `test_migration_016_rollback` | Apply then rollback migration | All tables dropped, schema returns to pre-migration state |
| `test_migration_016_preserves_existing_data` | Run migration on DB with existing tenant/user data | No data loss in existing tables |

**Total data migration tests: ~7**

### 7.7 Security Tests

| Test | Description | Tool |
|------|-------------|------|
| Credential encryption verification | Read connector_configs.credentials_encrypted -> not plaintext | SQL query + assertion |
| No credentials in logs | Search all log outputs for credential patterns | grep/regex |
| No credentials in API responses | All /connectors endpoints mask credentials | API test |
| Tenant isolation RLS | Tenant A's session cannot read Tenant B's kpi_cache | SQL test |
| RBAC endpoint enforcement | CFO token on /kpis/chro returns 403 | API test |
| JWT token validation | Expired token returns 401 | API test |
| JWT token scoping | Token with wrong tenant_id returns 403 | API test |
| SQL injection | Parameterized queries for all user input | sqlmap |
| XSS prevention | React auto-escaping + CSP headers | OWASP ZAP |
| CSRF protection | State-changing endpoints require CSRF token or same-origin | API test |
| Rate limiting | > 200 requests/minute from same IP returns 429 | k6 |
| Encryption at rest | DB encryption enabled, S3 encryption enabled | GCP audit |
| Encryption in transit | All API calls over HTTPS, no HTTP redirect | SSL test |
| PII masking | Employee PAN, Aadhaar masked in all API responses | API test |

### 7.8 Test Count Summary

| Category | Count |
|----------|-------|
| Agent unit tests | ~670 |
| KPI endpoint unit tests | ~84 |
| Connector unit tests | ~624 |
| Integration tests | ~20 |
| E2E tests (Playwright) | ~50 |
| Performance tests | ~10 |
| Negative & edge case tests | ~21 |
| Data migration tests | ~7 |
| Security tests | ~14 |
| **Total** | **~1,500** |

Note: Combined with existing test suite (~2,500 tests), total reaches **~4,000 tests**.

---

## 8. Documentation Updates

### 8.1 README.md

**Updates required:**

- Feature matrix: Add CHRO, COO, CBO dashboard entries alongside existing CFO, CMO
- Dashboard count: Update "2 CxO dashboards" -> "6 CxO dashboards"
- Agent count: Verify "35+ agents with domain logic" (after this PRD, all 35 have real logic)
- Connector count: Confirm "54 native connectors, 1000+ via Composio"
- Add "Connector Configuration" section explaining the admin setup flow
- Add "KPI Pipeline" section explaining how real data flows from connectors to dashboards
- Update screenshots/GIFs showing new dashboards

### 8.2 API Reference (`docs/api-reference.md`)

**New sections to add:**

- `GET /kpis/ceo` -- Full request/response documentation
- `GET /kpis/chro` -- Full request/response documentation
- `GET /kpis/coo` -- Full request/response documentation
- `GET /kpis/cbo` -- Full request/response documentation
- `GET /kpis/{role}/detail/{metric}` -- Drill-down endpoint
- `POST /connectors/{name}/configure` -- Configuration endpoint
- `GET /connectors/{name}/health` -- Health check endpoint
- `GET /connectors/health/all` -- All connectors health
- `POST /connectors/{name}/test` -- Test connection endpoint
- `POST /reports/board-pack` -- Report generation
- `GET /reports/{id}` -- Report status
- `GET /reports/{id}/download` -- Report download
- `POST /agents/{id}/run` -- Manual agent trigger
- `GET /agents/{id}/results` -- Agent execution history
- `GET /workflows/scheduled` -- Scheduled workflows
- `POST /workflows/{id}/schedule` -- Create schedule
- `DELETE /workflows/{id}/schedule` -- Remove schedule
- `POST /workflows/{id}/schedule/run-now` -- Manual run

### 8.3 Architecture (`docs/architecture.md`)

**New diagrams and sections:**

- KPI Data Pipeline diagram: Dashboard -> API -> Redis -> Agent -> Connector -> External
- Connector Health Monitoring architecture: Cron -> health_check() -> connector_health_log -> Dashboard
- Agent Execution Flow with domain logic: Task -> PreProcess -> Domain Rules -> LLM Reasoning -> Tool Calls -> PostProcess -> Result
- Caching Strategy diagram: TTL tiers, fallback chain, stale data handling
- Board Report Generation pipeline: Agents -> Sections -> PDF Engine -> S3 -> Email

### 8.4 User Guides

#### 8.4.1 CEO Quick Start Guide (`docs/ceo_guide.md` -- NEW)

1. Login as admin
2. Navigate to CEO Dashboard
3. Understanding the 4-quadrant view
4. Setting up alert thresholds
5. Using the Agent Observatory
6. Generating board packs
7. Managing cross-department approvals

#### 8.4.2 CFO Setup Guide (`docs/cfo_guide.md` -- UPDATE existing)

1. Connecting Tally (bridge setup for remote instances)
2. Configuring Account Aggregator (AA consent flow)
3. Setting up GSTN (GSP credentials + DSC)
4. Configuring Income Tax connector
5. Setting HITL thresholds (invoice amount, match tolerance, filing approval)
6. Understanding the Treasury tab
7. Running bank reconciliation
8. Filing GST returns
9. Month-end close checklist
10. Generating MIS pack

#### 8.4.3 CHRO Setup Guide (`docs/chro_guide.md` -- NEW)

1. Connecting Darwinbox or Keka (API key + OAuth)
2. Configuring EPFO (DSC + API key)
3. Setting up recruitment pipeline (Greenhouse + LinkedIn Talent)
4. Configuring onboarding workflow (Okta + Slack + Jira)
5. Setting payroll thresholds (PF ceiling, ESI threshold, TDS regime)
6. Understanding the Payroll tab
7. Running monthly payroll
8. Filing EPFO ECR
9. Managing performance reviews
10. Tracking compliance calendar

#### 8.4.4 CMO Setup Guide (`docs/cmo_guide.md` -- UPDATE existing)

1. Connecting HubSpot (OAuth flow)
2. Setting up Google Ads (OAuth + developer token)
3. Connecting Meta Ads + LinkedIn Ads
4. Configuring Bombora/G2/TrustRadius for ABM
5. Setting up Brandwatch for monitoring
6. Connecting Buffer for social scheduling
7. Campaign creation workflow
8. Understanding ROAS by channel
9. ABM workflow setup
10. Email marketing automation

#### 8.4.5 COO Setup Guide (`docs/coo_guide.md` -- NEW)

1. Connecting PagerDuty (API key)
2. Connecting ServiceNow (OAuth + instance URL)
3. Setting up Zendesk (API token)
4. Connecting Jira (API token + domain)
5. Connecting Confluence (for knowledge base)
6. Setting SLA thresholds (MTTR targets, uptime SLA)
7. Configuring incident escalation rules
8. Setting up support triage auto-classification
9. Vendor management setup
10. Facilities tracking

#### 8.4.6 CBO Setup Guide (`docs/cbo_guide.md` -- NEW)

1. Connecting DocuSign (JWT + account ID)
2. Setting up MCA Portal (DSC)
3. Configuring Sanctions.io API (API key)
4. Setting DPDPA compliance parameters
5. Contract review workflow setup
6. Board meeting management
7. Risk register configuration
8. Compliance calendar setup

### 8.5 Blog Posts

| Title | Target CxO | Key Message | SEO Keywords |
|-------|-----------|-------------|--------------|
| "How AI Agents Replace the Back Office" | CBO | AI handles contracts, compliance, and corporate secretary work. Zero manual statutory filing. | AI back office automation, corporate secretary automation |
| "Zero-Error Payroll: How CHRO Uses AI" | CHRO | Automated PF/ESI/PT computation, payroll journal posting, and compliance filing. | payroll automation India, EPFO automation |
| "MTTR Under 15 Minutes: AI-Powered IT Ops" | COO | PagerDuty + AI triage reduces incident resolution by 60%. | IT operations automation, incident management AI |
| "From 5-Day Close to 4-Hour Close: CFO's AI Playbook" | CFO | (Existing, update) Add bank reconciliation via AA, GST filing automation. | month-end close automation, CFO AI tools |
| "3.2x ROAS: CMO's Campaign Automation Guide" | CMO | ABM with Bombora intent + Google Ads + HubSpot pipeline. | marketing automation AI, ABM software India |

### 8.6 Landing Page Updates

**New sections to add to `Landing.tsx`:**

1. **Role-Specific Sections:**
   - "For CHROs" section: Headcount dashboard mockup, payroll automation stats, compliance calendar
   - "For COOs" section: Incident dashboard mockup, support deflection stats, vendor management
   - "For CBOs" section: Contract review mockup, compliance scoring, board meeting management
   - (CFO and CMO sections already exist -- update with new dashboard screenshots)

2. **"For Enterprise" Section:**
   - Scale examples: "Process 10,000 invoices/month like Pine Labs"
   - Security: "SOC 2, DPDPA compliant, zero PII in logs"
   - Multi-tenant: "Each department gets its own AI command center"

3. **Connector Logo Wall:**
   - Row 1 (Finance): Tally, SAP, Oracle, QuickBooks, Zoho Books, Stripe, GSTN, Account Aggregator
   - Row 2 (HR): Darwinbox, Keka, EPFO, Greenhouse, LinkedIn Talent, DocuSign, Okta
   - Row 3 (Marketing): HubSpot, Google Ads, Meta Ads, LinkedIn Ads, Mailchimp, Ahrefs, Bombora
   - Row 4 (Ops): Jira, PagerDuty, ServiceNow, Zendesk, Confluence, Slack, Teams

---

## 9. Rollout Plan

### Phase 1 (Weeks 1-4): Foundation

**Week 1:**
- [ ] Create `kpi_cache`, `agent_task_results`, `connector_configs` tables (migration 016)
- [ ] Implement Redis caching layer with TTL support
- [ ] Build `GET /kpis/cfo` with real connector queries (replace hardcoded)
  - Wire treasury: `banking_aa.check_account_balance` -> cash balances
  - Wire AP/AR: `tally.get_ledger_balance` -> aging buckets
  - Wire tax: `gstn.check_filing_status` -> calendar
- [ ] Build `GET /kpis/cmo` with real connector queries (replace hardcoded)
  - Wire pipeline: `hubspot.list_deals` -> pipeline value
  - Wire campaigns: `google_ads.get_campaign_performance` -> ROAS
  - Wire email: `hubspot.get_campaign_analytics` -> email metrics

**Week 2:**
- [ ] Build CHRO dashboard: `CHRODashboard.tsx` with all 5 tabs
- [ ] Build `GET /kpis/chro` endpoint
  - Wire workforce: `darwinbox.get_org_chart` -> headcount
  - Wire payroll: `darwinbox.run_payroll` -> payroll cost
  - Wire compliance: `epfo` -> filing status
- [ ] Add `/dashboard/chro` route to `App.tsx`
- [ ] Add CHRO nav items to Layout sidebar

**Week 3:**
- [ ] Build COO dashboard: `COODashboard.tsx` with all 4 tabs
- [ ] Build `GET /kpis/coo` endpoint
  - Wire IT Ops: `pagerduty.list_incidents` -> active incidents, MTTR
  - Wire Support: `zendesk` -> ticket volume, CSAT
  - Wire Vendors: `tally.get_ledger_balance` -> vendor payments
- [ ] Add `/dashboard/coo` route to `App.tsx`
- [ ] Implement connector configuration UI (`/dashboard/connectors/{name}/configure`)

**Week 4:**
- [ ] Write domain logic for top 10 agents:
  1. `ap_processor` -- Full 6-step invoice processing
  2. `ar_collections` -- Aging analysis + collection automation
  3. `recon_agent` -- Bank-to-book auto-matching
  4. `payroll_engine` -- Statutory computation (PF/ESI/PT/TDS)
  5. `talent_acquisition` -- Candidate scoring + pipeline management
  6. `support_triage` -- Ticket classification (88% accuracy target)
  7. `support_deflector` -- KB search + auto-response
  8. `it_operations` -- Incident severity classification
  9. `tax_compliance` -- GST filing preparation
  10. `fpa_agent` -- Budget vs actual + cash flow forecast
- [ ] Write unit tests for all 10 agents (~130 tests)
- [ ] Run full CI pipeline, ensure green

### Phase 2 (Weeks 5-8): Depth

**Week 5:**
- [ ] Build CEO dashboard: `CEODashboard.tsx` with 4-quadrant layout
- [ ] Build `GET /kpis/ceo` endpoint (aggregates from all roles)
- [ ] Build CBO dashboard: `CBODashboard.tsx` with all 4 tabs
- [ ] Build `GET /kpis/cbo` endpoint
  - Wire Legal: `docusign` -> contract status
  - Wire Risk: `sanctions_api` -> screening results
  - Wire Corporate: `mca_portal` -> filing status
- [ ] Add `/dashboard/ceo` and `/dashboard/cbo` routes

**Week 6:**
- [ ] Implement all workflow schedules (23 workflows)
- [ ] Build workflow scheduling endpoints (`POST /workflows/{id}/schedule`)
- [ ] Implement `workflow_schedules` table and cron executor
- [ ] Build board reporting engine:
  - PDF generation from aggregated KPIs
  - Section compilation (P&L, BS, HR, Marketing, Ops)
  - `POST /reports/board-pack` endpoint
- [ ] Build report storage and download endpoints

**Week 7:**
- [ ] Write domain logic for remaining 25 agents:
  1. `close_agent` -- Month-end close checklist
  2. `onboarding_agent` -- Account provisioning sequence
  3. `offboarding_agent` -- Access revocation + F&F
  4. `performance_coach` -- Review aggregation + PIP
  5. `ld_coordinator` -- Training enrollment + cert tracking
  6. `campaign_pilot` -- Campaign creation + ROAS monitoring
  7. `content_factory` -- Content creation + scheduling
  8. `seo_strategist` -- SEO audit + keyword research
  9. `brand_monitor` -- Sentiment tracking + crisis detection
  10. `crm_intelligence` -- ABM + intent scoring
  11. `vendor_manager` -- Vendor scoring + SLA monitoring
  12. `facilities_agent` -- Asset tracking + maintenance
  13. `legal_ops` -- Clause extraction + risk flagging
  14. `contract_intelligence` -- Contract lifecycle
  15. `risk_sentinel` -- Risk scoring + fraud detection
  16. `compliance_guard` -- Compliance scoring + deadline tracking
  17-25. Remaining agents (competitive_intel, email_agent, expense_manager, fixed_assets_agent, nexus_orchestrator, notification_agent, rev_rec_agent, sales_agent, social_media, treasury_agent)
- [ ] Write unit tests for remaining agents (~540 tests)

**Week 8:**
- [ ] Build connector health monitoring:
  - 5-minute cron for health checks
  - `connector_health_log` table
  - Health status panel on admin dashboard
  - Slack alerts for red status
- [ ] Implement KPI drill-down endpoint (`GET /kpis/{role}/detail/{metric}`)
- [ ] Build `kpi_history` table with monthly partitions
- [ ] Write integration tests (~20 tests)
- [ ] Write E2E tests for all dashboards (~30 tests)

### Phase 3 (Weeks 9-12): Polish

**Week 9:**
- [ ] Performance optimization:
  - Dashboard load time profiling and optimization (< 2s target)
  - Redis cache hit rate optimization (> 90% target)
  - SQL query optimization (EXPLAIN ANALYZE all KPI queries)
  - React component lazy loading optimization
  - API response compression (gzip)
- [ ] Add WebSocket support for real-time KPIs (incidents, tickets, approvals)

**Week 10:**
- [ ] Security hardening:
  - Penetration test (OWASP top 10)
  - Credential encryption audit
  - RLS verification across all new tables
  - PII masking audit (employee PAN, Aadhaar, bank details)
  - CSP header configuration
  - Rate limiting on all endpoints
- [ ] Write security tests (~14 tests)

**Week 11:**
- [ ] Documentation completion:
  - Update README.md
  - Complete API reference
  - Write all 6 user guides
  - Update architecture docs
  - Write 5 blog posts
  - Update landing page
- [ ] Complete E2E test suite (~50 total)
- [ ] Run full regression

**Week 12:**
- [ ] Customer pilot preparation:
  - Set up demo tenant with sample data
  - Create onboarding runbook for pilot customers
  - Configure monitoring and alerting for pilot
- [ ] Deploy to production (GCP)
- [ ] Begin pilot with 2-3 enterprises
- [ ] Production monitoring:
  - Agent success rate dashboards (Grafana)
  - Connector health dashboard
  - LLM cost tracking
  - Error rate alerting

---

## 10. Success Metrics

### 10.1 Technical Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Dashboard load time (warm cache) | < 2 seconds | Lighthouse + Playwright performance API |
| Dashboard load time (cold cache) | < 5 seconds | Lighthouse + Playwright performance API |
| KPI API response time (cache hit) | < 100 milliseconds | k6 load test P95 |
| Agent task success rate | > 95% | agent_task_results.count(completed) / total |
| Agent average latency | < 30 seconds (including LLM) | agent_task_results.avg(duration_ms) |
| Connector uptime | > 99.5% | connector_health_log analysis |
| Redis cache hit rate | > 90% | Redis INFO stats |
| Zero hardcoded data | 0 endpoints returning `"demo": true` | grep codebase for hardcoded values |
| Zero empty agents | 0 agents with only `super().execute()` | Code review of all 35 agent classes |

### 10.2 Product Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| All 6 dashboards render real data | 6 / 6 | Manual verification per dashboard |
| All 35 agents have domain logic | 35 / 35 | Code review checklist |
| All 54 connectors configurable | 54 / 54 | Configuration UI test |
| All 23 workflows executable | 23 / 23 | Workflow execution test |
| Support deflection rate | > 73% | support_deflector metrics |
| Auto-classification accuracy | > 88% | support_triage metrics |
| Invoice auto-match rate | > 85% | ap_processor metrics |
| Bank recon auto-match rate | > 95% | recon_agent metrics |

### 10.3 Quality Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Total test count | > 4,000 | `pytest --co -q \| wc -l` + Playwright test count |
| Unit test coverage | > 80% | coverage.py |
| Zero P1 bugs in production | 0 | Bug tracker |
| Zero security findings (OWASP top 10) | 0 | OWASP ZAP scan |
| CI pipeline green | 100% | GitHub Actions status |

### 10.4 Business Metrics (Post-Pilot)

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Pilot customer activation | 3 enterprises | CRM tracking |
| Pilot customer NPS | > 40 | Survey |
| Time to first value | < 2 hours from signup to first real dashboard data | Onboarding analytics |
| Monthly Active CxO Users | > 15 (across pilot customers) | Usage analytics |

---

## 11. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM cost overrun (agents calling LLM too frequently) | High | Medium | Per-agent monthly budget cap in `cost_controls`. Default to Gemini Flash (cheapest). Cache LLM responses for identical inputs. |
| Connector API changes (external service updates their API) | Medium | High | Version pin all connector base_urls. Health checks detect breaking changes. Fallback to last-known data. |
| Data privacy violation (PII in logs, responses, or LLM prompts) | Medium | Critical | PII masking layer before LLM calls. No PII in structured logs. Aadhaar/PAN masked in all API responses. |
| Customer resistance to AI autonomous actions | High | Medium | Shadow mode for all new agents (observe-only for 2 weeks). HITL on everything above configurable thresholds. Full audit trail. |
| Tally bridge disconnection (CA's local machine offline) | High | Medium | Queue pending operations. Show last-known data. Auto-reconnect with exponential backoff. Alert CA if disconnected > 1 hour. |
| Account Aggregator consent expiry | Medium | Medium | Proactive consent renewal 7 days before expiry. Fall back to manual bank statement upload. |
| GSTN rate limiting during filing season | High | Medium | Queue filings with priority. Retry with exponential backoff. Alert CFO if filing at risk of missing deadline. |
| Multi-tenant data leakage | Low | Critical | RLS on all tables. Session-scoped tenant_id. Integration tests for isolation. Pen test. |
| Redis cache corruption | Low | Medium | Cache values are never the source of truth (PostgreSQL is). Corrupted cache = cache miss = recompute from agents. |
| Agent hallucination (LLM generates fake financial data) | Medium | Critical | Anti-hallucination rules in all prompts. Post-LLM validation in domain logic. Never trust LLM for amounts/dates/IDs -- only from tool results. |
| Pilot customer churn due to incomplete features | Medium | High | Prioritize customer-specific features during pilot. Weekly check-in calls. Fast iteration on feedback. |
| Team bandwidth (12 weeks is aggressive) | High | Medium | Parallelize frontend and backend work. Reuse existing connector infrastructure. Focus on top 10 agents first (Phase 1). |

---

## 12. Appendix

### Appendix A. Complete Agent Registry

| # | Agent Type | Domain | Prompt File | Confidence Floor | HITL Conditions | Authorized Tools |
|---|-----------|--------|-------------|-----------------|-----------------|-----------------|
| 1 | `ap_processor` | finance | `ap_processor.prompt.txt` | 0.88 | amount > 500K, match delta > 2%, vendor risk > 7, confidence < 0.88 | tally.post_voucher, tally.get_ledger_balance, gstn.generate_einvoice_irn, banking_aa.fetch_bank_statement, sendgrid.send_email |
| 2 | `ar_collections` | finance | `ar_collections.prompt.txt` | 0.88 | overdue > 90d + amount > 1L, write-off > 50K, customer dispute | tally.get_ledger_balance, tally.post_voucher, sendgrid.send_email, whatsapp.send_message, hubspot.update_contact |
| 3 | `recon_agent` | finance | `recon_agent.prompt.txt` | 0.95 | break > 50K or > 0.01% volume, outstanding > 30d count > 5 | banking_aa.fetch_bank_statement, banking_aa.get_transaction_list, tally.get_ledger_balance, tally.get_trial_balance |
| 4 | `close_agent` | finance | `close_agent.prompt.txt` | 0.92 | TB imbalance, manual journal > 1L, depreciation variance > 5% | tally.post_voucher, tally.get_trial_balance, tally.get_ledger_balance, tally.export_tally_xml_data |
| 5 | `fpa_agent` | finance | `fpa_agent.prompt.txt` | 0.88 | budget variance > 15%, CAC increase > 20% MoM, forecast accuracy < 80% | tally.get_trial_balance, tally.get_ledger_balance, hubspot.list_deals |
| 6 | `tax_compliance` | finance | `tax_compliance.prompt.txt` | 0.92 | any GST filing, ITC mismatch > 10K, TDS variance > 5%, compliance notice received | gstn.fetch_gstr2a, gstn.push_gstr1_data, gstn.file_gstr3b, gstn.file_gstr9, gstn.generate_eway_bill, gstn.generate_einvoice_irn, gstn.check_filing_status, income_tax_india.file_tds_return, income_tax_india.get_26as, tally.generate_gst_report |
| 7 | `talent_acquisition` | hr | `talent_acquisition.prompt.txt` | 0.85 | salary > 30L, VP+ offer, background check discrepancy | linkedin_talent.post_job, linkedin_talent.search_candidates, greenhouse.create_candidate, greenhouse.advance_candidate, google_calendar.create_event, zoom.create_meeting, docusign.send_envelope, sendgrid.send_email |
| 8 | `onboarding_agent` | hr | `onboarding_agent.prompt.txt` | 0.95 | docs incomplete after 7d, equipment delay > 3d, background check issue | darwinbox.create_employee, okta.create_user, slack.invite_user, jira.create_issue, google_calendar.create_event, sendgrid.send_email, docusign.send_envelope |
| 9 | `payroll_engine` | hr | `payroll_engine.prompt.txt` | 0.95 | entire payroll (final approval), correction > 5K, first payroll for new employee, F&F settlement | darwinbox.run_payroll, darwinbox.get_payslip, darwinbox.get_attendance, keka.run_payroll, keka.get_leave_balance, epfo.file_ecr, epfo.generate_trrn, tally.post_voucher |
| 10 | `performance_coach` | hr | `performance_coach.prompt.txt` | 0.88 | PIP initiation, promotion VP+, rating < 2.0 | darwinbox.update_performance, darwinbox.get_employee, sendgrid.send_email |
| 11 | `ld_coordinator` | hr | `ld_coordinator.prompt.txt` | 0.85 | mandatory training not done in 30d, cert expiring in 30d | darwinbox.get_employee, google_calendar.create_event, sendgrid.send_email |
| 12 | `offboarding_agent` | hr | `offboarding_agent.prompt.txt` | 0.95 | F&F > 5L, access not revoked in 24h, F&F dispute | darwinbox.terminate_employee, okta.deactivate_user, slack.remove_user, jira.create_issue, epfo.check_claim_status, docusign.send_envelope, sendgrid.send_email |
| 13 | `campaign_pilot` | marketing | `campaign_pilot.prompt.txt` | 0.85 | daily spend > 120% budget, ROAS < 1.5x, new campaign > 1L | google_ads.search_campaigns, google_ads.get_campaign_performance, google_ads.mutate_campaign_budget, meta_ads.get_campaign_insights, linkedin_ads.get_campaign_analytics, hubspot.create_contact |
| 14 | `content_factory` | marketing | `content_factory.prompt.txt` | 0.82 | competitor mention in blog, email > 10K recipients, product launch video | wordpress.create_post, wordpress.get_posts, buffer.create_post, buffer.get_analytics, youtube.upload_video, mailchimp.create_campaign |
| 15 | `seo_strategist` | marketing | `seo_strategist.prompt.txt` | 0.85 | (none -- advisory only) | ahrefs.get_backlinks, ahrefs.get_keywords, ahrefs.get_site_audit, ga4.get_report |
| 16 | `brand_monitor` | marketing | `brand_monitor.prompt.txt` | 0.82 | negative sentiment > 3x baseline (crisis), competitor campaign detected | brandwatch.get_mentions, brandwatch.get_sentiment, brandwatch.get_competitors, twitter.search_mentions |
| 17 | `crm_intelligence` | marketing | `crm_intelligence.prompt.txt` | 0.85 | target list change > 20%, ABM budget > 50K/month | bombora.get_surge_scores, bombora.search_companies, g2.get_buyer_intent, trustradius.get_intent, hubspot.list_companies, hubspot.update_contact, linkedin_ads.get_campaign_analytics |
| 18 | `it_operations` | ops | `it_operations.prompt.txt` | 0.88 | P1 not ack'd in 5min, change to production, uptime < 99.5%, MTTR > 60min for P1 | pagerduty.create_incident, pagerduty.acknowledge_incident, pagerduty.resolve_incident, pagerduty.get_on_call, pagerduty.list_incidents, pagerduty.create_postmortem, servicenow.create_incident, servicenow.submit_change_request, servicenow.check_sla_status, jira.create_issue, jira.search_issues |
| 19 | `support_triage` | ops | `support_triage.prompt.txt` | 0.85 | confidence < 0.85, very negative sentiment, SLA breach imminent, refund > 10K | zendesk.create_ticket, zendesk.update_ticket, zendesk.get_ticket, zendesk.escalate_ticket, zendesk.get_sla_status |
| 20 | `support_deflector` | ops | `support_deflector.prompt.txt` | 0.90 | (auto-responds only when confidence > 0.90) | zendesk.apply_macro, zendesk.update_ticket, confluence.search_pages |
| 21 | `vendor_manager` | ops | `vendor_manager.prompt.txt` | 0.85 | new vendor contract > 10L, performance < 50, renewal without bidding | tally.get_ledger_balance, docusign.send_envelope, docusign.get_envelope_status, jira.create_issue, hubspot.create_company |
| 22 | `compliance_guard` | ops | `compliance_guard.prompt.txt` | 0.92 | compliance score < 70, new regulation identified | mca_portal.file_annual_return, mca_portal.complete_director_kyc, mca_portal.fetch_company_master_data, epfo.file_ecr, sanctions_api.generate_report, confluence.search_pages |
| 23 | `contract_intelligence` | ops | `contract_intelligence.prompt.txt` | 0.88 | contract value > 50L, high-risk clauses, NDA with competitor | docusign.send_envelope, docusign.get_envelope_status, docusign.download_document, s3.upload_file |
| 24 | `legal_ops` | backoffice | `legal_ops.prompt.txt` | 0.90 | contract value > 50L, high-risk clauses, NDA with competitor, litigation settlement | docusign.send_envelope, docusign.get_envelope_status, docusign.download_document, s3.upload_file, confluence.search_pages |
| 25 | `risk_sentinel` | backoffice | `risk_sentinel.prompt.txt` | 0.95 | any sanctions match, fraud alert, compliance < 70, data breach | sanctions_api.screen_entity, sanctions_api.screen_transaction, sanctions_api.batch_screen, sanctions_api.get_alert, sanctions_api.generate_report, pagerduty.list_incidents |
| 26 | `facilities_agent` | backoffice | `facilities_agent.prompt.txt` | 0.82 | expense > 110% budget, asset purchase > 50K | jira.create_issue, jira.search_issues, tally.get_ledger_balance |
| 27 | `email_agent` | marketing | `email_agent.prompt.txt` | 0.85 | unsubscribe rate > 1%, large send > 50K | mailchimp.create_campaign, mailchimp.send_campaign, sendgrid.send_email |
| 28 | `social_media` | marketing | `social_media.prompt.txt` | 0.82 | crisis detected, competitor mention | buffer.create_post, twitter.post_tweet, twitter.search_mentions |
| 29 | `expense_manager` | finance | `expense_manager.prompt.txt` | 0.88 | expense > policy limit, duplicate expense | tally.post_voucher, tally.get_ledger_balance |
| 30 | `fixed_assets_agent` | finance | `fixed_assets_agent.prompt.txt` | 0.88 | asset purchase > 1L, depreciation method change | tally.post_voucher, tally.get_ledger_balance |
| 31 | `rev_rec_agent` | finance | `rev_rec_agent.prompt.txt` | 0.92 | revenue recognition policy change, unbilled revenue > 10L | tally.post_voucher, tally.get_trial_balance |
| 32 | `treasury_agent` | finance | `treasury_agent.prompt.txt` | 0.92 | cash below 3-month runway, FD maturity unscheduled | banking_aa.check_account_balance, banking_aa.fetch_bank_statement, tally.get_ledger_balance |
| 33 | `sales_agent` | marketing | `sales_agent.prompt.txt` | 0.85 | deal value > 50L, competitive loss | hubspot.list_deals, hubspot.create_deal, hubspot.update_deal, salesforce.get_opportunity |
| 34 | `competitive_intel` | marketing | `competitive_intel.prompt.txt` | 0.82 | (advisory only) | brandwatch.get_competitors, ahrefs.get_backlinks |
| 35 | `notification_agent` | ops | `notification_agent.prompt.txt` | 0.85 | (notification delivery -- no HITL) | slack.send_message, sendgrid.send_email, whatsapp.send_message, twilio.send_sms |

### Appendix B. Complete Connector Registry

| # | Connector Name | Category | Tools | Auth Type | Rate Limit | Status |
|---|---------------|----------|-------|-----------|------------|--------|
| 1 | `tally` | finance | post_voucher, get_ledger_balance, generate_gst_report, export_tally_xml_data, get_trial_balance, get_stock_summary | tdl_xml | 60/min | Live |
| 2 | `banking_aa` | finance | fetch_bank_statement, check_account_balance, get_transaction_list, request_consent, fetch_fi_data | aa_oauth2 | 100/min | Live |
| 3 | `gstn` | finance | fetch_gstr2a, push_gstr1_data, file_gstr3b, file_gstr9, generate_eway_bill, generate_einvoice_irn, check_filing_status, get_compliance_notice | gsp_dsc | 50/min | Live |
| 4 | `income_tax_india` | finance | file_tds_return, get_26as, compute_advance_tax, generate_form16, check_itr_status | dsc | 20/min | Live |
| 5 | `sap` | finance | get_journal_entries, post_journal_entry, get_purchase_orders, get_grn, get_vendor_list | odata_oauth2 | 100/min | Live |
| 6 | `oracle_fusion` | finance | get_journal_entries, post_journal_entry, get_purchase_orders, get_gl_balance | rest_oauth2 | 100/min | Live |
| 7 | `quickbooks` | finance | create_invoice, get_profit_loss, get_balance_sheet, get_accounts | oauth2 | 200/min | Live |
| 8 | `zoho_books` | finance | create_invoice, get_reports, get_contacts, get_bank_accounts | oauth2 | 100/min | Live |
| 9 | `netsuite` | finance | get_transactions, create_journal, get_vendors, get_inventory | oauth2 | 50/min | Live |
| 10 | `stripe` | finance | create_charge, list_charges, get_balance, create_payout | api_key | 100/min | Live |
| 11 | `pinelabs_plural` | finance | create_payment_link, check_status, create_refund | api_key | 60/min | Live |
| 12 | `aa_consent` | finance | (internal -- used by banking_aa for consent management) | internal | N/A | Live |
| 13 | `gstn_sandbox` | finance | (sandbox version of GSTN for testing) | api_key | 100/min | Live |
| 14 | `darwinbox` | hr | get_employee, create_employee, run_payroll, get_attendance, apply_leave, get_org_chart, update_performance, terminate_employee, transfer_employee, get_payslip | api_key_oauth2 | 200/min | Live |
| 15 | `keka` | hr | get_employee, list_employees, run_payroll, get_leave_balance, post_reimbursement, get_attendance_summary | api_key | 100/min | Live |
| 16 | `epfo` | hr | file_ecr, get_uan, check_claim_status, download_passbook, generate_trrn, verify_member | dsc | 10/min | Live |
| 17 | `greenhouse` | hr | create_candidate, advance_candidate, schedule_interview, list_jobs, get_candidate | api_key | 100/min | Live |
| 18 | `linkedin_talent` | hr | post_job, search_candidates, get_candidate_profile | oauth2 | 50/min | Live |
| 19 | `docusign` | hr | send_envelope, get_envelope_status, void_envelope, download_document | jwt | 100/min | Live |
| 20 | `okta` | hr | create_user, deactivate_user, assign_group, reset_password | api_key | 100/min | Live |
| 21 | `zoom` | hr | create_meeting, get_meeting, list_recordings | oauth2 | 100/min | Live |
| 22 | `hubspot` | marketing | list_contacts, search_contacts, create_contact, get_contact, update_contact, list_deals, create_deal, get_deal, update_deal, list_pipelines, list_companies, create_company, get_campaign_analytics | oauth2 | 200/min | Live |
| 23 | `google_ads` | marketing | search_campaigns, get_campaign_performance, mutate_campaign_budget, get_search_terms, create_user_list | oauth2 | 200/min | Live |
| 24 | `meta_ads` | marketing | get_campaign_insights, create_campaign, update_campaign, get_ad_sets | oauth2 | 200/min | Live |
| 25 | `linkedin_ads` | marketing | get_campaign_analytics, create_campaign, get_audience | oauth2 | 100/min | Live |
| 26 | `mailchimp` | marketing | create_campaign, send_campaign, get_campaign_report, add_subscriber, get_list | api_key | 100/min | Live |
| 27 | `ahrefs` | marketing | get_backlinks, get_keywords, get_site_audit, get_domain_rating | api_key | 50/min | Live |
| 28 | `bombora` | marketing | get_surge_scores, get_topic_clusters, get_weekly_report, search_companies | api_key | 100/min | Live |
| 29 | `g2` | marketing | get_buyer_intent, get_reviews, get_competitors | api_key | 50/min | Live |
| 30 | `trustradius` | marketing | get_intent, get_reviews, get_product_ratings | api_key | 50/min | Live |
| 31 | `ga4` | marketing | get_report, get_realtime, get_user_properties | oauth2 | 100/min | Live |
| 32 | `mixpanel` | marketing | get_funnel, get_retention, get_events, track_event | api_key | 100/min | Live |
| 33 | `moengage` | marketing | create_campaign, get_campaign_stats, create_segment | api_key | 100/min | Live |
| 34 | `salesforce` | marketing | get_opportunity, create_lead, update_opportunity, get_report | oauth2 | 100/min | Live |
| 35 | `buffer` | marketing | create_post, get_analytics, get_profiles | oauth2 | 100/min | Live |
| 36 | `wordpress` | marketing | create_post, get_posts, update_post, get_analytics | api_key | 100/min | Live |
| 37 | `brandwatch` | marketing | get_mentions, get_sentiment, get_competitors, get_topics | api_key | 50/min | Live |
| 38 | `jira` | ops | list_projects, get_project, search_issues, get_issue, create_issue, update_issue, transition_issue, get_transitions, add_comment, get_sprint_data, get_project_metrics | oauth2 | 300/min | Live |
| 39 | `pagerduty` | ops | create_incident, acknowledge_incident, resolve_incident, get_on_call, list_incidents, create_postmortem | api_key | 100/min | Live |
| 40 | `servicenow` | ops | create_incident, update_incident, submit_change_request, get_cmdb_ci, check_sla_status, get_kb_article | rest_oauth2 | 100/min | Live |
| 41 | `zendesk` | ops | create_ticket, update_ticket, get_ticket, apply_macro, get_csat_score, escalate_ticket, merge_tickets, get_sla_status | api_token | 200/min | Live |
| 42 | `confluence` | ops | search_pages, get_page, create_page, update_page | api_token | 100/min | Live |
| 43 | `mca_portal` | ops | file_annual_return, complete_director_kyc, fetch_company_master_data, file_charge_satisfaction | dsc | 10/min | Live |
| 44 | `sanctions_api` | ops | screen_entity, screen_transaction, get_alert, batch_screen, generate_report | api_key | 500/min | Live |
| 45 | `slack` | comms | send_message, invite_user, remove_user, create_channel, get_channel_history | oauth2 | 100/min | Live |
| 46 | `sendgrid` | comms | send_email, send_template_email, get_bounces, get_stats | api_key | 100/min | Live |
| 47 | `twilio` | comms | send_sms, make_call, send_whatsapp | api_key | 100/min | Live |
| 48 | `whatsapp` | comms | send_message, send_template, get_message_status | api_key | 80/min | Live |
| 49 | `twitter` | comms | post_tweet, search_mentions, get_user_timeline, delete_tweet | oauth2 | 50/min | Live |
| 50 | `youtube` | comms | upload_video, list_videos, get_analytics | oauth2 | 50/min | Live |
| 51 | `gmail` | comms | send_email, list_emails, get_email, search_emails | oauth2 | 100/min | Live |
| 52 | `google_calendar` | comms | create_event, list_events, update_event, delete_event | oauth2 | 100/min | Live |
| 53 | `s3` | comms | upload_file, download_file, list_objects, delete_file | api_key | 200/min | Live |
| 54 | `teams_bot` | microsoft | send_message, create_channel, get_messages | oauth2 | 100/min | Live |

### Appendix C. Complete KPI Registry

#### CEO KPIs

| # | Metric | Data Source | Computation | Refresh | Unit |
|---|--------|-----------|-------------|---------|------|
| 1 | Monthly Revenue | tally.get_trial_balance -> Revenue group | SUM(credit entries in Revenue group for current month) | Daily | INR |
| 2 | Cash Runway | banking_aa.check_account_balance / fpa_agent | total_cash / avg_monthly_opex(last 3 months) | Daily | Months |
| 3 | Total Headcount | darwinbox.get_org_chart | COUNT(active employees) | Daily | Number |
| 4 | Monthly Attrition Rate | darwinbox terminations | exits_this_month / headcount_start_of_month * 100 | Monthly | % |
| 5 | Pipeline Value | hubspot.list_deals | SUM(deal.amount for open deals) | Hourly | INR |
| 6 | CAC | campaign_pilot aggregation | total_sales_marketing_spend / new_customers | Weekly | INR |
| 7 | Open Support Tickets | zendesk.get_ticket (search open) | COUNT(tickets where status IN (new, open, pending)) | Real-time | Number |
| 8 | MTTR | pagerduty.list_incidents | AVG(resolved_at - created_at) for last 30 days | Hourly | Minutes |
| 9 | Compliance Score | compliance_guard | Weighted average of all compliance area scores | Weekly | 0-100 |
| 10 | Pending Approvals | internal DB | COUNT(hitl_requests where status=pending) | Real-time | Number |

#### CFO KPIs (28 metrics across 5 tabs -- first 15 shown, rest follow same pattern)

| # | Tab | Metric | Data Source | Computation | Refresh | Unit |
|---|-----|--------|-----------|-------------|---------|------|
| 1 | Treasury | Total Cash Balance | banking_aa.check_account_balance | SUM(balance for all configured accounts) | Real-time | INR |
| 2 | Treasury | Cash Runway | banking_aa + fpa_agent | total_cash / avg_monthly_opex(3mo) | Daily | Months |
| 3 | Treasury | Monthly Burn Rate | tally.get_trial_balance | SUM(opex groups for current month) | Daily | INR |
| 4 | Treasury | FD Maturity Calendar | tally.get_ledger_balance (FD group) | List of FDs with maturity dates | Weekly | INR+Date |
| 5 | Treasury | Net Cash Position | banking_aa + tally | cash + fd - payables | Daily | INR |
| 6 | AP/AR | DPO | tally AP balance / COGS | avg_ap / (annual_cogs / 365) | Daily | Days |
| 7 | AP/AR | DSO | tally AR balance / Revenue | avg_ar / (annual_revenue / 365) | Daily | Days |
| 8 | AP/AR | AP Aging Buckets | tally AP ledger by date | Grouped by 0-30, 31-60, 61-90, 90+ | Daily | INR |
| 9 | AP/AR | AR Aging Buckets | tally AR ledger by date | Grouped by 0-30, 31-60, 61-90, 90+ | Daily | INR |
| 10 | AP/AR | Invoices Processed MTD | agent_task_results (ap_processor) | COUNT(completed this month) | Real-time | Number |
| 11 | Tax | GST Filing Calendar | gstn.check_filing_status | 12-month grid with status per return type | Daily | Status[] |
| 12 | Tax | ITC Mismatch | gstn.fetch_gstr2a vs tally | ABS(claimed - available) | Monthly | INR |
| 13 | Tax | TDS Quarterly Status | income_tax_india | Filing status per quarter | Quarterly | Status |
| 14 | Close | Close Completion % | close_agent checklist | completed_items / total_items * 100 | Real-time | % |
| 15 | Budget | Budget Variance | fpa_agent | actual - budget by department | Monthly | INR |

#### CHRO KPIs (22 metrics), CMO KPIs (25 metrics), COO KPIs (20 metrics), CBO KPIs (18 metrics)

All follow the same pattern documented in Section 2 for each job function.

**Total KPIs across all roles: ~123 metrics**

### Appendix D. Complete Workflow Registry

| # | Workflow Name | Domain | Trigger | Steps | HITL Points | Expected Duration |
|---|-------------|--------|---------|-------|-------------|-------------------|
| 1 | `bank_recon_daily` | finance | Cron: 0 9 * * 1-5 (weekday 9AM) | 7 steps | 1 (break escalation) | 15-30 min |
| 2 | `invoice_to_pay_v3` | finance | Event: invoice received | 6 steps | 1 (mismatch review) | 5-10 min |
| 3 | `gstr_filing_monthly` | finance | Cron: 0 10 1 * * (1st of month) | 6 steps | 1 (CFO approval) | 2-4 hours |
| 4 | `tds_quarterly_filing` | finance | Cron: quarterly | 5 steps | 1 (CFO approval) | 1-2 hours |
| 5 | `month_end_close` | finance | Cron: 0 8 1 * * (1st of month) | 9 steps | 1 (CFO sign-off) | 1-5 days |
| 6 | `daily_treasury` | finance | Cron: 0 9:30 * * 1-5 | 5 steps | 0 (alert only) | 10 min |
| 7 | `tax_calendar` | finance | Cron: daily | 3 steps | 0 (reminders only) | 2 min |
| 8 | `ar_collection_cycle` | finance | Cron: daily | 7 steps | 1 (90+ day decision) | varies |
| 9 | `employee_onboarding` | hr | Event: offer accepted | 11 steps | 1 (docs incomplete) | 30 days |
| 10 | `monthly_payroll` | hr | Cron: 0 8 25 * * (25th of month) | 10 steps | 1 (CHRO approval) | 3-5 days |
| 11 | `recruitment_pipeline` | hr | Event: new requisition | 10 steps | 1 (offer approval) | 2-8 weeks |
| 12 | `campaign_launch` | marketing | Event: brief created | 7 steps | 1 (variant selection) | 1-3 days |
| 13 | `email_drip_sequence` | marketing | Event: lead enrolled | 5 steps | 0 | 14-30 days |
| 14 | `abm_campaign` | marketing | Cron: weekly | 5 steps | 1 (target list change) | 1 hour |
| 15 | `content_pipeline` | marketing | Cron: weekly | 6 steps | 1 (publish approval) | 3-5 days |
| 16 | `weekly_marketing_report` | marketing | Cron: Friday 5PM | 4 steps | 0 | 15 min |
| 17 | `it_incident_escalation` | ops | Event: PagerDuty webhook | 9 steps | 1 (postmortem review) | varies |
| 18 | `support_triage` | ops | Event: new Zendesk ticket | 8 steps | 0 (auto-route) | 5 min |
| 19 | `vendor_onboarding` | ops | Event: new vendor approved | 6 steps | 1 (contract review) | 1-2 weeks |
| 20 | `compliance_review` | ops | Cron: quarterly | 6 steps | 1 (CBO review) | 1 week |
| 21 | `contract_review` | backoffice | Event: contract uploaded | 7 steps | 1 (risk review) | 1-5 days |
| 22 | `board_meeting_prep` | backoffice | Event: 14 days before meeting | 7 steps | 1 (minutes approval) | 2 weeks |
| 23 | `transaction_screening` | backoffice | Event: new payment/onboarding | 6 steps | 1 (match review) | 5 min |
| 24 | `daily_ceo_briefing` | executive | Cron: 0 8 * * 1-5 (weekday 8AM IST) | 5 steps (aggregate KPIs from all domains -> generate 1-page summary -> deliver via email + Slack) | 0 (informational) | 5 min |
| 25 | `weekly_board_prep` | executive | Cron: 0 17 * * 5 (Friday 5PM IST) | 4 steps (compile weekly metrics -> generate MIS pack -> format PDF -> deliver to board) | 0 (informational) | 30 min |
| 26 | `escalation_router` | executive | Event: HITL timeout (4 hours) | 3 steps (detect timeout -> route to next CxO/CEO -> notify all parties) | 0 (routing only) | 1 min |
| 27 | `monthly_board_pack` | executive | Cron: 0 10 1 * * (1st business day 10AM IST) | 8 steps (fpa_agent P&L/BS -> talent_acquisition headcount -> campaign_pilot marketing -> support_triage ops -> compliance_guard compliance -> compile PDF -> CEO HITL review -> deliver) | 1 (CEO approval) | 2-4 hours |

### Appendix E. Database Schema ERD

```
┌─────────────────┐    ┌──────────────────┐    ┌───────────────────┐
│    tenants       │    │    kpi_cache      │    │   kpi_history     │
├─────────────────┤    ├──────────────────┤    ├───────────────────┤
│ id (PK, UUID)   │───>│ tenant_id (FK)   │    │ tenant_id         │
│ name            │    │ role (VARCHAR)    │    │ role              │
│ domain          │    │ metric_name      │    │ metric_name       │
│ plan            │    │ metric_value (J)  │    │ metric_value (J)  │
│ created_at      │    │ computed_at      │    │ computed_at       │
└─────────────────┘    │ source_agent     │    └───────────────────┘
        │              │ refresh_type     │           (partitioned)
        │              │ ttl_seconds      │
        │              │ is_stale         │
        │              └──────────────────┘
        │
        │    ┌──────────────────────┐    ┌─────────────────────┐
        ├───>│  agent_task_results  │    │  connector_configs   │
        │    ├──────────────────────┤    ├─────────────────────┤
        │    │ id (PK, UUID)       │    │ id (PK, UUID)       │
        │    │ tenant_id (FK)      │<───│ tenant_id (FK)      │
        │    │ agent_id            │    │ connector_name      │
        │    │ agent_type          │    │ auth_type           │
        │    │ domain              │    │ credentials_encrypted│
        │    │ task_type           │    │ status              │
        │    │ input (JSONB)       │    │ health_status       │
        │    │ output (JSONB)      │    │ consecutive_failures│
        │    │ status              │    │ last_health_check   │
        │    │ confidence          │    │ auto_discovered_data│
        │    │ tool_calls (JSONB)  │    └─────────────────────┘
        │    │ reasoning_trace     │
        │    │ llm_model           │    ┌─────────────────────┐
        │    │ llm_cost_usd        │    │ connector_health_log│
        │    │ duration_ms         │    ├─────────────────────┤
        │    │ workflow_run_id     │    │ id (PK, UUID)       │
        │    └──────────────────────┘    │ tenant_id           │
        │                               │ connector_name      │
        │    ┌──────────────────────┐    │ status              │
        ├───>│  workflow_schedules  │    │ response_time_ms    │
        │    ├──────────────────────┤    │ error               │
        │    │ id (PK, UUID)       │    │ checked_at          │
        │    │ tenant_id (FK)      │    └─────────────────────┘
        │    │ workflow_id         │
        │    │ cron_expression     │    ┌─────────────────────┐
        │    │ is_active           │    │   board_reports     │
        │    │ last_run_at         │    ├─────────────────────┤
        │    │ next_run_at         │    │ id (PK, UUID)       │
        │    │ inputs (JSONB)      │    │ tenant_id (FK)      │
        │    └──────────────────────┘    │ report_type         │
        │                               │ report_period       │
        │    ┌──────────────────────┐    │ file_path           │
        └───>│ dashboard_preferences│    │ status              │
             ├──────────────────────┤    │ sections (JSONB)    │
             │ id (PK, UUID)       │    └─────────────────────┘
             │ tenant_id (FK)      │
             │ user_id             │
             │ role                │
             │ visible_tabs        │
             │ pinned_metrics      │
             └──────────────────────┘
```

**All tables have:**
- `ENABLE ROW LEVEL SECURITY`
- RLS policy enforcing `tenant_id = current_setting('app.current_tenant_id')::UUID`
- Appropriate indexes for query patterns
- `created_at` and `updated_at` timestamps where applicable

---

## 13. Edge Cases & Negative Scenarios

This section defines 35 edge cases with expected behavior, fallback strategies, and test case names. Every edge case must have a corresponding automated test.

### 13.1 Connector Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 1 | Connector returns HTTP 429 (rate limited) | Retry with exponential backoff: 1s, 2s, 4s, 8s, 16s. Max 5 retries. Log each retry. | After 5 retries, mark connector health as "yellow", return last-known cached value with staleness indicator. Queue the request for retry in 5 minutes. | `test_connector_429_retry_backoff` |
| 2 | Connector returns HTTP 401 (auth expired) | Attempt token refresh (for OAuth2 connectors). If refresh succeeds, retry original request once. | If refresh fails, mark connector status as "red", send admin alert via Slack, show "Reconnection required" badge on dashboard. | `test_connector_401_token_refresh` |
| 3 | Connector returns HTTP 500 (server error) | Retry once after 2 seconds. Log full error response body for debugging. | After 1 retry, return last-known cached value. Mark connector as "yellow". If 3 consecutive 500s, mark as "red" and alert admin. | `test_connector_500_retry_and_fallback` |
| 4 | Connector returns valid HTTP 200 but response body is empty or malformed JSON | Detect empty/malformed body. Log raw response for debugging. Treat as connector error. | Return last-known cached value. Mark connector health as "yellow". Alert admin if pattern repeats 3 times. | `test_connector_empty_response_handling` |
| 5 | Connector timeout (no response within 30 seconds) | Cancel the request. Log timeout event with connector name, endpoint, and tenant. | Return last-known cached value with "Data source timed out" indicator. Retry in background after 1 minute. | `test_connector_timeout_30s` |
| 6 | A tenant has zero connectors configured | Dashboard loads with all empty states (Section 6.8.1). No agent execution triggered. No errors thrown. | Show "Getting Started" wizard guiding the admin to configure at least one connector per CxO role. | `test_tenant_no_connectors_empty_state` |
| 7 | Connector API endpoint has been deprecated/removed by the external vendor | Health check starts failing with 404. Detect this pattern (404 on health check is different from 404 on missing resource). | Mark connector as "red" with "API endpoint unavailable -- possible breaking change" message. Email admin with connector update instructions. | `test_connector_api_deprecated_detection` |
| 8 | Webhook delivery fails (external system does not acknowledge) | Retry webhook delivery 3 times with exponential backoff: 10s, 60s, 300s. | After 3 failures, store the event in a dead-letter queue (`webhook_dead_letters` table). Admin can view and replay from UI. | `test_webhook_delivery_failure_retry` |
| 9 | Connector returns data in unexpected format (schema change) | Validate response against expected schema. If validation fails, treat as connector error. | Return last-known cached value. Log schema mismatch details. Alert admin with "Connector response format changed" notification. | `test_connector_schema_mismatch` |

### 13.2 Agent Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 10 | LLM returns garbage/hallucinated financial data | Post-processing validation in every finance agent checks: amounts must come from tool results (not LLM text), dates must be valid, GL codes must exist in Tally ledger. Any mismatch -> reject. | Mark result as `status=failed`, `error="LLM output failed domain validation"`. If HITL threshold met, escalate to CxO with full context. Otherwise retry with stricter prompt. | `test_agent_llm_hallucination_detection` |
| 11 | Agent confidence is below threshold but HITL queue is full (> 100 pending items for this tenant) | Accept the HITL request anyway (no hard cap on queue). Send escalation alert to admin: "HITL queue has 100+ pending items for [role]. Consider increasing auto-approval thresholds or adding approvers." | Never auto-approve below-confidence results. Queue overflow triggers daily digest email to CxO with summary of all pending items. | `test_agent_hitl_queue_overflow` |
| 12 | Agent calls a tool (connector function) that no longer exists in the registry | Tool call fails immediately with `ToolNotFoundError`. Agent receives error in tool_calls response. | Agent should not retry the same tool. Mark result as `status=failed` with clear error. Admin notification: "Agent [X] tried to use tool [Y] which is no longer registered." | `test_agent_calls_removed_tool` |
| 13 | Two agents try to approve/process the same entity simultaneously (e.g., both try to post the same journal entry to Tally) | Optimistic locking on `agent_task_results`: before executing write operations, agent checks if another task already processed this entity (using entity_id + entity_type dedup key). | If duplicate detected, second agent gets `status=duplicate`. First agent's result stands. Log the race condition for monitoring. | `test_agent_concurrent_same_entity` |
| 14 | Agent execution exceeds 5-minute timeout | Agent execution is hard-killed after `timeout_minutes` (configurable per workflow step, default 5 min for non-LLM steps, 2 min for LLM calls). | Task marked as `status=failed`, `error="Execution timeout after 300s"`. Workflow failure path triggered. Partial results are discarded (no half-written data). | `test_agent_execution_timeout` |
| 15 | LLM API is completely down (OpenAI/Anthropic/Google outage) | Agent cannot get LLM response. Circuit breaker trips after 3 consecutive LLM failures across all agents. | All agent executions are paused. Dashboard shows last-known cached data. Admin alert: "LLM provider is down. Agent executions paused." Auto-resume when LLM becomes available (health check every 60s). | `test_llm_provider_outage_circuit_breaker` |
| 16 | Agent produces a result that contradicts a previous result (e.g., revenue decreased by 90% vs last computation) | Anomaly detection in post-processing: if any KPI changes by > 50% compared to the previous value, flag for review. | Result is stored but marked as `anomalous=true`. HITL request generated: "Revenue changed by -90%. Please verify." Do not update Redis cache until verified. | `test_agent_anomaly_detection` |

### 13.3 Workflow Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 17 | Workflow step times out after configured timeout | Step marked as `status=timed_out`. Workflow engine evaluates failure path: if step has `on_failure: retry`, retry up to `max_retries`. If `on_failure: skip`, move to next step. If `on_failure: abort`, terminate workflow. | Log timeout, notify workflow owner. If step was a write operation, verify no partial writes occurred (idempotency check). | `test_workflow_step_timeout` |
| 18 | GST filing fails mid-submission (network error after sending data to GSTN but before receiving acknowledgment) | The tax_compliance agent must query `gstn.check_filing_status` to determine if the filing was received. This is a reconciliation step, not a retry. | If filing status shows "filed", record success. If "not filed", safe to retry. If "status unknown", HITL escalation to CFO with context: "GSTN filing may or may not have been submitted. Manual verification required." | `test_gst_filing_midstream_failure` |
| 19 | Payroll runs with incorrect employee data (wrong salary, wrong deductions) | Payroll engine validates against: (a) previous month's payroll (+/- 10% tolerance per employee), (b) CTC from Darwinbox, (c) attendance records. Any mismatch -> HITL before disbursement. | Never auto-disburse if validation fails. Flag specific employees with discrepancies. CHRO reviews and corrects before re-running. | `test_payroll_incorrect_data_validation` |
| 20 | Bank reconciliation has 100% mismatches (e.g., wrong date range pulled, wrong account) | If auto-match rate is 0% (or < 10%), recon_agent should flag this as "likely configuration error" rather than 1000 individual mismatches. | HITL escalation: "Bank reconciliation returned 0% match rate. Possible causes: wrong account configured, date range mismatch, or Tally data not synced." Do not generate BRS report. | `test_recon_100_percent_mismatch` |
| 21 | HITL request expires (4-hour timeout) with no CxO action | Automatically escalate to CEO (as per escalation_router workflow). If CEO does not act within another 4 hours, mark as `status=expired`. | For non-critical items: auto-reject and notify workflow owner. For critical items (GST filing, payroll): keep in queue indefinitely, send hourly reminders to all admins. | `test_hitl_escalation_timeout` |
| 22 | Two HITL approvals arrive for the same filing simultaneously | First approval is processed. Second approval gets 409 Conflict. | Return clear message: "This item has already been approved by [user] at [timestamp]." | `test_hitl_double_approval_race` |

### 13.4 Infrastructure Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 23 | Redis cache is completely down | Cache operations fail. `get_kpi()` catches `RedisConnectionError`, falls through to PostgreSQL `kpi_cache` table. | All dashboards continue working from PostgreSQL (slightly slower: ~500ms vs ~50ms). Log Redis outage. Alert infra team. Auto-reconnect when Redis is back. | `test_redis_down_postgres_fallback` |
| 24 | Redis cache is corrupted (returns malformed data) | `json.loads()` fails on cached value. Treat as cache miss. | Delete the corrupted key. Recompute from agent/PostgreSQL. Log corruption event. If > 10 corrupted keys in 5 minutes, flush entire cache (it will rebuild from PostgreSQL). | `test_redis_corrupted_data_handling` |
| 25 | PostgreSQL is in read-only mode (failover in progress) | Write operations fail with `ReadOnlyTransactionError`. Read operations continue. | Queue writes in an in-memory buffer (max 1000 entries, max 5 minutes). When DB comes back to read-write, flush the buffer. Dashboard reads continue from cache/read-replica. | `test_postgres_readonly_mode` |
| 26 | Encryption key is rotated mid-operation | Active connector operations use the old key (still valid). New credential encryptions use the new key. `encryption_key_version` column tracks which key version encrypted each record. | Background job re-encrypts all existing credentials with new key. Old key remains valid for 24 hours (grace period). No downtime. | `test_encryption_key_rotation` |
| 27 | Peak load: month-end close for all clients simultaneously (100 tenants run `month_end_close` workflow at midnight IST) | Rate-limited workflow execution queue with fair scheduling: max 10 concurrent workflow runs globally, round-robin across tenants. Each tenant gets at least 1 slot per 5-minute window. | If queue depth > 200, send admin alert. Auto-scale agent workers (GCP Cloud Run min/max instances). Show "Processing queued" status on dashboard. | `test_peak_load_month_end` |
| 28 | Network partition between API server and database | API health check detects DB unreachable. Return 503 for write operations. For reads, serve from Redis cache. | Circuit breaker opens for DB operations. Dashboard shows "Limited functionality -- some features temporarily unavailable." Auto-retry DB connection every 10 seconds. | `test_db_network_partition` |

### 13.5 Security Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 29 | User tries to access another tenant's data by manipulating `tenant_id` in request | JWT token contains `tenant_id`. Server-side always uses the token's `tenant_id`, never from request body/params. RLS adds defense-in-depth. | Any attempt to cross-tenant access is logged in `audit_log` with `action=cross_tenant_attempt`. 3 attempts in 1 hour -> account locked, admin alerted. | `test_cross_tenant_access_attempt` |
| 30 | PII data (Aadhaar, PAN, bank account) appears in agent reasoning trace | PII masking layer runs on all agent inputs and outputs before storage. Regex patterns: Aadhaar (`\d{4}\s\d{4}\s\d{4}` -> `XXXX XXXX ****`), PAN (`[A-Z]{5}\d{4}[A-Z]` -> `XXXXX****X`), bank account (last 4 digits only). | If PII detected in stored data (audit scan), auto-redact and alert data protection officer. | `test_pii_masking_in_agent_traces` |
| 31 | Admin accidentally configures connector with wrong tenant's credentials | Health check will likely succeed (valid credentials, wrong account). Data returned will be from wrong tenant. | Connector auto-discovery step (after configuration) shows discovered data summary (company name, employee count, etc.). Admin must confirm: "Is this [Company Name]?" before activating. | `test_connector_wrong_tenant_credentials` |
| 32 | DDoS attack on KPI endpoints | Rate limiting at API gateway (Cloud Armor). Per-IP rate: 200/min. Per-tenant rate: 120/min (see 5.0.3). | WAF blocks IPs exceeding rate limit. Cloud Armor auto-scales. If attack bypasses rate limiting, circuit breaker on Redis prevents cache stampede. | `test_ddos_rate_limiting` |

### 13.6 Data Integrity Edge Cases

| # | Scenario | Expected Behavior | Fallback | Test Case |
|---|----------|-------------------|----------|-----------|
| 33 | KPI value from agent contradicts the value in cache (agent says revenue=78L, cache has revenue=80L) | Cache is always overwritten with the latest agent result. The previous value is preserved in `kpi_history` table. Dashboard shows the latest value. | If the delta is > 50%, anomaly detection triggers (see #16). `kpi_history` provides full audit trail for investigation. | `test_kpi_cache_overwrite_audit` |
| 34 | Tally bridge disconnects mid-sync (CA's local machine goes offline) | Tally connector health check fails. Pending operations are queued. | Queue up to 100 pending operations per tenant. Notify admin and CA: "Tally bridge disconnected. [N] operations pending." Auto-retry connection every 5 minutes. After 1 hour disconnect, send urgent alert. | `test_tally_bridge_disconnect_mid_sync` |
| 35 | Account Aggregator consent expires while daily treasury workflow is running | AA API returns consent-expired error. treasury workflow step fails. | Return last-known bank balance with "AA consent expired" badge. Trigger consent renewal flow. Email CFO: "Bank balance data is stale -- AA consent needs renewal." Provide one-click renewal link. | `test_aa_consent_expiry_mid_workflow` |

---

## 14. Non-Functional Requirements

### 14.1 Performance

| Requirement | Target | Measurement | Priority |
|-------------|--------|-------------|----------|
| Dashboard page load (warm cache, first contentful paint) | < 1.5 seconds | Lighthouse FCP metric | P0 |
| Dashboard page load (warm cache, time to interactive) | < 2.0 seconds | Lighthouse TTI metric | P0 |
| Dashboard page load (cold cache, time to interactive) | < 5.0 seconds | Lighthouse TTI metric | P0 |
| KPI API response time (Redis cache hit) | < 100 milliseconds (P95) | k6 load test | P0 |
| KPI API response time (PostgreSQL fallback) | < 500 milliseconds (P95) | k6 load test | P0 |
| KPI API response time (cache miss, agent computation) | < 3.0 seconds (P95) | k6 load test | P1 |
| Agent execution time (including LLM call) | < 30 seconds (P95) | agent_task_results.duration_ms | P0 |
| Agent execution time (tool calls only, no LLM) | < 10 seconds (P95) | agent_task_results.duration_ms | P0 |
| Connector API call latency | < 5 seconds (P95) per call | connector_health_log.response_time_ms | P1 |
| WebSocket message delivery (real-time KPIs) | < 500 milliseconds | Client-side timestamp delta | P1 |
| PDF report generation (board pack, 10 sections) | < 120 seconds | report generation timer | P1 |
| Concurrent users per tenant | 50 simultaneous users, all dashboards responsive | k6 load test, 0 errors | P0 |
| Concurrent users across platform | 500 simultaneous users (across all tenants) | k6 load test, 0 errors | P1 |
| React bundle size (gzipped) | < 500 KB initial, < 200 KB per lazy-loaded dashboard chunk | Webpack bundle analyzer | P1 |

### 14.2 Scalability

| Dimension | Current Target | Scale Ceiling | Scaling Strategy |
|-----------|---------------|---------------|------------------|
| Number of tenants | 100 | 1,000 | Horizontal scaling via GCP Cloud Run auto-scaling. DB connection pooling via PgBouncer. |
| Agents per tenant | 35 (all active) | 100 (with custom agents) | Agent execution queue (Celery/Cloud Tasks) with per-tenant fair scheduling. |
| Concurrent agent executions (global) | 50 | 500 | Cloud Run auto-scaling with max 50 instances. Each instance handles 10 concurrent agents. |
| Concurrent agent executions (per tenant) | 10 | 25 | Per-tenant semaphore in Redis. Queue overflow returns `429` with `Retry-After`. |
| KPI metrics per tenant | ~123 | 500 | Indexed queries. Partitioned `kpi_history`. Redis cluster for cache. |
| Connector configs per tenant | 54 | 100 (with custom connectors) | Indexed `connector_configs` table. Connection pooling per connector. |
| Workflow runs per day (per tenant) | 23 (one per workflow) | 100 (including ad-hoc) | Workflow queue with priority scheduling. |
| Agent task results storage | 10,000 per tenant per month | 100,000 per tenant per month | Annual purge of records older than 1 year. Partitioning if volume exceeds 10M rows. |
| Report storage | 12 per tenant per month | 100 per tenant per month | S3 lifecycle policy: move to IA after 90 days, delete after 1 year. |

### 14.3 Availability

| Requirement | Target | Strategy |
|-------------|--------|----------|
| Platform uptime | 99.9% (8.77 hours downtime/year max) | GCP Cloud Run multi-zone deployment, managed PostgreSQL with HA, Redis Memorystore with auto-failover |
| RTO (Recovery Time Objective) | < 15 minutes for API services, < 1 hour for full platform | Automated deployment via CI/CD. Database failover is automatic (GCP-managed). Pre-baked container images. |
| RPO (Recovery Point Objective) | 0 for database (synchronous replication), < 5 minutes for Redis | PostgreSQL: synchronous replication to standby. Redis: AOF persistence, 1-second fsync. |
| Health check endpoints | `GET /health/live` (liveness), `GET /health/ready` (readiness) | Liveness: returns 200 if process is running. Readiness: returns 200 only if DB and Redis are connected. |
| Circuit breaker pattern | Per-connector and per-LLM-provider | Open after 5 consecutive failures. Half-open after 60 seconds (try one request). Close on success. |
| Graceful degradation priority | 1. Dashboard reads (always available from cache) 2. Agent execution 3. Workflow scheduling 4. Report generation | Under load, shed lower-priority operations first. |
| Deployment strategy | Blue-green deployment | Zero-downtime deploys. Health check must pass before traffic switches. Automatic rollback on health check failure. |
| Database maintenance windows | Sunday 2:00-4:00 AM IST | Automated vacuum, reindex, partition maintenance. Read-only mode during maintenance (API returns cached data). |

### 14.4 Observability

#### 14.4.1 Metrics (Prometheus)

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `agentic_kpi_request_duration_seconds` | Histogram | `role`, `metric_name`, `cache_hit` | KPI API response time |
| `agentic_agent_execution_duration_seconds` | Histogram | `agent_type`, `domain`, `status` | Agent execution time |
| `agentic_agent_executions_total` | Counter | `agent_type`, `domain`, `status` | Total agent executions |
| `agentic_connector_health_status` | Gauge | `connector_name`, `tenant_id` | 1=green, 0.5=yellow, 0=red |
| `agentic_connector_response_time_ms` | Histogram | `connector_name`, `method` | Connector API call latency |
| `agentic_hitl_queue_depth` | Gauge | `tenant_id`, `role` | Pending HITL items per tenant per role |
| `agentic_cache_hit_rate` | Gauge | `role` | Redis cache hit percentage |
| `agentic_llm_cost_usd` | Counter | `model`, `agent_type` | Cumulative LLM spending |
| `agentic_workflow_runs_total` | Counter | `workflow_name`, `status` | Total workflow runs by status |
| `agentic_workflow_duration_seconds` | Histogram | `workflow_name` | Workflow execution time |

#### 14.4.2 Logs (structlog)

All logs are structured JSON with these mandatory fields:

```json
{
  "timestamp": "2026-04-08T10:30:00Z",
  "level": "info",
  "event": "agent_execution_completed",
  "tenant_id": "uuid",
  "request_id": "uuid",
  "agent_type": "ap_processor",
  "duration_ms": 2450,
  "status": "completed",
  "confidence": 0.92
}
```

**Log levels:**
- `DEBUG`: Tool call details, LLM prompt/response (PII-masked), cache operations
- `INFO`: Agent execution start/complete, workflow step transitions, HITL created
- `WARNING`: Connector health degraded, cache miss, anomaly detected, PII detected in unexpected field
- `ERROR`: Agent execution failure, connector error, workflow step failure, authentication failure
- `CRITICAL`: Data breach detected, cross-tenant access attempt, all LLM providers down, DB connection lost

**PII masking in logs:** All log middleware strips PII (Aadhaar, PAN, bank account numbers, salary figures) before emission. Patterns defined in `core/logging/pii_patterns.py`.

#### 14.4.3 Traces (OpenTelemetry)

Distributed tracing with OpenTelemetry SDK:

- **Trace spans:** API request -> KPI service -> Cache lookup -> Agent execution -> LLM call -> Tool call -> Connector API -> External system
- **Trace propagation:** W3C TraceContext headers propagated across all internal service calls
- **Trace sampling:** 10% of requests in production, 100% for requests with errors
- **Trace storage:** Exported to GCP Cloud Trace (free tier: 2.5M spans/month)

#### 14.4.4 Alerting

| Alert | Condition | Channel | Severity |
|-------|-----------|---------|----------|
| API P95 latency > 5s | 5-minute rolling window | PagerDuty + Slack #alerts | P2 |
| Agent success rate < 90% | 1-hour rolling window | PagerDuty + Slack #alerts | P1 |
| Connector health red (any) | 3 consecutive failures | Slack #connectors | P3 |
| All connectors red for a tenant | All configured connectors red | PagerDuty + Slack #alerts | P1 |
| Redis down | Health check fails | PagerDuty | P1 |
| DB connection pool exhausted | Active connections > 90% of pool | PagerDuty | P1 |
| LLM provider down | Circuit breaker open | PagerDuty + Slack #alerts | P1 |
| HITL queue > 50 items | Per-tenant check | Slack #hitl-overflow | P3 |
| LLM cost > $100/day | Daily aggregation | Slack #cost-alerts | P2 |
| Disk usage > 80% | GCP monitoring | Slack #infra | P2 |
| Error rate > 5% | 5-minute rolling window | PagerDuty | P1 |

### 14.5 Data Retention

| Data Type | Retention Period | Storage Location | Purge Strategy |
|-----------|-----------------|-----------------|----------------|
| KPI history (`kpi_history`) | 2 years | PostgreSQL (partitioned) | Drop partitions older than 24 months (monthly cron) |
| Audit logs (`audit_log`) | 7 years | PostgreSQL (partitioned) | Drop partitions older than 84 months (monthly cron). Archive to GCS before drop. |
| Agent task results (`agent_task_results`) | 1 year | PostgreSQL | Cron job deletes records with `created_at < now() - interval '1 year'` (weekly) |
| Chat/conversation history | 90 days | PostgreSQL | Cron job deletes records older than 90 days (daily) |
| Connector health logs | 90 days | PostgreSQL | Cron job deletes records older than 90 days (weekly) |
| Board reports (PDFs) | 1 year active, 5 years archive | S3 (Standard -> IA -> Glacier) | S3 lifecycle: IA after 90 days, Glacier after 1 year, delete after 5 years |
| Dashboard preferences | Indefinite | PostgreSQL | Cleaned up when user is deleted |
| HITL requests | 2 years | PostgreSQL | Cron job deletes records older than 2 years (monthly) |
| Redis cache | Governed by TTL | Redis Memorystore | Automatic expiry per key TTL |
| Webhook dead letters | 30 days | PostgreSQL | Cron job deletes records older than 30 days (daily) |

### 14.6 Compliance

| Standard | Requirement | Implementation |
|----------|------------|----------------|
| **DPDPA (India Digital Personal Data Protection Act)** | Consent management, data localization, right to erasure, data processing records | All PII stored in India (GCP `asia-south1`). Consent tracking in `audit_log`. Right-to-erasure API endpoint (`DELETE /users/{id}/data`). Data processing register in `audit_log` with `resource_type=pii_processing`. |
| **SOC-2 Type II readiness** | Access controls, encryption, monitoring, incident response | RBAC + RLS for access control. AES-256-GCM encryption at rest. Structured logging with 7-year retention. Incident response playbook in `docs/incident_response.md`. |
| **ISO 27001 alignment** | Information security management system | Risk register (Section 11). Access control policy (RBAC). Encryption policy (Section 3.4.2). Business continuity (Section 2.5.7). |
| **RBI AA framework compliance** | Account Aggregator consent management, data handling | AA consent tracked per-user. FI data stored only for configured retention period. Consent revocation deletes all stored FI data within 24 hours. |
| **GSTN/Income Tax compliance** | DSC-based signing, audit trail for filings | All tax filings logged in `audit_log`. DSC certificates stored encrypted. Filing receipts archived in S3. |
| **SEBI regulations (if applicable)** | Board meeting minutes, statutory filings | MCA portal integration for statutory filings. Board resolution tracking with DocuSign signatures. |

### 14.7 Accessibility

| Requirement | Standard | Implementation |
|-------------|----------|----------------|
| All dashboards must be screen-reader compatible | WCAG 2.1 AA | All charts have `aria-label` and text alternatives. Data tables use proper `<thead>/<tbody>` markup. |
| Color is not the only indicator | WCAG 1.4.1 | Health status uses color + icon (green checkmark, yellow warning triangle, red X). Chart data points use different shapes in addition to colors. |
| Keyboard navigation | WCAG 2.1.1 | All interactive elements reachable via Tab. All actions triggerable via Enter/Space. Tab order follows visual layout. |
| Focus indicators | WCAG 2.4.7 | Visible focus ring (2px solid blue) on all focusable elements. |
| Text contrast | WCAG 1.4.3 | All text has >= 4.5:1 contrast ratio against background. Large text (18px+) has >= 3:1. |
| Form labels | WCAG 1.3.1 | All form inputs have associated `<label>` elements. Required fields marked with `aria-required="true"`. |
| Error identification | WCAG 3.3.1 | All form errors identified by both color and text message. Error messages are programmatically associated with the input. |

### 14.8 Internationalization

| Dimension | Primary | Secondary | Implementation |
|-----------|---------|-----------|----------------|
| **Language** | English (en-IN) | Hindi (hi-IN) | React-i18next with lazy-loaded locale files. All user-facing strings externalized. Agent-generated text stays in English (LLM output). |
| **Currency** | INR (Indian Rupee) | USD (US Dollar) | All financial KPIs stored in INR. USD conversion using daily exchange rate from RBI. User preference for display currency. INR formatting: "12,34,567.89" (Indian lakhs/crore grouping). |
| **Timezone** | IST (Asia/Kolkata) | UTC | All timestamps stored in UTC in database. Displayed in user's configured timezone (default IST). Cron schedules specify timezone explicitly. |
| **Number formatting** | Indian system (lakhs, crores) | International system (millions) | User preference. Default: Indian. "1,23,45,678" (Indian) vs "12,345,678" (International). Abbreviations: "1.2Cr" vs "12.3M". |
| **Date format** | DD-MMM-YYYY (08-Apr-2026) | YYYY-MM-DD (2026-04-08) | User preference. API always returns ISO 8601. UI formats based on locale. |
| **RTL support** | Not required | N/A | Hindi uses Devanagari (LTR). No RTL languages planned. |

### 14.9 Browser Support

| Browser | Minimum Version | Testing Frequency |
|---------|----------------|-------------------|
| Google Chrome | 120+ | Every release (CI: Playwright with Chromium) |
| Mozilla Firefox | 120+ | Every release (CI: Playwright with Firefox) |
| Apple Safari | 17+ | Every release (CI: Playwright with WebKit) |
| Microsoft Edge | 120+ | Weekly (manual or CI with Edge) |

**Not supported:** Internet Explorer (any version), Chrome < 120, Firefox < 120, Safari < 17.

**Polyfills included:** None. Modern ES2022+ features only. Build target: ES2020.

### 14.10 Mobile Responsiveness

| Device | Support Level | Notes |
|--------|-------------|-------|
| Desktop (1440px+) | Full | Primary development target |
| Laptop (1024px-1439px) | Full | All features available, slightly condensed layout |
| Tablet (768px-1023px) | Functional | All dashboards work. Charts may be simplified. Tables scroll horizontally. |
| Mobile (< 768px) | Read-only summary | KPI summary cards only. No charts, no tables, no workflow management. Banner: "For full experience, use tablet or desktop." |

---

## 15. Cross-Reference & Consistency Matrix

This section ensures every entity referenced in one section appears correctly in all other sections.

### 15.1 Agent Cross-Reference

Every agent mentioned in Section 2 (CxO Role Definitions) must appear in Appendix A (Agent Registry) and Section 3.2.2 (Domain Logic Specification).

| Agent Type | Section 2 Reference | Appendix A Row | Section 3.2.2 Table | Consistent? |
|-----------|---------------------|----------------|---------------------|-------------|
| `ap_processor` | 2.2.2 (CFO AP) | #1 | Finance Domain Agents | Yes |
| `ar_collections` | 2.2.3 (CFO AR) | #2 | Finance Domain Agents | Yes |
| `recon_agent` | 2.2.4 (CFO Recon) | #3 | Finance Domain Agents | Yes |
| `close_agent` | 2.2.6 (CFO Close) | #4 | Finance Domain Agents | Yes |
| `fpa_agent` | 2.1.1A, 2.2.1, 2.2.6, 2.2.7, 2.2.8, 2.2.10 (CEO, CFO) | #5 | Finance Domain Agents | Yes |
| `tax_compliance` | 2.2.5 (CFO Tax) | #6 | Finance Domain Agents | Yes |
| `talent_acquisition` | 2.1.1A, 2.3.1, 2.3.10 (CEO, CHRO) | #7 | HR Domain Agents | Yes |
| `onboarding_agent` | 2.3.2 (CHRO Onboarding) | #8 | HR Domain Agents | Yes |
| `payroll_engine` | 2.2.9, 2.3.3, 2.3.4, 2.3.8 (CFO, CHRO) | #9 | HR Domain Agents | Yes |
| `performance_coach` | 2.3.5, 2.3.7 (CHRO) | #10 | HR Domain Agents | Yes |
| `ld_coordinator` | 2.3.6 (CHRO L&D) | #11 | HR Domain Agents | Yes |
| `offboarding_agent` | 2.3.9 (CHRO Offboarding) | #12 | HR Domain Agents | Yes |
| `campaign_pilot` | 2.1.1A, 2.4.1, 2.4.4, 2.4.7, 2.4.9, 2.4.10 (CEO, CMO) | #13 | Marketing Domain Agents | Yes |
| `content_factory` | 2.4.2, 2.4.4, 2.4.5, 2.4.9, 2.6.4 (CMO, CBO) | #14 | Marketing Domain Agents | Yes |
| `seo_strategist` | 2.4.3 (CMO SEO) | #15 | Marketing Domain Agents | Yes |
| `brand_monitor` | 2.4.5, 2.4.8, 2.6.4 (CMO, CBO) | #16 | Marketing Domain Agents | Yes |
| `crm_intelligence` | 2.1.1E, 2.4.6, 2.4.7 (CEO, CMO) | #17 | Marketing Domain Agents | Yes |
| `it_operations` | 2.5.1, 2.5.7 (COO) | #18 | Ops Domain Agents | Yes |
| `support_triage` | 2.1.1A, 2.5.2 (CEO, COO) | #19 | Ops Domain Agents | Yes |
| `support_deflector` | 2.5.2 (COO) | #20 | Ops Domain Agents | Yes |
| `vendor_manager` | 2.5.3, 2.5.5 (COO) | #21 | Ops Domain Agents | Yes |
| `compliance_guard` | 2.1.1A, 2.1.1H, 2.3.8, 2.5.6, 2.6.2, 2.6.3, 2.6.5 (CEO, CHRO, COO, CBO) | #22 | Ops Domain Agents | Yes |
| `contract_intelligence` | 2.6.1 (CBO Legal) | #23 | Ops Domain Agents | Yes |
| `legal_ops` | 2.6.1, 2.6.3, 2.6.4 (CBO) | #24 | Back Office Domain Agents | Yes |
| `risk_sentinel` | 2.1.1H, 2.5.7, 2.6.2, 2.6.5 (CEO, COO, CBO) | #25 | Back Office Domain Agents | Yes |
| `facilities_agent` | 2.5.4 (COO) | #26 | Back Office Domain Agents | Yes |
| `email_agent` | Mentioned in Appendix only | #27 | Not in 3.2.2 tables | **GAP -- Added below** |
| `social_media` | Mentioned in Appendix only | #28 | Not in 3.2.2 tables | **GAP -- Added below** |
| `expense_manager` | Mentioned in Appendix only | #29 | Not in 3.2.2 tables | **GAP -- Added below** |
| `fixed_assets_agent` | Mentioned in Appendix only | #30 | Not in 3.2.2 tables | **GAP -- Added below** |
| `rev_rec_agent` | Mentioned in Appendix only | #31 | Not in 3.2.2 tables | **GAP -- Added below** |
| `treasury_agent` | Mentioned in Appendix only | #32 | Not in 3.2.2 tables | **GAP -- Added below** |
| `sales_agent` | Mentioned in Appendix only | #33 | Not in 3.2.2 tables | **GAP -- Added below** |
| `competitive_intel` | Mentioned in Appendix only | #34 | Not in 3.2.2 tables | **GAP -- Added below** |
| `notification_agent` | Mentioned in Appendix only | #35 | Not in 3.2.2 tables | **GAP -- Added below** |

**Agents 27-35 Domain Logic (fills the gap from Section 3.2.2):**

| Agent | Pre-Processing | Domain Rules | Tool Selection | Post-Processing | Confidence Floor |
|-------|---------------|--------------|----------------|-----------------|-----------------|
| `email_agent` | Parse email template, validate recipient list | Unsubscribe rate check (>1% = HITL), list size check (>50K = HITL) | Mailchimp for campaigns, SendGrid for transactional | Verify delivery rate, check bounce rate | 0.85 |
| `social_media` | Parse post content, detect platform targets | Brand voice check, competitor mention check, crisis keyword detection | Buffer for scheduling, Twitter for direct post | Verify post published, track initial engagement | 0.82 |
| `expense_manager` | Extract expense claim fields (amount, category, receipts) | Policy limit check per category, duplicate expense detection, receipt validation | Tally for posting, S3 for receipt storage | Verify GL posting, check policy compliance | 0.88 |
| `fixed_assets_agent` | Parse asset details (type, cost, useful life) | Capitalization threshold (>INR 5,000), depreciation method selection (SLM/WDV per asset type) | Tally for asset register and depreciation posting | Verify depreciation calculation, check asset tagging | 0.88 |
| `rev_rec_agent` | Identify revenue contracts and performance obligations | ASC 606/Ind AS 115 rules, milestone-based recognition, time-based allocation | Tally for journal entries, contract data from CRM | Verify recognition schedule, check unbilled revenue accuracy | 0.92 |
| `treasury_agent` | Fetch all bank account balances, FD positions | Cash runway alert (< 3 months), FD maturity alert (< 7 days), balance discrepancy check | Banking AA for balances, Tally for book balance | Verify balance reconciliation, check forecast accuracy | 0.92 |
| `sales_agent` | Parse deal data from CRM, extract pipeline stage | Win probability scoring, competitive loss analysis, deal value threshold check | HubSpot/Salesforce for deal management | Verify pipeline value calculation, check forecast accuracy | 0.85 |
| `competitive_intel` | Gather competitor data from monitoring tools | Competitive positioning analysis, feature comparison, pricing analysis | Brandwatch for mentions, Ahrefs for SEO comparison | Verify data freshness, check analysis completeness | 0.82 |
| `notification_agent` | Parse notification request (channel, recipients, template) | Channel selection rules (Slack for internal, Email for external, WhatsApp for Indian customers) | Slack, SendGrid, WhatsApp, Twilio for delivery | Verify delivery confirmation, track read/open rates | 0.85 |

### 15.2 Connector Cross-Reference

All connectors referenced in Section 2 agent descriptions must appear in Appendix B.

**Verification summary:** All 54 connectors in Appendix B are referenced by at least one agent in Section 2 or Appendix A. No orphaned connectors.

### 15.3 KPI Cross-Reference

All KPIs defined in Section 2 job functions must appear in Appendix C or in the dashboard response schemas (Section 5).

**Verification summary:** Appendix C explicitly documents CEO (10 KPIs) and CFO (15 of 28 KPIs). CHRO, CMO, COO, and CBO KPIs are documented in Section 2 subsections and Section 5 response schemas but abbreviated in Appendix C. The total KPI count of ~123 is consistent.

### 15.4 Workflow Cross-Reference

All workflows referenced in Section 2 must appear in Appendix D.

| Workflow | Section 2 Reference | Appendix D Row | Consistent? |
|----------|---------------------|----------------|-------------|
| `bank_recon_daily` | 2.2.4 | #1 | Yes |
| `invoice_to_pay_v3` | 2.2.2 | #2 | Yes |
| `gstr_filing_monthly` | 2.2.5 | #3 | Yes |
| `tds_quarterly_filing` | 2.2.5 | #4 | Yes |
| `month_end_close` | 2.2.6 | #5 | Yes |
| `daily_treasury` | 2.2.1 | #6 | Yes |
| `tax_calendar` | 2.2.5 | #7 | Yes |
| `ar_collection_cycle` | 2.2.3 | #8 | Yes |
| `employee_onboarding` | 2.3.2 | #9 | Yes |
| `monthly_payroll` | 2.3.3 | #10 | Yes |
| `recruitment_pipeline` | 2.3.1 | #11 | Yes |
| `campaign_launch` | 2.4.1 | #12 | Yes |
| `email_drip_sequence` | 2.4.4 (implied) | #13 | Yes |
| `abm_campaign` | 2.4.6 (implied) | #14 | Yes |
| `content_pipeline` | 2.4.2 (implied) | #15 | Yes |
| `weekly_marketing_report` | Not explicitly in Section 2 | #16 | **Minor gap -- advisory workflow** |
| `it_incident_escalation` | 2.5.1 | #17 | Yes |
| `support_triage` | 2.5.2 | #18 | Yes |
| `vendor_onboarding` | 2.5.3 (implied) | #19 | Yes |
| `compliance_review` | 2.6.1 | #20 | Yes |
| `contract_review` | 2.6.1 | #21 | Yes |
| `board_meeting_prep` | 2.6.3 | #22 | Yes |
| `transaction_screening` | 2.6.2 | #23 | Yes |
| `daily_ceo_briefing` | 2.1.1A | #24 (added) | Yes |
| `weekly_board_prep` | 2.1.1A | #25 (added) | Yes |
| `escalation_router` | 2.1.1A | #26 (added) | Yes |
| `monthly_board_pack` | 2.1.1D | #27 (added) | Yes |

All 27 workflows are now registered in Appendix D.

### 15.5 HITL Threshold Consistency

HITL thresholds must be consistent everywhere they are referenced.

| Threshold | Section 2 Value | Section 7 Test Value | Appendix A Value | Consistent? |
|-----------|----------------|---------------------|------------------|-------------|
| Invoice amount > INR 5,00,000 | 2.2.2 (CFO AP) | test_ap_hitl_amount_threshold: 600000 | #1: "amount > 500K" | Yes |
| 3-way match delta > 2% | 2.2.2 (CFO AP) | test_ap_three_way_match_over_tolerance: 3% | #1: "match delta > 2%" | Yes |
| Bank break > INR 50,000 | 2.2.4 (CFO Recon) | test_recon_break_escalation: 60,000 | #3: "break > 50K" | Yes |
| Candidate salary > INR 30,00,000 | 2.3.1 (CHRO Recruitment) | test_salary_hitl_threshold: >30L | #7: "salary > 30L" | Yes |
| F&F settlement > INR 5,00,000 | 2.3.9 (CHRO Offboarding) | test_ff_hitl_threshold: >5L | #12: "F&F > 5L" | Yes |
| Confidence floor per agent | Section 3.2.2 tables | Referenced in individual test | Appendix A per-row | Yes |
| CEO escalation invoice > INR 5,00,000 | 2.1.1 (CEO HITL) | test_ceo_escalation_threshold_invoice: 6L | Consistent | Yes |
| ITC mismatch > INR 10,000 | 2.2.5 (CFO Tax) | test_gst_itc_mismatch_hitl: 15,000 | #6: "ITC mismatch > 10K" | Yes |

All thresholds are consistent across sections.

---

*End of PRD v5.0.1 -- Updated 2026-04-08 with Sections 13-15 (Edge Cases, Non-Functional Requirements, Cross-Reference Matrix)*

*This document is the single source of truth for the CxO Dashboard Platform build. Every feature, agent, connector, KPI, workflow, test, API endpoint, and UI component described herein must be implemented exactly as specified. Ambiguity kills products -- if something is unclear, ask before building.*
