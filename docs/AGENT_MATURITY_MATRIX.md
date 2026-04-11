# Agent Maturity Matrix

This document labels every agent in AgenticOrg with a maturity level so
customers know what is production-ready vs. preview.

| Maturity | Meaning                                                      |
|----------|--------------------------------------------------------------|
| **GA**   | Generally available. Covered by SLA, backward-compat promise, Tier-1 support. |
| **BETA** | Feature-complete but still stabilizing. Breaking changes allowed with 30-day notice. Best-effort support. |
| **ALPHA**| Early preview. API may change without notice. Community support only. |
| **DEPRECATED** | Still runs but scheduled for removal. See the `replaced_by` column. |

Agents read this label from the database column `agents.maturity`
(added in migration `v460_enterprise`).  The UI shows the badge next to
the agent name.

## Finance domain (v3.2.0)

| Agent                    | Maturity | Since  | SLA     | Notes |
|--------------------------|----------|--------|---------|-------|
| ap_processor             | GA       | v3.0.0 | 99.9%   | AP invoice capture + posting |
| ar_collections           | GA       | v3.0.0 | 99.9%   | Collection email sequences |
| tax_compliance           | GA       | v3.1.0 | 99.9%   | GSTR-1/3B/TDS  |
| payroll_engine           | GA       | v3.2.0 | 99.9%   | Monthly payroll run |
| cash_flow_forecaster     | BETA     | v4.0.0 | —       | 12-week cash projection |
| expense_manager          | BETA     | v4.4.0 | —       | Policy-checked reimbursements |
| reconciliation_agent     | GA       | v3.2.0 | 99.9%   | Bank + Tally reconciliation |

## HR domain

| Agent                | Maturity | Since  | SLA   | Notes |
|----------------------|----------|--------|-------|-------|
| onboarding           | GA       | v3.0.0 | 99.9% | Multi-step employee onboarding |
| offboarding          | GA       | v3.0.0 | 99.9% | Asset return + access revoke |
| leave_policy_agent   | BETA     | v4.0.0 | —     | Leave balance checker |
| performance_review   | ALPHA    | v4.5.0 | —     | Preview — prompt-heavy |

## Sales / Marketing

| Agent               | Maturity | Since  | SLA   | Notes |
|---------------------|----------|--------|-------|-------|
| lead_qualifier      | GA       | v3.0.0 | 99.9% | MQL scoring + routing |
| abm_outreach        | BETA     | v4.2.0 | —     | Account-based sequences |
| drip_campaign       | GA       | v4.0.0 | 99.9% | Multi-touch email drip |
| ab_test_runner      | BETA     | v4.2.0 | —     | Variant traffic-split |
| content_generator   | BETA     | v4.0.0 | —     | Landing-page copy |

## Operations / IT

| Agent               | Maturity | Since  | SLA   | Notes |
|---------------------|----------|--------|-------|-------|
| it_operations       | GA       | v3.0.0 | 99.9% | L1 incident triage |
| compliance_monitor  | GA       | v3.1.0 | 99.9% | SOC2 evidence collection |
| incident_responder  | BETA     | v4.3.0 | —     | On-call response |

## CxO dashboards (agents that back KPI pages)

| Agent   | Maturity | Notes                         |
|---------|----------|-------------------------------|
| ceo     | GA       | Aggregated org KPIs           |
| cfo     | GA       | Finance KPIs                  |
| cmo     | GA       | Marketing KPIs                |
| coo     | GA       | Ops KPIs                      |
| chro    | GA       | HR KPIs                       |
| cbo     | BETA     | Business development KPIs     |

## Industry packs

| Pack                  | Maturity | Notes                             |
|-----------------------|----------|-----------------------------------|
| ca_firms              | GA       | CA practice management (v4.2.0)  |
| healthcare            | BETA     | Claims + prior-auth (v4.5.0)     |
| insurance             | BETA     | Underwriting (v4.5.0)            |
| legal                 | BETA     | Contract review (v4.5.0)         |
| manufacturing         | BETA     | QC + MRO (v4.5.0)                |

## How to change an agent's maturity

1. Update the `maturity` column in the database (or via `PATCH /api/v1/agents/{id}`).
2. Update this matrix in the same PR.
3. If promoting to GA, ensure there is at least one regression test and
   a monitoring dashboard entry.
4. If deprecating, announce in the monthly release notes 90 days before
   removal and set the `replaced_by` pointer.

Last reviewed: 2026-04-11
