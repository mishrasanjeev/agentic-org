# CMO Guide — AgenticOrg Marketing Platform

## Overview

AgenticOrg gives CMOs a unified command center for marketing operations, but the CMO agent surface is not yet production-grade across all CMO pillars. Current production-strength work is concentrated in Campaign Pilot, with substantial beta capability in Content Factory, Email Marketing, Social Media, ABM, Competitive Intel, Brand Monitor, SEO Strategist, and CRM Intelligence. Every CMO marketing-agent pillar in `core/agents/marketing` now has first-class deterministic code, but production claim still requires real-vendor pilot proof, persistent evidence storage, and vendor adapter rollout. Web push notifications and HITL approvals apply where the underlying workflow is implemented.

This guide distinguishes production, beta, stub, unavailable, and demo surfaces so buyers and operators do not confuse roadmap or demo coverage with production-grade CMO autonomy.

### Real-Company Readiness Rule

A CMO capability is production-ready only when it works against a company's configured marketing systems, exposes data lineage and freshness, follows approval policy, and writes audit records for recommendations and actions. Mocks, sample data, and stubs are allowed only in tests, local development, or explicitly labeled demo tenants. They must not be used to claim production readiness for a real marketing department.

---

## Marketing Dashboard (`/dashboard/cmo`)

The CMO Dashboard is the marketing command center for currently available KPI and agent-task data. It does not prove that all 9 CMO pillars are production-ready. If the API returns `"demo": true`, the dashboard shows a **Demo Data** badge and the KPI values should be treated as sample data. Access it at `/dashboard/cmo` or by selecting "CMO Dashboard" from the sidebar.

### CMO Work Queue

The CMO KPI API now includes `cmo_work_queue` and `cmo_work_queue_summary`. This is a deterministic operator queue, not persistent task storage. It turns hidden readiness metadata into visible work items for approvals, overdue approval timeout risk, escalations, connector auth/scope/health/degraded issues, field-mapping and backfill blockers, workflow promotion blockers, external write rejection/timeout/unconfirmed states, missing policy or decision-audit evidence, blocked/degraded KPI results, failed/warning reconciliation checks, blocked/warning/internal-only/draft-only report quality gates, production demo/sample blockers, and crisis/public-response risks where available.

Each work item includes severity, priority score, owner role, affected workflow/capability/KPI/report/connector, due/SLA timestamp, source refs, audit refs, status, and a next-action CTA. Prioritization puts critical customer-facing external-write risk first, then overdue approvals and escalations, then write-blocking connector/policy/audit issues, report delivery blockers, KPI/reconciliation blockers, and lower-severity read-only or stale optional-data warnings.

The dashboard renders the queue before connector, data, and workflow readiness sections. An empty queue only means there are no projected open work items; it is not a production-readiness claim for stub, unavailable, or demo capabilities.

### CMO Approval Review

The CMO KPI API now includes `cmo_approval_reviews` and `cmo_approval_review_summary`. This is a deterministic approval-review projection, not a full persistent approvals system. It gives reviewers enough context to approve, reject, override, request changes, escalate, or pause approval-sensitive marketing actions without bypassing HITL, policy, timeout, escalation, external-write, or audit safeguards.

Approval reviews cover campaign launches, ad budget changes, content publishing, email sends, landing-page changes, target-account list changes, crisis/public responses, high-risk copy/pricing/legal claims, and workflow promotion. Each row includes approval/workflow/run/step refs, requester and agent refs, assigned approver/role, created and due timestamps, timeout state, preview payload, before/after diff, budget impact, audience/list impact, brand/legal/policy risk flags, source and connector refs, agent rationale, policy result/ref, escalation result/ref, timeout result/ref, external-write readiness/result ref, audit refs, rollback/stop plan, allowed reviewer actions, blocked reasons, related work queue item IDs, and next-action CTA.

Approval review fails closed. A reviewer cannot approve a customer-facing or external-write action when policy coverage is missing or blocking, connector/write readiness is unsafe, timeout policy requires manual resolution, or required decision-audit evidence is absent. Shadow and draft/internal-only actions remain clearly labeled and non-executable. The dashboard renders compact approval review cards after the work queue, showing the preview, impact, safeguards, rollback/stop plan, allowed actions, and CTA before KPI lineage or readiness sections.

### CMO Pilot Proof

The CMO KPI API now includes `cmo_pilot_proof` and `cmo_pilot_proof_summary`. This is a deterministic evidence package for a pilot tenant, not proof that the CMO organization is fully production-ready. It evaluates connector setup, connector contracts, field mapping/backfill, workflow activation and linting, policy, escalation, approval timeout readiness, external-write confirmation, decision-audit evidence, KPI schema and reconciliation, report quality gates, work queue blockers, KPI drill-down lineage, approval reviews, agent contracts, and scenario/chaos test evidence where available.

Pilot proof distinguishes `real_vendor`, `vendor_sandbox`, `demo`, `test_double`, and `unknown` environments. Demo tenants return `demo_only`; test-double/mock environments return `test_only`; neither can pass production proof. Vendor-sandbox proof can pass only sandbox criteria and must not be described as real-vendor production proof. Real-vendor proof can pass only when all critical readiness criteria pass. Social Media, ABM, Competitive Intel, Brand Monitor, SEO Strategist, and CRM Intelligence are first-class beta but remain unproven for production until real-vendor/pilot proof exists. The evidence bundle serializer redacts obvious secrets and tokens before it can be attached to docs or a future UI.

### Weekly Marketing Report Pilot Proof (CMO-PROD-1)

`/kpis/cmo` also exposes `weekly_report_pilot_proof`, `weekly_report_pilot_proof_summary`, and `weekly_report_pilot_evidence_bundle`. This is a stricter, read-only proof gate specifically for the weekly marketing report. It is satisfied only when CRM + Ads + Analytics + Email connectors are configured and read-ready, every required field mapping (lifecycle_stages, opportunity_revenue, campaign_ids, utm_fields, consent_unsubscribe, fiscal_calendar, currency, timezone) is valid, every required backfill category is completed, every required weekly-report KPI (CAC, MQL, SQL, MQL→SQL conversion rate, ROAS, pipeline contribution, conversion rates by funnel stage, email performance) is present and not blocked, every reconciliation check passes, the weekly-report quality gate is `pass`, and a report artifact + decision-audit + real-source-lineage reference set is attached. Demo, test-double, and vendor-sandbox inputs never produce a real-vendor production claim. A `scripts/validate_weekly_report_pilot_proof.py` CLI accepts a JSON evidence file (or STDIN) and emits a redacted verdict for pilot-tenant operators. **As of 2026-05-24 no live or vendor-sandbox evidence is present in the worktree**, so the proof returns `unavailable` / `blocked` until a pilot tenant supplies a real evidence bundle.

### Persisted Weekly Report Pilot Proof (CMO-PROD-2)

Every successful CMO weekly report run now persists a verdict to `weekly_report_pilot_proofs` (Alembic migration `v4917_weekly_report_proof`). The report generator hydrates a `WeeklyReportPilotEvidence` bundle from the same `/kpis/cmo` projections it already uses for the report (connector setup, mapping + backfill, unified KPIs, reconciliation, report-quality gate), attaches `report_artifact_refs` and `decision_audit_refs`, runs the CMO-PROD-1 validator, redacts secrets, and inserts a single append-only row. `/kpis/cmo` exposes `latest_weekly_report_pilot_proof` (full redacted row) and `latest_weekly_report_pilot_proof_summary` (proof status, environment, production-claim flag, readiness score, blocker count, next-action CTA). The CMO-PROD-1 validator remains the only authority for `production_claim_allowed=True`; the persistence path adds durability, not laxity. Demo / non-UUID tenants short-circuit before INSERT so the table never contains fake evidence. **No `weekly_report_pilot_proofs` row exists yet**; the first row will only appear when a credentialed pilot tenant runs the weekly report.

### Sandbox Walk-Through Runner (CMO-PROD-3)

A fail-closed orchestrator lives at `scripts/run_weekly_report_sandbox_pilot.py` (callable via `core.marketing.weekly_report_sandbox_pilot.run_sandbox_pilot`). It discovers sandbox configuration only from `AGENTICORG_CMO_SANDBOX_*` env vars and `AGENTICORG_DB_URL`, refuses to insert fake proof, and forces `params.pilot_environment_type="vendor_sandbox"` upstream so a successful pilot can never produce a real-vendor production claim. Run `python scripts/run_weekly_report_sandbox_pilot.py --preflight-only` to see exactly which env vars must be populated before the first `sandbox_proven` verdict can materialise. The full env-var list and runbook are in `docs/STRICT_CMO_AGENTIC_EXECUTION_BACKLOG_2026-05-23.md` under "CMO-PROD-3". **As of 2026-05-24, no sandbox credentials are configured in this worktree, so the runner returns `unavailable` / `blocked` (exit code 3) and no row is inserted.**

### CMO Agent Contract Coverage

CMO agent contract tests now cover the current marketing surfaces: Campaign Pilot, Content Factory, Email Marketing / `email_agent`, Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM / `abm_agent`, and Competitive Intel. Implemented and beta surfaces must return a stable contract shape with status, confidence, rationale, recommended actions, source refs, policy/approval/HITL state, audit refs, degraded or blocked reasons, and external-write confirmation status where applicable.

The contract tests are a safety net, not a production-readiness shortcut. Campaign Pilot has the strongest production implementation today. Content Factory, Email Marketing, Social Media, ABM, Competitive Intel, Brand Monitor, SEO Strategist, and CRM Intelligence remain beta. Production workflow linting and activation continue to block beta or unavailable agents from active customer-facing workflows when connector/write, policy, approval, escalation, audit, or pilot-proof prerequisites are missing.

### Marketing Connector Setup Checklist

The CMO KPI API now includes a `connector_setup` checklist for real-company onboarding. Each row exposes the connector key, name, category, required scopes or credentials, configured/unconfigured status, health status, last sync timestamp, owner, account/workspace ID when stored, data coverage, missing scopes, and the setup/reconnect action state.

The dashboard renders this checklist before KPI cards so operators can see which marketing systems are connected, missing, stale, expired, insufficiently scoped, healthy, or degraded. Missing systems create setup actions; expired OAuth creates reconnect actions; missing permissions create add-scope actions; stale syncs create refresh actions. A production tenant with no real CMO data must not silently fall back to demo KPI values. Instead the API suppresses the demo flag, returns `production_data_blocked: true`, and points operators back to connector setup.

### Marketing Connector Contracts

The CMO KPI API also returns `connector_contracts` and `connector_contract_summary`. These rows harden connector setup into a contract that agents and workflow gates can rely on. Each connector contract separates read capabilities from write capabilities, lists required and granted scopes, reports missing read or write scopes, exposes auth and health state, tracks account/workspace/source object refs where stored, shows data freshness and TTL, and includes retry-budget and idempotency-key metadata.

Contract rows can report `healthy`, `missing_scope`, `insufficient_scope`, `auth_expired`, `rate_limited`, `timeout`, `vendor_5xx`, `partial_data`, `stale_data`, `malformed_payload`, `quota_exhausted`, `connector_disabled`, `degraded`, `write_unconfirmed`, or `write_confirmed`. Read access does not imply write readiness. A connector can be read-ready while still blocking active workflows that need to publish, send, spend, update CRM, or mutate ad budgets.

The retry/degraded-mode projection is now policy-backed. Connector failure classes expose retryability, max attempts, backoff strategy metadata, safe-retry idempotency requirements, degraded-mode allowance, confidence impact, external-write blocking, production KPI confidence blocking, the required operator CTA, and audit event code. Reporting and recommendation workflows may continue only in explicitly labeled degraded mode with affected connectors, workflows, KPIs, capability, confidence impact, and next action visible. Active external-write workflows fail closed when connector state is not write-safe.

External write steps cannot be treated as complete unless the connector contract has matching external write confirmation, or the workflow is explicitly in shadow/draft/internal-only mode. Mock, sample, stub, or test-double connector proof is blocked from production readiness. The dashboard shows connector contract read/write readiness, auth/freshness, retry/idempotency state, write confirmation, degraded reasons, and next action before field mapping and backfill readiness.

Workflow step execution now carries the same confirmation discipline. Marketing external-write steps can finish as `write_confirmed`, `idempotent_recovered`, `retry_scheduled`, `rejected`, `timeout_unknown`, `write_unconfirmed`, `draft_created`, or `shadow_only`. Active publish, send, launch, spend, or CRM-update steps fail closed unless a confirmed write includes an explicit external object ID, connector key, idempotency key where applicable, request fingerprint, confirmation timestamp, actor/agent/workflow/run IDs where available, and audit reference. Shadow workflows never execute external writes; they can only complete recommendations, simulations, drafts, or internal approval records. Timeout retries are scheduled only when idempotency metadata exists, and duplicate retries recover prior confirmed writes only when the same idempotency key is present.

Approval-sensitive CMO actions now have explicit timeout policy. Ad campaign launches, ad budget changes, email sends, content publishing, landing-page changes, target-account list changes, crisis/public responses, social post targeting, and high-risk copy/pricing/claims reviews each carry default SLA, escalation role, timeout outcome, audit event code, and safe fallback CTA. Supported timeout outcomes are `auto_cancel`, `auto_escalate`, `continue_read_only`, `pause_workflow`, and `require_manual_resolution`. Customer-facing writes fail closed after timeout unless a policy explicitly pre-approves that action after timeout. The CMO KPI API exposes `approval_timeout_risk` for pending and overdue marketing approvals.

A machine-checkable CMO marketing policy manifest now defines conservative default rules for budget thresholds, publishing/sending/launching, high-risk copy, pricing/legal/compliance claims, competitor mentions, region constraints, audience and target-account thresholds, crisis/public response, allowed autonomous read-only actions, disallowed destructive actions, required owner roles, and required audit evidence classes. Policy evaluation returns `allowed`, `blocked`, `requires_approval`, `requires_escalation`, `read_only_only`, or `missing_policy` with matched rules, reason, required approver/escalation role, required audit evidence, affected workflow/action, and next action CTA. Active customer-facing writes fail closed without policy coverage and satisfied approval/escalation evidence; shadow/read-only recommendations can continue only when clearly non-executable. The CMO KPI API exposes `marketing_policy_manifest` and `marketing_policy_summary`.

A code-backed CMO escalation matrix now defines routes for approval timeouts, crisis/public response, budget threshold exceptions, connector auth/degraded failures, data mapping blockers, backfill failures, missing policy, rejected or unknown external writes, high-risk copy, pricing/legal claims, and target-account changes. Escalation decisions return `notify_owner`, `escalate`, `escalate_to_legal`, `escalate_to_finance`, `escalate_to_admin`, `pause_workflow`, or `require_manual_resolution` with owner role, backup role, chain, SLA, due time, fallback outcome, notification channels, audit event code, and audit reference. The CMO KPI API exposes `marketing_escalation_matrix` and `marketing_escalation_summary`.

CMO decision audit packages are now code-backed for major marketing decisions. Policy decisions, escalation decisions, approval timeouts, connector degraded/failure decisions, workflow promotion evaluations, and external write attempts/final states attach deterministic audit packages with actor identity, timestamps, workflow/run/step IDs, input snapshot hashes, source and connector refs, policy/escalation/approval/timeout/write refs, rationale, alternatives, risk flags, confidence, final outcome, override reason/replacement action, and WORM-ready canonical JSON serialization. The package redacts obvious secrets/tokens before serialization. Production customer-facing workflow linting fails when a step lacks CMO-6.3 decision-audit evidence metadata. The CMO KPI API exposes `marketing_decision_audit` and `marketing_decision_audit_summary`.

### Marketing Field Mapping And Backfill Readiness

The CMO KPI API also returns `field_mapping_status`, `backfill_status`, and `kpi_readiness`. These are code-backed projections from stored connector configuration metadata, not demo proof. Field mappings cover lifecycle stages, opportunity revenue fields, campaign IDs, UTM fields, account domains, consent/unsubscribe fields, fiscal calendar, currency, and timezone.

Mapping rows can be `unmapped`, `partially_mapped`, `valid`, `invalid`, `stale`, or `blocked`. Backfill rows can be `not_started`, `queued`, `running`, `completed`, `partial`, `failed`, or `blocked`, and include the source connector key, requested date range, available record counts, last run timestamp, blocking reason, and next action. The dashboard renders these states before KPI cards so Marketing Ops can see why CAC, ROAS, pipeline, email, or nurture readiness is blocked or degraded.

Production tenants must not treat KPI values as confident when required mappings or historical backfills are missing. In strict production mode, `/kpis/cmo` marks `kpi_confidence_status` as `blocked` or `degraded` and returns an operator-facing message instead of silently presenting sample, hardcoded, or incomplete data as production-ready.

### Unified CMO KPI Schema

The CMO KPI API now also returns `unified_cmo_kpi_schema`, `unified_cmo_kpi_results`, and `unified_cmo_kpi_summary`. The schema defines canonical formulas and readiness requirements for CAC, MQL, SQL, MQL-to-SQL conversion, ROAS, pipeline contribution, conversion rates by funnel stage, LTV/CAC, experiment velocity, content performance, email performance, brand sentiment, and ABM intent/account readiness.

Each KPI result is computed only from structured tenant source facts that satisfy connector setup, connector contract, field-mapping, backfill, freshness, degraded-mode, and audit-lineage requirements. Results are labeled `ready`, `degraded`, `blocked`, or `unavailable` and include formula refs, source refs, missing requirements, confidence, freshness status, last computed timestamp, and next action. Production tenants must not use sample/demo/hardcoded values as KPI proof; missing connectors, stale/partial data, missing mappings, missing backfills, or absent source fields block or downgrade only the affected KPIs.

### CMO KPI Drill-Down And Data Lineage

The CMO KPI API now also returns `cmo_kpi_drilldowns` and `cmo_kpi_drilldown_summary`. Drill-down rows explain each canonical KPI with KPI name/description, current status/value/unit/confidence, formula, resolved formula inputs where available, required source domains/connectors/mappings/backfills, actual connector and source refs, field mappings used, backfill state, reconciliation checks affecting the KPI, freshness/TTL state, confidence-impact reasons, missing requirements, related work queue item IDs, related report gate IDs, policy/audit refs, owner role, and next action CTA.

The dashboard renders these drill-downs as a compact lineage table after the work queue and before readiness setup sections. This is not persistent KPI history storage and not a full drill-down workbench yet; it is the code-backed explanation layer that prevents KPI cards and reports from hiding source, freshness, confidence, reconciliation, mapping, or backfill gaps. Demo, mock, stub, hardcoded, sample, fallback, or test-double inputs are explicitly marked as not production lineage proof and do not count as trusted production source refs.

### CMO KPI Reconciliation

The CMO KPI API now also returns `cmo_kpi_reconciliation_checks` and `cmo_kpi_reconciliation_summary`. Reconciliation checks compare paid spend totals against campaign-level spend, ad platform conversions against CRM campaign-attributed outcomes, GA4 conversions against CRM leads, email engagement against CRM/list data, CMS content traffic against GA4 content performance, ABM target account domains against CRM and intent domains, and currency/timezone consistency across sources.

Each reconciliation result is labeled `passed`, `warning`, `failed`, `blocked`, or `unavailable` and includes severity, affected KPI keys, compared sources, expected and observed values, absolute and percentage deltas, tolerance, confidence/freshness impact, source refs, missing requirements, next action, and decision-audit ref where relevant. Failed high-severity checks block affected KPIs such as CAC, ROAS, LTV/CAC, pipeline contribution, and ABM readiness. Warnings and medium-severity failures downgrade confidence instead of pretending the KPI is fully trusted. Production KPI and report readiness must not ignore failed reconciliation; report quality gates consume this reconciliation state before a report can be deliverable.

### CMO Report Quality Gates

The CMO KPI API now also returns `report_quality_gates` and `report_quality_summary`. These gates cover `weekly_marketing_report`, `daily_ad_performance`, `monthly_marketing_roi`, `campaign_performance_ad_hoc`, and `executive_board_summary`, with compatibility aliases for existing `cmo_weekly` and `campaign_report` generator paths.

Each gate consumes unified KPI status/confidence/freshness, KPI reconciliation results, connector setup/contracts, mapping/backfill readiness, workflow activation mode, policy/escalation/audit readiness, approval refs for sensitive or external delivery, and production data policy. Gate status can be `pass`, `warning`, `blocked`, or `unavailable`; safe report mode can be `deliverable`, `internal_only`, or `draft_only`. A report is externally deliverable only when the gate passes. Blocked or warning reports may still exist as clearly labeled draft/internal outputs, but they must not be sent as trusted production reports.

### CMO Workflow Promotion Gates

The CMO KPI API also returns `workflow_activation_status` and `workflow_activation_summary`. This projection covers `weekly_marketing_report`, `campaign_launch`, `daily_spend_optimization`, `content_pipeline`, `lead_nurture`, `social_publishing`, `abm_sprint`, `brand_crisis_response`, and `seo_sprint`.

Workflow rows can be `unavailable`, `shadow`, `promotion_blocked`, `promotion_ready`, `active`, `degraded`, or `paused`. Promotion is per workflow. A workflow cannot become active just because the CMO dashboard or another workflow is active. Each row shows required connector categories, required mappings, required backfills, approval owner, policy owner, marketing policy readiness, approval timeout policy readiness, escalation matrix readiness, shadow-run quality status, blocked/degraded reasons, and the next action.

Shadow mode is read-only. Agents may recommend, draft, simulate, and create internal approval records, but they must not publish, send, spend, update CRM, mutate ad budgets, or write to external systems unless that specific workflow is active. Social Publishing, ABM Sprint, Brand Crisis Response, and SEO Sprint are beta-gated workflows and can become promotion-ready only after their required connectors, mapping/backfill, policy, approval, escalation, audit, and write-confirmation gates pass. Connector setup alone does not make those workflow pillars production-grade.

Marketing workflow YAML can also be linted through `core.marketing.workflow_linter` before promotion or execution. The linter validates known marketing `agent_type` values, declared action metadata, production/beta/stub/unavailable capability state, declared connector key/category readiness, CMO-5.3 write-confirmation metadata, idempotency metadata, CMO-5.4 approval timeout policy, CMO-6.1 marketing policy coverage, CMO-6.2 escalation routes, CMO-6.3 decision-audit evidence metadata, and shadow-mode read-only behavior. Production lint fails undefined agents/actions, stub or unavailable production steps, missing write-ready connector contracts, missing approval timeout policy, missing/blocking marketing policy, missing escalation route, missing decision-audit evidence, and unsafe external-write steps. Target, demo, and shadow workflows can carry roadmap placeholders only as warnings.

### KPI Cards

| KPI | What It Shows | Data Source |
|-----|---------------|-------------|
| **CAC** | Customer Acquisition Cost by channel (Google, Meta, LinkedIn, organic) | Campaign Pilot + CRM Intelligence (ad spend / new customers) |
| **MQLs** | Marketing Qualified Leads — count, trend, and conversion rate to SQL | CRM Intelligence (HubSpot/Salesforce pipeline data) |
| **SQLs** | Sales Qualified Leads — count, trend, and conversion rate to opportunity | CRM Intelligence (pipeline stage analysis) |
| **Pipeline Value** | Total opportunity value by stage (Discovery, Proposal, Negotiation, Closed) | CRM Intelligence (deal pipeline aggregation) |
| **ROAS by Channel** | Return on Ad Spend for Google Ads, Meta Ads, LinkedIn Ads | Campaign Pilot (ad spend vs. attributed revenue per channel) |
| **Email Performance** | Open rate, click-through rate, unsubscribe rate, deliverability score | Email Marketing Agent (Mailchimp/SendGrid metrics) |
| **Brand Sentiment** | Positive / Negative / Neutral trend over time | Brand Monitor (Brandwatch + social listening data) |
| **Content Performance** | Top pages by traffic, engagement time, conversions | SEO Strategist (GA4 + Ahrefs data) |

### How Data Flows

Each KPI card refreshes on a configurable schedule — typically every 30 minutes for ad performance and hourly for content and brand metrics. Ad spend data syncs from Google Ads, Meta Ads, and LinkedIn Ads. CRM data comes from HubSpot or Salesforce. Content metrics flow from GA4 and Ahrefs. Email metrics come from Mailchimp or SendGrid. Social data comes from Buffer, Twitter/X, and YouTube.

The dashboard respects RBAC: only users with CMO or CEO roles can access `/dashboard/cmo`.

### Current Capability Status

| Capability | Status | Current truth |
|------------|--------|---------------|
| Campaign Pilot | Production | Strongest CMO agent today: domain-specific campaign execution, budget checks, performance polling, and HITL gates. |
| Content Factory | Beta | Substantial content workflow exists, including draft generation and brand checks; publish feedback loops and QA policy are still maturing. |
| Email Marketing | Beta | Available through a separate LangGraph path and approval-gated tooling; not a complete autonomous CMO pillar yet. |
| Brand Monitor | Beta | First-class deterministic brand monitoring for mention aggregation, sentiment trends, spike detection, false-positive suppression, crisis severity, playbook recommendation, escalation, and safety-gated public response; not production-grade without real-vendor/pilot proof. |
| SEO Strategist | Beta | First-class deterministic SEO logic for keyword gaps, ranking deltas, technical issue prioritization, recommendation bundling, content optimization, sprint planning, stale/partial data handling, and guarded technical site-write actions; not production-grade without real SEO/Analytics/CMS connectors, policy/approval/audit evidence, confirmed writes, and pilot proof. |
| CRM Intelligence | Beta | First-class deterministic CRM/pipeline intelligence for pipeline velocity, funnel conversion, lead scoring refresh, churn risk signal extraction, segment recommendation, SQL promotion criteria, account/deal health, stale/partial/missing-mapping data handling, and guarded CRM-write actions (lead score, lifecycle stage, segment/list, target accounts, bulk CRM update); not production-grade without real HubSpot/Salesforce/intent connector readiness, policy/approval/audit evidence, confirmed writes, and pilot proof. |
| Social Media | Beta | First-class core marketing agent for calendar, schedule optimization, engagement triage, reply risk classification, drafts, and guarded publish/reply recommendations; not production without real Social connector write readiness, policy/approval/escalation/audit evidence, confirmed writes, and pilot proof. |
| ABM | Beta | First-class core marketing agent for account scoring, ICP fit, intent heat scoring, configurable intent-source weighting, next-best action, CSV ingest validation, high-intent alerts, and guarded target-list/campaign/budget actions; not production without real ABM/CRM/Ads connector readiness, policy/approval/escalation/audit evidence, confirmed writes, and pilot proof. |
| Competitive Intel | Beta | First-class core marketing agent for weekly competitor snapshots, profile normalization, pricing-change detection, feature/capability diffing, win/loss signal extraction, duplicate suppression, alert thresholds, and guarded positioning/public-response recommendations; not production without real competitive-source connectors, policy/approval/escalation/audit evidence, confirmed writes where applicable, and pilot proof. |
| CMO KPI feed | Demo when badge is shown | Demo KPI data is sample data, not proof of end-to-end autonomous CMO operation. |

---

## CMO Capability Inventory

### 1. Content Factory
**Status**: Beta. Content Factory has substantial core marketing logic, but publishing policy, formal QA scoring, and performance feedback loops are still maturing.

**What it does**: End-to-end content creation — from ideation to drafting to SEO optimization to publishing.

**Key capabilities**: Topic ideation based on keyword gaps (via Ahrefs/Semrush). Content brief generation with target keywords, word count, and structure. Draft creation with brand voice consistency. SEO optimization (title tags, meta descriptions, heading structure, internal linking). WordPress publishing via API.

**HITL triggers**: All content publishing requires CMO approval. Draft review before publishing. Brand voice deviations flagged automatically.

**Connected systems**: WordPress, Ahrefs, Semrush, GA4.

### 2. Campaign Pilot
**Status**: Production. Campaign Pilot is the strongest current CMO agent and has domain-specific execution logic for campaign setup, budget checks, channel performance polling, optimization decisions, and HITL triggers.

**What it does**: Multi-channel advertising campaign management — from setup to optimization to reporting.

**Key capabilities**: Campaign creation across Google Ads, Meta Ads, and LinkedIn Ads. Budget allocation optimization based on ROAS. A/B test management (ad copy, targeting, bidding). Bid adjustment recommendations. Campaign performance reporting with cross-channel attribution.

**HITL triggers**: Budget changes above threshold. New campaign launches. Bid strategy changes. Audience targeting modifications.

**Connected systems**: Google Ads, Meta Ads, LinkedIn Ads, GA4.

### 3. SEO Strategist
**Status**: Beta first-class core marketing agent. `core/agents/marketing/seo_strategist.py` now implements deterministic SEO domain logic, but SEO Strategist is not production-ready until real SEO/Analytics/CMS connectors, write scopes, policy approval, audit evidence, confirmed external writes where applicable, and pilot proof are present.

**Implemented beta behavior**: Technical and content SEO recommendations - keyword gap analysis, ranking delta computation, technical SEO issue prioritization, effort/impact recommendation bundling, content optimization recommendations, SEO sprint planning, stale/partial data handling, and guarded technical site-write paths.

**Key capabilities**: Keyword gap analysis and content opportunity scoring. Ranking improvements, drops, new terms, and lost terms. Technical SEO issue prioritization by severity, impact, effort, and affected pages. Recommendation bundling by effort and impact. Content optimization recommendations for titles, metadata, headings, content depth, and internal links. SEO sprint planning from structured SEO, analytics, and CMS inputs. Guarded technical site changes for metadata, canonical tags, redirects, robots.txt, sitemaps, landing-page updates, and indexing submissions.

**HITL triggers**: Technical site changes (robots.txt, sitemaps, redirects, canonical tags, page metadata, landing-page updates, indexing submissions) require policy coverage, approval, audit evidence, connector write safety, and confirmed external write evidence. Advisory recommendations can remain read-only in beta/shadow mode.

**Connected systems**: Ahrefs, Semrush, GA4, Google Search Console, and CMS/WordPress where configured. Active technical site changes additionally require a write-safe CMS/SEO connector contract and confirmed external write evidence.

### 4. CRM Intelligence
**Status**: Beta first-class core marketing agent. `core/agents/marketing/crm_intelligence.py` now implements deterministic CRM/pipeline intelligence (pipeline velocity, funnel conversion, lead scoring refresh, churn risk extraction, segment recommendation, SQL promotion, account/deal health, stale/partial/missing-mapping handling) with policy/approval/audit/connector-write/external-write-confirmation gates. Production claim is blocked until real HubSpot/Salesforce/intent connector readiness, policy/approval/audit evidence, confirmed external CRM writes, and pilot proof are present.

**Target behavior**: CRM data analysis — pipeline health, lead scoring, customer segmentation, and lifecycle analysis.

**Key capabilities**: Pipeline health scoring (velocity, conversion rates, deal size trends). Lead scoring model management. Customer segmentation (by industry, size, behavior, engagement). Churn risk identification. Customer lifetime value estimation. Win/loss analysis.

**HITL triggers**: Lead scoring model changes. Segment-based campaign triggers. High-value lead alerts.

**Connected systems**: HubSpot, Salesforce.

### 5. Brand Monitor
**Status**: Beta first-class core marketing agent. `core/agents/marketing/brand_monitor.py` now implements deterministic brand monitoring domain logic, but Brand Monitor is not production-ready until real Brand/Social connectors, policy approval, escalation, audit evidence, confirmed external writes where applicable, and pilot proof are present.

**Implemented beta behavior**: Brand reputation monitoring recommendations -- mention aggregation, sentiment classification and trend calculation, negative spike detection, false-positive suppression, crisis severity classification, competitor/brand grouping, deterministic response playbooks, and guarded public/crisis response paths.

**Key capabilities**: Real-time social media monitoring across Twitter/X, LinkedIn, Reddit, and news. Sentiment analysis (positive/negative/neutral with trend). Brand mention tracking with alert thresholds. Competitor brand monitoring. Crisis detection and escalation. Share of voice analysis.

**HITL triggers**: Negative sentiment spikes. Crisis indicators (volume + negativity above threshold). Public/crisis responses. Pricing, legal, comparative, or other high-risk brand claims.

**Connected systems**: Brandwatch, Twitter/X, YouTube, and social publishing systems where configured. Active public response additionally requires a write-safe Social connector contract and confirmed external write evidence.

### 6. Email Marketing Agent
**Status**: Beta. Email Marketing is available through a separate LangGraph path with approval-gated tools. Treat it as partial CMO coverage, not a complete autonomous pillar.

**What it does**: Email campaign management — from list management to campaign creation to performance analysis.

**Key capabilities**: Email campaign creation with templates. List segmentation based on behavior, demographics, and engagement. A/B testing (subject lines, content, send times). Automated drip sequences. Deliverability monitoring. Unsubscribe management and compliance (CAN-SPAM, GDPR).

**HITL triggers**: All email sends require CMO approval. List segment changes above threshold. Template modifications. Drip sequence changes.

**Connected systems**: Mailchimp, SendGrid, HubSpot (contact data).

### 7. Social Media Agent
**Status**: Beta first-class core marketing agent. `core/agents/marketing/social_media.py` now implements deterministic domain logic, but Social Media is not production-ready until real Social connectors, write scopes, policy approval, escalation, audit, confirmed external writes, and pilot proof are present.

**Current behavior**: Social media management recommendations — content calendar generation, posting schedule optimization, engagement triage, reply risk classification, social post draft/recommendation, and guarded publish/reply paths.

**Key capabilities**: Content calendar creation across channels, deterministic best-time posting recommendations, triage of normal/negative/pricing-legal/executive/crisis mentions, risk classification for replies, social post drafts with source refs and rationale, escalation recommendations for crisis/high-risk mentions, and publish/reply safeguards that refuse unconfirmed external writes.

**HITL triggers**: All social media posts require CMO approval before publishing. Responses to negative mentions. Content involving brand claims or pricing.

**Connected systems**: Buffer, Twitter/X, YouTube, MoEngage where configured. Active publishing additionally requires a write-safe Social connector contract and confirmed external write evidence.

### 8. ABM Agent (Account-Based Marketing)
**Status**: Beta first-class core marketing agent. `core/agents/marketing/abm_agent.py` now implements deterministic ABM domain logic, but ABM is not production-ready until real ABM/CRM/Ads connectors, write scopes, policy approval, escalation, audit, confirmed external writes, and pilot proof are present.

**Implemented beta behavior**: Account-based marketing account scoring, ICP fit scoring, intent signal aggregation, high-intent alerting, next-best-action recommendation, and CSV target-account ingest validation using structured account data and configurable Bombora, G2, TrustRadius, and CRM-style weights.

**Key capabilities**: Target account list management (ICP scoring). **Intent data aggregation** — Bombora (40% weight), G2 (30%), TrustRadius (30%) for account-level buying signals. Account-level engagement scoring. Personalized content recommendations per account. Multi-touch attribution at the account level. ABM campaign orchestration. **CSV upload** for target account lists. **Intent heatmap** visualization.

**HITL triggers**: Target account list changes. High-intent account alerts (immediate outreach recommended). Budget allocation to specific accounts.

**Connected systems**: HubSpot/Salesforce (CRM), Bombora (intent data), G2 (buyer intent), TrustRadius (review + intent), GA4 (intent signals), LinkedIn Ads (account targeting).

### 9. Competitive Intel Agent
**Status**: Beta first-class core marketing agent. `core/agents/marketing/competitive_intel.py` now implements deterministic domain logic, but Competitive Intel is not production-ready until real competitive-source connectors, policy approval, escalation, audit evidence, confirmed external writes where applicable, and pilot proof are present.

**Implemented beta behavior**: Competitive intelligence recommendations — weekly competitor snapshots, profile normalization, pricing-change detection, feature/capability diffing, win/loss signal extraction, change confidence scoring, duplicate suppression, alert thresholding, and positioning recommendations.

**Key capabilities**: Competitor website and content monitoring. Pricing change detection and alerting. Feature/capability comparison matrix maintenance. Market positioning analysis. Win/loss pattern analysis by competitor. New competitor entry detection. Public response, pricing claim, comparative claim, and campaign actions are guarded by policy, approval, escalation, audit, connector write-safety, and external-write confirmation.

**HITL triggers**: Competitive pricing changes. New competitor product launches. Win rate drops against specific competitors. Comparative/pricing claims, public responses, and competitive campaign actions require approval or escalation before any external action.

**Connected systems**: Ahrefs (competitor SEO), Brandwatch or social listening sources (competitor mentions), CRM (win/loss data), and Social/Ads connectors only for guarded external actions.

---

## NL Query for Marketing

The NL Query interface lets you ask questions in plain English. Press **Cmd+K** (or **Ctrl+K** on Windows) from any page, or click the chat icon to open the slide-out chat panel.

### Example Marketing Queries

| Query | What You Get |
|-------|-------------|
| "How did Google Ads perform last week?" | Spend, impressions, clicks, CTR, CPC, conversions, ROAS for all Google Ads campaigns |
| "What's our CAC?" | Customer Acquisition Cost broken down by channel with trend vs. previous period |
| "Show me top content this month" | Top 10 pages by traffic with engagement metrics, conversions, and SEO ranking positions |
| "How many MQLs did we generate in March?" | MQL count with source breakdown, conversion rate to SQL, and comparison to target |
| "What's the email open rate for the product launch campaign?" | Open rate, CTR, unsubscribe rate, and deliverability for the specified campaign |
| "Which competitor is winning the most deals against us?" | Win/loss analysis by competitor with deal count, average deal size, and common objections |
| "What's our LinkedIn Ads ROAS?" | Return on ad spend for LinkedIn campaigns with breakdown by campaign and audience |
| "Show me brand sentiment this week" | Positive/negative/neutral mention count and trend with top positive and negative mentions |
| "What keywords are we ranking for?" | Top ranking keywords with position, traffic estimate, and trend |
| "How is the lead nurture sequence performing?" | Drip sequence metrics: open rates, click rates, conversion rates by step |

Every answer includes **agent attribution** — you can see which agent provided the data.

---

## Report Scheduling for Marketing

Navigate to **Reports > Scheduled Reports** to set up automated marketing reports.

### Common Marketing Reports

#### Weekly Marketing Report
- **Schedule**: Every Monday at 9:00 AM
- **Content**: Channel performance summary (spend, leads, ROAS), email campaign results, content performance, social engagement, brand sentiment, pipeline impact
- **Format**: PDF (executive summary) + Excel (detail tabs)
- **Delivery**: Email to CMO + Slack #marketing channel

#### Daily Ad Performance
- **Schedule**: Every weekday at 8:00 AM
- **Content**: Yesterday's ad spend, impressions, clicks, conversions, and ROAS by channel and campaign
- **Format**: PDF
- **Delivery**: Email to CMO + performance marketing team

#### Monthly Marketing ROI
- **Schedule**: 5th business day of each month
- **Content**: Full-month marketing spend vs. pipeline generated, CAC trend, channel mix analysis, content ROI, event ROI, budget utilization
- **Format**: Excel (with pivot-ready data)
- **Delivery**: Email to CMO + CEO + CFO

#### Campaign Performance (Ad-Hoc)
- **Schedule**: On-demand (run-now)
- **Content**: Detailed performance for a specific campaign — impressions, clicks, CTR, conversions, cost per conversion, ROAS, audience breakdown
- **Format**: PDF
- **Delivery**: Email to requester

---

## Workflow Templates for Marketing

### Campaign Launch (`campaign_launch`)
**Agents involved**: Content Factory (beta), Campaign Pilot (production), SEO Strategist (beta; production gated), Social Media (beta), Email Marketing (beta)

**Steps**:
1. Content Factory: Generate campaign brief from business objectives
2. Content Factory: Create ad copy variants and landing page content
3. SEO Strategist: Optimize landing page for target keywords
4. HITL: CMO reviews creative and copy (mandatory)
5. Campaign Pilot: Set up campaigns across Google Ads, Meta, LinkedIn
6. Email Marketing: Create and schedule email sequence
7. Social Media: Schedule social posts for launch
8. HITL: CMO gives final launch approval (mandatory)
9. Implemented production/beta steps activate only after approval and connector/write/audit confirmation gates pass; stub and unavailable steps remain target behavior
10. Campaign Pilot: Begin performance monitoring

### Content Pipeline (`content_pipeline`)
**Agents involved**: Content Factory (beta), SEO Strategist (beta; production gated)

**Steps**:
1. SEO Strategist: Identify keyword gaps and content opportunities
2. Content Factory: Generate content brief (topic, target keyword, structure, word count)
3. Content Factory: Create draft
4. SEO Strategist: Optimize draft (title tag, meta description, headings, internal links)
5. HITL: CMO reviews and approves content (mandatory)
6. Content Factory: Publish to WordPress
7. SEO Strategist: Submit URL to Google for indexing

### Lead Nurture (`lead_nurture`)
**Agents involved**: CRM Intelligence (beta; production gated), Email Marketing (beta), ABM (beta first-class core agent; production gated)

**Steps**:
1. CRM Intelligence: Score and segment new leads
2. CRM Intelligence: Route leads to appropriate nurture track
3. Email Marketing: Enroll leads in drip sequence
4. ABM: Identify high-value accounts for personalized outreach
5. Email Marketing: Monitor engagement and adjust cadence
6. CRM Intelligence: Promote engaged leads to SQL status
7. CRM Intelligence: Hand off SQLs to sales team with context

### Weekly Marketing Report (`weekly_marketing_report`)
**Agents involved**: Campaign Pilot (production), CRM Intelligence (beta; production gated), SEO Strategist (beta; production gated), Email Marketing (beta), Brand Monitor (beta; production gated)

**Steps**:
1. Campaign Pilot: Collect ad performance data across all channels
2. CRM Intelligence: Pull pipeline and lead metrics
3. SEO Strategist: Pull content and ranking metrics from GA4/Ahrefs
4. Email Marketing: Pull email campaign metrics
5. Brand Monitor: Pull sentiment and mention data
6. FP&A integration: Pull marketing budget utilization
7. Generate consolidated report (PDF + Excel)
8. Deliver via configured channels (email, Slack)

---

## CMO Approval Gates

**All implemented publishing actions require CMO approval.** This is a hard requirement for production and beta workflows — implemented agents cannot publish content, send emails, or launch ads without explicit CMO sign-off. For stub or unavailable capabilities, approval behavior is target behavior until the domain-specific agent exists.

### What Requires Approval

| Action | Agent | Why |
|--------|-------|-----|
| Blog/page publishing | Content Factory | Brand voice, accuracy, legal compliance |
| Ad campaign launch | Campaign Pilot | Budget commitment, brand representation |
| Ad budget changes | Campaign Pilot | Financial impact |
| Email campaign send | Email Marketing | Recipient list, content, compliance (CAN-SPAM, GDPR) |
| Social media post | Social Media (beta; production publish requires proof) | Brand representation, timing, tone |
| Drip sequence changes | Email Marketing | Customer experience impact |
| Target account list changes | ABM (beta; production changes require approval, policy, audit, connector write safety, and confirmed write evidence) | Resource allocation |
| Landing page changes | Content Factory + SEO Strategist (beta; production changes require approval, policy, audit, connector write safety, and confirmed write evidence) | Brand, conversion impact |

### Approval Workflow

1. Agent prepares the content/campaign/email and creates an approval request
2. CMO receives notification (in-app badge + email + Slack)
3. CMO reviews in the Approvals queue (`/approvals`) — sees the full content, targeting, budget, and agent recommendation
4. CMO clicks **Approve** (agent publishes), **Reject** (agent stops, with reason logged), or **Override** (CMO edits and publishes modified version)
5. Every decision is logged in the WORM-compliant audit trail

### Approval Timeout Policy

Pending approvals do not hang forever. Approval-sensitive marketing work is evaluated against default CMO timeout policy unless a stricter workflow policy is supplied. The default is fail-closed for customer-facing writes: timed-out campaign launches auto-cancel, ad budget changes and crisis responses auto-escalate, email sends/content/social targeting pause the workflow, and landing-page, target-account, and high-risk copy/pricing/claims changes require manual resolution. A timed-out decision writes audit evidence with approval ID, workflow/run/step IDs where available, requested approver and role, created/due/timed-out timestamps, outcome, escalation target, blocked action, escalation route evidence, and audit reference.

### Why Mandatory Approval Matters

Marketing content directly represents your brand to the public. Unlike finance operations (where an agent processes internal data), marketing agents create externally visible assets. A poorly worded social post, an email with incorrect pricing, or an ad targeting the wrong audience can cause immediate brand damage. The mandatory CMO approval gate ensures no AI-generated content reaches the public without human review.

Agents will never bypass this gate, regardless of confidence score. Even at 99% confidence, the agent creates an approval request and waits.

---

## A/B Testing Workflow

AgenticOrg supports automated A/B testing for email campaigns and ad creatives.

### How It Works
1. **Create Variants**: Use the `ab_test_campaign` workflow template. Define Variant A and Variant B (different subject lines, content, or CTAs)
2. **Run Test**: The system sends each variant to a configurable test audience (default: 20% of total list, split 50/50)
3. **Auto-Winner Selection**: After the test period (configurable — default 24 hours), the system automatically selects the winner based on open rate or CTR
4. **CMO Override**: Before the winner is sent to the remaining audience, a HITL approval is created. The CMO can approve the auto-selected winner OR override and pick the other variant
5. **Send to Remaining**: After approval, the winning variant is sent to the remaining 80% of the audience
6. **Reporting**: Full A/B test metrics are available — variant performance, sample size, statistical confidence

### HITL Gates
- CMO approval required before sending winner to remaining audience
- CMO can override auto-winner selection at any time
- All override decisions are logged in the audit trail

---

## Email Drip Sequences

Behavior-triggered email sequences that nurture leads based on their engagement.

### Creating a Drip Sequence
Use the `email_drip_sequence` workflow template:
1. Define email steps (e.g., Welcome, Follow-up, Last Chance)
2. Set time delays between steps (minutes, hours, or days)
3. Configure behavior triggers: what happens when a recipient opens, clicks, or ignores an email
4. Set re-engagement rules for non-openers

### Behavior Triggers
| Trigger | Action |
|---------|--------|
| Email opened | Move to next step immediately (or after configured delay) |
| Link clicked | Tag lead, update score, trigger next sequence |
| No open after X hours | Send re-engagement variant |
| Drip completed | Rescore lead, hand off to sales if qualified |

### Wait-for-Event Steps
The `lead_nurture` workflow template now includes `wait_for_event` steps — the workflow pauses until a specific event occurs (email opened, link clicked, form submitted). This replaces the old time-only delays with event-driven progression.

### Email Webhooks
Real-time tracking via SendGrid, Mailchimp, and MoEngage webhooks:
- `POST /webhooks/email/sendgrid` — open/click/bounce events
- `POST /webhooks/email/mailchimp` — open/click/unsubscribe events
- `POST /webhooks/email/moengage` — engagement events

All events are stored and linked to the drip sequence, updating lead scores and triggering next steps automatically.

---

## ABM with Intent Data

The ABM Dashboard (`/dashboard/abm`) provides a unified view of target accounts and their buying intent. Dashboard/API surfaces and the beta first-class ABM core agent are not proof that ABM is production-ready without real connector, policy, approval, audit, write-confirmation, and pilot evidence.

### Intent Data Sources
| Source | Weight | What It Provides |
|--------|--------|-----------------|
| **Bombora** | 40% | Topic-level intent surges, company-level buying signals |
| **G2** | 30% | Product category research, comparison activity, review engagement |
| **TrustRadius** | 30% | Product research signals, review views, buyer intent indicators |

### ABM Workflow
1. **Upload Target Accounts**: CSV upload with company name, domain, and tier (POST /abm/accounts/upload)
2. **View Intent Scores**: Dashboard shows blended intent scores per account with per-source breakdown
3. **Intent Heatmap**: Visual heatmap showing intent levels (low/medium/high) across accounts over time
4. **Filter by Tier**: Focus on Tier 1 (enterprise), Tier 2 (mid-market), or Tier 3 (SMB) accounts
5. **Launch Campaign**: Select accounts and launch a personalized outreach campaign with one click

### ABM API Endpoints
- `GET /abm/accounts` — list all target accounts with intent scores
- `POST /abm/accounts` — add a target account
- `POST /abm/accounts/upload` — bulk CSV upload
- `GET /abm/accounts/{id}/intent` — per-account intent breakdown
- `POST /abm/accounts/{id}/campaign` — launch campaign for account
- `GET /abm/dashboard` — dashboard summary data

---

## Web Push for Marketing Approvals

Enable browser push notifications to approve or reject marketing decisions without opening the dashboard.

### Setup
1. Click the bell icon in the dashboard header
2. Allow push notifications when prompted by the browser
3. Notifications are sent for all HITL items in your approval queue

### One-Tap Decisions
When a marketing agent needs CMO approval (campaign launch, email send, content publish), you receive a push notification with:
- Agent name and action summary
- **Approve** button — one tap to approve
- **Reject** button — one tap to reject

Every push decision is logged in the WORM-compliant audit trail with timestamp, decision, and "push_notification" as the source.

### Push API Endpoints
- `POST /push/subscribe` — register browser for push notifications
- `POST /push/unsubscribe` — remove push subscription
- `GET /push/vapid-key` — get the VAPID public key for subscription
- `POST /push/test` — send a test notification

---

## Getting Started

1. **Log in** as CMO: `cmo@agenticorg.local` / `cmo123!` (demo) or your enterprise credentials
2. **Navigate** to `/dashboard/cmo` to see your marketing KPIs
3. **Try NL Query**: Press Cmd+K and ask "How did Google Ads perform last week?"
4. **Review Approvals**: Check `/approvals` for any pending content/campaign approvals
5. **Set Up Reports**: Go to Reports > Scheduled Reports to configure your weekly marketing report
6. **Explore Workflows**: Navigate to Workflows to see the campaign launch and content pipeline templates
7. **Connect Platforms**: Go to Settings > Connectors to connect Google Ads, Meta Ads, HubSpot, Mailchimp, etc.

For questions or pilot setup, contact sales@agenticorg.ai.
