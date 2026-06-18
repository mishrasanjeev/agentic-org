# Strict CMO Agentic Execution Backlog

Date: 2026-05-23
Source review: root workspace CMO review moved into this local AgenticOrg task
Purpose: convert the CMO autonomy gap review into a strict local execution backlog for Codex CLI tasks
Status: CMO-1.1, CMO-1.2, CMO-1.3, CMO-2.1, CMO-3.1, CMO-3.2, CMO-3.3, CMO-4.1, CMO-4.2, CMO-4.3, CMO-5.1, CMO-5.2, CMO-5.3, CMO-5.4, CMO-6.1, CMO-6.2, CMO-6.3, CMO-7.1, CMO-7.2, CMO-7.3, CMO-8.1, CMO-8.2, CMO-8.3, CMO-9.1, CMO-9.2, CMO-9.3, and CMO-9.4 complete; CMO-PROD-1 (weekly-report pilot-evidence validation path), CMO-PROD-2 (durable persistence + report-task wiring), and CMO-PROD-3 (sandbox walk-through runner with fail-closed preflight) complete in code; CMO-PROD-3-DB-LOCAL (local Postgres setup) completed 2026-05-24 — a `pgvector/pgvector:pg16` container `agenticorg-cmo-postgres` runs on host port 5433, the `weekly_report_pilot_proofs` table exists, `alembic_version` is stamped at `v4917_weekly_report_proof`, the table has **0 rows** (no fake / synthetic proof inserted), and the CMO-PROD-3 preflight's database blocker is resolved; remaining CMO-PROD-3 blockers are QA-owned (`AGENTICORG_CMO_SANDBOX_TENANT_ID` + one connector group per CRM/Ads/Analytics/Email category — see CMO-PROD-3 "Local DB setup status" for the exact QA command sequence). Remaining work is live sandbox/vendor execution, real-vendor pilot evidence capture, and a CMO-PROD UI surface.

## Target State

AgenticOrg should be able to run the CMO organization through agents, with humans involved only at explicit policy gates such as high-risk copy, budget changes, legal claims, crisis escalation, and executive approval.

## Brutal Current Status

CMO autonomous operations capability today: **40/100**.

What is production-credible or beta-implemented now: **7.5 of 9 CMO agent pillars** (Campaign Pilot is production; Content Factory, Email Marketing, Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM, and Competitive Intel are beta).

- Stronger: Campaign Pilot, Content Factory
- Partial/minimal: Email Marketing through a separate LangGraph path
- New beta first-class core agents: Social Media, ABM, Competitive Intel, Brand Monitor, SEO Strategist, CRM Intelligence
- CMO-WS-3 first-class agent buildout and CMO-WS-4 wrapper deepening are both complete at beta status; production proof is still absent

This is not ready to claim "CMO org fully run by agents." Every marketing-agent pillar in `core/agents/marketing` now has first-class deterministic code, but production claim requires real-vendor pilot proof, persistent evidence storage, and vendor adapter rollout — none of which is delivered by the deepening tasks alone.

## Evidence-Based Gaps

## 1. Agent Coverage Gap

Claimed in `docs/cmo_guide.md`: 9 marketing agents with broad capabilities and strict HITL behavior.

Implemented in `core/agents/marketing` (as of 2026-05-24):

- `campaign_pilot.py`: substantial logic (production)
- `content_factory.py`: substantial logic (beta)
- `brand_monitor.py`: first-class beta brand monitoring logic (CMO-4.1)
- `seo_strategist.py`: first-class beta SEO logic (CMO-4.2)
- `crm_intelligence.py`: first-class beta CRM/pipeline intelligence logic (CMO-4.3)

No CMO-WS-3 or CMO-WS-4 pillar remains as a wrapper or stub-only marketing agent. Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM, and Competitive Intel have first-class deterministic beta core logic with policy/approval/audit/external-write-confirmation safety gates.

Impact: AgenticOrg cannot honestly sell or demo a fully autonomous CMO organization while beta Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM, and Competitive Intel lack real-vendor pilot proof, persistent evidence stores, and vendor-adapter rollout. The honest claim today is "every CMO agent pillar has first-class deterministic beta code with safety gates; production claims still require pilot proof."

## 2. Workflow-To-Agent Mismatch Gap

Marketing workflows reference agent types such as `social_media`, `abm_agent`, and `competitive_intel`. All three now have first-class beta core logic, while production workflow activation still depends on connector/write, policy, approval, escalation, audit, and pilot-proof evidence.

Impact: workflows can appear complete while execution either falls back to generic behavior or lacks domain-specific outputs.

## 3. Functional Depth Gap

Campaign Pilot is the strongest current marketing agent.

Current strengths:

- Budget allocation logic
- Channel performance polling
- ROAS-driven pause/scale actions
- HITL triggers for high-budget scenarios

Remaining gaps:

- Robust attribution model strategy
- Rollback/safe-mode orchestration
- Policy controls by spend tier, region, and product

Content Factory is useful but incomplete.

Current strengths:

- LLM content generation
- Brand and compliance checks
- WordPress and Buffer scheduling hooks

Remaining gaps:

- Formal content QA rubric with graded publish gates
- Stronger brand policy framework beyond brittle keyword checks
- Draft to approval to publish to performance feedback loop

Brand Monitor now has deterministic beta domain logic for mention aggregation, sentiment trends, spike detection, false-positive suppression, crisis classification, playbook recommendation, escalation, and safety-gated response actions. SEO Strategist now has deterministic beta domain logic for keyword gaps, ranking deltas, technical issue prioritization, recommendation bundling, content optimization, sprint planning, and safety-gated site changes. CRM Intelligence is not yet a CMO-grade operator; its local file still mostly defers to shared/base behavior instead of owning domain-specific execution.

## 4. Testing And Reliability Gap

Missing proof for "fully by agents":

- End-to-end regression packs for each CMO workflow
- Connector contract tests plus real connector or vendor-sandbox proof for production paths
- Failure injection for API outages, stale channel data, approval timeout, malformed payloads, and budget overspend races
- Golden KPI datasets across channels and CRM outcomes

## 5. Governance Gap

A real CMO agent org needs machine-checkable operating policy:

- Budget authority matrix by role, channel, threshold, and region
- Legal/compliance rules per channel and geography
- Escalation matrix and SLA clocks
- Approval timeout behavior, the broader marketing policy manifest, escalation matrix, and structured decision audit package are now code-backed for CMO workflows.
- Persistent audit storage/UI remains open beyond the CMO-6.3 package object.

Current HITL pieces are not yet a full CMO operating policy layer.

## 6. Measurement Gap

AgenticOrg needs a CMO agent scorecard:

- Pipeline contribution quality
- Experiment throughput
- Forecast accuracy
- Creative quality index
- Autonomous decision cost
- Human override rate
- SLA adherence

Without this, the CMO agent org cannot be managed like a real department.

## Program Rules

1. No CMO agent may ship as production-ready if it only wraps shared/base execution.
2. Every workflow `agent_type` and `action` must resolve to implemented, tested behavior.
3. Any budget, publishing, claim-making, crisis-response, or customer-facing action must pass HITL policy.
4. KPI claims must be derived from canonical formulas and source-of-truth priority rules.
5. Dashboard labels must distinguish production, beta, stub, unavailable, and demo states.
6. No customer-facing production tenant may use mocks, sample arrays, hardcoded KPI values, or fake connector success as a substitute for real configured data.
7. Test doubles are allowed only inside automated tests and local harnesses. They are not production proof.
8. Every task below must include proof: tests, changed files, behavior notes, real-data path, and known residual risk.

## Global Definition Of Done

This backlog is done only when all of the following are true:

- All 9 CMO agents have domain-specific production logic.
- No marketing agent in `core/agents/marketing` is merely a pass-through wrapper.
- Core CMO workflows pass deterministic end-to-end tests with contract test doubles and separate production-readiness proof against real connectors, vendor sandboxes, or pilot tenant integrations.
- Failure scenarios are tested for each high-risk workflow.
- HITL policies are explicit, enforceable, and audited.
- KPI calculations are canonical, reconciled, and freshness-aware.
- CMO dashboards truthfully label capability maturity and confidence.
- A weekly autonomous CMO runbook can execute with measurable SLA and quality thresholds.
- A real-company onboarding path exists: connect systems, validate scopes, map fields, configure policy, backfill data, run shadow mode, and promote workflows individually.
- Production CMO surfaces never silently fall back to demo data, stubs, or mock connector results.
- The CMO UX is good enough for daily marketing work: work queue, approvals, drill-downs, data lineage, connector health, confidence, freshness, and clear next actions.

## Sequenced Workstreams

Execution order is mandatory unless dependencies are formally reworked.

| Workstream | Priority | Purpose |
| --- | --- | --- |
| CMO-WS-0 PRD And Product Reality Gate | P0 | Replan the CMO PRD around real companies, real connectors, and excellent operator UX |
| CMO-WS-1 Enterprise Onboarding And Data Foundation | P0 | Build real-company setup: connectors, field mapping, policies, backfill, shadow mode |
| CMO-WS-2 Real Connector Readiness | P0 | Ensure marketing connectors have setup UI, health, scopes, sync, retries, and degraded states |
| CMO-WS-3 Missing Agent Buildout | P0 | Social Media, ABM, and Competitive Intel beta complete; production proof still required |
| CMO-WS-4 Wrapper Agent Deepening | P0 | Brand Monitor and SEO Strategist beta complete; upgrade CRM Intelligence |
| CMO-WS-5 Workflow Integrity | P0 | Ensure every workflow reference maps to implemented behavior and confirmed external writes |
| CMO-WS-6 Governance OS | P0 | Encode budget, legal, approval, escalation, audit, rollback, and timeout policy |
| CMO-WS-7 KPI Trust Layer | P0 | Canonicalize marketing metrics, reconciliation, freshness, and data lineage |
| CMO-WS-8 CMO UX Workbench | P0 | Build a serious marketing cockpit, not decorative status cards |
| CMO-WS-9 Test Hardening And Pilot Proof | P0 | Add contract, E2E, failure-injection, and pilot-readiness evidence |

## CMO-WS-0 PRD And Product Reality Gate

### CMO-0.1 Replan PRD For Real CMO Departments

Priority: P0

Objective: replan the PRD so the CMO product is designed for real companies and marketing departments, not mocks, stubs, or demo-only dashboards.

Files in scope:

- `docs/PRD.md`
- `docs/cmo_guide.md`
- `docs/PRD_CxO_v5.0.md`
- `ui/src/pages/CMODashboard.tsx`
- Related locale/test files if dashboard copy changes

Required planning content:

- Define what "production CMO" means: real tenant data, real connectors, field mapping, policies, approvals, audit, and rollback.
- Add real-company onboarding: connector setup, scope validation, historical backfill, field mapping, policy setup, shadow mode, workflow promotion.
- Add marketing-team personas: CMO, growth lead, RevOps/Marketing Ops, content lead, brand/comms lead, SDR/sales partner, CFO/CEO viewer.
- Add no-mock rule: mocks and stubs are allowed only in tests/local harnesses and must never count as production readiness.
- Add UX requirements: work queue, approvals, drill-downs, data lineage, connector health, confidence, freshness, empty/degraded states, and concrete next actions.
- Add production gates: zero hardcoded production KPI values, zero prompt-wrapper agents marked production, no external action without connector confirmation and audit.

Acceptance tests:

- `npm --prefix ui test -- CMODashboard` if UI changes
- Any existing docs or markdown lint command if configured
- Search PRD/docs for unqualified claims that the CMO org is already fully autonomous

Definition of done:

- `docs/PRD.md` and `docs/PRD_CxO_v5.0.md` define real-company production requirements.
- CMO surfaces distinguish production, beta, shadow, stub, unavailable, demo, and degraded capabilities.
- No "fully autonomous CMO" claim remains without qualification or production proof.
- Existing dashboard tests are updated if UI behavior changes.

### CMO-0.2 Mark Current Capability Honestly In Product

Priority: P0

Objective: prevent product/docs/UI from implying full CMO autonomy before implementation proves it.

Files in scope:

- `docs/cmo_guide.md`
- `docs/PRD_CxO_v5.0.md`
- `ui/src/pages/CMODashboard.tsx`
- Related locale/test files if dashboard copy changes

Acceptance tests:

- `npm --prefix ui test -- CMODashboard`
- Search docs/UI for remaining unqualified CMO autonomy claims.

Definition of done:

- CMO UI/docs distinguish production, beta, shadow, stub, unavailable, demo, and degraded states.
- No "fully autonomous CMO" claim remains without qualification.
- Tests cover displayed capability states.

## CMO-WS-1 Enterprise Onboarding And Data Foundation

### CMO-1.1 Build Marketing Connector Setup Checklist

Priority: P0

Status: Complete on 2026-05-23

Objective: give real companies a clear path to connect CRM, ads, analytics, CMS, email, social, SEO, brand, ABM, and finance systems.

Required behavior:

- Connector setup UI shows owner, account ID, scopes, health, last sync, data coverage, and reconnect action.
- Missing connectors create actionable setup states instead of fake "healthy" cards.
- Production tenants cannot use demo data unless explicitly marked as demo tenants.

Acceptance tests:

- Tests for missing connector, healthy connector, expired auth, stale sync, insufficient scope, and reconnect CTA.

Completion notes:

- Added a code-backed CMO marketing connector setup projection from existing `connector_configs` fields.
- `GET /kpis/cmo` now returns `connector_setup` and `connector_setup_summary`.
- Strict production runtimes suppress silent CMO demo KPI fallback and return `production_data_blocked: true` when no real CMO KPI data exists.
- The CMO dashboard now shows connected, missing, stale, expired-auth, insufficient-scope, healthy, and degraded connector states with setup/reconnect/add-scope/refresh/review CTAs.
- Tests added/updated for missing connector, healthy connector, expired auth, insufficient scope, stale sync, degraded state, reconnect/setup CTA rendering, and production tenant demo fallback suppression.
- Proof commands run: `python -m pytest tests\unit\test_cmo_marketing_connector_setup.py`, `python -m ruff check core\marketing\connector_setup.py api\v1\kpis.py tests\unit\test_cmo_marketing_connector_setup.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, `python -m compileall core\marketing\connector_setup.py api\v1\kpis.py`, and `git diff --check`.

Remaining work moves to CMO-1.3 and CMO-2.1: shadow promotion gates, live vendor-sandbox proof, and external write confirmation are not implemented by this task.

### CMO-1.2 Build Marketing Field Mapping And Backfill

Priority: P0

Status: Complete on 2026-05-23

Objective: map real customer data into canonical CMO metrics before agents can act.

Required mappings:

- Lifecycle stages
- Opportunity revenue fields
- Campaign IDs
- UTM fields
- Account domains
- Consent/unsubscribe fields
- Fiscal calendar and currency

Acceptance tests:

- Tests for required-field validation, partial mapping, currency/timezone handling, and backfill progress states.

Completion notes:

- Added a code-backed CMO marketing data readiness projection from existing `connector_configs.config` metadata.
- Field mapping readiness now covers lifecycle stages, opportunity revenue fields, campaign IDs, UTM fields, account domains, consent/unsubscribe fields, fiscal calendar, currency, and timezone.
- Field mapping states include `unmapped`, `partially_mapped`, `valid`, `invalid`, `stale`, and `blocked`.
- Backfill states include `not_started`, `queued`, `running`, `completed`, `partial`, `failed`, and `blocked`, with source connector key, requested date range, available record counts, last run timestamp, blocking reason, and next action.
- `GET /kpis/cmo` now returns `field_mapping_status`, `field_mapping_summary`, `backfill_status`, `backfill_summary`, and `kpi_readiness`.
- Strict production runtimes now mark CMO KPI confidence blocked or degraded when required mappings/backfills are incomplete, instead of silently treating incomplete real data or demo fallback as production-ready.
- The CMO dashboard now shows marketing data readiness before KPI cards, including mapping/backfill status, blockers, stale/partial states, and action CTAs.
- Tests added/updated for all required mappings present, missing lifecycle mapping, missing revenue mapping, missing consent mapping, invalid currency/timezone, partial/stale mapping, completed/failed/blocked/progress backfill states, production KPI confidence blocking, and dashboard rendering.
- Proof commands run: `python -m pytest tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_marketing_connector_setup.py`, `python -m ruff check core\marketing\connector_setup.py core\marketing\data_readiness.py api\v1\kpis.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py`, `python -m compileall core\marketing\connector_setup.py core\marketing\data_readiness.py api\v1\kpis.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, and `git diff --check`.

Remaining work moves to CMO-1.3, CMO-2.1, and CMO-7.1: this task does not implement shadow-mode promotion gates, live vendor-sandbox proof, external write confirmation, or canonical KPI formulas.

### CMO-1.3 Add Shadow Mode Promotion Gates

Priority: P0

Status: Complete on 2026-05-23

Objective: prevent agents from writing to external systems until a CMO promotes a workflow from shadow to active.

Required behavior:

- Shadow mode recommendations are read-only.
- Promotion is per workflow, not global.
- Promotion requires connector health, policy setup, and passing recent shadow-run quality gates.

Acceptance tests:

- Tests prove shadow mode cannot write externally.
- Tests prove promotion fails if connector/policy prerequisites are missing.

Completion notes:

- Added a code-backed CMO workflow activation projection from existing connector setup, field mapping, backfill, and workflow metadata stored in `connector_configs.config`.
- `GET /kpis/cmo` now returns `workflow_activation_status` and `workflow_activation_summary`.
- Workflow states include `unavailable`, `shadow`, `promotion_blocked`, `promotion_ready`, `active`, `degraded`, and `paused`.
- Covered workflows are `weekly_marketing_report`, `campaign_launch`, `daily_spend_optimization`, `content_pipeline`, `lead_nurture`, `social_publishing`, `abm_sprint`, `brand_crisis_response`, and `seo_sprint`.
- Promotion is per workflow. A workflow is active only when its own activation row is explicitly promoted and connectors, mappings, backfills, approval/policy owners, and shadow-run quality gates pass.
- Shadow, ready, blocked, degraded, paused, and unavailable states are read-only for external marketing writes. Read-only actions such as recommendation, draft, simulation, and internal approval creation remain allowed.
- Social Publishing, ABM Sprint, Brand Crisis Response, and SEO Sprint are beta-gated and remain blocked until their required connectors, mapping/backfill, policy, approval, escalation, audit, and write-confirmation gates pass. Connector setup alone does not mark those workflow pillars production-grade.
- The CMO dashboard now shows workflow activation state, owners, shadow quality, blockers/degraded reasons, external-write readiness, and next action before KPI cards.
- Tests added/updated for default non-active workflow state, missing/unhealthy connector blockers, invalid mapping blockers, failed/blocked backfill blockers, missing approval/policy owner blockers, promotion-ready state, explicit active promotion, read-only shadow enforcement, per-workflow promotion isolation, degraded optional/partial data, unavailable stub workflows, and dashboard rendering.
- Proof commands run: `python -m pytest tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_marketing_connector_setup.py`, `python -m ruff check core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py api\v1\kpis.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py`, `python -m compileall core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py api\v1\kpis.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, `git diff --check`, and claim search for unqualified CMO autonomy language.

Remaining work moves to CMO-2.1, CMO-5.3, CMO-6.2/CMO-6.3, and missing production agents: this task does not implement external write confirmation, escalation matrices, decision audit packages, or missing production agents.

## CMO-WS-2 Real Connector Readiness

### CMO-2.1 Marketing Connector Contract Hardening

Priority: P0

Status: Complete on 2026-05-23

Objective: harden connector adapters so CMO agents can rely on real data and confirmed writes.

Required behavior:

- Read/write permission separation.
- `last_sync_at`, `source_account_id`, `source_object_id`, and `source_url` where available.
- Idempotency keys for write actions.
- Retry budgets and explicit degraded output on failures.
- External write confirmation before workflow step is marked complete.

Acceptance tests:

- Contract tests for success, 401/expired auth, 403/insufficient scope, 429/rate limit, 5xx, partial data, stale data, and duplicate write retry.

Completion notes:

- Added `core.marketing.connector_contracts`, a code-backed marketing connector contract projection from existing connector setup and connector configuration metadata.
- Contract rows distinguish read capabilities, write capabilities, required read scopes, required write scopes, granted scopes, missing scopes, auth status, health status, contract state, read readiness, write readiness, data freshness/TTL, retry budget metadata, idempotency-key support, source object refs, degraded-mode reason, and external write confirmation status.
- Contract states now cover `healthy`, `missing_scope`, `auth_expired`, `rate_limited`, `timeout`, `vendor_5xx`, `partial_data`, `stale_data`, `degraded`, `write_unconfirmed`, and `write_confirmed`.
- A connector with read access alone is not write-ready. Missing write scopes, missing idempotency proof, expired auth, stale data, partial data, rate limits, timeouts, vendor 5xx, or mock/test-double proof block or degrade production readiness instead of passing silently.
- Added write-completion and retry helpers so active workflow write steps cannot be marked complete without an external write confirmation, while shadow/draft/internal-only modes can remain internal without pretending a vendor write happened.
- `GET /kpis/cmo` now returns `connector_contracts` and `connector_contract_summary`; strict production runtimes block CMO production confidence when connector contracts are blocked or based on mock/test proof, and degrade confidence when connector contracts are degraded.
- Workflow activation now consumes connector contract rows. Workflows that require external writes are blocked until the required connector category has a write-ready contract; read-ready connector contracts are also required for required connector categories.
- Marketing connector setup and KPI readiness now downgrade stale/partial/degraded contract states so stale or partial connector data cannot produce fake healthy KPI readiness.
- The CMO dashboard now renders a connector contract table before mapping/backfill readiness, including read/write readiness, auth/freshness, retry/idempotency state, external write confirmation, degraded reasons, mock-proof warnings, and setup/reconnect/add-scope/retry/idempotency CTAs.
- Tests added/updated for read-only connectors not being write-ready, missing write scopes blocking workflow readiness, expired auth blocking readiness, rate-limit/timeout/vendor-5xx degraded states with retry metadata, partial/stale data downgrading KPI readiness, write-confirmation requirements, confirmed writes completing, idempotent duplicate retry planning, and mock/test proof not satisfying production readiness.
- Proof commands run: `python -m pytest tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_workflow_no_false_success.py`, `python -m ruff check core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\connector_contracts.py api\v1\kpis.py workflows\step_types.py workflows\step_results.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_workflow_no_false_success.py`, `python -m compileall core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\connector_contracts.py api\v1\kpis.py workflows\step_types.py workflows\step_results.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_workflow_no_false_success.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, `git diff --check`, and claim search for unqualified CMO autonomy language.
- No dedicated markdown/docs lint script is configured in `ui/package.json`; no root `package.json` exists in this worktree.

Remaining work moves to CMO-5.3, CMO-6.2/CMO-6.3, and the agent production-proof/deepening tracks: this task does not implement full vendor write execution for every connector, escalation matrices, decision audit packages, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

## CMO-WS-3 Missing Agent Buildout

### CMO-3.1 Create Social Media Agent

Priority: P0

Status: Complete on 2026-05-24

Objective: add a first-class Social Media agent with domain-specific execution.

Files in scope:

- `core/agents/marketing/social_media.py`
- `core/agents/marketing/__init__.py`
- Agent registry files
- Prompt files if this repo uses prompt-backed agent registration
- Unit tests under `tests/`

Required behavior:

- Content calendar generation
- Posting schedule optimization
- Engagement triage
- Escalation rules for sensitive replies, claims, pricing, legal, crisis, and executive mentions
- HITL gate for high-risk replies and publish actions

Acceptance tests:

- Add focused pytest coverage for each action path.
- Run the smallest relevant pytest command for marketing agents.

Definition of done:

- Agent is registered and callable by the same `agent_type` used in workflows.
- Tests prove happy path, risky-content HITL, malformed input, and connector failure behavior.

Implementation summary:

- Added `core.agents.marketing.social_media`, a first-class beta Social Media agent registered under `agent_type="social_media"` with deterministic domain actions for content calendar generation, posting schedule optimization, engagement triage, reply risk classification, social post drafting, and guarded post/reply publishing paths.
- Social Media outputs now follow the CMO agent contract shape: status, confidence, rationale, recommended actions, source refs, policy result/ref, approval/HITL flags, escalation refs where required, audit refs/packages, degraded/blocker reasons, and external-write refs only when a write confirmation exists.
- Publishing and replying fail closed unless the workflow is active, the Social connector is write-safe, policy and approval requirements are satisfied, decision-audit evidence exists, and the external write is confirmed with vendor object evidence. Shadow, draft, and internal-only modes remain read-only and cannot execute external writes.
- Social Media is registered in the agent loader, reflected in `core.marketing.agent_contracts` as beta/non-production, recognized by workflow linting and workflow activation, and integrated with marketing policy, approval timeout, escalation, audit, connector/write-readiness, and pilot-proof truth labels.
- Tests cover content calendar generation, schedule optimization, engagement triage classifications, high-risk reply approval/escalation, shadow read-only behavior, connector/write-safety blockers, unconfirmed-write blocking, confirmed-write completion, contract output shape, workflow linter behavior, workflow activation gating, and pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_social_media_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py -q --tb=short`, `python -m ruff check core\agents\marketing\social_media.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\pilot_proof.py tests\unit\test_cmo_social_media_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, `python -m compileall core\agents\marketing\social_media.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\pilot_proof.py tests\unit\test_cmo_social_media_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, and `git diff --check`.

Remaining work:

- Social Media remains beta, not production: no live vendor credentials, vendor-specific production adapters, persistent evidence store, or real-vendor pilot proof was added.
- ABM is completed as beta by CMO-3.2; Competitive Intel is completed as beta by CMO-3.3.
- Brand Monitor is completed as beta by CMO-4.1; SEO Strategist is completed as beta by CMO-4.2; CRM Intelligence is completed as beta by CMO-4.3 (production claim still requires real-vendor pilot proof).

### CMO-3.2 Create ABM Agent

Priority: P0

Status: Complete on 2026-05-24

Objective: add a first-class ABM agent for account scoring and next-best-action planning.

Files in scope:

- `core/agents/marketing/abm_agent.py`
- `core/agents/marketing/__init__.py`
- Agent registry files
- Tests under `tests/`

Required behavior:

- Account scoring using Bombora, G2, TrustRadius, CRM, and CSV inputs where available
- Configurable scoring weights
- ICP fit scoring
- Intent heat scoring
- Next-best-action output
- CSV account ingest validation

Acceptance tests:

- Tests for weighting math, threshold alerts, missing fields, malformed CSV, and deterministic NBA output.

Definition of done:

- ABM agent is registered and workflow-addressable.
- Outputs include score, rationale, confidence, and recommended action.

Implementation completed:

- Added `core.agents.marketing.abm_agent`, a first-class beta ABM agent registered under `agent_type="abm"` with deterministic account scoring, ICP fit scoring, intent heat scoring, configurable Bombora/G2/TrustRadius/CRM-style weighting, engagement aggregation, next-best-action planning, CSV account ingest validation, high-intent alerts, and guarded target-list/campaign/budget write paths.
- Updated ABM contract, workflow lint, workflow activation, policy manifest, approval-timeout, escalation, decision-audit, and pilot-proof metadata so ABM is known and beta, not unavailable, while production writes remain blocked without connector write safety, policy approval, escalation/audit evidence, write confirmation, and pilot proof.
- Tests cover ABM scoring, weighting math, ICP fit, intent heat, next-best action, CSV happy/invalid paths, high-intent alerting, target-list approval, budget approval/escalation, shadow read-only behavior, unsafe connector blocking, unconfirmed-write blocking, confirmed-write evidence, contract shape, workflow linter status, activation gating, and pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_abm_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py -q --tb=short`, `python -m ruff check core\agents\marketing\abm_agent.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py core\marketing\pilot_proof.py tests\unit\test_cmo_abm_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py`, `python -m compileall core\agents\marketing\abm_agent.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py core\marketing\pilot_proof.py tests\unit\test_cmo_abm_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py`, and `git diff --check`.

Remaining work:

- ABM remains beta, not production: no live ABM/CRM/Ads vendor credentials, vendor-specific production write adapters, persistent evidence store, or real-vendor pilot proof was added.
- Competitive Intel is completed as beta by CMO-3.3.
- Brand Monitor is completed as beta by CMO-4.1; SEO Strategist is completed as beta by CMO-4.2; CRM Intelligence is completed as beta by CMO-4.3 (production claim still requires real-vendor pilot proof).

### CMO-3.3 Create Competitive Intel Agent

Priority: P0

Status: Complete on 2026-05-24

Objective: add a first-class Competitive Intel agent for market monitoring and competitive alerts.

Files in scope:

- `core/agents/marketing/competitive_intel.py`
- `core/agents/marketing/__init__.py`
- Agent registry files
- Tests under `tests/`

Required behavior:

- Weekly competitor snapshot
- Pricing-change detection
- Win/loss signal extraction
- Change diffing
- Confidence score for detected changes
- Alerting thresholds

Acceptance tests:

- Tests for signal parsing, diff confidence, duplicate suppression, and alert threshold behavior.

Definition of done:

- Competitive Intel agent is registered and workflow-addressable.
- Outputs are structured and auditable.

Implementation completed:

- Added `core.agents.marketing.competitive_intel`, a first-class beta Competitive Intel agent registered under `agent_type="competitive_intel"` with deterministic weekly competitor snapshots, profile normalization, pricing-change detection, feature/capability diffing, win/loss signal extraction, confidence scoring, duplicate suppression, alert thresholding, and positioning recommendation logic.
- Updated Competitive Intel contract, workflow lint, workflow activation, policy manifest, approval-timeout, escalation, decision-audit, and pilot-proof metadata so Competitive Intel is known and beta, not unavailable, while production external actions remain blocked without connector write safety, policy approval, escalation/audit evidence, write confirmation, and pilot proof.
- Tests cover weekly snapshots, profile normalization, pricing-change confidence, feature diffing, win/loss extraction, duplicate suppression, alert severity thresholds, positioning next-best actions, comparative/pricing policy requirements, major-launch/crisis escalation, degraded connector behavior, shadow read-only behavior, unsafe connector blocking, unconfirmed-write blocking, confirmed-write evidence, contract shape, workflow linter status, activation gating, and pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_competitive_intel_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py -q --tb=short`, `python -m ruff check core\agents\marketing\competitive_intel.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py core\marketing\pilot_proof.py tests\unit\test_cmo_competitive_intel_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, `python -m compileall core\agents\marketing\competitive_intel.py core\agents\__init__.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py core\marketing\pilot_proof.py tests\unit\test_cmo_competitive_intel_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, and `git diff --check`.

Remaining work:

- Competitive Intel remains beta, not production: no live Brandwatch/Ahrefs/social/ads credentials, vendor-specific production adapters, persistent evidence store, or real-vendor pilot proof was added.
- Brand Monitor is completed as beta by CMO-4.1; SEO Strategist is completed as beta by CMO-4.2; CRM Intelligence is completed as beta by CMO-4.3 (production claim still requires real-vendor pilot proof).

## CMO-WS-4 Wrapper Agent Deepening

### CMO-4.1 Deepen Brand Monitor

Priority: P0

Objective: replace wrapper behavior with explicit brand monitoring logic.

Files in scope:

- `core/agents/marketing/brand_monitor.py`
- Relevant connector adapters
- Tests under `tests/`

Required behavior:

- Mention aggregation
- Sentiment trend detection
- Spike detection
- False-positive suppression
- Crisis severity classification
- Escalation playbooks

Acceptance tests:

- Tests for false positives, spike detection, crisis escalation, and normal-noise suppression.

Status: Complete on 2026-05-24.

Implementation completed:

- Replaced Brand Monitor wrapper behavior with `core.agents.marketing.brand_monitor.BrandMonitorAgent`, a first-class beta marketing agent with deterministic mention aggregation, sentiment classification/trends, negative-volume spike detection, false-positive suppression, crisis severity classification, competitor/brand grouping, response playbook recommendation, and escalation recommendation.
- Updated Brand Monitor contract, workflow lint, workflow activation, policy manifest, approval-timeout, escalation, decision-audit, and pilot-proof metadata so Brand Monitor is beta/implemented, not stub-only, while active public/crisis response remains blocked without connector write safety, policy approval, escalation/audit evidence, write confirmation, and pilot proof.
- Tests cover grouping, sentiment distribution, spike alerts, false-positive suppression, crisis severity, competitor/brand grouping, deterministic playbooks, public/crisis approval and escalation gates, degraded connector behavior, shadow read-only behavior, unsafe connector blocking, unconfirmed-write blocking, confirmed-write evidence, contract shape, workflow linter/activation status, and pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_brand_monitor_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py -q --tb=short`, `python -m pytest tests\unit\test_cmo_brand_monitor_agent.py -q --tb=short`, `python -m ruff check core\agents\marketing\brand_monitor.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py tests\unit\test_cmo_brand_monitor_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py`, `python -m compileall core\agents\marketing\brand_monitor.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\escalation_matrix.py core\marketing\decision_audit.py tests\unit\test_cmo_brand_monitor_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_e2e_scenarios.py`, and `git diff --check`.

Remaining work:

- Brand Monitor remains beta, not production: no live Brandwatch/Twitter credentials, vendor-specific production adapters, persistent evidence store, real-vendor pilot proof, or production delivery rollout was added.
- SEO Strategist is completed as beta by CMO-4.2; CRM Intelligence is completed as beta by CMO-4.3 (production claim still requires real-vendor pilot proof).

### CMO-4.2 Deepen SEO Strategist

Priority: P0

Objective: replace wrapper behavior with SEO analysis and recommendation logic.

Files in scope:

- `core/agents/marketing/seo_strategist.py`
- Relevant connector adapters
- Tests under `tests/`

Required behavior:

- Keyword gap analysis
- Ranking deltas
- Technical issue prioritization
- Recommendation bundling by effort and impact

Acceptance tests:

- Tests for ranking trend math, issue ordering, and deterministic recommendation shape.

Status: Complete on 2026-05-24.

Implementation completed:

- Replaced SEO Strategist wrapper behavior with `core.agents.marketing.seo_strategist.SeoStrategistAgent`, a first-class beta marketing agent with deterministic keyword gap analysis, ranking delta computation, technical SEO issue prioritization, effort/impact recommendation bundling, content optimization recommendations, SEO sprint planning, and stale/partial data handling.
- Updated SEO Strategist contract, workflow lint, workflow activation, policy manifest, approval-timeout, decision-audit, and pilot-proof metadata so SEO Strategist is beta/implemented, not stub-only, while active technical site changes remain blocked without CMS/SEO connector write safety, policy approval, audit/escalation evidence where needed, external write confirmation, and pilot proof.
- Tests cover keyword gaps, ranking improvements/drops/new/lost terms, technical issue ordering, recommendation bundling, content optimization, sprint planning, stale/partial connector degradation, approval/policy/audit/write gates, shadow read-only behavior, unsafe connector blocking, unconfirmed-write blocking, confirmed-write evidence, contract shape, workflow linter/activation status, and pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_seo_strategist_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py -q --tb=short`, `python -m ruff check core\agents\marketing\seo_strategist.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\decision_audit.py tests\unit\test_cmo_seo_strategist_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, `python -m compileall core\agents\marketing\seo_strategist.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py core\marketing\decision_audit.py tests\unit\test_cmo_seo_strategist_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py`, and `git diff --check`.

Remaining work:

- SEO Strategist remains beta, not production: no live Ahrefs/Search Console/GA4/CMS credentials, vendor-specific production adapters, persistent evidence store, real-vendor pilot proof, or production delivery rollout was added.
- CRM Intelligence remains stub/non-production until CMO-4.3 replaces shared/base behavior.

### CMO-4.3 Deepen CRM Intelligence

Priority: P0

Objective: replace wrapper behavior with pipeline intelligence and segmentation logic.

Files in scope:

- `core/agents/marketing/crm_intelligence.py`
- `core/marketing/agent_contracts.py`
- `core/marketing/workflow_linter.py`
- `core/marketing/workflow_activation.py`
- `core/marketing/policy_manifest.py`
- `core/marketing/approval_timeouts.py`
- Tests under `tests/`

Required behavior:

- Pipeline velocity analysis
- Funnel conversion analysis (safe division, low-sample handling)
- Lead scoring refresh (deterministic, explainable)
- Churn risk signal extraction
- Segment recommendation
- SQL promotion criteria
- Account/deal health summary
- Stale/partial/missing-mapping data handling
- Policy/approval/audit/connector-write/external-write-confirmation gates for CRM writes

Acceptance tests:

- Tests for pipeline velocity bottlenecks, funnel math, scoring refresh, churn signal thresholds, segment output schema, SQL promotion classifier, account health, degraded data, write safety, target-account threshold approval, contract shape, workflow linter/activation, and pilot-proof truth.

Status: Complete on 2026-05-24.

Implementation completed:

- Replaced CRM Intelligence wrapper behavior with `core.agents.marketing.crm_intelligence.CrmIntelligenceAgent`, a first-class beta marketing agent with deterministic pipeline velocity analysis (per-stage averages, bottlenecks, stuck deals), funnel conversion analysis with safe division and low-sample handling, deterministic explainable lead scoring refresh, churn risk signal extraction (login inactivity, support load, NPS, payment health, usage trend), segment recommendation (industry/size/behaviour buckets with enrol/follow-up/win-back action plans), SQL promotion classification (score floor + recent engagement + demo/intent), account/deal health composite scoring, and stale/partial/missing-mapping data handling.
- Promoted CRM Intelligence contract from stub to beta in `core.marketing.agent_contracts`, registered all read and write actions, and added per-action lint/activation/policy/approval-timeout rules in `core.marketing.workflow_linter`, `core.marketing.workflow_activation`, `core.marketing.policy_manifest`, and `core.marketing.approval_timeouts`. Pilot proof keeps CRM Intelligence non-production until a real vendor/sandbox pilot lands evidence.
- Active CRM writes (lead score push, lifecycle stage change, segment/list change, target account change, bulk CRM update) require active mode, write-safe CRM connector contract, satisfied policy decision (`requires_approval` / `requires_escalation`), audit reference, and confirmed external-write evidence (`external_write_confirmation_status="write_confirmed"` plus `external_object_id` / `source_url` / `confirmed_at` / `audit_ref`). Without those, the agent fails closed with `blocked` / `write_unconfirmed` / `shadow_only` outputs and HITL routing.
- Tests in `tests/unit/test_cmo_crm_intelligence_agent.py` cover pipeline velocity bottlenecks, funnel safe division and low sample, deterministic lead scoring with explanations, SQL promotion criteria, segment buckets and action plan, churn signals, account health, stale/partial/missing-mapping degradation, shadow read-only block, active-write approval/audit/connector requirements, target-account threshold approval and escalation, unsafe-connector blocking, unconfirmed-write blocking, confirmed-write evidence with contract shape, and workflow-linter/activation/pilot-proof non-production truth.
- Proof commands run: `python -m pytest tests\unit\test_cmo_crm_intelligence_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_escalation_matrix.py tests\unit\test_cmo_decision_audit_package.py tests\unit\test_cmo_seo_strategist_agent.py tests\unit\test_cmo_brand_monitor_agent.py tests\unit\test_cmo_social_media_agent.py tests\unit\test_cmo_abm_agent.py tests\unit\test_cmo_competitive_intel_agent.py tests\unit\test_cmo_e2e_scenarios.py tests\unit\test_cmo_chaos_failure_modes.py -q --tb=short`, `python -m ruff check core\agents\marketing\crm_intelligence.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py tests\unit\test_cmo_crm_intelligence_agent.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py`, `python -m compileall core\agents\marketing\crm_intelligence.py core\marketing\agent_contracts.py core\marketing\workflow_linter.py core\marketing\workflow_activation.py core\marketing\policy_manifest.py core\marketing\approval_timeouts.py tests\unit\test_cmo_crm_intelligence_agent.py`, and `git diff --check`.

Remaining work:

- CRM Intelligence remains beta, not production: no live HubSpot/Salesforce/Bombora/G2/TrustRadius credentials, vendor-specific production adapters, persistent evidence store, real-vendor pilot proof, or production delivery rollout was added.
- CMO-WS-3 (first-class agent buildout) and CMO-WS-4 (wrapper agent deepening) are now both closed at beta status across Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM, and Competitive Intel.

## CMO-WS-5 Workflow Integrity

### CMO-5.1 Add Marketing Workflow Linter

Priority: P0

Status: Complete on 2026-05-23

Objective: fail fast when marketing workflow YAML references undefined agents or actions.

Files in scope:

- `workflows/examples/*.yaml`
- Workflow parser/validation modules
- Tests under `tests/`

Acceptance tests:

- A test fixture with an unknown `agent_type` fails validation.
- A test fixture with an unknown action fails validation.
- Existing valid marketing workflows pass validation.

Completion notes:

- Added `core.marketing.workflow_linter`, a code-backed marketing workflow linter with `lint_marketing_workflow`, `lint_marketing_workflow_file`, and `lint_marketing_workflow_paths` entry points.
- Lint findings are structured with workflow file, workflow id/name, step id/name, severity (`error`, `warning`, `info`), code, message, and suggested fix.
- The linter applies only to marketing workflows, either by top-level `domain: marketing` or by marketing `agent_type`, and reports non-marketing workflows as out of scope instead of making CI noisy for unrelated domains.
- Production/active workflow lint now fails unknown marketing `agent_type`, unknown actions when action metadata exists, beta Brand Monitor/SEO Strategist/CRM Intelligence/Social Media/ABM/Competitive Intel production-safety gaps, missing connector contracts where connector requirements are declared, and unsafe production external-write steps.
- Target/demo/shadow workflows can reference stub or unavailable marketing agents only with warnings, not as production passes.
- Shadow workflow lint enforces read-only behavior: recommendation/report/simulation style steps can pass, but external-write actions fail lint.
- Production external-write steps must declare connector key/category, have matching write-ready connector contracts, include CMO-5.3 write-confirmation metadata, and include idempotency metadata before lint can pass.
- Existing `workflows/examples/*.yaml` can now be linted directly. Current target/demo workflow gaps are exposed rather than silently treated as production-ready; for example `campaign_launch.yaml` reports beta-agent production-safety gaps and unsafe write metadata gaps.
- Tests added for unknown agent, unknown action, valid implemented action, Social Media/ABM/Competitive Intel beta production-safety blockers, stub agent production blocker, target/demo/shadow warnings, write-ready connector contract checks, missing idempotency/write-confirmation metadata, shadow read-only behavior, non-marketing out-of-scope handling, and linting a real workflow example file.
- Proof commands run: `python -m pytest tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_workflow_templates.py`, `python -m ruff check core\marketing\workflow_linter.py tests\unit\test_cmo_marketing_workflow_linter.py`, `python -m compileall core\marketing\workflow_linter.py tests\unit\test_cmo_marketing_workflow_linter.py`, and `git diff --check`.
- No UI files were changed for CMO-5.1, so CMO dashboard, locale, Playwright, and typecheck commands were not rerun for this task.
- No dedicated markdown/docs lint script is configured: there is no root `package.json`, and `ui/package.json` exposes ESLint but no markdown/docs validation script.

Remaining work moves to CMO-6.2/CMO-6.3, CMO-7.1, and the agent production-proof/deepening tracks: this task does not implement Social Media/ABM/Competitive Intel production proof, deepen wrapper agents, escalation matrices, decision audit packages, or make current target/demo workflows production-ready.

### CMO-5.2 Add Connector Retry And Degraded Mode Policy

Priority: P0

Status: Complete on 2026-05-23

Objective: make marketing workflows resilient to connector outages and stale data.

Files in scope:

- Marketing agents
- Connector framework modules
- Workflow retry modules
- Tests under `tests/`

Acceptance tests:

- Tests simulate connector timeout, partial response, stale data, and rate limiting.
- Outputs clearly mark degraded mode and confidence impact.

Completion notes:

- Added `core.marketing.connector_retry_policy`, a shared code-backed retry/degraded-mode policy for `timeout`, `rate_limited`, `vendor_5xx`, `auth_expired`, `insufficient_scope`, `partial_data`, `stale_data`, `malformed_payload`, `quota_exhausted`, and `connector_disabled`.
- Each failure class now exposes retryability, max attempts, backoff metadata, idempotency requirements, degraded-mode allowance, confidence impact, external-write blocking, production KPI confidence blocking, CTA, and audit event code.
- Marketing connector contracts now project `failure_class`, `retry_policy`, retry-budget defaults from policy, `write_safe`, `blocks_external_writes`, `blocks_production_kpi_confidence`, and structured `degraded_mode` metadata.
- Connector setup recognizes malformed payload, quota exhaustion, and disabled-connector states with actionable CTAs instead of generic healthy/degraded ambiguity.
- CMO data readiness consumes connector degraded-mode policy so production KPI readiness is blocked or degraded with affected KPIs, affected connectors, confidence impact, and next action.
- Workflow activation consumes degraded-mode policy: read-only/reporting workflows can become explicitly degraded when policy allows, while external-write workflows remain blocked unless matching connector contracts are write-safe.
- Marketing external-write completion now fails closed when an explicit connector contract marks the connector non-write-safe, while shadow/internal workflows remain read-only and can continue with labeled recommendations.
- Marketing workflow linting flags production workflow dependencies that can only run in degraded mode and uses `write_safe` rather than optimistic write readiness for external-write steps.
- Tests added for every failure class, retry/backoff metadata, auth/scope/setup CTAs, partial and stale confidence downgrades, malformed/quota/disabled behavior, workflow degradation/blocking, external write fail-closed behavior, shadow read-only continuation, and linter degraded-only dependency reporting.
- Proof commands run: `python -m pytest tests\unit\test_cmo_connector_retry_policy.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_marketing_workflow_linter.py`, `python -m ruff check core\marketing\connector_retry_policy.py core\marketing\connector_contracts.py core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\external_writes.py core\marketing\workflow_linter.py api\v1\kpis.py tests\unit\test_cmo_connector_retry_policy.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_marketing_workflow_linter.py`, `python -m compileall core\marketing\connector_retry_policy.py core\marketing\connector_contracts.py core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\external_writes.py core\marketing\workflow_linter.py api\v1\kpis.py tests\unit\test_cmo_connector_retry_policy.py`, and `git diff --check`.
- No UI files were changed for CMO-5.2, so CMO dashboard, locale, Playwright, and typecheck commands were not rerun for this task.
- No dedicated markdown/docs lint script is configured: there is no root `package.json`, and `ui/package.json` exposes ESLint but no markdown/docs validation script.

Remaining work moves to CMO-6.2/CMO-6.3, CMO-7.1, and the agent production-proof/deepening tracks: this task does not implement vendor-specific adapters for every marketing platform, escalation matrices, decision audit packages, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

### CMO-5.3 Confirm External Writes Before Completion

Priority: P0

Status: Complete on 2026-05-23

Objective: prevent workflows from marking posts, emails, campaigns, or CRM updates complete until the external connector confirms the write.

Acceptance tests:

- Tests simulate connector accepted, connector rejected, timeout after write, duplicate retry, and idempotent recovery.

Completion notes:

- Added `core.marketing.external_writes`, a code-backed final-state evaluator for marketing workflow external-write steps.
- Workflow step result handling now distinguishes `accepted`, `rejected`, `timeout_unknown`, `retry_scheduled`, `idempotent_recovered`, `write_confirmed`, `write_unconfirmed`, `draft_created`, and `shadow_only`.
- Active marketing workflows fail closed when publish/send/launch/update actions are rejected, timed out without safe idempotency metadata, unconfirmed, missing an explicit external object ID, or only created a draft.
- Accepted connector writes complete only when the step output carries explicit external confirmation evidence, including connector key, external object ID, optional source URL, idempotency key, request fingerprint, confirmation timestamp, actor/agent/workflow/run IDs when available, and audit reference.
- Generic business IDs such as `campaign_id` are not treated as connector confirmation unless they appear inside an explicit external-write confirmation payload; this prevents draft or internal identifiers from masquerading as customer-facing writes.
- Timeout/unknown writes schedule retry only when idempotency metadata is present. Retry requests without idempotency fail closed instead of risking duplicate vendor actions.
- Duplicate retries recover a prior confirmed write only when the prior confirmation matches the same idempotency key, returning `idempotent_recovered` without duplicating the external action.
- Shadow workflows remain read-only. They can complete recommendations, drafts, simulations, and internal approval records as `shadow_only`, but any reported external object or confirmation is rejected as a shadow-mode violation.
- Draft/internal-only steps can complete only when the final state is explicitly draft/internal and are not labeled as published, sent, launched, or externally updated.
- Every write decision embeds audit evidence in the step output for attempt, confirmation, rejection, timeout, retry scheduling, and idempotent recovery.
- Tests added/updated for accepted confirmed writes, rejected writes, timeout/unknown writes, retry blocked without idempotency, retry scheduled with idempotency, idempotent recovery, draft/internal completion, shadow read-only enforcement, active unconfirmed failure, and audit evidence.
- Proof commands run: `python -m pytest tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_workflow_no_false_success.py`, `python -m ruff check core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\connector_contracts.py core\marketing\external_writes.py api\v1\kpis.py workflows\step_types.py workflows\step_results.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_workflow_no_false_success.py`, `python -m compileall core\marketing\connector_setup.py core\marketing\data_readiness.py core\marketing\workflow_activation.py core\marketing\connector_contracts.py core\marketing\external_writes.py api\v1\kpis.py workflows\step_types.py workflows\step_results.py tests\unit\test_cmo_marketing_connector_setup.py tests\unit\test_cmo_marketing_data_readiness.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_workflow_no_false_success.py`, `git diff --check`, and claim search for unqualified CMO autonomy language.
- No UI files were changed for CMO-5.3, so CMO dashboard, locale, and typecheck commands were not rerun for this task.
- No dedicated markdown/docs lint script is configured: there is no root `package.json`, and `ui/package.json` exposes ESLint but no markdown/docs validation script.

Remaining work moves to CMO-6.2/CMO-6.3 and the agent production-proof/deepening tracks: this task does not implement vendor-specific write adapters for every marketing platform, escalation matrices, decision audit packages, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

### CMO-5.4 Add Approval Timeout Policy

Priority: P0

Status: Complete on 2026-05-23

Objective: enforce policy when HITL approvals are not completed within SLA.

Files in scope:

- HITL/approval modules
- Workflow event wait modules
- Marketing workflow tests

Acceptance tests:

- Timeout can auto-cancel, auto-escalate, or continue in read-only mode based on policy.
- Timeout decisions are auditable.

Completion notes:

- Added `core.marketing.approval_timeouts`, a code-backed CMO approval timeout policy projection for ad campaign launch, ad budget change, email send, content publish, landing-page change, target account list change, crisis/public response, social post targeting, and high-risk copy/pricing/claims actions.
- Timeout outcomes now include `auto_cancel`, `auto_escalate`, `continue_read_only`, `pause_workflow`, and `require_manual_resolution`, with default SLA duration, escalation role, external-write allowance, notification/audit event code, and safe fallback CTA/message.
- Timed-out approval decisions create structured audit evidence with approval/workflow/run/step IDs where available, requested approver/role, created/due/timed-out timestamps, outcome, escalation target, blocked action, external-write allowance, and audit reference.
- Active customer-facing marketing writes fail closed after approval timeout unless the timeout policy explicitly pre-approves external writes after timeout. Shadow/draft/internal-only paths remain read-only.
- Workflow activation rows now expose `approval_timeout_policy` readiness and block approval-sensitive workflow promotion when timeout policy is missing.
- Marketing workflow linting now flags production approval-sensitive steps that do not have either a known default timeout policy or explicit timeout policy metadata.
- `GET /kpis/cmo` now returns `approval_timeout_risk`, a pending/overdue CMO HITL approval projection for marketing/content/sales-domain HITL items.
- Proof commands run: `python -m pytest tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_external_write_completion.py`, `python -m ruff check core\marketing\approval_timeouts.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py core\marketing\external_writes.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_workflow_activation.py`, `python -m compileall core\marketing\approval_timeouts.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py core\marketing\external_writes.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_workflow_activation.py`, and `git diff --check`.
- No UI files were changed for CMO-5.4, so CMO dashboard, locale, Playwright, and typecheck commands were not rerun for this task.

Remaining work moves to CMO-6.2, CMO-6.3, CMO-7.1, and the agent production-proof/deepening tracks: this task does not implement complete escalation matrix UX, vendor-specific write adapters, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

## CMO-WS-6 Governance OS

### CMO-6.1 Add Marketing Policy Manifest

Priority: P0

Status: Complete on 2026-05-23

Objective: define machine-checkable policy for marketing decisions.

Required policy areas:

- Budget thresholds by channel
- High-risk copy categories
- Region and legal constraints
- Approval owners
- Allowed autonomous actions
- Disallowed autonomous actions

Completion notes:

- Added `core.marketing.policy_manifest`, a code-backed CMO marketing policy manifest with policy ID/version, conservative default rules, budget thresholds by channel, audience/list-size and target-account thresholds, high-risk copy/legal/compliance/comparative/competitor/crisis rules, region/legal constraints, allowed/disallowed autonomous actions, required owner roles, and required audit evidence classes.
- Policy evaluation now returns deterministic decisions: `allowed`, `blocked`, `requires_approval`, `requires_escalation`, `read_only_only`, and `missing_policy`, with matched rules, reason, approver/escalation role, required audit evidence, affected workflow/action, and next action CTA.
- Workflow activation rows now expose `marketing_policy` readiness and block promotion when required active/customer-facing write policy is missing or blocking.
- Marketing workflow linting now flags production workflow steps with missing, blocking, or read-only-only policy coverage while preserving non-marketing workflow scope boundaries.
- Active marketing external-write completion now requires both connector write confirmation and policy allowance or satisfied approval/escalation evidence; missing policy, blocked policy, read-only-only policy, or unsatisfied approval/escalation fails closed.
- Approval timeout decisions now include the manifest-derived required policy role where applicable, so timeout audit evidence can point to the required owner/escalation role.
- `GET /kpis/cmo` now returns the active/default `marketing_policy_manifest` and `marketing_policy_summary` projection used by CMO readiness gates.
- Proof commands run: `python -m pytest tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_approval_timeout_policy.py`, `python -m ruff check core\marketing\policy_manifest.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py core\marketing\external_writes.py core\marketing\approval_timeouts.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_external_write_completion.py`, `python -m compileall core\marketing\policy_manifest.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py core\marketing\external_writes.py core\marketing\approval_timeouts.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_external_write_completion.py`, and `git diff --check`.
- No UI files were changed for CMO-6.1, so CMO dashboard, locale, Playwright, and typecheck commands were not rerun for this task.

Remaining work moves to CMO-7.1 through CMO-7.3 and the agent production-proof/deepening tracks: CMO-6.3 now adds the decision audit package; this task still does not implement vendor-specific write adapters, canonical KPI formulas, persistent audit storage/UI, or Social Media/ABM/Competitive Intel production proof.

### CMO-6.2 Add Escalation Matrix

Priority: P0

Status: Complete on 2026-05-23

Objective: define escalation ownership and SLA behavior.

Required behavior:

- Owner map: Growth Lead to CMO to CEO
- SLA timers
- Notification channels
- Fallback behavior

Completion notes:

- Added `core.marketing.escalation_matrix`, a code-backed CMO escalation matrix with policy ID/version, trigger types, severity, primary/backup owner roles, escalation chains, SLA duration, notification channels, fallback outcome, audit event code, next action CTA, and deterministic evaluation decisions.
- Default routes now cover approval timeout, crisis/public response, budget threshold exceeded, connector auth expired, connector degraded, data mapping blocked, backfill failed, missing policy, external write rejected, external write timeout/unknown, high-risk copy, pricing/legal claims, and target account changes.
- Escalation decisions now include structured evidence with event ID/type, workflow/run/step IDs where available, severity, owner/escalation target, SLA/due time, fallback outcome, notification channels, audit event code, and audit reference.
- Approval timeout decisions now include escalation decisions/evidence in both the decision body and timeout audit evidence.
- Marketing policy evaluation now attaches escalation route/evidence for missing policy, budget threshold, target-account, crisis, high-risk, pricing/legal, and approval-sensitive decisions.
- Connector retry/degraded projections and CMO data readiness blockers now expose escalation evidence for connector auth/degradation, data mapping blockers, and failed/blocked backfills.
- Marketing external-write completion now attaches escalation evidence for approval timeout, missing policy, non-write-safe connector state, rejected writes, timeout/unknown writes, and unconfirmed write failures; workflow step outputs preserve this escalation evidence.
- Workflow activation rows now expose `escalation_matrix` readiness and block promotion when an active/write workflow has escalation-sensitive actions but no escalation route.
- Marketing workflow linting now flags production escalation-sensitive steps when their required escalation route is missing.
- `GET /kpis/cmo` now returns `marketing_escalation_matrix` and `marketing_escalation_summary`.
- Tests added/updated for default matrix coverage, approval timeout evidence, crisis routing, finance routing, admin/IT routing, data mapping/backfill routing, missing-policy route, external-write rejection route, legal/pricing route, target-account route, connector/data readiness escalation evidence, workflow activation missing-route blocker, linter missing-route blocker, and unrelated workflow isolation.
- Proof commands run: `python -m pytest tests\unit\test_cmo_escalation_matrix.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_connector_retry_policy.py tests\unit\test_cmo_external_write_completion.py`, `python -m ruff check core\marketing\approval_timeouts.py core\marketing\connector_retry_policy.py core\marketing\data_readiness.py core\marketing\escalation_matrix.py core\marketing\external_writes.py core\marketing\policy_manifest.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_escalation_matrix.py`, and `python -m compileall core\marketing api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_escalation_matrix.py`.
- No UI files were changed for CMO-6.2, so CMO dashboard, locale, Playwright, and typecheck commands were not rerun for this task.

Remaining work moves to CMO-7.1 through CMO-7.3, CMO-8.x, persistent audit storage/UI, and the agent production-proof/deepening tracks: CMO-6.3 now adds the decision audit package; this task still does not implement a full escalation editor UI, vendor-specific write adapters, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

### CMO-6.3 Add Decision Audit Package

Priority: P0

Status: Complete on 2026-05-23

Objective: persist rationale, alternatives, and overridden decisions for major marketing actions.

Required behavior:

- Every budget, publish, campaign, crisis, and high-confidence recommendation action is auditable.
- Human overrides are recorded with actor, timestamp, reason, and replacement action.

Implementation completed:

- Added `core.marketing.decision_audit`, a code-backed CMO decision audit package model with schema version `2026-05-23.cmo-6.3`, deterministic `audit_id`, WORM-ready canonical JSON serialization, input snapshot hashing, source/connector refs, policy/escalation/approval/timeout/write refs, actor identity, rationale, alternatives, risk flags, confidence, final outcome, override fields, and secret/token redaction.
- Policy manifest evaluations now attach `decision_audit`, `decision_audit_ref`, and audit references for policy decisions.
- Escalation matrix evaluations now attach decision audit packages and add audit refs into escalation evidence.
- Approval timeout decisions now attach decision audit packages into the decision and timeout audit evidence.
- External-write handling now creates audit packages for write attempts and final states including confirmation, rejection, timeout/unknown, retry scheduling, idempotent recovery, unconfirmed writes, draft creation, and shadow-only results.
- Connector degraded/failure projections, data mapping blockers, and backfill blockers now attach decision audit refs where they create governance evidence.
- Workflow activation rows now expose decision-audit readiness and workflow-promotion audit packages; production workflows block when decision audit is disabled.
- Marketing workflow linting now flags production customer-facing steps that lack CMO-6.3 decision-audit evidence metadata.
- `GET /kpis/cmo` now exposes `marketing_decision_audit` and `marketing_decision_audit_summary` projections.
- Tests added/updated for stable audit shape, WORM serialization, policy/escalation/timeout/write/promotion audit evidence, override fields, input snapshot/source refs, production lint blocking for missing audit evidence, shadow read-only auditability, and secret redaction.
- Proof commands run: `python -m pytest tests\unit\test_cmo_decision_audit_package.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_marketing_policy_manifest.py tests\unit\test_cmo_escalation_matrix.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_connector_retry_policy.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_workflow_activation.py`, `python -m ruff check core\marketing\decision_audit.py core\marketing\approval_timeouts.py core\marketing\connector_retry_policy.py core\marketing\data_readiness.py core\marketing\escalation_matrix.py core\marketing\external_writes.py core\marketing\policy_manifest.py core\marketing\workflow_activation.py core\marketing\workflow_linter.py api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_decision_audit_package.py tests\unit\test_cmo_marketing_workflow_linter.py`, `python -m compileall core\marketing api\v1\kpis.py workflows\step_types.py tests\unit\test_cmo_decision_audit_package.py tests\unit\test_cmo_marketing_workflow_linter.py`, and `git diff --check`.

Remaining work moves to CMO-7.1 through CMO-7.3, CMO-8.x, persistent audit storage/UI, and the agent production-proof/deepening tracks: this task does not implement an audit log UI, vendor-specific write adapters, canonical KPI formulas, or Social Media/ABM/Competitive Intel production proof.

## CMO-WS-7 KPI Trust Layer

### CMO-7.1 Define Unified KPI Schema

Priority: P0

Objective: canonicalize CMO metrics.

Status: Complete on 2026-05-23

Implementation completed:

- Added `core.marketing.kpi_schema`, a code-backed CMO KPI schema with schema version `2026-05-23.cmo-7.1` and stable definitions for CAC, MQL, SQL, MQL-to-SQL conversion rate, ROAS, pipeline contribution, conversion rates by funnel stage, LTV/CAC, experiment velocity, content performance, email performance, brand sentiment, and ABM intent/account readiness.
- Each KPI definition now carries display name, description, formula, required connector categories/source domains, required field mappings, required backfill categories, refresh TTL, unit, owner role, confidence/freshness rules, missing-data behavior, source lineage refs, and required audit evidence classes.
- Added deterministic KPI evaluation helpers that compute only from structured source facts and otherwise return `ready`, `degraded`, `blocked`, or `unavailable` with value, unit, confidence, formula refs, source refs, missing requirements, freshness status, last computed timestamp, and next action CTA.
- KPI readiness now consumes the CMO-1.1 connector setup projection, CMO-1.2 field mapping/backfill readiness, CMO-2.1 connector contracts, CMO-5.2 degraded/confidence impact, and CMO-6.3 audit refs where available.
- `GET /kpis/cmo` now exposes `unified_cmo_kpi_schema`, `unified_cmo_kpi_results`, and `unified_cmo_kpi_summary` without changing the existing dashboard KPI-card compatibility fields.
- Production paths do not convert source facts into ready KPI values when required connectors, connector contracts, mappings, backfills, or source fields are missing; affected KPI results are blocked/degraded with actionable CTAs instead of using sample/demo/hardcoded proof.
- Tests cover stable KPI definitions, CAC, MQL, SQL, MQL-to-SQL zero denominator behavior, ROAS, pipeline contribution, experiment velocity, conversion rates, stale freshness downgrade, partial-readiness downgrade, missing connector/mapping/backfill blocking, formula/source lineage, all-core-KPI ready evaluation from mapped fresh inputs, and blocked production projection with no connector readiness.
- Proof commands run: `python -m pytest tests\unit\test_cmo_unified_kpi_schema.py`, `python -m ruff check core\marketing\kpi_schema.py api\v1\kpis.py tests\unit\test_cmo_unified_kpi_schema.py`, and `python -m compileall core\marketing\kpi_schema.py api\v1\kpis.py tests\unit\test_cmo_unified_kpi_schema.py`.

Remaining work moves to CMO-7.2 and CMO-7.3: this task does not implement KPI reconciliation checks, report quality gates, persistent KPI history storage, missing production marketing agents, or vendor-specific adapters.

Required metrics:

- CAC
- MQL
- SQL
- ROAS
- Pipeline contribution
- Conversion rates
- Experiment velocity

### CMO-7.2 Add KPI Reconciliation Checks

Priority: P0

Objective: cross-check ad platform totals against CRM-attributed outcomes.

Status: Complete on 2026-05-23

Implementation completed:

- Added `core.marketing.kpi_reconciliation`, a code-backed reconciliation projection with schema version `2026-05-23.cmo-7.2`.
- Reconciliation checks now cover paid spend totals by channel vs campaign-level spend, ad platform conversions vs CRM campaign-attributed MQLs, GA4/web conversion events vs CRM lead creation, email sends/clicks/unsubscribes vs CRM/list engagement, WordPress/CMS content traffic vs GA4 content sessions, ABM target account domains vs CRM and intent-source domains, source currency consistency, source timezone consistency, and stale/partial connector data.
- Each reconciliation result returns reconciliation key, status (`passed`, `warning`, `failed`, `blocked`, `unavailable`), severity, affected KPI keys, compared sources, expected/observed values, absolute/percentage delta, tolerance, confidence/freshness impact, source refs, missing requirements, next action CTA, and decision-audit ref for non-passing checks.
- Unified KPI evaluation now consumes reconciliation results. High-severity failed or blocked reconciliation checks block affected KPIs; medium warning/failed checks degrade affected KPIs and lower confidence. KPI results now expose reconciliation status, refs, and confidence impact.
- `GET /kpis/cmo` now exposes `cmo_kpi_reconciliation_checks` and `cmo_kpi_reconciliation_summary` through the unified CMO KPI projection while preserving the existing dashboard compatibility fields.
- Tests cover matching spend, spend mismatch blocking CAC/ROAS, ad conversion vs CRM mismatch downgrading affected KPIs, GA4 conversion mismatch, email unsubscribe mismatch, content traffic mismatch, ABM domain mismatch, currency mismatch, timezone warning, stale source freshness impact, partial-data warning, missing source blocking, `/kpis/cmo` reconciliation exposure, and failed reconciliation changing KPI status/confidence.
- Proof commands run: `python -m pytest tests\unit\test_cmo_unified_kpi_schema.py tests\unit\test_cmo_kpi_reconciliation.py`, `python -m ruff check core\marketing\kpi_schema.py core\marketing\kpi_reconciliation.py api\v1\kpis.py tests\unit\test_cmo_unified_kpi_schema.py tests\unit\test_cmo_kpi_reconciliation.py`, `python -m compileall core\marketing\kpi_schema.py core\marketing\kpi_reconciliation.py api\v1\kpis.py tests\unit\test_cmo_unified_kpi_schema.py tests\unit\test_cmo_kpi_reconciliation.py`, and `git diff --check`.

Remaining work moves to CMO-7.3: this task does not implement report quality gates, weekly report blocking, persistent KPI history storage, missing production marketing agents, or vendor-specific adapters.

Acceptance tests:

- Tests cover matching totals, mismatched totals, missing attribution, stale sync, and confidence downgrade.

### CMO-7.3 Add Report Quality Gates

Priority: P0

Objective: block weekly CMO report generation when critical fields are missing or confidence is below threshold.

Status: Complete on 2026-05-23

Implementation summary:

- Added `core.marketing.report_quality`, a code-backed report quality gate projection with schema version `2026-05-23.cmo-7.3`.
- Gate coverage now includes `weekly_marketing_report`, `daily_ad_performance`, `monthly_marketing_roi`, `campaign_performance_ad_hoc`, and `executive_board_summary`, with aliases for existing `cmo_weekly` and `campaign_report` report generator types.
- Each gate evaluates required KPI definitions/results from CMO-7.1, reconciliation results from CMO-7.2, connector setup/contracts, field-mapping/backfill readiness, workflow activation mode, policy/escalation/audit readiness, source freshness, confidence floor, approval refs for sensitive/external delivery, and production demo/sample/fallback data blocking.
- Gate output now includes report key/type, status (`pass`, `warning`, `blocked`, `unavailable`), severity, required KPI keys, blocked/degraded KPI keys, failed reconciliation keys, stale/missing source refs, confidence floor and actual confidence, required approval/escalation/audit refs, missing requirements, next action CTA, and safe report mode (`draft_only`, `internal_only`, `deliverable`).
- `GET /kpis/cmo` now exposes `report_quality_gates`, `report_quality_summary`, and the report quality spec alongside unified KPI and reconciliation projections.
- The existing report generator attaches CMO report quality gate metadata and renders a visible quality-gate banner for non-deliverable CMO reports. The scheduled report task now skips external delivery when a CMO report gate is not `deliverable`.
- Tests cover weekly pass, missing CAC/pipeline blockers, missing ad source blockers, monthly ROI reconciliation/confidence blockers, ad-hoc stale optional warning, draft-only mode, demo/hardcoded production denial, failed reconciliation blocking, stale critical source blocking, missing approval for sensitive delivery, `/kpis/cmo` exposure, and delivery helper denial for non-deliverable gates.
- Proof commands run: `python -m pytest tests\unit\test_cmo_unified_kpi_schema.py tests\unit\test_cmo_kpi_reconciliation.py tests\unit\test_cmo_report_quality_gates.py`, `python -m pytest tests\unit\test_report_engine.py -q --tb=short`, `python -m ruff check core\marketing\report_quality.py api\v1\kpis.py core\reports\generator.py core\tasks\report_tasks.py tests\unit\test_cmo_report_quality_gates.py`, `python -m compileall core\marketing\report_quality.py api\v1\kpis.py core\reports\generator.py core\tasks\report_tasks.py tests\unit\test_cmo_report_quality_gates.py`, and `git diff --check`.

Remaining work moves to CMO-8.x, persistent KPI/report/audit storage, and production proof for beta marketing agents: this task does not build a full report rendering engine, persistent report history, Social Media/ABM/Competitive Intel production proof, or vendor-specific adapters.

Acceptance tests:

- Reports fail closed for missing critical KPI fields.
- Reports include confidence and freshness metadata.

## CMO-WS-8 CMO UX Workbench

### CMO-8.1 Build CMO Work Queue

Priority: P0

Objective: give the CMO a daily operating queue for approvals, risks, stale data, connector issues, budget exceptions, crisis alerts, and recommendations.

Status: Complete on 2026-05-23

Implementation summary:

- Added `core.marketing.work_queue`, a deterministic CMO work queue projection with schema version `2026-05-23.cmo-8.1`.
- The queue now derives prioritized operator-visible work items from approval timeout risk, escalations, connector setup/contract failures, field-mapping/backfill blockers, workflow activation blockers, external-write rejected/timeout/unconfirmed states, policy and audit gaps, blocked/degraded KPIs, failed/warning reconciliation checks, report quality gates, production demo/sample blockers, and crisis/public-response risks where available.
- Work items include item ID, type/category, severity, priority score, title/message, affected workflow/capability/KPI/report/connector, owner role, due/SLA timestamp, source refs, audit refs, CTA label/path/action key, status, and created/updated timestamps.
- Priority rules put customer-facing external-write risk first, overdue approvals/escalations ahead of stale optional data, write-blocking policy/audit/connector issues ahead of read-only warnings, and report blockers ahead of report warnings. Related connector setup and connector-contract items are grouped where practical.
- `GET /kpis/cmo` now exposes `cmo_work_queue` and `cmo_work_queue_summary`.
- The CMO dashboard renders the queue before connector/data/workflow readiness sections, with critical/high summary badges, per-item severity/status, owner/affected object/due date, CTA buttons, and a non-deceptive empty state that does not imply stub/demo/unavailable capabilities are production-ready.
- Tests cover connector auth issues, missing mapping/backfill, workflow activation blockers, overdue approvals/escalations, external-write failures, policy/audit gaps, blocked KPIs, failed reconciliation, blocked/warning report gates, prioritization, connector deduping, API exposure, dashboard rendering, and empty state.
- Proof commands run: `python -m pytest tests\unit\test_cmo_work_queue.py -q --tb=short`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `python -m ruff check core\marketing\work_queue.py api\v1\kpis.py tests\unit\test_cmo_work_queue.py`, `python -m compileall core\marketing\work_queue.py api\v1\kpis.py tests\unit\test_cmo_work_queue.py`, `npm --prefix ui run typecheck`, and `git diff --check`.

Remaining work after CMO-8.1 has since moved through CMO-8.2, CMO-8.3, CMO-3.1, CMO-3.2, CMO-3.3, CMO-4.1, and CMO-4.2; open gaps are persistent task/approval storage, beta-agent production proof, and vendor-specific adapters.

Acceptance tests:

- Tests cover prioritization, filters, empty state, connector issue, approval item, and risk item.

### CMO-8.2 Add KPI Drill-Down And Data Lineage

Priority: P0

Objective: every KPI should explain formula, source systems, source rows, last sync, confidence, owner, and reconciliation state.

Status: Complete on 2026-05-23

Implementation summary:

- Added `core.marketing.kpi_drilldown`, a deterministic KPI drill-down/data-lineage projection with schema version `2026-05-23.cmo-8.2`.
- Drill-downs now cover canonical CMO KPI rows from the unified KPI schema, including CAC, MQL, SQL, MQL-to-SQL conversion, ROAS, pipeline contribution, experiment velocity, content performance, email performance, brand sentiment, and ABM intent/account readiness.
- Each drill-down includes KPI key/name/description, current status/value/unit/confidence, formula refs, resolved formula inputs where source facts are available, required source domains/connectors/mappings/backfills, connector refs, field mappings used, backfill state, reconciliation checks affecting the KPI, freshness/TTL state, confidence-impact reasons, missing requirements, blockers/degraders, related work queue item IDs, related report gate IDs, policy/audit refs, owner role, and next action CTA.
- Demo, hardcoded, mock, stub, sample, fallback, or test-double lineage is explicitly marked as not production proof; drill-down rows clear source refs and block production lineage instead of claiming those inputs are trusted.
- `GET /kpis/cmo` now exposes `cmo_kpi_drilldowns` and `cmo_kpi_drilldown_summary`.
- The CMO dashboard renders a compact KPI drill-down table after the work queue and before readiness sections, showing formula, inputs, connector/freshness, confidence, issue, work queue/report refs, and CTA without redesigning the full dashboard.
- Tests cover CAC formula/input/source/confidence/CTA, MQL/SQL lifecycle mapping and CRM refs, zero-denominator MQL-to-SQL explanation, ROAS spend/revenue and reconciliation refs, pipeline opportunity/revenue lineage, stale freshness/confidence impact, missing mapping/backfill blockers, related work queue/report refs, required core KPI coverage, `/kpis/cmo` exposure, dashboard rendering, and demo/mock lineage denial.
- Proof commands run: `python -m pytest tests\unit\test_cmo_kpi_drilldown.py -q --tb=short`, `python -m ruff check core\marketing\kpi_drilldown.py api\v1\kpis.py tests\unit\test_cmo_kpi_drilldown.py`, `python -m compileall core\marketing\kpi_drilldown.py api\v1\kpis.py tests\unit\test_cmo_kpi_drilldown.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, and `git diff --check`.

Remaining work moves to persistent KPI history/storage, deeper KPI drill-down interactions, and production proof for beta marketing agents: this task does not implement full persistent KPI history, report rendering, approval decision persistence, Social Media/ABM/Competitive Intel production proof, or vendor-specific adapters.

Acceptance tests:

- Tests cover drill-down render, missing data, stale data, confidence downgrade, and export.

### CMO-8.3 Improve Approval Review UX

Priority: P0

Objective: make approvals useful for real marketing decisions.

Status: Complete on 2026-05-23

Implementation summary:

- Added `core.marketing.approval_review`, a deterministic approval-review projection with schema version `2026-05-23.cmo-8.3`.
- Approval reviews cover campaign launch, ad budget change, content publish, email send, landing page change, target account list change, crisis/public response, high-risk copy/pricing/legal claim, and workflow promotion approval contexts.
- Each review row includes approval/workflow/run/step refs, action type/status, requester/agent/approver refs, created/due/timeout state, preview payload, before/after diff, budget impact, audience/list impact, risk flags, source and connector refs, agent rationale, policy/escalation/timeout/write/audit refs, rollback/stop plan, allowed reviewer actions, blockers, related work queue item IDs, and CTA.
- Approval decision helpers fail closed: approve is blocked when the policy result is missing or blocking, write readiness is unsafe, timeout policy requires manual resolution, or required audit evidence is missing. Reject/override/request-change/escalate/pause request shapes require an explicit reason, and override also requires a replacement action.
- `GET /kpis/cmo` now exposes `cmo_approval_reviews` and `cmo_approval_review_summary`, links approval-timeout decisions to approval-review IDs, and lets CMO work queue approval items point at their review payloads.
- The CMO dashboard now renders compact approval review cards after the work queue and before KPI lineage, showing status, owner/due date, risk flags, budget/audience impact, diff, rationale, policy/timeout/write/audit safeguards, rollback/stop plan, allowed actions, related work queue refs, and CTA without redesigning the dashboard.
- Tests cover preview/diff/rationale/source refs, campaign budget/audience impact and rollback plan, content/email brand/legal risk flags, crisis escalation/timeout/policy refs, fail-closed behavior for missing policy, unsafe write readiness, manual-timeout resolution, missing audit evidence, reject/override request shapes, allowed actions by status/risk, work queue review links, `/kpis/cmo` approval review summary exposure, and dashboard rendering.
- Proof commands run: `python -m pytest tests\unit\test_cmo_approval_review.py -q --tb=short`, `python -m ruff check core\marketing\approval_review.py core\marketing\work_queue.py api\v1\kpis.py tests\unit\test_cmo_approval_review.py`, `python -m compileall core\marketing\approval_review.py core\marketing\work_queue.py api\v1\kpis.py tests\unit\test_cmo_approval_review.py`, `npm --prefix ui test -- CMODashboard`, `npm --prefix ui test -- i18n_coverage_tripwire`, `npm --prefix ui run typecheck`, and `git diff --check`.

Remaining work moves to CMO-9.x pilot/test hardening, persistent approval/task/audit storage, deeper approval decision UI, beta-agent production proof, and vendor-specific write adapters. This task does not implement a full persistent approvals system or bypass HITL, policy, timeout, escalation, external-write, or audit safeguards.

Required behavior:

- Before/after preview
- Budget impact
- Audience impact
- Brand/legal risk flags
- Source refs
- Agent rationale
- Policy result
- Timeout
- Rollback/stop action

Acceptance tests:

- Tests cover approve, reject, override, timeout, and audit record.

## CMO-WS-9 Test Hardening And Pilot Proof

### CMO-9.1 Per-Agent Contract Tests

Priority: P0

Status: Complete on 2026-05-24

Objective: define input/output contracts for every marketing agent action.

Implementation summary:

- Added `core.marketing.agent_contracts`, a deterministic CMO agent contract projection with contract version `2026-05-24.cmo-9.1`, required output keys, truthful capability status, aliases, and production-readiness blockers.
- Contract inventory now covers Campaign Pilot, Content Factory, Email Marketing / `email_agent`, Brand Monitor, SEO Strategist, CRM Intelligence, Social Media, ABM / `abm_agent`, and Competitive Intel.
- Campaign Pilot and Content Factory now emit the shared CMO contract fields for approval/HITL state, policy result, audit reference, source refs, degraded or blocked reasons, external-write confirmation, and production status.
- Campaign Pilot active write paths now fail closed when connector write confirmation is absent; Content Factory publish/schedule paths remain draft/approval-gated until external delivery is explicitly confirmed.
- Marketing workflow linting recognizes the `email_agent` beta surface while continuing to block stub or unavailable agents from active production workflows.

Validation completed:

- `tests/unit/test_cmo_agent_contracts.py` covers implemented/beta happy paths, invalid input, HITL/policy paths, degraded connector state, audit/source/confidence shape, external-write safety, and stub/unavailable production blocking.
- Focused regression coverage was run with workflow linter and workflow activation tests to prove stub/unavailable production workflows remain blocked.

Definition of done:

- All 9 CMO agents have contract tests.
- Tests cover happy path, invalid input, HITL path, connector failure, and degraded output.

Remaining work:

- CMO-9.1 did not implement production agents; CMO-3.1, CMO-3.2, and CMO-3.3 now add Social Media, ABM, and Competitive Intel as beta/non-production.
- CMO-9.1 did not deepen Brand Monitor, SEO Strategist, or CRM Intelligence. CMO-4.1 now deepens Brand Monitor to beta; CMO-4.2 now deepens SEO Strategist to beta; CMO-4.3 now deepens CRM Intelligence to beta.
- CMO-9.4 now adds code-backed pilot proof packaging; live shadow-run evaluation metrics, persistent pilot evidence history, and pilot go/no-go execution remain outside CMO-9.1.

### CMO-9.2 End-To-End CMO Scenario Tests

Priority: P0

Status: Complete on 2026-05-24

Objective: prove the CMO operating-system projections work together across readiness, governance, KPI/report quality, workbench, approval, write-safety, audit, and agent-contract surfaces without network, LLM, or vendor dependencies.

Implementation summary:

- Added `tests/unit/test_cmo_e2e_scenarios.py`, a deterministic E2E-style scenario suite that composes the same CMO projection builders used by API/dashboard paths.
- Weekly marketing review scenario proves blocked KPI/reconciliation/report gates prevent trusted report delivery, preserve `draft_only` safe mode, create work queue items, and expose KPI drill-down lineage for blocked CAC.
- Campaign launch scenario proves shadow/non-promoted workflows do not become active, active launch requires write-ready connectors, valid data readiness, policy approval, approval review, decision-audit evidence, linter-safe metadata, and confirmed external write before completion.
- Crisis response scenario proves public response remains unavailable in workflow activation, requires escalation, timeout/approval review, policy result, decision-audit evidence, and fails closed for public writes without approval/escalation safeguards.
- ABM sprint scenario now proves ABM is a beta first-class core marketing agent that still blocks production workflow lint/activation unless connector, policy, approval, audit, write-confirmation, and pilot-proof gates pass; target/demo/shadow paths remain explicitly non-production.
- Content production-to-publish scenario proves Content Factory can produce contract-shaped draft output, publishing requires approval/policy/audit/write-safe connector state, and unconfirmed publish writes cannot complete as published.

Validation completed:

- `python -m pytest tests\unit\test_cmo_e2e_scenarios.py tests\unit\test_cmo_agent_contracts.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_approval_review.py tests\unit\test_cmo_approval_timeout_policy.py tests\unit\test_cmo_unified_kpi_schema.py tests\unit\test_cmo_kpi_reconciliation.py tests\unit\test_cmo_report_quality_gates.py tests\unit\test_cmo_work_queue.py tests\unit\test_cmo_kpi_drilldown.py -q --tb=short` passed.
- `python -m ruff check tests\unit\test_cmo_e2e_scenarios.py` passed.
- `python -m compileall tests\unit\test_cmo_e2e_scenarios.py` passed.

Required scenarios:

- Weekly marketing review
- Campaign launch
- Crisis response
- ABM sprint
- Content production to approval to publish

Remaining work:

- CMO-9.2 does not implement missing agents; CMO-3.1, CMO-3.2, and CMO-3.3 later add Social Media, ABM, and Competitive Intel as beta/non-production.
- CMO-9.2 does not add vendor-specific live integrations, persistent scenario history, or live pilot execution.
- CMO-9.3 now covers chaos/failure tests across outage, stale-data, malformed-payload, timeout, overspend race, and duplicate replay cases; CMO-9.4 now packages pilot proof status without replacing live tenant rollout.

### CMO-9.3 Chaos And Failure Tests

Priority: P0

Status: Complete on 2026-05-24

Objective: prove CMO readiness/governance/KPI/report/workbench/agent-contract surfaces fail closed or degrade explicitly under deterministic connector, data, approval, policy, write, audit, reconciliation, and report failures.

Implementation summary:

- Added `tests/unit/test_cmo_chaos_failure_modes.py`, a deterministic chaos/failure suite that composes connector retry/degraded policy, connector contracts, setup, data readiness, workflow activation, workflow linter, policy manifest, escalation matrix, approval timeout policy, external write completion, decision audit, KPI schema/reconciliation/report gates, work queue, approval review, and agent contract projections.
- Covered connector outage, auth expiry, insufficient scope, rate limiting, quota exhaustion, stale/partial data windows, malformed payloads, approval timeout, budget overspend race, duplicate event replay, timeout/unknown writes, rejected writes, idempotent recovery, missing policy/escalation/audit evidence, report quality gate failure, and KPI reconciliation failure.
- Adjusted connector-contract write-step tests so active write-confirmation assertions explicitly satisfy marketing policy approval before testing missing or present external-write confirmation.

Validation:

- `python -m pytest tests\unit\test_cmo_chaos_failure_modes.py -q --tb=short`
- `python -m pytest tests\unit\test_cmo_chaos_failure_modes.py tests\unit\test_cmo_e2e_scenarios.py tests\unit\test_cmo_marketing_connector_contracts.py tests\unit\test_cmo_connector_retry_policy.py tests\unit\test_cmo_external_write_completion.py tests\unit\test_cmo_workflow_activation.py tests\unit\test_cmo_marketing_workflow_linter.py tests\unit\test_cmo_report_quality_gates.py tests\unit\test_cmo_kpi_reconciliation.py -q --tb=short`

Remaining work:

- CMO-9.3 does not add live vendor integrations, persistent chaos-run history, or live pilot execution.
- CMO-9.3 does not implement production agents; CMO-3.1, CMO-3.2, and CMO-3.3 later add Social Media, ABM, and Competitive Intel as beta/non-production.
- CMO-9.4 now adds the pilot proof package; complete CMO production readiness still requires real-vendor pilot evidence, beta-agent production proof, and persistent rollout operations.

Required scenarios:

- Connector outages
- Stale data windows
- Malformed payloads
- Approval timeout
- Budget overspend race
- Duplicate event replay

### CMO-9.4 Pilot Tenant Proof

Priority: P0

Status: Complete on 2026-05-24

Objective: add a code-backed pilot proof package that shows what is and is not proven for a real-vendor, vendor-sandbox, demo, test-double, or unknown CMO tenant without treating mocks, stubs, or sample data as production readiness.

Implementation summary:

- Added `core.marketing.pilot_proof`, a deterministic proof package with proof version `2026-05-24.cmo-9.4`, stable proof IDs, redacted evidence bundle serialization, readiness scoring, proof status, proven/unproven capabilities, blockers, risks, evidence refs, source refs, report refs, audit refs, test evidence refs, and next actions.
- Pilot proof evaluates connector setup, connector contracts, data mapping/backfill, workflow activation, workflow linting, policy, escalation, approval timeout readiness, external-write confirmation, decision-audit evidence, unified KPIs, reconciliation, report quality gates, work queue blockers, KPI drill-down/data lineage, approval review readiness, agent contract status, and scenario/chaos test evidence.
- Demo environments return `demo_only`; test-double/mock environments return `test_only`; neither can produce production-passed proof.
- Vendor-sandbox proof can pass only sandbox criteria and never sets a real-vendor production claim. Real-vendor proof can pass only when all critical criteria pass.
- Social Media, ABM, Competitive Intel, Brand Monitor, and SEO Strategist are now first-class beta CMO capabilities but remain explicitly unproven for production until real-vendor/pilot proof exists.
- `GET /kpis/cmo` now exposes `cmo_pilot_proof`, `cmo_pilot_proof_summary`, and the redacted pilot evidence bundle alongside existing CMO readiness surfaces.

Validation completed:

- `python -m pytest tests\unit\test_cmo_pilot_proof.py -q --tb=short` passed.
- Focused CMO regression subset covering agent contracts, approval review/timeouts, chaos/failure, connector contracts/retry/setup, decision audit, E2E scenarios, escalation, external writes, KPI drill-down/reconciliation/schema, marketing policy, workflow linter/activation, report quality, work queue, and pilot proof passed.
- `python -m ruff check core\marketing\pilot_proof.py api\v1\kpis.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_marketing_connector_setup.py` passed.
- `python -m compileall core\marketing\pilot_proof.py api\v1\kpis.py tests\unit\test_cmo_pilot_proof.py tests\unit\test_cmo_marketing_connector_setup.py` passed.

Definition of done:

- Pilot proof package exists in code and is deterministic/test-covered.
- Demo/test-double proof cannot be mistaken for production proof.
- Real-vendor, vendor-sandbox, demo, test-only, and unknown states are clearly distinguished.
- Proof output includes status, score, proven/unproven capabilities, blockers, risks, refs, and next actions.
- Missing production agents and beta agents without pilot proof remain explicitly unproven.
- Serialization redacts secrets/tokens.

Remaining work:

- CMO-9.4 is proof packaging, not live pilot rollout; real customer or vendor-sandbox credentials, persistent pilot evidence storage, monitoring, and pilot go/no-go operations remain to be done.
- Complete CMO autonomy still requires beta-agent production pilot proof, and live vendor adapter rollout where needed.

## Production Proof (post-beta)

The Production Proof track exists because every CMO marketing pillar in
`core/agents/marketing` now has first-class deterministic beta code with
policy/approval/audit/external-write-confirmation gates, *but no
real-vendor or vendor-sandbox proof has been recorded in the worktree*.
A capability is only honestly "production" when:

1. The deterministic code path is complete (CMO-WS-1 → CMO-WS-9 + CMO-WS-3
   + CMO-WS-4 ✅).
2. A pilot tenant supplies real or vendor-sandbox credentials, configured
   connectors, mapping, backfill, and consents.
3. A strict, code-backed proof gate verifies the evidence and refuses to
   mark proof passed for demo / sample / mock / test-double inputs.
4. The proof artefact (signed, redacted) is stored alongside the report,
   audit, and KPI artefacts that back it.

Production Proof tasks introduce step 3 first (so the moment a pilot lands
in step 2, step 4 has somewhere to land).

### CMO-PROD-1 Real-Vendor Pilot Evidence Path For Weekly Marketing Report

Priority: P0
Status (2026-05-24): Code-backed validation path complete; **no live or
vendor-sandbox evidence is present in the worktree**, so no production
claim is allowed today.

Objective: prove the read-only `weekly_marketing_report` workflow on real
connected CRM + Ads + Analytics + Email sources for one pilot tenant,
through a strict evidence model that fails closed for demo / test-double
/ vendor-sandbox shortcuts.

Files in scope:

- `core/marketing/weekly_report_pilot_proof.py` (new module)
- `scripts/validate_weekly_report_pilot_proof.py` (new CLI / callable)
- `api/v1/kpis.py` (wires the projection into `/kpis/cmo`)
- `tests/unit/test_cmo_weekly_report_pilot_proof.py` (acceptance tests)
- `docs/STRICT_CMO_AGENTIC_EXECUTION_BACKLOG_2026-05-23.md` (this section)

Required behavior — delivered:

- `WeeklyReportPilotEvidence` dataclass capturing tenant/company IDs,
  environment type, connector evidence, mapping evidence, backfill
  evidence, KPI results, reconciliation checks, report-quality gates,
  report artifact refs, decision-audit refs, source refs, generated_at,
  and a free-form `source_context`.
- `evaluate_weekly_marketing_report_proof()` returns a deterministic
  redacted verdict with `proof_status`, `production_claim_allowed`,
  `real_vendor_claim_allowed`, blockers, risks, proven capabilities,
  evidence refs, next actions, and a stable `proof_id`.
- Validation rules enforce environment honesty:
  - `demo` → `demo_only`, `production_claim_allowed=False`;
  - `test_double` → `test_only`, `production_claim_allowed=False`;
  - `vendor_sandbox` → `sandbox_proven` / `partial`, never real-vendor;
  - `real_vendor` → `passed` only when CRM/Ads/Analytics/Email are
    configured + read-ready, every required mapping is `valid`, every
    required backfill category is `completed`, every required weekly
    report KPI is present and not blocked, every reconciliation check
    is `pass` (or warning at most), the `weekly_marketing_report`
    quality gate is `pass`, and at least one report artifact + decision
    audit ref + real source-lineage ref is attached;
  - mock / test-double markers on any connector evidence row block.
- Bundle serializer (`serialize_weekly_marketing_report_evidence_bundle`)
  redacts any map key containing `secret`, `token`, `password`,
  `api_key`, `apikey`, `authorization`, `credential`, or `private_key`.
- CLI/callable validator at `scripts/validate_weekly_report_pilot_proof.py`
  reads JSON evidence from a file or STDIN, prints a human-readable
  verdict (or `--format json` redacted bundle), and exits with code
  `0` for `passed` / `sandbox_proven`, `2` for `partial`, `3` for
  `blocked` / `demo_only` / `test_only` / `unavailable`.
- `/kpis/cmo` now exposes `weekly_report_pilot_proof`,
  `weekly_report_pilot_proof_summary`, and
  `weekly_report_pilot_evidence_bundle` alongside the broader CMO
  pilot proof.

Acceptance tests run (24 passing):

- demo evidence returns `demo_only` and blocks production claim
- test-double evidence returns `test_only` and blocks production claim
- unknown environment blocks production claim
- mock/test-double connector marker blocks proof
- vendor-sandbox evidence returns sandbox/partial only, never real-vendor
- vendor-sandbox missing optional refs degrades to partial
- real-vendor evidence with all critical criteria passes
- real-vendor missing each of CRM / Ads / Analytics / Email blocks proof
- real-vendor missing a required field mapping blocks proof
- real-vendor missing a required backfill category blocks proof
- failed KPI reconciliation blocks proof
- blocked report-quality gate blocks proof
- missing audit/report artifact refs block real-vendor proof
- demo/test-double source markers on `source_refs` block proof
- proof bundle serializer redacts `api_key` / `authorization` / `password`
- summary returns proof_status + next action CTA + schema version
- projection wraps proof + summary + redacted bundle
- CLI returns exit 0 for real-vendor passed
- CLI returns exit 3 for demo evidence with blockers in stdout
- CLI `--format json` output is secret-redacted
- CLI reads evidence from STDIN

Definition of done:

- Strict code-backed validation path exists and is exercised by tests.
- Proof cannot be satisfied by demo / test-double / sample / mock
  inputs.
- Missing evidence produces explicit blockers and next actions.
- Secret / token redaction is tested.
- Backlog clearly distinguishes (a) beta feature completeness across
  CMO-WS-1 → CMO-WS-9 + CMO-WS-3 / 4; (b) proof validation path
  complete via CMO-PROD-1; (c) live / vendor-sandbox proof present or
  absent — currently **absent**.

Live evidence status (2026-05-24):

- No real-vendor evidence file is present in the worktree.
- No vendor-sandbox evidence file is present in the worktree.
- `core/marketing/weekly_report_pilot_proof.PILOT_PROOFS` does not
  exist; once a pilot lands, a tenant-specific JSON evidence file
  (or hydrated `WeeklyReportPilotEvidence`) should be passed through
  the validator before any "weekly report production-proven" claim is
  made externally.

Remaining work:

- Acquire real-vendor or vendor-sandbox credentials for HubSpot or
  Salesforce + Google Ads / Meta Ads / LinkedIn Ads + GA4 + Mailchimp
  or SendGrid for at least one pilot tenant.
- Persist evidence bundles and proof verdicts in durable storage
  (database + WORM audit log) so a future UI can render the bundle
  without re-running the validator from session state.
- Wire the deterministic report-generation path (`core/reports/generator.py`,
  `core/tasks/report_tasks.py`) to attach `report_artifact_refs` and
  `decision_audit_refs` for weekly reports as they are produced.
- Add scheduled CI / cron run of the validator against the persisted
  evidence to detect when a pilot tenant's proof degrades.

### CMO-PROD-2 Persist Weekly Report Pilot Evidence And Wire Report Generator

Priority: P0
Status (2026-05-24): Persistence path and report-task wiring complete in
code; **no live or vendor-sandbox evidence rows exist in any tenant DB**.
The first persisted row will only land when a configured tenant actually
runs the CMO weekly report against connected systems.

Objective: give CMO-PROD-1 a durable home. Each successful CMO weekly
report run hydrates a `WeeklyReportPilotEvidence` bundle from the same
`/kpis/cmo` projections the report itself uses, runs the CMO-PROD-1
validator, redacts secrets, and persists the verdict in a dedicated
append-only table. The CMO KPI endpoint exposes the latest persisted
verdict so dashboards stop relying on ad-hoc validator runs.

Files in scope:

- `core/models/weekly_report_pilot_proof.py` (new ORM model)
- `migrations/versions/v4_9_17_weekly_report_pilot_proof.py` (new
  Alembic migration; revision id `v4917_weekly_report_proof`, 25 chars,
  on top of `v4916_merge_p0_heads`)
- `core/marketing/weekly_report_pilot_persistence.py` (new persistence
  helper, evidence builder, sync wrapper for Celery)
- `core/tasks/report_tasks.py` (wires the persistence call after a
  successful `cmo_weekly` / `weekly_marketing_report` run)
- `api/v1/kpis.py` (adds `latest_weekly_report_pilot_proof` and
  `latest_weekly_report_pilot_proof_summary` to `/kpis/cmo`)
- `tests/unit/test_cmo_weekly_report_pilot_persistence.py` (20 tests)

Required behavior — delivered:

- New ORM model `WeeklyReportPilotProof` (table
  `weekly_report_pilot_proofs`) with: tenant_id, company_id, proof_id,
  environment_type, proof_status, production_claim_allowed,
  real_vendor_claim_allowed, readiness_score, evaluated_at,
  evidence_bundle (JSONB), verdict (JSONB), blockers, next_actions,
  report_artifact_refs, decision_audit_refs, created_at, updated_at.
- Idempotent migration: `CREATE TABLE IF NOT EXISTS` plus
  `(tenant_id, company_id, evaluated_at)` composite index for the
  "latest verdict per tenant/company" lookup.
- `persist_weekly_report_pilot_proof()` accepts evidence (dict or
  `WeeklyReportPilotEvidence`), invokes the CMO-PROD-1 validator,
  redacts secrets/tokens/API keys, and writes a single new row per
  evaluation. It never UPDATEs; rows are an append-only verdict log.
- `latest_weekly_report_pilot_proof()` returns the newest persisted row
  for a tenant + optional company, ordered by `evaluated_at DESC`.
- `build_weekly_report_evidence_from_report_output()` turns a
  successful weekly-report run + the `/kpis/cmo` payload that fed it
  into a `WeeklyReportPilotEvidence` bundle, attaching
  `report_artifact_refs` (artifact id + path + format) and
  `decision_audit_refs` (`weekly_report_delivered` plus any
  `required_approval_audit_refs` from the report quality gate).
- `persist_weekly_report_pilot_proof_from_report_output_sync()` is the
  Celery-safe entry point. It refuses to persist when the tenant id is
  not a UUID (e.g., the demo `"default"` literal), swallows DB errors
  with a `weekly_report_pilot_proof_persist_failed` log entry, and
  returns the verdict summary so the report task can include it in
  its return value.
- `core/tasks/report_tasks.generate_report` calls the sync wrapper only
  for `cmo_weekly` / `weekly_marketing_report` runs and includes the
  resulting summary in the task return payload.
- `/kpis/cmo` exposes `latest_weekly_report_pilot_proof` (full
  serialised row, redacted) and `latest_weekly_report_pilot_proof_summary`
  (proof_id, environment_type, proof_status, production_claim_allowed,
  readiness_score, blocker count, next-action CTA). The CMO-PROD-1
  ad-hoc projection from CMO-PROD-1 remains alongside the persisted
  fields so existing consumers don't need to migrate.
- Both layers redact: the validator redacts in-flight, the persistence
  helper redacts again before INSERT, and the `serialize_persisted_proof`
  projection returns the redacted JSONB back to the API surface.

Acceptance tests run (20 passing):

- persists demo evidence as `demo_only` with `production_claim_allowed=False`
- persists test-double evidence as `test_only` with `production_claim_allowed=False`
- persists vendor-sandbox evidence as `sandbox_proven`/`partial`, not real-vendor
- persists real-vendor `passed` only when all CMO-PROD-1 criteria met
- persists `blocked` when required report_artifact_refs missing
- persists `blocked` when required decision_audit_refs missing
- persists `blocked` when a required connector category is missing
- stored evidence/verdict/blockers JSON redacts `api_key` /
  `authorization` / `credential.password` triples
- `latest_weekly_report_pilot_proof()` returns the newest row when two
  exist for the same tenant + company
- evidence builder attaches report artifact + audit refs from a
  successful weekly run
- evidence builder marks `demo` env when `report_data.demo=True`
- sync wrapper skips persistence when tenant id is not a UUID
  (e.g., the literal `default` used in non-tenant tests)
- sync wrapper persists and returns summary when DB available
- sync wrapper persists `blocked` verdict when evidence is incomplete
- sync wrapper swallows DB errors and returns `None` rather than
  failing the Celery report task
- `report_tasks.generate_report` calls the persistence wrapper on
  `cmo_weekly` runs and returns the summary in the task result
- `report_tasks.generate_report` does NOT call the persistence wrapper
  for non-weekly reports (e.g., `cfo_daily`)
- `/kpis/cmo` helper returns `None` when no persisted row exists
- `/kpis/cmo` helper returns the redacted serialised row + summary
  when a persisted row exists
- summariser handles `None` input gracefully

Definition of done:

- Verdicts are durably representable and test-covered.
- Report generation/task path attaches artifact/audit refs into the
  evidence bundle on successful weekly runs.
- CMO-PROD-1 remains the only authority for what counts as
  `production_claim_allowed=True`.
- Missing evidence persists as `blocked` / `unavailable` rather than
  silently succeeding.
- `/kpis/cmo` exposes the latest persisted proof + summary.

Live evidence status (2026-05-24):

- Migration is included but **has not been deployed to any tenant DB
  in this worktree**. Running `alembic upgrade head` against a CI or
  staging Postgres will create the table.
- No row has been INSERTed by the production code path; the
  `weekly_report_pilot_proofs` table will be empty until a tenant runs
  the CMO weekly report with credentials.
- The Celery report task already calls the persistence wrapper, but it
  short-circuits whenever the tenant id is not a UUID (e.g., during
  the existing `default` / demo path in `report_tasks` tests), so no
  fake / demo evidence ever lands in the table.

Remaining work / hand-off to CMO-PROD-3:

- A first pilot tenant must actually run the weekly report on
  configured CRM + Ads + Analytics + Email connectors so the first
  persisted verdict materialises. CMO-PROD-3 should walk one
  vendor-sandbox tenant end-to-end (connector setup → mapping →
  backfill → weekly run → persisted `sandbox_proven` verdict in
  `weekly_report_pilot_proofs`).
- A WORM audit-trail link from `audit_log` to
  `weekly_report_pilot_proofs.proof_id` would let auditors trace each
  verdict back to the report run that produced it. Currently the link
  is implicit via `report_artifact_refs.artifact_id` matching the
  report-task `report_id`.
- A minimal CMO dashboard panel that surfaces
  `latest_weekly_report_pilot_proof_summary` is out of scope; the
  data is on `/kpis/cmo` ready to be rendered.

### CMO-PROD-3 Vendor-Sandbox Pilot Walk-Through For Weekly Marketing Report

Priority: P0
Status (2026-05-24, ops run 1): **Code path complete. Live sandbox proof
BLOCKED on credentials and a running DB.** No row has been inserted into
`weekly_report_pilot_proofs`. The most recent CMO-PROD-3-OPS run on
2026-05-24 confirmed the blocked status; see "Last CMO-PROD-3-OPS run"
below for verbatim evidence of which prerequisites are still missing.

Re-run `python scripts/run_weekly_report_sandbox_pilot.py` after
populating the env vars below to materialise the first `sandbox_proven`
verdict.

#### Last CMO-PROD-3-OPS run

| Field | Value |
|---|---|
| Run date | 2026-05-24 |
| Worktree | `C:\tmp\agenticorg-cmo-1-1` |
| `alembic upgrade head` | **Failed** — no `AGENTICORG_DB_URL`; default URL pointed at `localhost:5432`, no server listening. Migration `v4917_weekly_report_proof` was **not applied**. |
| Preflight CLI exit code | `3` (blocked) |
| Tenant id | missing (`AGENTICORG_CMO_SANDBOX_TENANT_ID` not set) |
| Company id | not set |
| Missing connector categories | CRM, Ads, Analytics, Email |
| `weekly_report_pilot_proofs` rows inserted by this run | **0** |
| Fake / synthetic evidence inserted | **None.** Runner refused to invent data. |
| `production_claim_allowed` claimed | always `False` |
| `real_vendor_claim_allowed` claimed | always `False` |
| Secrets / tokens printed | none (only env-var names appear in output) |

#### Local DB setup status (CMO-PROD-3-DB-LOCAL, 2026-05-24)

A local Postgres has been provisioned on this workstation so the CMO-PROD-3
preflight no longer fails on DB connectivity. The DB blocker is resolved;
remaining blockers are QA-owned (sandbox tenant + per-category connector
env vars).

| Field | Value |
|---|---|
| DB setup method | New Docker container, repo `pgvector/pgvector:pg16` image (matches `docker-compose.yml` for parity). No existing project DB or container was modified or deleted; an unrelated stopped `grantex-postgres-1` container was left untouched. |
| Container name | `agenticorg-cmo-postgres` |
| Host port | `5433` (5432 reserved by the unrelated stopped container; using 5433 avoids any future conflict) |
| `AGENTICORG_DB_URL` shape (password redacted) | `postgresql+asyncpg://agenticorg:[REDACTED]@localhost:5433/agenticorg` |
| Postgres extensions enabled | `uuid-ossp`, `vector` (one-shot `CREATE EXTENSION IF NOT EXISTS …` on the fresh DB; required by repo migrations) |
| Schema bootstrap method | `BaseModel.metadata.create_all` via `import core.models` (the same path `tests/unit/conftest.py` uses for fresh DBs), then `alembic stamp head` to record the version. This is the documented "fresh dev DB" path because the v4.x migration chain expects `init_db()`-style baseline tables (e.g., `tenants`) to pre-exist before `v4_8_6_knowledge_embedding` runs. |
| Alembic migration status | `alembic_version.version_num = v4917_weekly_report_proof` (head). No pending migrations. |
| `weekly_report_pilot_proofs` table | **exists** with the 18 columns + 4 indexes from migration `v4917_weekly_report_proof`. Row count = **0**. |
| Fake / synthetic rows inserted | **None.** The DB is empty by design. |
| CMO-PROD-3 preflight result after DB setup | exit code `3` (still blocked), but the **`database` blocker is gone**. Remaining blockers are: `tenant` (1) + `connector` × 4 categories (CRM, Ads, Analytics, Email). |
| Remaining QA-owned setup | Preferred path: tenant-scoped `ConnectorConfig` rows for CRM, Ads, Analytics, and Email with usable status/health, `config.proof_scope=vendor_sandbox`, no `local_test_only`, and no `mock_or_test_double`. Env vars remain local/dev fallback only. |
| Acceptance checks | `pytest` on the three CMO-PROD-3 test files → **62 passed** after CMO-PROD-3E. `ruff check` on the three CMO-PROD-3 modules → all checks passed. `compileall` → exit 0. `git diff --check` → clean. |

QA command sequence to materialise the first `sandbox_proven` row from
this workstation:

```
# 1. Export the DB URL this CMO-PROD-3-DB-LOCAL pass created.
export AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev_password@localhost:5433/agenticorg

# 2. Pick / create a sandbox tenant UUID and export it (optionally company too).
export AGENTICORG_CMO_SANDBOX_TENANT_ID=<sandbox-tenant-uuid>
# optional:
export AGENTICORG_CMO_SANDBOX_COMPANY_ID=<sandbox-company-uuid>

# 3. Preferred: configure tenant ConnectorConfig rows through the normal
#    connector setup path for CRM, Ads, Analytics, and Email.
#    Required row metadata:
#    - status is configured/active/healthy/ready equivalent
#    - health_status is healthy/ready/connected/unknown
#    - config.cmo_category is CRM, Ads, Analytics, or Email
#    - config.proof_scope=vendor_sandbox
#    - config.local_test_only=false or absent
#    - config.mock_or_test_double=false or absent
#    Store real sandbox secrets only through ConnectorConfig credential storage.
#
#    Local/dev fallback only: populate ONE env connector group per missing
#    DB category from SANDBOX_CONNECTOR_OPTIONS.

# 4. Confirm preflight is now READY (exit 0):
python scripts/run_weekly_report_sandbox_pilot.py --preflight-only

# 5. Run the pilot, redacted JSON output, persist verdict:
python scripts/run_weekly_report_sandbox_pilot.py --format json > sandbox-proof.json

# 6. Verify in DB (host port 5433 from the CMO-PROD-3-DB-LOCAL container):
docker exec agenticorg-cmo-postgres psql -U agenticorg -d agenticorg -c \
  "SELECT proof_id, environment_type, proof_status, production_claim_allowed, \
          real_vendor_claim_allowed, readiness_score, evaluated_at \
   FROM weekly_report_pilot_proofs ORDER BY evaluated_at DESC LIMIT 1;"
```

Missing env vars (verbatim from preflight output, names only — no
values are ever printed):

  - `AGENTICORG_DB_URL`
  - `AGENTICORG_CMO_SANDBOX_TENANT_ID`
  - `AGENTICORG_CMO_SANDBOX_HUBSPOT_ACCESS_TOKEN`
    *(or the Salesforce 4-var alternative)*
  - `AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_DEVELOPER_TOKEN`,
    `_REFRESH_TOKEN`, `_CUSTOMER_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`
    *(or one of the Meta / LinkedIn Ads alternatives)*
  - `AGENTICORG_CMO_SANDBOX_GA4_PROPERTY_ID`, `_REFRESH_TOKEN`,
    `_CLIENT_ID`, `_CLIENT_SECRET`
  - `AGENTICORG_CMO_SANDBOX_SENDGRID_API_KEY`, `_SENDER`
    *(or the Mailchimp 3-var alternative)*

Acceptance checks rerun by this CMO-PROD-3-OPS pass:

- `python -m pytest tests/unit/test_cmo_weekly_report_sandbox_runner.py
   tests/unit/test_cmo_weekly_report_pilot_persistence.py
   tests/unit/test_cmo_weekly_report_pilot_proof.py --no-cov -q` → **62 passed**.
- `python -m ruff check` on the three CMO-PROD-3 modules → all checks passed.
- `python -m compileall` on the three CMO-PROD-3 modules → exit 0.
- `git diff --check` → clean (CRLF informational warnings only).

#### CMO-PROD-3E ConnectorConfig Sandbox Preflight

Status: Complete in code on 2026-05-24; live `sandbox_proven` remains blocked until QA replaces local/preflight-only rows with real vendor-sandbox ConnectorConfig rows.

Implementation summary:

- `discover_sandbox_pilot_config()` now prefers tenant-scoped `ConnectorConfig` rows for `AGENTICORG_CMO_SANDBOX_TENANT_ID`, maps CRM, Ads, Analytics, and Email categories, and uses env vars only for missing DB categories or DB-discovery outage.
- DB rows are considered usable only when status/health are configured/active/healthy equivalents, category metadata is present, `proof_scope` is `vendor_sandbox` or equivalent, and the row is not `local_test_only` or `mock_or_test_double`.
- Rows marked `config.local_test_only=true` or `config.proof_scope=preflight_only` produce `proof_status=local_preflight_only`, `production_claim_allowed=false`, `real_vendor_claim_allowed=false`, and `proof_inserted=false`; the report task is not invoked for those rows.
- JSON/text output includes only safe connector key/name/source/readiness metadata. Credential payloads, tokens, API keys, refresh tokens, and secret-like fields are not emitted.
- Tests now prove DB rows satisfy category preflight, local/preflight-only rows do not insert proof, missing DB categories stay blocked unless env fallback is configured, env fallback still works, DB rows beat env vars, and mock/test-double DB rows block proof even when env vars exist.

Exact QA instructions:

- Apply Alembic head and verify `weekly_report_pilot_proofs` exists.
- For tenant `d3f0d84c-836f-4cda-8896-ce2f1623213d`, configure one real vendor-sandbox ConnectorConfig row each for CRM, Ads, Analytics, and Email.
- Required ConnectorConfig metadata: `status` configured/active/healthy/ready equivalent, `health_status` healthy/ready/connected/unknown, `config.cmo_category` set to the category, `config.proof_scope=vendor_sandbox`, no `config.local_test_only=true`, and no `config.mock_or_test_double=true`.
- Store all sandbox secrets only in normal ConnectorConfig credential storage. Do not put secrets in docs, logs, env output, or committed files.
- Run `python scripts/run_weekly_report_sandbox_pilot.py --preflight-only --format json`. Expected: four categories are `source=db`, `readiness_state=ready`, `proof_status=preflight_ready`, and not `local_preflight_only`.
- Only then run `python scripts/run_weekly_report_sandbox_pilot.py --format json > sandbox-proof.json` and verify the persisted row remains `environment_type=vendor_sandbox`, `production_claim_allowed=false`, and `real_vendor_claim_allowed=false`.

#### CMO-PROD-3F Vendor-Sandbox ConnectorConfig Configuration

Status: Blocked on 2026-05-25; this worktree has no local `.env`, no gitignored `secrets/cmo_vendor_sandbox_connectors.json`, and this shell has no `AGENTICORG_DB_URL`, no `AGENTICORG_CMO_SANDBOX_TENANT_ID`, and no complete CRM/Ads/Analytics/Email sandbox credential groups. No ConnectorConfig rows were changed and no proof row was inserted.

Implementation completed:

- Added `scripts/configure_cmo_vendor_sandbox_connectors.py`, a local QA helper that reads real sandbox credentials from env vars or gitignored `secrets/cmo_vendor_sandbox_connectors.json`, encrypts credentials with the repo's `encrypt_for_tenant()` convention, and upserts tenant `ConnectorConfig` rows only when all four categories are supplied.
- The helper refuses to proceed without tenant UUID and all four real category credential sets, never logs credential values, and emits only safe connector metadata.
- The weekly report sandbox runner now also supports `--json` as an alias for `--format json`.
- 2026-05-25 UI follow-up: added an admin-only guided setup path at
  `/dashboard/connectors/cmo-vendor-sandbox` backed by
  `GET/POST /api/v1/connectors/cmo-vendor-sandbox`. The path upserts one
  encrypted ConnectorConfig row each for CRM, Ads, Analytics, and Email,
  sets `proof_scope=vendor_sandbox`, `environment_type=vendor_sandbox`,
  `local_test_only=false`, and `mock_or_test_double=false`, refuses obvious
  placeholder credential values, and returns only safe metadata plus
  credential key names. Full QA instructions live in
  `docs/CMO_VENDOR_SANDBOX_QA_FLOW.md`.

Observed local status:

- 2026-05-25 follow-up: `.env` is absent, `secrets/cmo_vendor_sandbox_connectors.json`
  is absent, and the process environment does not contain the required DB URL,
  target tenant id, or any complete vendor-sandbox credential group. Because
  `AGENTICORG_DB_URL` is missing, this run could not re-verify
  `alembic_version`, `weekly_report_pilot_proofs`, or tenant
  `ConnectorConfig` rows. Real vendor-sandbox categories verified from DB:
  **none**. Preflight passed: **no**. Proof row inserted: **no**.
- `python scripts/configure_cmo_vendor_sandbox_connectors.py --tenant-id
  d3f0d84c-836f-4cda-8896-ce2f1623213d --dry-run --format json` returned
  `blocked`; missing categories are CRM, Ads, Analytics, and Email. No
  credential values were printed and no DB writes were attempted.
- `python scripts/run_weekly_report_sandbox_pilot.py --preflight-only --json`
  returned `blocked` with `db_discovery_state=not_checked`, `tenant_id=null`,
  missing `AGENTICORG_DB_URL`, missing `AGENTICORG_CMO_SANDBOX_TENANT_ID`, and
  missing CRM/Ads/Analytics/Email credential groups. No env fallback was usable.
- 2026-05-24 follow-up: local DB connectivity works with the workstation
  Postgres on port 5433. Direct DB inspection confirmed
  `alembic_version=v4917_weekly_report_proof`,
  `weekly_report_pilot_proofs` exists, tenant
  `d3f0d84c-836f-4cda-8896-ce2f1623213d` now exists with slug
  `cmo-weekly-sandbox-local`, `connector_configs` row count for the tenant
  is **0**, and `weekly_report_pilot_proofs` row count is **0**.
- `python scripts/run_weekly_report_sandbox_pilot.py --preflight-only --json`
  now reaches DB discovery and reports `db_discovery_state=ready`, but it
  remains blocked because CRM, Ads, Analytics, and Email have no usable DB
  ConnectorConfig rows and no env fallback credentials.
- `python scripts/configure_cmo_vendor_sandbox_connectors.py --dry-run --format json`
  now reaches tenant validation and returns `blocked` because real
  vendor-sandbox credentials are missing for CRM, Ads, Analytics, and Email.
- No ConnectorConfig rows were created by the dry-run, no full proof was run,
  no `sandbox-proof.json` was created, and no proof row was inserted.


Remaining QA actions:

- Keep `AGENTICORG_DB_URL` and `AGENTICORG_CMO_SANDBOX_TENANT_ID=d3f0d84c-836f-4cda-8896-ce2f1623213d` available in the local shell when running the helper/runner.
- Provide real vendor-sandbox credentials either through existing `AGENTICORG_CMO_SANDBOX_*` env vars or a gitignored `secrets/cmo_vendor_sandbox_connectors.json` file.
- Run `python scripts/configure_cmo_vendor_sandbox_connectors.py --dry-run --format json`; only when it reports all four categories ready, rerun without `--dry-run`.
- Then run `python scripts/run_weekly_report_sandbox_pilot.py --preflight-only --json` and verify CRM, Ads, Analytics, and Email are all `source=db`, `readiness_state=ready`, `proof_scope=vendor_sandbox`, `local_test_only=false`, and `mock_or_test_double=false`.
- Vendor-sandbox proof is still not real-vendor production proof; `production_claim_allowed` and `real_vendor_claim_allowed` must remain false for any `vendor_sandbox` row.

Hand-off to a credentialed operator: re-run this CMO-PROD-3-OPS task in
an environment that has the tenant ConnectorConfig rows + Postgres listed above.
The same runner is expected to produce a `vendor_sandbox` / `sandbox_proven` row;
this Status section should be updated with that run's `proof_id`,
`evaluated_at`, `readiness_score`, and the SQL verification query from
the CMO-PROD-3 runbook immediately above.


Objective: prove an honest first `vendor_sandbox` verdict end-to-end:
tenant ConnectorConfig rows or local/dev env fallback + DB + migration → real sandbox connectors → real
weekly-report run → CMO-PROD-2 persistence → readback verdict that has
`environment_type="vendor_sandbox"`, `proof_status="sandbox_proven"`,
`production_claim_allowed=False`, `real_vendor_claim_allowed=False`.

Files in scope (delivered):

- `core/marketing/weekly_report_sandbox_pilot.py` — pure orchestration
  module: `discover_sandbox_pilot_config()` (tenant `ConnectorConfig` preferred, env fallback),
  `build_blocked_preflight_envelope()`, and `run_sandbox_pilot()`. The
  ready-preflight branch calls `core.tasks.report_tasks.generate_report.run`
  unchanged so the CMO-PROD-2 hook persists the verdict.
- `scripts/run_weekly_report_sandbox_pilot.py` — CLI wrapper. `--preflight-only`
  reports missing env vars without invoking the report task. `--format json`
  emits a secret-redacted bundle. Exit codes: `0`=passed/sandbox_proven,
  `2`=partial, `3`=blocked / preflight-failed / unavailable.
- `tests/unit/test_cmo_weekly_report_sandbox_runner.py` — 18 acceptance
  tests covering every fail-closed path.

Preflight result in this worktree (2026-05-24):

```
$ python scripts/run_weekly_report_sandbox_pilot.py --preflight-only
Preflight status:         blocked
Tenant id:                <missing>
Missing connector categories: CRM, Ads, Analytics, Email
Missing env vars:
  - AGENTICORG_CMO_SANDBOX_HUBSPOT_ACCESS_TOKEN
  - AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_DEVELOPER_TOKEN
  - AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_REFRESH_TOKEN
  - AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_CUSTOMER_ID
  - AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_CLIENT_ID
  - AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_CLIENT_SECRET
  - AGENTICORG_CMO_SANDBOX_GA4_PROPERTY_ID
  - AGENTICORG_CMO_SANDBOX_GA4_REFRESH_TOKEN
  - AGENTICORG_CMO_SANDBOX_GA4_CLIENT_ID
  - AGENTICORG_CMO_SANDBOX_GA4_CLIENT_SECRET
  - AGENTICORG_CMO_SANDBOX_SENDGRID_API_KEY
  - AGENTICORG_CMO_SANDBOX_SENDGRID_SENDER
  - AGENTICORG_DB_URL
  - AGENTICORG_CMO_SANDBOX_TENANT_ID
Exit code: 3
```

Runbook — exact command sequence to run once a sandbox tenant is ready:

1. **Stand up Postgres + apply the migration:**

   ```
   export AGENTICORG_DB_URL=postgresql+asyncpg://USER:PASS@HOST:5432/agenticorg
   alembic upgrade head
   ```

   This applies `v4917_weekly_report_proof` and creates the
   `weekly_report_pilot_proofs` table.

2. **Pick a sandbox tenant UUID and export it:**

   ```
   export AGENTICORG_CMO_SANDBOX_TENANT_ID=<uuid-of-sandbox-tenant>
   # optional:
   export AGENTICORG_CMO_SANDBOX_COMPANY_ID=<uuid-of-sandbox-company>
   ```

3. **Populate one sandbox connector per category** (the runner accepts
   any of the listed alternatives — fill one group per category, not
   all of them):

   - **CRM** — HubSpot test portal *or* Salesforce sandbox:
     - `AGENTICORG_CMO_SANDBOX_HUBSPOT_ACCESS_TOKEN`
     - *or* `AGENTICORG_CMO_SANDBOX_SALESFORCE_INSTANCE_URL` +
       `AGENTICORG_CMO_SANDBOX_SALESFORCE_REFRESH_TOKEN` +
       `AGENTICORG_CMO_SANDBOX_SALESFORCE_CLIENT_ID` +
       `AGENTICORG_CMO_SANDBOX_SALESFORCE_CLIENT_SECRET`
   - **Ads** — Google Ads test account, Meta sandbox, or LinkedIn Ads sandbox:
     - `AGENTICORG_CMO_SANDBOX_GOOGLE_ADS_DEVELOPER_TOKEN`,
       `_REFRESH_TOKEN`, `_CUSTOMER_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`
     - *or* `AGENTICORG_CMO_SANDBOX_META_ADS_ACCESS_TOKEN` +
       `AGENTICORG_CMO_SANDBOX_META_ADS_AD_ACCOUNT_ID`
     - *or* `AGENTICORG_CMO_SANDBOX_LINKEDIN_ADS_REFRESH_TOKEN`,
       `_ACCOUNT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET`
   - **Analytics** — GA4 demo property:
     - `AGENTICORG_CMO_SANDBOX_GA4_PROPERTY_ID`, `_REFRESH_TOKEN`,
       `_CLIENT_ID`, `_CLIENT_SECRET`
   - **Email** — SendGrid sandbox or Mailchimp test account:
     - `AGENTICORG_CMO_SANDBOX_SENDGRID_API_KEY` +
       `AGENTICORG_CMO_SANDBOX_SENDGRID_SENDER`
     - *or* `AGENTICORG_CMO_SANDBOX_MAILCHIMP_API_KEY` +
       `AGENTICORG_CMO_SANDBOX_MAILCHIMP_SERVER_PREFIX` +
       `AGENTICORG_CMO_SANDBOX_MAILCHIMP_AUDIENCE_ID`

4. **Complete CMO field mapping + backfill in the dashboard / API**
   for the sandbox tenant: `lifecycle_stages`, `opportunity_revenue`,
   `campaign_ids`, `utm_fields`, `consent_unsubscribe`,
   `fiscal_calendar`, `currency`, `timezone`. The runner does not
   bypass these — the underlying CMO-PROD-1 validator requires every
   mapping to be `valid` before any verdict can be `sandbox_proven`.

5. **Confirm preflight passes:**

   ```
   python scripts/run_weekly_report_sandbox_pilot.py --preflight-only
   # expect: "Preflight status: ready" and exit code 0
   ```

6. **Run the pilot:**

   ```
   python scripts/run_weekly_report_sandbox_pilot.py --format json | tee sandbox-proof.json
   ```

   The runner invokes `core.tasks.report_tasks.generate_report.run(...)`
   with `params.pilot_environment_type="vendor_sandbox"`, which fires
   the CMO-PROD-2 hook and inserts a single row in
   `weekly_report_pilot_proofs`. The script then re-reads the latest
   row from `/kpis/cmo` and prints the redacted summary.

7. **Verify the row landed and is honest:**

   ```
   psql $AGENTICORG_DB_URL -c "
     SELECT proof_id, environment_type, proof_status,
            production_claim_allowed, real_vendor_claim_allowed,
            readiness_score, evaluated_at
       FROM weekly_report_pilot_proofs
       WHERE tenant_id = '$AGENTICORG_CMO_SANDBOX_TENANT_ID'
       ORDER BY evaluated_at DESC LIMIT 1;
   "
   ```

   - `environment_type` MUST be `vendor_sandbox`.
   - `proof_status` MUST be `sandbox_proven` (or `partial` / `blocked`
     if any evidence is still incomplete — never silently `passed`).
   - `production_claim_allowed` and `real_vendor_claim_allowed` MUST
     both be `false`.

Acceptance tests (13 passing, all in
`tests/unit/test_cmo_weekly_report_sandbox_runner.py`):

- preflight with empty env → `blocked` envelope, `production_claim_allowed=False`
- preflight with complete env → `ready`, picks one connector per category
- preflight with missing single category lists only that category
- preflight envelope is redactable (no leaked secret-named values when
  the redactor runs)
- `run_sandbox_pilot()` with missing creds returns the blocked envelope
  and never calls the persistence helper
- `run_sandbox_pilot()` with ready env calls `generate_report.run` with
  `params.pilot_environment_type="vendor_sandbox"` and the persisted
  verdict is sandbox/partial only — never real-vendor passed
- `run_sandbox_pilot()` tolerates DB readback failure and still returns
  the task-result summary with `production_claim_allowed=False`
- runner reads back the latest persisted verdict via the
  `latest_weekly_report_pilot_proof` helper
- runner output redacts `api_key` / `authorization` named fields in
  the task result
- CLI `--preflight-only` exits 3 when blocked and lists the missing
  envs
- CLI `--format json` does not echo any sandbox env var values
- `SANDBOX_CONNECTOR_OPTIONS` covers the same categories as
  `REQUIRED_BACKFILL_CATEGORIES` (CRM/Ads/Analytics/Email)
- preflight is pure (does not mutate env)

Definition of done (CMO-PROD-3):

- ✅ Fail-closed sandbox runner exists and is test-covered.
- ✅ Runner never inserts a fake / synthetic row.
- ✅ Runner forces `environment_type="vendor_sandbox"` upstream so a
  successful pilot can never accidentally produce a real-vendor
  production claim.
- ✅ All output (text and JSON) goes through the same secret-marker
  redactor used by `core.marketing.weekly_report_pilot_proof`.
- ✅ Backlog distinguishes "validator complete" / "persistence
  complete" / "sandbox live evidence present or blocked" cleanly.
- ⏳ A pilot tenant must populate the listed env vars before the first
  `sandbox_proven` row can materialise. **No row exists today**.

Hand-off to CMO-PROD-4:

- After the first `sandbox_proven` row lands, CMO-PROD-4 walks one
  real-customer tenant through the same orchestrator with live
  vendor credentials to produce the first `real_vendor` + `passed`
  verdict. The runner already supports this — `environment_type` will
  be inferred from `source_context` / connector contracts at evidence-
  build time. No code changes are expected for CMO-PROD-4 beyond the
  decision to label a tenant `real_vendor` in its connector contracts.

## Suggested Sprint Order

1. Week 1: CMO-0.1, CMO-0.2
2. Week 1-2: CMO-1.1, CMO-1.2, CMO-1.3, CMO-2.1
3. Week 2-4: CMO-3.1, CMO-3.2, CMO-3.3, CMO-4.1, CMO-4.2, CMO-4.3
4. Week 4-5: CMO-5.1, CMO-5.2, CMO-5.3, CMO-5.4, CMO-6.1, CMO-6.2, CMO-6.3
5. Week 5-6: CMO-7.1, CMO-7.2, CMO-7.3, CMO-8.1, CMO-8.2, CMO-8.3
6. Week 6+: CMO-9.1, CMO-9.2, CMO-9.3, CMO-9.4; then live pilot rollout, persistent evidence storage/UI, beta-agent production proof, and vendor adapter rollout

Sequencing note after CMO-5.1, CMO-5.2, CMO-5.3, CMO-5.4, CMO-6.1, CMO-6.2, CMO-6.3, CMO-7.1, CMO-7.2, CMO-7.3, CMO-8.1, CMO-8.2, CMO-8.3, CMO-9.1, CMO-9.2, CMO-9.3, CMO-9.4, CMO-3.1, CMO-3.2, CMO-3.3, CMO-4.1, and CMO-4.2: connector contracts now expose read/write readiness, retry/degraded policy, confidence impact, idempotency metadata, mock/test proof blocking, and external write confirmation status; marketing workflow steps fail closed on rejected, timeout/unknown, unconfirmed, non-write-safe connector states, unsafe retry, approval timeout, missing/blocking marketing policy, unsatisfied policy approval/escalation, missing escalation routes, missing decision-audit evidence, and shadow-mode external-write attempts; marketing workflow YAML can be linted before execution for undefined agents/actions, beta/stub/unavailable production behavior, connector-readiness gaps, degraded-only production dependencies, unsafe external-write metadata, missing approval timeout policy, missing/blocking marketing policy coverage, missing escalation routes, missing CMO-6.3 decision-audit evidence, and CMO-9.1 agent contract status; pending/overdue CMO approval timeout risk and approval-review payloads are visible in the CMO KPI API; the CMO KPI API exposes the active/default marketing policy manifest, marketing escalation matrix, decision audit package schema/summaries, unified CMO KPI schema/evaluation projection, KPI reconciliation checks/summary, report quality gates/summary, a prioritized CMO work queue/summary, KPI drill-down/data-lineage projection, approval-review projection, and CMO pilot proof projection/summary; per-agent contract tests now prove implemented/beta contract shape plus stub/unavailable non-production status; deterministic CMO E2E-style scenarios now prove weekly review, campaign launch, crisis response, ABM sprint, and content-to-publish paths fail closed across those surfaces; deterministic chaos/failure tests now prove connector outage, auth/scope failure, rate/quota limits, stale/partial/malformed data, approval timeout, budget race, duplicate replay, unsafe writes, missing policy/escalation/audit evidence, failed reconciliation, and report-gate failure are surfaced as blocked, degraded, draft/internal-only, retry-scheduled, or manual-resolution states rather than hidden production success; pilot proof packaging now distinguishes real-vendor, vendor-sandbox, demo, test-double, and unknown tenants while keeping demo/test-double proof from passing production readiness; and Social Media/ABM/Competitive Intel/Brand Monitor/SEO Strategist/CRM Intelligence now have first-class beta core logic while remaining unproven for production without real-vendor/pilot proof. The next safety gaps are live pilot execution, persistent KPI/report/audit/task/approval/proof storage and UI, vendor adapter rollout where needed, and production proof for beta CMO agents before claiming complete CMO autonomy.

## Codex CLI Task Template

Use this template for each implementation task:

```text
Task ID:
Objective:
Files in scope:
Non-goals:
Acceptance tests:
Definition of done:
Risk checks:
```

## First Codex CLI Task To Start

```text
Task ID: CMO-0.1
Objective: Replan the CMO PRD so real companies can use it with real data, real connectors, real approvals, and a serious marketing-team UX.
Files in scope:
- docs/PRD.md
- docs/cmo_guide.md
- docs/PRD_CxO_v5.0.md
- ui/src/pages/CMODashboard.tsx
- related locale/test files if dashboard copy changes
Non-goals:
- Do not implement missing agents in this task.
- Do not redesign the dashboard.
Acceptance tests:
- npm --prefix ui test -- CMODashboard if UI changed.
- Run any existing markdown/docs validation if available.
- Search docs/UI for unqualified CMO autonomy claims and mock/stub production claims.
Definition of done:
- PRD defines real-company onboarding, data foundation, connector readiness, governance, UX, production gates, and pilot proof.
- CMO UI/docs distinguish production, beta, shadow, stub, unavailable, demo, and degraded capabilities.
- No unqualified "fully autonomous CMO" claim remains.
- No mock/stub-only capability is treated as production-ready.
Risk checks:
- Search for CMO autonomy claims after the edit.
- Confirm copy does not undercut implemented Campaign Pilot and Content Factory capabilities.
- Confirm the plan requires real connector data and does not bless mock-only implementation.
```
