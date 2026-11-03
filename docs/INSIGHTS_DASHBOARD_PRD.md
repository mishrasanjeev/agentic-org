# AgenticOrg Insights Dashboard PRD

29 October 2026, revised 1 November 2026 · Sanjeev Kumar · Revised (decisions approved 29 Oct 2026; corrections verified against the code on 1 Nov 2026)

## Purpose and scope

AgenticOrg needs one Insights Dashboard that answers, for every kind of user, the three questions they actually ask: what are my agents doing, what is it costing and saving me, and what needs my decision today. Today that information is scattered across fourteen dashboard pages (home Dashboard, CEO/CFO/CHRO/CMO/COO/CBO dashboards, Cost, Observability, Observatory, Company, Partner, Scope, ABM; the Report Scheduler page stays), several of which show placeholders or are only partly wired to real data. This document specifies the replacement: a single, role-aware dashboard family with a shared metric catalogue, consistent charts, and every number downloadable. The legacy pages stay until each one's working features have a new home (see Delivery status).

Scope: tenant-facing insights for all human roles plus partner/merchant views; the metric catalogue, chart standards, export/report contract, data model and pipeline, non-functional requirements, and a phased plan. Out of scope: the public marketing `/evals` page (keeps its own data-quality labelling), per-agent configuration screens, and billing checkout.

Three principles govern every requirement below.

1. **Actionable, not decorative.** Every tile, chart and table must carry at least one of: a threshold with a state (OK / watch / act), a trend against a prior period, a drill-down to the rows behind it, or a direct action (approve, pause, raise cap, open run). A number with none of these does not ship.
2. **Honest numbers.** Every metric declares its source and its data-quality state (`measured`, `estimated`, `seeded` for rows the demo seeder wrote, `simulated`, or `no_data` when nothing is recorded; a view that mixes states says `mixed`). Estimates (money saved, hours saved, forecast spend) show their assumptions inline and are never mixed into a measured total without a label. Nothing is fabricated when data is missing; the empty state says what is missing and how to populate it.
3. **Everything exportable.** Any table, chart series or report is downloadable as CSV, XLSX, JSON, PDF or PNG, scheduled or on demand, and reachable through the API with the same filters the UI used.

## Delivery status (1 November 2026)

Being delivered first:

- **Phase 0 fixes.** The KPI pages enforce their roles on the server (admin plus the matching CxO role), the KPI cache honours the company selector, the COO board counts agents in the `ops` domain as well as `operations`, and analysts land on the workbench instead of an Access Denied page.
- **Phase 1, Executive and Finance views.** The metric registry, the `run_facts` writer, daily rollups with nightly reconciliation, and the Executive and Finance views with inline CSV, XLSX and JSON export. Insights is on for all tenants (a tenant admin can turn it off for their tenant). Run tracing (`run_spans`), model gateway records and spend metering are turned on in production as part of this delivery (owner decision 1 Nov 2026).
- **Platform view.** The cross-tenant operator view, for the platform-operator identity only.
- **BI access.** Per-tenant BI database access over read-only `insights_*` views (see Exports, downloads, reports and API).

**Phase 2, in progress (2 November 2026).** Three releases: (1) the explorers and run evidence (Level 2 and
Level 4; shipped first), (2) the money-saved model and alerts, (3) the Operations, HR, Marketing and Commerce
views. Release 1 delivers eight of the nine explorers (Value and savings arrives with the money-saved model);
the Security and audit explorer reads the audit log, and sign-in, lockout and rate-limit counters arrive with
`security_events_daily` later. Connector call counts wait for a `tool_calls` writer. Release 2 adds the money-saved model (seeded baselines for the built-in agent types; hourly rates from BLS OEWS May 2025 for USD tenants and MoSPI PLFS 2025 for INR tenants, each cited and confirmed by the tenant administrator), the Value and savings explorer, and alerts (in-app and an email for `act` alerts). Workflow steps count as tasks per completed step run (the rollup keeps no per-workflow distinct count), so a retried step charges its failure once in the error cost. A forecast-above-budget alert is not in this release. Release 3 adds the Operations, HR, Marketing and Commerce views (the HR view reads HR runs, approvals and professional tax returns: the onboarding desk is the merchant onboarding desk, not an HR funnel) and the usability test script (`docs/insights/phase2-usability-test.md`). Confirming a savings baseline and revoking a learned rule are permission-bound; the script scores them for other roles as finding the control and naming who may act, which the product owner confirms before the sessions.

**Phase 3, in progress (3 November 2026).** Four releases: (1) run evidence as a trace waterfall with the cause of a failure, the run evidence bundle, the Compliance view (auditor) and the Engineering view (developer), and the 60-second refresh with the late-data banner; (2) background export jobs, the Downloads page and tenant PII masking on exports; (3) PDF views, the finance pack and the period evidence pack with auditor sign-off; (4) scheduled Insights reports. The Security and audit explorer reads the audit log; sign-in, lockout and rate-limit counters still wait for `security_events_daily`. Tool calls in the trace come from `run_spans` and `tool.*` audit rows (`tool_calls` has no writer).

Later phases are unchanged. In v1 the legacy dashboard pages stay: deleting them would remove working features that need a new home first, namely the CMO work queue, approvals, connector setup and promotion gates; the Observability run debugger and synthetic-check management; the Company management list, bulk upload and archive; and ABM account upload and campaign launch.

## Personas and what each must find end to end

Every role lands on a view whose first-question tiles render within 1.5 s (p95) and whose answer the Phase 2 usability test shows participants reading without scrolling or filtering, and can reach any other view by drilling down, never by knowing a URL. The matrix below is the acceptance test for "all kinds of users find everything".

| Persona (role in `core/rbac.py`) | First question on landing | Must be able to reach end to end | Default landing view |
| --- | --- | --- | --- |
| CEO / Admin (`admin`; there is no `ceo` role) | Is the agent workforce paying for itself, and what needs me today? | Fleet size and health → money saved vs spend → open approvals/incidents → any agent → any run → any tool call | Executive overview |
| CFO (`cfo`) | What did agents cost this month, where, what did they save, and does the provider bill match? | Spend by provider/model/agent/cost centre → budget vs actual → forecast → provider invoice reconciliation → restatements → finance-agent outcomes (AP, AR, GST filings, TDS) | Finance view |
| CHRO (`chro`) | How are HR agents performing on onboarding, payroll and compliance? | Onboarding-desk funnel (leads → applications → documents → handoffs) → professional-tax returns → HITL load on HR → learned-rule changes | HR view |
| CMO (`cmo`) | Which marketing agents produce results and at what cost per outcome? | Lead pipeline and ABM campaigns → content drafts and approval cycle time → external writes (posts, emails, sequences) → cost per asset | Marketing view |
| COO (`coo`) | Are operations agents meeting SLAs and where are the bottlenecks? | Workflow runs by status → step-level latency → wait/HITL stalls → connector failures → human adoption | Operations view |
| Back office (legal, risk, corporate, comms; there is no `cbo` role, so `admin` reads this view until one exists) | Are legal/risk/corporate agents keeping us compliant? | Transaction findings → compliance deadlines → content clauses → approvals | Back-office view |
| Merchant / Commerce (`merchant`) | Are commerce agents converting and settling correctly? | Carts → checkout sessions → orders → payment operations → arbiter decisions → webhook deliveries | Commerce view |
| Domain lead / analyst (`domain_lead`, `analyst`) | What happened in my domain this week and what should I fix? | Domain-filtered everything; feedback, corrections, learned rules; export | Domain view |
| Developer (`developer`) | Why did this run fail or cost so much, and did my prompt change help? | Run → trace (`run_spans`) → tool calls → model gateway records → prompt version → before/after comparison → eval run | Engineering view |
| Auditor (`auditor`) | Can I prove who approved what, under which delegation, and that the audit chain is intact? | Audit log and chain anchors → HITL decisions and delegations → DSAR requests → residency attestations → export bundle | Compliance view |
| Partner (CA firm / reseller) | How are my client companies doing and what do I bill? | Per-company rollups → compliance deadlines → client invoices and payments → pilot proofs | Partner view |
| Platform operator (platform context only) | Is the platform healthy and are tenants within plan? | Cross-tenant health, synthetic checks, plan/usage vs limits, migration state | Platform view |

Role gating is server-side (`get_allowed_domains`, `route_meta` scope families); the dashboard filters data by the caller's domains and never renders a tile it cannot populate for that role. `core/rbac.py` has no `ceo`, `cbo` or platform-operator role: the Executive view is for `admin`, and the Platform view needs a separate platform-operator identity (an admin of the operator tenant with MFA enrolled).

## Information architecture

One dashboard family, four drill levels, one global filter bar. Users never need to know a URL: every number links to the rows beneath it and filters follow the drill.

```
Global bar (every page): tenant / company / date range / domain / environment / compare period / data-quality badge / export

Level 1 · Role landing view
  Executive · Finance · HR · Marketing · Operations · Back office · Commerce · Engineering · Compliance · Partner · Platform
          |
Level 2 · Domain explorers
  Agents · Runs · Workflows · Approvals · Cost & tokens · Value & savings · Quality & learning · Connectors · Security & audit
          |
Level 3 · Entity pages
  One agent · one workflow · one connector · one cost centre · one company · one user
          |
Level 4 · Evidence
  One run · its trace spans · tool calls · model gateway records · HITL decision · audit rows (all exportable)

Rule: any number at a higher level is a link to the rows that produced it at the level below; filters persist down the drill.
```

**Global bar (every page).** Tenant (platform operators only), company, date range with presets (today, 7d, 30d, MTD, QTD, custom), compare period (previous period / same period last year / none), domain, environment (production / shadow / sandbox), currency (INR default for Indian tenants, USD for others, rate from `spend_fx_rates`), a data-quality badge for the current view, and an Export menu. Filter state is encoded in the URL so any view is shareable and schedulable as a report.

**Level 1 — role landing views.** Each is a fixed grid of "decision tiles" (number + state + trend + one action) above two or three charts and a "needs you" list (approvals, budget alerts, failing connectors, agents awaiting promotion, DSAR requests). Admins can switch between all landing views; other roles see only theirs.

**Level 2 — domain explorers.** Nine explorers, each a filterable, sortable, exportable table with a chart strip above it: Agents, Runs, Workflows, Approvals (HITL), Cost & tokens, Value & savings, Quality & learning, Connectors, Security & audit. Every explorer supports saved views (named filter sets) per user and per tenant.

**Level 3 — entity pages.** One agent, workflow, connector, cost centre, company or user. Same layout: header facts, a timeline of lifecycle events, metric cards scoped to the entity, and the explorers pre-filtered to it.

**Level 4 — evidence.** A single run with its `run_spans` waterfall, `tool_calls`, `model_gateway_records`, prompt version, HITL decision, feedback and audit rows. This is the end of every drill and the unit of export for audits.

**Navigation rules.** Breadcrumbs reflect the drill path; back preserves filters; Cmd/Ctrl-K search over agents, workflows, runs (by id), connectors and users; keyboard-only operation for all tables; a "what changed since I last looked" strip on landing views driven by the audit log.

## Metric catalogue

The catalogue is the contract between data, API and UI: every metric has one definition, one owner table, one chart form and one action. Sources are existing tables unless marked *new*. "Action" is what a user can do from the tile.

### A. Agent workforce (counts, purpose, lifecycle)

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Agents by status | Count in shadow / active / paused / retired / deleted, by domain, trend over 90 d | `agents.status`, `agents.domain` | Click status → Agents explorer; paused > 0 for 7 d → watch |
| Agents by purpose | Count by `agent_type`, `domain`, `specialization`, `maturity`, `tags`; description shown | `agents` | Click → entity page |
| Virtual employees org chart | Agents by `reporting_to`, `org_level`, `designation`, `cost_center_id` | `agents` | Open agent; reassign cost centre (admin) |
| Fleet utilisation | Runs per active agent per day; idle = active agents with 0 runs in 30 d | `run_facts` (new, see Data model) × `agents` | Idle > 30 d → suggest pause / retire |
| Shadow pipeline | Scored samples vs `shadow_min_samples`; human reviews vs 3; blended and human confidence vs floor; eligibility reason | `agents.shadow_*` | Promote (admin) when the gate passes; link to evidence |
| Lifecycle events | Promotions, pauses, rollbacks, retirements with reason and actor | `agent_lifecycle_events` | Open event; filter by actor |
| Version and rule drift | Current vs verified-good version; amendment count; last prompt change | `agent_versions`, `agents.prompt_amendments`, `prompt_template_edit_history` | Diff versions; revoke amendment |
| Agent ratings | Mean `score`, count, trend | `agent_ratings` | Below 3.5 with n ≥ 10 → act |
| Agent memory | Memories per agent, recall rate, expiring in 7 d | `agent_memories` | Review / purge |

### B. Runs, outcomes and throughput

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Run volume | Runs per period by status (completed / failed / hitl\_triggered / limit\_exceeded / rejected) | `run_facts` | Click segment → Runs explorer |
| Success rate | completed ÷ (completed + failed), by agent, domain, task\_type; 7 d moving average | `run_facts` | < 95% → watch, < 90% → act |
| Straight-through rate | Completed runs with no HITL ÷ all completed | `run_facts` | Down two consecutive weeks → act |
| Latency | p50 / p95 duration by agent and task\_type; per-span breakdown | `run_facts`, `run_spans` | p95 above the agent SLA → act |
| Confidence distribution | Histogram of confidence; share below `confidence_floor` | `run_facts`, `agents.confidence_floor` | Floor breaches rising → review prompt |
| Task mix | Runs by task\_type and use\_case | `run_facts`, `spend_usage_records.use_case` | Filter |
| Error taxonomy | Failed runs by error class; `tool_calls.error_code`; `model_gateway_records.error_type` | `run_facts`, `tool_calls`, `model_gateway_records` | Top class → open runs |
| Retries and recovery | Retry attempts per step and recovery outcome | `step_executions` + new `attempt` counter on `tool_calls` | Recovery < 80% → act |
| Prompt / model change impact | Success, latency, cost per run before vs after a version or model change | `core/finops/forecast.py` `comparison()`, `agent_versions` | Roll back version |

### C. Workflows and HITL

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Workflow runs by status | running / waiting\_hitl / waiting\_event / completed / failed / rejected / timed\_out; definition → outcome flow | `workflow_runs`, `workflow_run_states` | Click → Workflows explorer |
| Step-level bottlenecks | Mean / p95 time per step; steps most often failing or waiting | `step_executions`, `run_spans` | Open workflow |
| Stalled runs | Runs waiting beyond SLA or past HITL `expires_at` | `hitl_queue.expires_at`, `workflow_event_waits` | Nudge / escalate / cancel |
| HITL queue | Pending by priority, assignee\_role, age; expired count | `hitl_queue` | Approve / reject inline (approvals:write) |
| Approval cycle time | `decision_at` − `created_at`, p50 / p95, by role and trigger\_type | `hitl_queue` | p95 > SLA → act |
| Decision mix | approve / reject share per agent and trigger; rejects with notes | `hitl_queue.decision`, `agent_feedback.decision` | Reject share > 20% → review agent |
| Delegated decisions | Decisions taken under a delegation, by delegator and delegate | `hitl_queue.decision_by`, `user_delegations` | Review delegation |
| Policy coverage | Workflows with / without an approval policy; steps and timeouts in force | `approval_policies`, `approval_steps`, `workflow_definitions` | Attach policy |
| A/B variants | Runs, success and cost per variant | `workflow_variants` | Promote variant |

### D. Cost, tokens and model economics (detail under Token, cost and model economics)

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Spend | Priced amount by day, provider, model, usage type, agent, use\_case, org node / BU, environment, risk tier | `spend_usage_records`, `spend_usage_rollups` | Drill to records; set budget |
| Spend by company | Spend attributed through `agents.company_id` (records carry agent, not company) | `spend_usage_rollups` × `agents` | Drill |
| Tokens and units | Input / output / cached-input tokens; OCR pages, speech minutes, tool calls, storage, GPU hours | `spend_usage_records.unit`, `model_gateway_records.input_tokens/output_tokens` | Drill |
| Unit economics | Cost per run, per completed task, per 1k tokens, per outcome | `spend_usage_rollups` ÷ `run_facts` | Cost per run up 25% week on week → act |
| Budget vs actual | Spend vs `budget_alerts` / `finops_thresholds` | `budget_alerts`, `finops_thresholds`, `agent_cost_ledger.budget_pct_used` | Pause agent; raise cap |
| Forecast | Month-end projection with low–high band | `core/finops/forecast.py` `project()` | Projection > budget → act |
| Cost data quality | Unpriced, unconverted, FX-estimated and estimated-quantity counts from the rollup; unattributed records counted from spend\_usage\_records.unattributed\_reason | `spend_usage_rollups.*_count`, `spend_meter_gaps` | Fix rate card / attribution / FX |
| Commitments | Drawn vs committed per provider commitment; overage | `spend_commitments` | Renegotiate |
| Provider invoice reconciliation | Provider bill vs metered spend; variance; items needing review | `spend_invoices`, `spend_invoice_lines`, `spend_reconciliations`, `spend_reconciliation_items` | Open item; mark reconciled |
| Restatements | Amount restated in period; billing-date vs event-date variance (month close) | `spend_usage_records.revised_at`, `billing_date`, `event_date` | Re-run rollup |
| Provider concentration and residency | Share of spend per provider; residency attestation per provider; model risk tier inventory | `spend_usage_rollups`, `provider_residency_attestations`, `model_cards` | Policy review |
| Gateway routing | Fallback rate, restricted-model refusals, admission wait p95 | `model_gateway_records` | Fallback > 5% → investigate |

### E. Value and money saved (detail under Money saved and ROI model)

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Hours saved (estimated) | Completed business tasks × baseline minutes per task type (see Money saved) | `run_facts` × `value_baselines` (new) | Edit baselines; headline hidden until a CFO confirms them |
| Money saved gross / net (estimated) | Hours saved × loaded rate; net subtracts spend, review time and error cost | derived | Open assumptions |
| ROI (estimated) | net saved ÷ spend, shown only above a minimum spend floor | derived | < 0 → act |
| Outcome volume (measured) | Counts per task type validated by `output_schema`: invoices processed, collections recovered, candidates screened, leads qualified, filings submitted, drafts approved | `run_facts.outcome_*` | Drill; never summed into savings |
| Payback per agent | Months to recover `setup_cost` | derived from `value_baselines.setup_cost` | — |

### F. Quality, learning and safety (detail under Quality, learning, HITL and governance)

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Feedback volume and mix | thumbs\_up / thumbs\_down / correction / hitl\_\* per agent; unapplied count | `agent_feedback` | Analyze (admin) |
| Correction heatmap | task\_type × corrected field, computed on read from `original_output` vs `corrected_output` (`core/feedback/analyzer.correction_diff`) | `agent_feedback` | Open corrections |
| Learned rules | Amendments per agent, applied history, revocations, success rate before vs after | `agents.prompt_amendments`, `agent_feedback.applied_at`, `audit_log` | Revoke rule |
| Eval results | pass\_rate, scores, cost per eval run; the scorecard is shown as measured only when it was executed live (`evals/scorecard.json` has no `execution_mode` field today, so it reads as simulated) | `eval_runs`, scorecard file | Open run |
| Shadow calibration | Model vs human confidence; `outputs_match` rate vs reference | `agents.shadow_*`, `shadow_comparisons` | Promote / hold |
| Guardrail hits | Actions by stage and detector | `guardrail_rules` + new `guardrail_events` counter | Tune rule |
| PII redaction | Redactions per run; de-anonymisation failures | new runner counters | Investigate |

### G. Connectors, operations and security (detail under Operations, reliability, connectors and security)

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Connector health | Status, last test, error rate, p95 latency per connector and company | `connectors`, `connector_configs`, `tool_calls` | Test / reconnect |
| Tool usage | Calls per tool, success rate, idempotent replays | `tool_calls` (no writer yet; this metric waits for one) | Drill |
| Synthetic checks | Pass rate and latency per check | `synthetic_checks`, `synthetic_check_results` | Open check |
| Audit integrity | Rows HMAC-verified; chain anchors and witness checkpoints current; fork evidence | `audit_log`, `audit_chain_anchors`, `audit_chain_witness_checkpoints`, the `core.tasks.audit_chain_tasks.verify_audit_chains` Celery task | Export evidence |
| Security events | Failed logins, lockouts, rate-limit and scope denials, revoked sessions, API-key use, Grantex rejections, webhook signature failures | new `security_events_daily` counters fed from auth/middleware logs | 3-sigma spike vs 30 d → investigate |
| DSAR | Requests by type / status; time to complete | `dsar_requests` | Open |
| Human adoption | Weekly active users by role; logins; approvals per reviewer; seats vs plan | `users`, `audit_log` (login events), `hitl_queue.decision_by` | Seats > plan → upgrade |
| Alert effectiveness | Fired vs acknowledged vs actioned; time to ack; repeat alerts | `insights_alerts` (new) | Retune thresholds |
| Tenancy posture | RLS forced vs total tables, DB role bypass flag, migration head | `core/database` checks | Platform view only |

### H. Commerce, partner and platform

| Metric | Definition | Source | Action / threshold |
| --- | --- | --- | --- |
| Commerce funnel | Carts → checkout sessions → orders → payment operations succeeded; arbiter decisions by outcome; webhook delivery failures | `commerce_carts`, `commerce_checkout_sessions`, `commerce_orders`, `commerce_payment_operations`, `commerce_arbiter_decisions`, `commerce_order_webhook_deliveries` | Drill |
| Customer mandates | Active mandates, caps, autonomy level, actions taken, revocations | `customer_mandates`, `customer_actions`, `customer_sessions` | Revoke |
| Partner rollup | Per client company: agents, runs, approvals, deadlines met, invoices and payments | `companies`, `ca_client_invoices`, `ca_client_payments`, `compliance_deadlines` | Open company |
| Plan and usage | Agent runs vs plan limit; agent count; storage (documents, recordings) vs plan | `billing_subscriptions` (raw-SQL table, no ORM model), `ca_subscriptions`, usage counters, `documents`, `speech_recordings` | Upgrade |
| Platform health | Tenants active, API error rate, worker queue depth, beat lag | health snapshots | Page on-call |

### I. Domain outcomes (what each officer's agents actually delivered)

| Domain | Outcome metrics | Source |
| --- | --- | --- |
| Finance (CFO) | Invoices processed and value; AR collected; GST uploads submitted on time; filings approved / rejected; transaction findings by severity and disposition | `invoices`, `gstn_uploads`, `filing_approvals`, `txn_findings` |
| HR (CHRO) | Onboarding funnel: leads → contact attempts → applications → documents verified → handoffs; consent and suppression counts; professional-tax returns filed | `onboarding_desk_*`, `professional_tax_returns` |
| Marketing (CMO) | Leads by stage and pipeline value; ABM campaigns and launches; content drafts by status and approval time; email sequences sent | `lead_pipeline`, `abm_*`, `content_drafts`, `email_sequences` |
| Operations (COO) | Workflow SLA attainment; RPA schedule runs and failures; bridge sessions | `workflow_runs`, `rpa_schedules`, `bridge_sessions` |
| Back office (legal, risk, corporate, comms) | Compliance deadlines met / missed; findings resolved; content clauses applied | `compliance_deadlines`, `txn_findings`, `content_clauses` |

## Money saved and ROI model

Money saved is an estimate built from measured task counts and tenant-owned baselines; it is always labelled `estimated`, its assumptions are one click away, it is never summed with measured spend without that label, and the headline tile stays hidden until a CFO or admin has confirmed the baselines. No table holds baselines today, so this section defines them.

**Formula.** The unit is a completed *business task* (one invoice, one candidate, one filing), not an agent run: tasks are counted once per `workflow_run_id`, or per `run_facts` row when the run is not part of a workflow. Failed and rejected tasks are not counted as saved; their manual redo is charged once, in `error_cost`. For task type *t* in the period:

```latex
\text{hours\_saved}_t = \frac{\text{completed\_tasks}_t \times \text{baseline\_minutes}_t}{60}
```

```latex
\text{gross\_saved} = \sum_t \text{hours\_saved}_t \times \text{loaded\_rate}_{\text{role}(t)}
```

```latex
\text{net\_saved} = \text{gross\_saved} - \text{spend} - \text{review\_hours} \times \text{reviewer\_rate} - \sum_t \text{failed\_tasks}_t \times \frac{\text{baseline\_minutes}_t}{60} \times \text{loaded\_rate}_{\text{role}(t)}
```

```latex
\text{ROI} = \frac{\text{net\_saved}}{\text{spend}}
```

**Inputs and where they live.**

| Input | Source | Default | Who edits |
| --- | --- | --- | --- |
| `completed_tasks_t`, `failed_tasks_t` | `run_facts` deduplicated on `workflow_run_id` (measured) | — | — |
| `baseline_minutes_t` | `value_baselines` (new): tenant, task\_type, minutes, role, `setup_cost`, source, confirmed\_by, updated\_by | Seeded per industry pack (e.g. AP invoice 12 min, candidate screen 20 min, GST reconciliation 45 min); `source = seeded` until confirmed | Tenant admin; every change audited |
| `loaded_rate_role`, `reviewer_rate` | `value_rates` (new): tenant, role, hourly cost, currency, source | Seeded per country with the source cited in the row; INR for India | Tenant admin |
| `review_hours` | Σ (`decision_at` − `first_viewed_at`) over decided HITL items, capped at 30 min each; `first_viewed_at` is a new column, items without it use 5 min | `hitl_queue` (measured) | Auto |
| `spend` | Priced amount, same filters, in the tenant's reporting currency | The metric's one cost source: `spend_usage_rollups` when spend metering is on for the tenant, otherwise `agent_cost_ledger` (measured) | — |
| `setup_cost` (payback only) | One-off cost to configure the agent | `value_baselines.setup_cost` | Admin |

**Outcome volume (measured, never money).** Where a task type produces a countable outcome, the dashboard shows it beside hours saved, read from `run_facts.outcome_*` fields that the agent's `output_schema` validated: invoices processed and their face value, AR collected, candidates screened, leads qualified and pipeline value, filings submitted, drafts approved. These are volumes and GMV, labelled as such; they are never added to savings.

**Presentation and guards.** Executive and Finance views show a waterfall: gross saved → minus spend → minus review cost → minus error cost → net saved, an ROI gauge and a 90-day trend; each bar opens the inputs table. Three guards apply. First, an **assumptions unconfirmed** state: while any baseline or rate used in the period is `source = seeded`, the headline tile shows "Confirm assumptions to see savings" with the confirm action (tenant admin only), and the waterfall renders greyed with the label `estimated (unconfirmed)`. Second, a **confidence chip**: high when ≥ 80% of hours come from tenant-confirmed baselines, medium ≥ 50%, otherwise low. Third, a **spend floor**: ROI is shown only when period spend ≥ ₹5,000 (US$60 for USD tenants), or the tenant's configured floor, because net ÷ spend is meaningless at near-zero spend; below the floor the tile shows net saved only. Partner views aggregate per client company. Every figure exports with an assumptions sheet so a CFO can audit it in Excel.

## Token, cost and model economics

The cost view answers "where does the money go, is it priced correctly, and what will it be at month end" from the FinOps tables that already exist (`spend_usage_records`, `spend_usage_rollups`, `spend_rate_cards`, `spend_commitments`, `spend_fx_rates`, `finops_cost_ledger`, `model_gateway_records`). Nothing here is estimated except the forecast and FX-estimated records, both labelled.

**One cost source per metric.** The registry names exactly one cost source for each metric: spend metering rollups (`spend_usage_rollups`) when metering is on for the tenant, otherwise the agent cost ledger (`agent_cost_ledger.cost_usd`). Each response says which source it used. `run_facts.cost_usd` is the runtime-reported cost and is used for per-run unit economics only.

**Dimensions (all filterable, all exportable).** Provider, model (and `spend_model_aliases`), usage type as the FinOps vocabulary defines it (`llm_tokens`, `embedding_tokens`, `ocr_pages`, `speech_minutes`, `tool_calls`, `storage`, `gpu_hours`) with its unit (for LLM tokens: `input_token`, `output_token`, `cached_input_token`), agent and agent version, use case, application, workflow, run, org node and business unit, product line, environment, region, risk tier, initiating user, billing account, commitment. Company is not stored on usage records; it is derived through `agents.company_id` and shown as such.

**Views.**

1. **Spend over time** — stacked area by provider or model, daily/weekly/monthly, with the compare period as a dashed line and budget threshold as a reference line; GPU hours and other non-LLM usage types appear as their own series. Toggle amount in INR or billing currency; FX-estimated share shown as a hatched band.
2. **Where it goes** — treemap agent → use case → model; click to filter everything else. A "top 10 movers" table shows agents whose spend changed most versus the compare period, decomposed into volume (Δ quantity × prior unit price), price (prior quantity × Δ unit price), and mix (the remainder, from model or usage-type shifts), so the driver is stated, not guessed.
3. **Tokens** — input / output / cached-input stacked bars (from \`spend\_usage\_records.unit\`; \`model\_gateway\_records\` carries input/output only); cache hit rate = cached-input ÷ total input over the period; tokens per run distribution; prompt-size trend per agent (a rising input-token trend with flat output flags prompt bloat).
4. **Unit economics** — cost per run, per completed task, per outcome, per 1k tokens, by agent; a scatter of cost per run vs success rate marks agents in the "expensive and unreliable" quadrant for action.
5. **Budgets** — bullet chart per budget (`budget_alerts`, `agent_cost_ledger.budget_pct_used`): actual, forecast, threshold, warn line; state OK / warn / breached; inline actions raise cap, pause agent, notify owner.
6. **Forecast** — month-end projection from `core/finops/forecast.py` with a low–high band (one middle point plus whisker per day, never a floating bar), and the assumption (run-rate window) shown.
7. **Rate cards and commitments** — effective rate card per model with history; committed vs drawn vs overage per commitment; alerts when a model has no active rate card (unpriced records).
8. **Gateway behaviour** — fallback rate (`fallback_from`), restricted refusals, admission wait p95, error types; a model-routing policy hit table.
9. **Cost data quality** — tiles for unpriced, unconverted, fx-estimated and estimated-quantity record counts from `spend_usage_rollups`; each opens the gap list (`spend_meter_gaps`) with the fix (add rate card, map attribution, set FX).

**Alerts.** Budget warn/breach, forecast above budget, spend per run up more than 25% week on week, new model seen without a rate card, fallback rate above 5%, cache hit rate down more than 20 points against the trailing 7-day average, any tenant over plan limit. Alerts post to the "needs you" list and to configured channels.

**Exports.** Any view as CSV/XLSX with the full dimension set; a monthly FinOps pack (PDF + XLSX) with spend by BU/cost centre, unit economics and data-quality notes, schedulable via `report_schedules`.

## Quality, learning, HITL and governance insights

This view shows whether agents are getting better, who is correcting them, and whether the controls around them are working. It is the view a domain lead opens weekly and an auditor opens before sign-off.

**Learning loop (per agent and per domain).**

- Feedback funnel: runs → runs with feedback → negative feedback → analysed → rule proposed → rule applied (shadow only) → rule revoked. Drop-off at each stage is the signal; "analysed but no rule" with a heuristic source means the LLM analysis is unavailable.
- Correction heatmap: task\_type × corrected field (from `correction_diff`), so a lead sees that, say, `currency` is corrected in 30% of AP runs.
- Learned-rule ledger: every amendment with applied date, source confidence, the feedback ids behind it, and a revoke action; before/after success rate for runs following the rule.
- Shadow calibration: model confidence vs human confidence over samples; the promotion gate state (scored samples, human reviews, floors) as a checklist with the exact reason an agent is not yet eligible.
- Eval trend: pass rate and score per dataset and version; a `simulated` scorecard is shown with its banner and excluded from quality KPIs.

**HITL effectiveness.**

- Review load by role and person (count, hours), queue age distribution, expired items, escalations, and the share of approvals that were effectively rubber-stamps (decided in under 20 seconds) versus substantive (override or reject).
- Decision quality: reject rate per agent and trigger type, override reasons word-cloud-free (a ranked table), and the "should have triggered" count from shadow comparisons (`shadow_hitl_would_trigger` vs `reference_hitl_triggered`).
- Policy coverage: approval policies in force, steps, timeouts, and which workflows run without a policy.

**Governance and safety.**

- Guardrail hits by stage (pre-LLM, post-LLM, pre-tool) and detector, with the action taken (block / redact / flag) and the top affected agents.
- PII posture: redactions per run, de-anonymisation failures, and whether `before_llm` redaction is on per agent.
- Action policy outcomes: external writes allowed / contained (shadow) / denied, by connector and agent; strict-env containment rate should be 100% for shadow agents.
- Compliance register: open compliance deadlines by company, alerts sent, filings on time; DSAR requests by status and time to complete; audit-log HMAC verification result for the period.
- Change log: prompt edits, policy edits, connector credential rotations, role changes, RLS/migration state changes, each with actor and link, from `audit_log`.

Every list here is exportable as CSV, and the governance tab as a one-click "period evidence pack" (PDF summary + CSV appendices + the HMAC verification report) for auditors.

## Operations, reliability, connectors and security

The operations view is built for the COO, developer and platform operator: it turns the trace and tool-call tables into bottleneck and failure pictures, and the security counters into a posture page.

**Reliability.** Success rate, p95 latency and error rate per agent and per workflow with 7-day and 30-day windows; an error-budget tile per agent SLA (target configurable per agent; default 95% success, p95 under 30 s); incident markers from synthetic-check failures overlaid on the latency line.

**Trace analytics (`run_spans`).** For any run, a waterfall of spans (LLM call, tool call, retrieval, HITL wait) with durations; across runs, the top spans by total time and by p95, grouped by `name` and `kind`, so a developer sees that one connector call dominates a workflow. Span attributes are searchable; traces export as JSON.

**Connectors.** A status grid (healthy / degraded / unhealthy / not configured) per connector and company; per connector: calls, success rate, p95 latency, error codes (Pareto), idempotent replays, credential age and rotation due date, OAuth refresh failures, and the agents that depend on it. Actions: test connection, rotate credential, pause dependent agents. Bridge sessions (Tally) show connected/disconnected time and request backlog.

**Queues and workers.** Celery queue depth and oldest message age per queue, beat heartbeat, task failure rate by task name, scheduled-report and compliance-alert runs with outcome; stalled workflow runs and HITL items past expiry with one-click re-drive.

**Capacity and limits.** Runs vs plan limit, concurrency vs `max_concurrent_workflows`, document-extraction and RPA queue waits, Gemini/provider daily caps per tenant with headroom; a tile goes to "act" at 80% of any limit.

**Security posture.** Failed logins and lockouts, rate-limit denials by class, scope denials by family, sessions revoked, API-key usage by key (last used, scopes), Grantex token verifications and rejections (bad audience, unknown agent DID, JWKS failures), CSRF/CORS rejections, webhook signature failures by provider, and egress denials. Each series has a 30-day baseline and flags a 3-sigma spike.

**Tenancy posture (platform view only).** Tables with RLS forced vs total, whether the DB role bypasses RLS, Alembic head vs expected, startup DDL flag state, and per-tenant data-quality coverage. These are health facts, not metrics, and render as a checklist.

**Alerts and routing.** Every threshold above produces an alert with severity, owner role and a deep link; alerts fan out to the needs-you list, email, push and Slack (where configured) and are acknowledged in place so the same alert does not re-notify.

## Exports, downloads, reports and API

Every number on the dashboard is downloadable with the filters that produced it, in the format the user's tool wants, on demand or on a schedule, and the same query is available over the API so finance and BI teams never screen-scrape.

**Formats.**

| Object | Formats | Notes |
| --- | --- | --- |
| Any table / explorer | CSV, XLSX, JSON (Parquet later) | Full dimension set, not just visible columns; XLSX carries a Filters sheet and a Definitions sheet (metric definitions and data-quality flags) |
| Any chart | PNG, SVG, PDF, plus its series as CSV/XLSX | Rendered with title, period, filters and data-quality badge burned in |
| Any view / landing page | PDF (paginated); PPTX later (one slide per tile group) | For board and client decks |
| Run evidence | JSON bundle (run, spans, tool calls, gateway records, HITL, audit rows) + PDF summary | Hash of bundle included for chain of custody |
| Period evidence pack | ZIP: PDF summary, CSV appendices, HMAC verification report | Auditor view |
| Money-saved report | XLSX with assumptions sheet; PDF narrative | Finance |
| FinOps pack | XLSX (spend by BU / cost centre / agent / model), PDF | Monthly, schedulable |
| Partner client pack | PDF per client company + XLSX rollup | CA firms |

**Behaviour.** Exports up to 50,000 rows run inline (v1: `GET /api/v1/insights/metrics/{key}/export`); larger ones run as a background job (Phase 3) and deliver by email, push and an in-app Downloads page with 7-day retention. Downloads go through an authenticated API route backed by cloud storage: a storage signed URL cannot be revoked on logout-all, and report files today land in the worker's local `/tmp`, which the API cannot read on Cloud Run. Exports respect the caller's domain and company scope, apply PII masking according to the tenant's masking policy (raw values only for roles with `audit:read` plus an explicit "include PII" confirmation that is itself audited), and every export writes an `audit_log` row (who, what, filters, row count, format).

**Scheduling.** Any view can be saved as a report schedule (daily / weekly / monthly / cron) with recipients, format and delivery channel (email or Slack; WhatsApp later). Scheduled reports are never delivered when the underlying data is in a demo or fallback state; the run is marked failed with the reason, as the report engine already does.

**API.** `GET /api/v1/insights/metrics/{metric_key}` with `from`, `to`, `granularity`, `group_by[]`, `filter[...]` returns series or rows plus metadata (`definition`, `source`, `data_quality`, `computed_at`). In v1, `GET /api/v1/insights/metrics/{metric_key}/export?format=csv|xlsx|json` serves inline exports of up to 50,000 rows. From Phase 3, `POST /api/v1/insights/exports` creates an export job and returns a job id, and `GET .../exports/{id}` returns its status and an authenticated download link. Reads are rate-limited under the `reporting-read` class and exports under `reporting-export`, scoped by the same RBAC families as the UI, and documented in OpenAPI. Tenants connect their own BI tools directly to a read replica over read-only `insights_*` views. GUC-based RLS cannot isolate a SQL login (any session can SET the tenant setting), so each tenant gets its own login role, policies are tied to the login (`current_user`), and restrictive policies stop the existing tenant policy from widening access; Metabase is the reference integration, with Apache Superset and Grafana verified next.

**Freshness.** Each export carries `computed_at`; landing tiles refresh every 60 s, explorers on demand, daily rollups by 02:00 tenant local time; a late-rollup banner shows if yesterday's daily aggregates are missing.

## Visualisation standards and interaction rules

Charts are chosen by the shape of the data, not by what looks rich; one system of colour, scale and interaction across every view so a user who learns one tile understands all of them.

| Data shape | Chart | Rules |
| --- | --- | --- |
| One quantity over time | Line (area only when stacked parts sum to a whole) | Compare period as dashed line; thresholds as reference lines; p50/p95 as two lines, never averages alone |
| Several series summing to a whole over time | Stacked area / stacked bar | Max 8 series, rest grouped as "other"; legend click isolates |
| One measure across items | Horizontal bar, sorted | Top N + "other"; value label at bar end |
| Parts of a whole at one point | Treemap or 100% bar | No pie charts above 5 parts |
| Distribution | Histogram or box plot | Floor/SLA marker drawn |
| Range or forecast | Point + whisker band | Never floating bars or a single-edge line |
| Gross-to-net | Waterfall | Money saved, budget variance |
| Funnel | Funnel scaled by count | Drop-off % between stages shown |
| Two measures per item | Scatter with quadrant lines | Cost/run vs success rate; label outliers only |
| Item × period or item × item | Heatmap | Workflow × step latency; task\_type × corrected field |
| Flow between stages | Sankey | Workflow definition → outcome; approvals by role → decision |
| Hierarchy | Org tree / collapsible tree | Virtual employees, cost centres |
| Status across many entities | Status grid | Connectors, synthetic checks |
| Progress to a target | Bullet chart | Budgets, shadow gates, plan limits |
| A single KPI | Decision tile: value, delta vs compare, state, sparkline, one action | Never a bare number |

**Colour and state.** One categorical palette (max 8), semantic colours reserved for state (good / warning / critical) and never reused for categories; data-quality states shown as a badge, with `estimated` series hatched and `simulated`/`demo` views carrying a banner. Light and dark themes from tokens; print/PDF uses the light theme.

**Interaction.** Hover shows the exact value, period and definition; click drills; shift-click adds to the filter; brushing a time range refilters the page; every chart has a "view data" table toggle and an export menu; empty and error states say what is missing and how to populate it (e.g. "No rate card for gemini-2.5-pro — add one in FinOps settings"); loading shows skeletons, never spinners over stale numbers.

**Accessibility and numbers.** WCAG 2.2 AA contrast; every chart has a text summary and keyboard-navigable data table; units in axis titles and headers; INR formatted with Indian grouping (12,34,567) and USD with western grouping; percentages to one decimal; durations humanised (1.2 s, 4 m 10 s); time zones shown per tenant setting.

**Performance.** First meaningful paint under 1.5 s on landing views from pre-aggregated daily tables; explorers paginate server-side; charts render up to 10k points client-side and down-sample beyond; no chart queries raw `run_facts` for ranges over 31 days without the daily rollup.

## Data model, pipelines and gaps in the current schema

Production records almost none of the tables this document reads unless recording is on: run tracing (`run_spans`), model gateway records and spend metering are being turned on in production as part of this delivery (owner decision 1 Nov 2026). `tool_calls` has no writer yet, so tool-usage metrics wait for one.

Most metrics are computable from tables that already exist, with one critical exception: **there is no runtime writer for per-run facts.** `agent_task_results` is written only by `core/seed_demo_data.py`; the LangGraph runner writes an `audit_log` row (`event_type = agent.run`), the gateway writes `model_gateway_records`, and FinOps writes `spend_usage_records`, but nothing persists one row per run with status, confidence, duration, HITL flag and validated outcome fields. Today `/costs/*` and the `/kpis/*` fallbacks read `agent_task_results` and therefore show zeros or seeded demo rows. The dashboard needs a thin rollup layer, a run-fact writer, new tables (v1 adds three; the others arrive with the phases that use them), and a few counters the runtime does not emit yet. Nothing is served from in-memory stores.

**Existing sources the dashboard reads directly.** `agents`, `agent_versions`, `agent_lifecycle_events`, `agent_task_results` (not migrated: every row comes from the demo seeder, which now also writes `run_facts` rows labelled `seeded`), `agent_cost_ledger`, `agent_feedback`, `agent_ratings`, `agent_memories`, `shadow_comparisons`, `hitl_queue`, `workflow_runs`, `workflow_run_states`, `step_executions`, `workflow_event_waits`, `workflow_variants`, `run_spans`, `tool_calls`, `model_gateway_records`, `spend_usage_records`, `spend_usage_rollups`, `spend_meter_gaps`, `spend_rate_cards`, `spend_commitments`, `spend_fx_rates`, `finops_cost_ledger`, `budget_alerts`, `billing_subscriptions`, `eval_runs`, `guardrail_rules`, `connectors`, `connector_configs`, `synthetic_checks`, `audit_log`, `dsar_requests`, `compliance_deadlines`, `report_schedules`, `txn_findings`, `commerce_*`, `customer_sessions / customer_mandates / customer_actions`.

**New tables (RLS forced, tenant-scoped).** v1 adds `run_facts`, `insights_daily` and `insights_rollup_state` in migration `v6z106_insights_foundation`; `value_baselines`, `value_rates`, `insights_alerts`, `security_events_daily`, `saved_views` and the `guardrail_events` counter arrive with the phases that use them.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `run_facts` | **One row per agent run, written by the runtime.** There is no single run-finished hook, so it is written at the LangGraph runner (agents, channels, chat, A2A, MCP, voice, HITL resume, debug), `BaseAgent.execute` (workflow steps, sales), customer-agent turns and RPA. Bridge is a tool proxy, not an agent run, and writes none. Partitioned monthly by `started_at`; replaces the seed-only `agent_task_results` as the run truth | `tenant_id`, `company_id`, `run_key` (unique per tenant), `entrypoint`, `agent_id` / `agent_ref`, `agent_type`, `agent_version`, `domain`, `correlation_id`, `trace_id`, `workflow_run_id`, `step_id`, `task_type`, `use_case`, `environment`, `status`, `confidence`, `confidence_floor`, `duration_ms`, `hitl_required`, `tokens_in` / `out` / `cached` / `total`, `cost_usd` (runtime-reported), `provider`, `model`, `error_class`, `outcome_validated`, `data_quality` (measured / seeded), `started_at`; typed `outcome_*` fields arrive in Phase 2 |
| `insights_daily` | Pre-aggregated facts per tenant-local day × company × agent × agent type × domain × entrypoint × task\_type × status × environment × data quality | `runs`, `hitl_required`, `outcome_validated`, tokens in / out / cached / total, `cost_usd`, duration sum and count, 12 fixed latency buckets, confidence sum and count, `below_floor`. Percentiles use buckets, not t-digest: t-digest needs an extension the migrations do not install, and per-day percentiles cannot be combined, whereas bucket counts sum across days and p50 / p95 are estimated from them |
| `insights_rollup_state` | Rollup and reconciliation state per tenant-day | `tenant_id`, `day`, `status` (rolled / reconciled / mismatch), source vs rolled runs, tokens and cost, `detail` |
| `value_baselines` | Minutes per task\_type and role, setup cost, provenance and confirmation | tenant\_id, task\_type, baseline\_minutes, role, setup\_cost, source (seeded / tenant), confirmed\_by, confirmed\_at, updated\_by, updated\_at |
| `value_rates` | Loaded hourly cost per role | tenant\_id, role, hourly\_cost, currency, source, updated\_by |
| `insights_alerts` | Alert instances with acknowledgement state | tenant\_id, metric\_key, severity, owner\_role, entity\_ref, fired\_at, acked\_by, acked\_at, actioned\_at, deep\_link |
| `security_events_daily` | Daily counters of auth and policy events (today only in Redis / logs) | tenant\_id, day, event\_kind, count |
| `saved_views` | Named filter sets per user / tenant | tenant\_id, user\_id, view\_key, filters JSON, shared |

**Runtime counters to add.** PII redactions and de-anonymisation failures per run; guardrail hits by stage and detector (`guardrail_events`); action-policy outcomes (allowed / contained / denied) per connector; `attempt` on `tool_calls` for retry analysis (once `tool_calls` has a writer); `first_viewed_at` on `hitl_queue` for review time; `outcome_validated` set by the runner when `output_schema` validation passes; login, lockout, rate-limit, scope-denial, session-revocation and Grantex-rejection events emitted as structured logs and rolled into `security_events_daily`.

**Pipeline.** Celery beat tasks (`core/tasks/insights_tasks.py`) roll each tenant's local today every 15 minutes, roll closed days after 02:00 tenant-local, and reconcile yesterday after 02:30 tenant-local; each rollup is idempotent per (tenant, day) under a transaction advisory lock; landing tiles read `insights_daily` plus a bounded live query for the current day. Rollups are platform-context jobs that iterate tenants and then work under `get_tenant_session` so RLS holds. Late data (records revised via `spend_usage_records.revised_at`) triggers a re-rollup of the affected days. Tenant-local days come from the tenant's reporting settings (timezone, fiscal-year start month, reporting currency) stored in tenant settings; spend rollups still use one reporting timezone.

**Metric registry.** `core/insights/registry.py` declares every metric once: key, definition text, SQL or Python resolver, dimensions allowed, unit, data-quality rule, threshold defaults, owner role. The API, the UI tile catalogue and the XLSX Definitions sheet all generate from it, so a definition cannot drift between surfaces. A unit test asserts every tile in the UI references a registry key.

**Known gaps today, verified against the code.** `agent_task_results` has no production writer (above). `api/v1/kpis.py` called `KPICache.get_all_for_role(tenant_id, role)` without `company_id` even though the cache is keyed per company, so cached KPIs ignored the company selector (fixed in Phase 0). `kpi_confidence_status` values are `blocked` / `degraded` / `ready`, and dashboards show a "Demo Data" badge whenever `total_tasks_30d == 0`, which conflates "no data" with "demo" — the new tiles distinguish no data, seeded (tenant ran the seeder) and measured. `budget_alerts` and `finops_thresholds` both express budgets, and `agent_cost_ledger` and `finops_cost_ledger` both express cost: the registry names exactly one cost source per metric (spend metering rollups when metering is on for the tenant, otherwise `agent_cost_ledger`). `agent_task_results.tokens_used` has no input/output split; `run_facts` carries input, output and cached tokens where the runtime's usage metadata provides them. `billing_subscriptions` exists only as a raw-SQL table `(created in v4_0_0_project_apex_tables.py, repaired in v6_z19_repair_billing_cdc_tables.py)`; it needs an ORM model before the plan-usage tile is built.

## Non-functional requirements

**Tenancy and authorisation.** Every query runs under `get_tenant_session`; rollups and platform views use the platform context explicitly and are visible only to the platform-operator identity (an admin of the operator tenant with MFA enrolled). Company scoping follows `_require_company_for_tenant`; domain scoping follows `get_allowed_domains`; routes declare `route_meta` scopes in a new `insights` family mapped to read scopes per role, so an analyst cannot export audit data. Cross-tenant comparison exists only on the platform view and only as anonymised aggregates. A tenant-A-reads-tenant-B test exists for every explorer and every export endpoint.

**Privacy.** PII is masked by default on screen and in exports (emails, names in outputs, actor ids rendered as display names); unmasking requires `audit:read` and an explicit, audited confirmation, as the Exports section states. No tenant identifiers, emails or run ids appear in metric labels or telemetry. `run_facts` and `insights_daily` hold no subject identifiers (no user id or email), so DSAR erasure of the source tables needs no change there.

**Performance and scale.** Landing views p95 under 1.5 s and explorers under 3 s on a synthetic tenant with 1M `run_facts` rows per month generated by `scripts/seed_perf_tenant.py` (new; log-normal durations, 40 agents, 12 task types, 8% failures, 15% HITL) in the staging environment; daily rollups complete inside 20 minutes for 1,000 tenants; 1M-row exports finish inside 10 minutes as background jobs; no query scans more than 31 days of raw rows without the rollup; `run_facts` is indexed on (`tenant_id`, `started_at`) and (`tenant_id`, `agent_id`, `started_at`), and every other fact table read has the equivalent indexes.

**Correctness.** Metric definitions live once in the registry and each resolver has unit tests on fixture data. A nightly job reconciles `insights_daily` against the raw tables per tenant per day on three measures — run count (exact), tokens and cost — excluding records with `revised_at` after the rollup time; any day off by more than 0.01% re-rolls and alerts the platform view. Money-saved figures are reproducible from the exported assumptions sheet; every `estimated` figure carries its inputs.

**Reliability.** Dashboards degrade gracefully: a missing rollup shows a late-data banner and falls back to a bounded live query (at most 31 days, `LIMIT 10000`, 5 s statement timeout); Redis unavailability disables saved-view caching but never blocks a page; failed exports retry three times and then notify the requester.

**Security.** Export downloads are served through an authenticated, single-tenant API route backed by cloud storage, so logout-all revokes access (a storage signed URL could not be revoked); export jobs run under the requesting user's scope, never a service account; alert deep links carry no tokens; each tenant's BI login role reads only the `insights_*` views on the read replica, isolated by login-bound and restrictive policies; the `insights` route-meta scope family and the `reporting-read` and `reporting-export` rate-limit classes are new and registered in `core/rbac.py` and `api/route_enforcement.py`.

**Accessibility and localisation.** WCAG 2.2 AA; keyboard navigation for all tables and charts; screen-reader summaries per chart; English and Hindi UI strings; INR and USD formatting; tenant time zone and fiscal year (April–March default for Indian tenants, calendar year elsewhere) applied to all period presets.

**Observability of the dashboard itself.** Tile load times, query durations, export job outcomes and rollup lag are metrics on the platform view; a synthetic check loads each landing view hourly.

## Phased delivery plan and acceptance criteria

Fourteen weeks in four phases; each phase ships to production behind a feature flag per tenant and is gated by a measurable test, not a demo.

```
P1 · Foundation (weeks 1–4)    P2 · Explorers (weeks 5–8)     P3 · Evidence (weeks 9–11)       P4 · Scale (weeks 12–14)
  Metric registry                9 explorers, drill L1→L4        Trace waterfall, run bundle       Insights API + BI views
  run_facts writer + rollup      Money-saved model               Compliance + Eng views            Partner + Platform views
  Exec + Finance views           Alerts + needs-you list         Evidence packs, PDF/PPTX          Saved views, Hindi UI
  CSV/XLSX export                Ops, HR, Mkt, Commerce          Scheduled reports                 Rehome, then retire legacy
        ◆ gate: rollup = raw ±0.01%   ◆ gate: 5 roles, 7 tasks unaided    ◆ gate: auditor signs pack   exit: legacy pages off, p95 < 1.5 s
```

Phase 1 lays the registry, the `run_facts` writer and the rollup so every later tile reads one source; its gate is the nightly reconciliation of `insights_daily` against raw tables passing for 14 consecutive nights. Phase 2 delivers the explorers and the money-saved model; its gate is a moderated usability test with one participant for each role whose view is live by then (admin, CFO, CHRO, CMO, COO; the auditor, developer and partner views ship later and are tested in their own phases, as are back-office, merchant, analyst and platform) completing these seven tasks unaided within 3 minutes each, pass = every participant completes at least 6: find the most expensive agent this month; export last month's approvals as XLSX; explain a cost spike (name the driver); promote a shadow agent or state why it is ineligible; list agents idle for 30 days; revoke a learned rule; confirm a savings baseline. Phase 3 is evidence and reporting; its gate is an auditor signing a period evidence pack from a staging tenant, plus the three usability tasks that need Phase 3 features: find why a given run failed (trace), download the finance pack, schedule a weekly report. Phase 4 scales out and retires each legacy page once its working features have a new home.

**Acceptance criteria — approved by the product owner on 29 Oct 2026 as the release gate for the whole programme; every item must be demonstrated before the legacy pages are retired. None is demonstrated yet.** PPTX, Parquet, WhatsApp delivery and the full Hindi UI are deferred (see Decisions) and are not part of this gate.

- [ ] Every tile, chart and explorer references a registry metric key; a unit test fails otherwise.
- [ ] Every number is downloadable in CSV, XLSX and JSON with the filters applied; charts in PNG/SVG/PDF; views in PDF.
- [ ] Money saved shows its assumptions inline, exports with an assumptions sheet, and is labelled `estimated` everywhere.
- [ ] No tile renders demo or placeholder values; missing data renders the defined empty state with the remediation hint.
- [ ] Tenant-isolation tests pass for every explorer and export endpoint, including under a `NOBYPASSRLS` role and each tenant's BI login role.
- [ ] Landing views p95 under 1.5 s and explorers under 3 s on the 1M-runs synthetic tenant.
- [ ] Rollup-vs-raw reconciliation within 0.01% for 14 consecutive nights.
- [ ] Playwright e2e covers each role's landing view, one drill to Level 4, one export per format, and one scheduled report delivery.
- [ ] WCAG 2.2 AA audit passes; every chart has a text summary and data table.
- [ ] Each legacy dashboard page listed in Purpose redirects to the new views and is deleted once its working features have a new home (see Delivery status); Report Scheduler remains as the scheduling UI.

## Decisions

| Decision | Choice |
| --- | --- |
| Run truth | New `run_facts` table written by the runtime: the LangGraph runner (agents, channels, chat, A2A, MCP, voice, HITL resume, debug), `BaseAgent.execute` (workflow steps, sales), customer-agent turns and RPA; bridge is a tool proxy and writes none. `agent_task_results` is not migrated (every row is seeded demo data); the seeder writes `run_facts` rows labelled `seeded` |
| Cost truth | Exactly one cost source per metric: spend metering rollups (`spend_usage_rollups`) when metering is on for the tenant, otherwise `agent_cost_ledger`; `run_facts.cost_usd` for per-run unit economics only |
| Savings unit | Completed business task, deduplicated on `workflow_run_id`; headline hidden until baselines are confirmed |
| Baseline sign-off | The tenant admin confirms seeded baselines and rates (audited); partners cannot confirm on a client's behalf |
| Hourly rates | Country defaults shipped with a cited source; tenant-editable |
| Fiscal year default | April–March for Indian tenants; calendar year elsewhere (Philippines included); configurable per tenant |
| Cross-tenant benchmarks | Out of scope for v1; if added later, opt-in only with at least 20 tenants in any cohort |
| BI integration | Free/open-source tools first — Metabase, then Apache Superset and Grafana — over read-only `insights_*` views on a read replica, through per-tenant login roles with login-bound and restrictive policies; Power BI and Looker later on request |
| Retention | 25 months of daily rollups; 7 days for export files; audit evidence packs follow the 7-year audit setting |
| Commerce metrics | Same registry; the merchant runtime UI shows the commerce subset |
| Later (not in the 14-week plan) | PPTX export of views, Parquet, full Hindi UI (landing views first), WhatsApp delivery, cross-tenant benchmarks, Power BI / Looker |
