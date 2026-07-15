# Complete Product Readiness Gap Analysis

**Audit date:** 2026-07-13
**Repository version:** 4.8.0
**Repository baseline:** `384543788bcd1f66aed8cff8ab03699ae384926e` on `agent/security-runtime-audit`
**Status:** point-in-time repository and public-surface audit; not a release approval
**Accountable owner/reviewer:** unassigned until roadmap item `W0-05`
**Last reviewed:** 2026-07-15
**Next review:** material implementation change, owner assignment, or 2026-07-27
**Limitations:** no real provider, controlled tenant, cloud-console, or complete visual-browser evidence was available; mutable external observations must be recaptured before decision use.
**Related test:** `tests/regression/test_readiness_documentation.py`
**Related runbook:** [BUILD_ROADMAP.md](BUILD_ROADMAP.md)
**Scope:** Marketing/CMO, Finance/CFO, CA firms, HR/CHRO, COO, CBO, landing pages, documentation, security, data, integrations, workflows, testing, observability, release engineering, and production operations.

## Executive verdict

AgenticOrg has a broad enterprise control-plane foundation and a large amount of deterministic code. It is not yet production-complete across any of the six requested domains.

The runtime inventory reports 29 registered core agents, 56 registered connectors, and 399 indexed tools. Those counts describe code registration, not verified capability. The current core-agent split is six Finance, six HR, eight Marketing, six Operations, and three Back Office agents. Several public surfaces describe additional prompt-only or LangGraph wrappers as equivalent production agents.

| Domain | Internal maturity | Gate | Public posture | Why it is not ready |
|---|---|---|---|---|
| Finance / CFO | **Implemented** | **Blocked** | Beta | Six first-class finance agents exist, but four advertised finance roles are thin wrappers; the CFO dashboard is generic run telemetry; pre-write financial controls are not centrally enforced. |
| CA firms | **Integrated** | **Blocked** | Beta workspace/draft; filing unavailable | Multi-company, pack, credentials, portal, billing, approvals, and draft compliance paths exist, but approval does not authorize the exact filing payload and filing/payment tools can execute before HITL. |
| Marketing / CMO | **Implemented** | **Blocked** | Beta | Strong readiness projections and fail-closed step controls exist; eight of nine agent contracts are beta and no real-vendor or sandbox proof row exists. Workflow-run creation does not enforce the readiness projections. |
| HR / CHRO | **Scaffolded** | **Blocked** | Preview | Six registered classes exist, but there is no HR system of record, tools often do not match registered connectors, writes precede approval, and the dashboard has no HR business KPIs. |
| COO | **Scaffolded** | **Blocked** | Preview | Jira/Zendesk/ServiceNow/PagerDuty clients exist, but agents, workflows, persistence, approvals, and COO metrics are disconnected; destructive runbooks can run before HITL. |
| CBO | **Scaffolded** | **Blocked** | Preview | No complete CBO role/RBAC, domain taxonomy, system of record, substantive workflows, business KPIs, or production tests exist. Public claims materially exceed implementation. |

## Audit method

The audit traced each domain through public claims, routes, dashboards, API handlers, data models, registered agents, tool resolution, workflow templates, connector clients, approval timing, audit persistence, tests, documentation, and production operations. It treated mocks, seeded data, prompt wrappers, registry entries, and historical release notes as lower-level evidence.

The following checks were also run:

- `python scripts/consistency_sweep.py` passed its existing version/count checks, but that sweep does not validate capability truth, outcomes, domain readiness, or connector certification.
- Public HTML retrieval showed the home page correctly, while `/solutions/cmo` returned generic platform metadata/content rather than route-specific CMO content.
- The in-app Browser runtime failed to initialize twice and timed out during setup; full visual/browser sign-off remains an explicit follow-up.

## Post-baseline local implementation delta

The baseline findings below remain the audit record for commit `384543788bcd1f66aed8cff8ab03699ae384926e`. The working tree now contains selected containment and control-plane foundations: an action-risk policy, strict shadow-only handling for unsafe actions, tenant/company context propagation on selected paths, a persisted capability-readiness ledger and admin API, connector company-scope changes, a public-claim scanner, a typed billing catalog, and public-copy remediation. These changes are local and unpromoted. They have no merged-PR, required-check, migration-application, deployment, sandbox, pilot, owner, or external-approver evidence and do not close any P0 in full.

### P0 traceability and claim crosswalk

This crosswalk records the exact local implementation delta without rewriting the original findings. `Pending` evidence means no readiness promotion is permitted. Claim treatment is conservative and applies until an unexpired registry record says otherwise.

| P0 | Capability rows | Roadmap packages | Local implementation reference | Local test reference | Evidence state | Permitted claim treatment |
|---|---|---|---|---|---|---|
| P0-01 External write before approval | `PLAT-C02`, `PLAT-C03` | `W0-06`, `PLAT-02/03/04` | `core/governance/action_policy.py`, `core/tool_gateway/gateway.py`, `core/langgraph/agent_graph.py`, `workflows/step_types.py` | `tests/unit/test_action_policy.py`, `tests/unit/test_workflow_no_false_success.py` | Local containment tests only; signed/expiring/single-use exact-payload authorization and provider evidence pending | Unsafe live action hidden/unavailable; shadow/draft behavior may be qualified |
| P0-02 Entry-point divergence | `PLAT-C04`, `PLAT-C05` | `PLAT-05/06/07` | Context propagation in `core/agents/base.py`, `core/agents/registry.py`, `core/langgraph/runner.py`, `core/langgraph/tool_adapter.py`, `api/v1/agents.py`, `workflows/step_types.py` | Action-policy and workflow regression tests above | No five-entry-point parity bundle; durable attempt/outcome audit and MCP/A2A company contract pending | Hidden as unified-runtime claim |
| P0-03 Server authorization gaps | `PLAT-C01`, `PLAT-C04` | `PLAT-00/01/06` | `api/deps.py`, readiness-admin authorization, connector company-scope model/migration | `tests/unit/test_capability_readiness_api.py`, `tests/security/test_connector_company_scope.py` | Selected boundaries only; all by-ID/domain routes not recertified | Qualified only; no blanket isolation claim |
| P0-04 Sensitive reasoning in logs | `PLAT-C05` | `PLAT-08`, `PRIV-01` | No sufficient closure implementation; baseline redaction components remain partial | Dedicated stored-trace leakage/retention suite pending | Pending privacy model, deletion/legal-hold and integrity evidence | Hidden |
| P0-05 Untrusted KPI pipeline | `PLAT-C08`, domain command-center rows | `DATA-01/02/03/04`, `KPI-01/02`, domain cockpit packages | Readiness ledger does not create authoritative domain facts | Domain lineage/reconciliation tests pending | No reconciled domain-source or pilot evidence | KPI samples illustrative only |
| P0-06 Workflow truth | `PLAT-C06` | `WF-01/02/03` | Action policy reaches workflow connector steps; compiler/runtime migration remains open | `tests/unit/test_workflow_no_false_success.py` plus existing domain linters | Containment regression only; no installed-definition migration/replay bundle | Template inventory qualified; executable-readiness claim hidden |
| P0-07 CA filing boundary | `CA-C05/06/07`, `PLAT-C03/04/07` | `CA-03/04/07`, `CONN-03` | Company-scoped connector configuration and public filing-claim removal | `tests/security/test_connector_company_scope.py`, `tests/unit/test_ca_api_functional.py` | No valid-DSC exact-payload sandbox submission/receipt evidence | Filing/payment unavailable; draft/manual handoff qualified |
| P0-08 Employment and destructive Operations | `HR-C02/03/07/11`, `OPS-C03/10` | `HR-02/03/07`, `OPS-04/07` | Central action taxonomy keeps unsafe actions shadow-only in strict environments | `tests/unit/test_action_policy.py` | No HR/legal/privacy or change-authority pilot evidence | Preview/draft only; no autonomous decision/remediation claim |
| P0-09 Public claim overreach | `PLAT-C12` plus all surfaced capability rows | `W0-02/03`, `WEB-01/02/05`, `DOC-04/06` | `core/claims/`, `config/public_claim_registry.json`, `scripts/lint_public_claims.py`, typed billing catalog, public-copy and generated-LLM remediation | `tests/unit/claims/`, `tests/unit/test_billing_catalog.py`, public UI tests | Local scan/build evidence only; owners unassigned, registry coverage/expiry and deployed crawler/visual evidence pending | `PLAT-INVENTORY-PRODUCT-FACTS` evidence-backed only for repository inventory; `PLAT-CLAIM-CONTROL-PREVIEW` illustrative; all other claims hidden/qualified |
| P0-10 Release enforcement/topology | `PLAT-C09/10` | `W0-07`, `REL-01/02/03` | Readiness migrations and route inventory exist locally; production job/topology unchanged | Migration/API tests and existing deploy-script regressions | No protected-main, release manifest, worker/beat parity, deploy, post-deploy, restore or DR evidence | No production-readiness or deployment-success claim |

Source line numbers elsewhere in this baseline refer to the repository baseline above. New evidence must use commit-pinned references or stable symbols so formatting changes cannot silently invalidate traceability.

## P0 release blockers

### P0-01 — External writes can occur before approval

**Evidence**

- The shared graph order is reason → validate scope → execute tools → evaluate → HITL in `core/langgraph/agent_graph.py:471-505`.
- The HITL evaluator only understands confidence and simple numeric expressions at `core/langgraph/agent_graph.py:587-625`; the CA sentinel `always_before_filing` is not a recognized condition.
- CA filing/payment tools are authorized in `core/agents/packs/ca/__init__.py:120-185`.
- HR and Operations classes perform mutations before their later HITL sections, including talent decisions, offboarding/access changes, support replies, and IT remediation.
- Finance does not use the marketing-only external-write confirmation path in `workflows/step_types.py:251-272,342-368`.

**Impact:** financial postings, statutory filings, payments, employment decisions, customer communications, and operational remediation may occur before the advertised human decision.

**Closure gate:** classify every tool action by risk; interrupt before dispatch; bind approval to tenant, company, actor, tool, provider, payload hash, policy version, expiry, and idempotency key; prove zero connector calls before approval/reject/expiry and exactly one after a valid approval.

### P0-02 — Execution behavior diverges by entry point

**Evidence**

- Direct agent, A2A, and MCP paths use the generic LangGraph runner (`api/v1/agents.py:2426-2508`, `api/v1/a2a.py:107,158`, `api/v1/mcp.py:101,197`).
- Workflow steps instantiate handwritten classes through `AgentRegistry.create_from_config` (`workflows/step_types.py:497-571`).
- Those workflow-created classes normally receive no ToolGateway, and `core/agents/base.py:417-425` returns a no-gateway error for tool calls.
- Fake mode swaps specialized agents for `BaseAgent`, allowing functional tests to pass without domain behavior (`workflows/step_types.py:560-570`).
- Unlinked agents may scan the global connector registry because `connector_names_for_tools=None` is unconstrained in `core/langgraph/tool_adapter.py:409-417,493-520`.

**Impact:** the same agent can plan different tools, apply different controls, fail differently, and produce different audit evidence depending on whether it was called from the UI, workflow, A2A, or MCP.

**Closure gate:** one executor and one versioned AgentRun contract for UI/API/workflow/A2A/MCP; tenant-bound healthy connector IDs are mandatory; tool names resolve deterministically; policy, HITL, persistence, idempotency, confirmation, and audit are identical across surfaces.

### P0-03 — API role metadata is not equivalent to authorization

**Evidence**

- `route_meta` describes routes but does not enforce handler behavior (`api/route_metadata.py:35`).
- Agent run and workflow run by-ID paths lack complete object-domain enforcement (`api/v1/agents.py:2284-2289`, `api/v1/workflows.py:753-757`).
- CHRO, COO, and CBO KPI routes require a tenant but do not enforce the role/domain boundary in the handler (`api/v1/kpis.py:930-944,968-1001`).
- UI route guards cannot secure direct API calls.
- CBO is absent from the central RBAC/domain/scope and invite-role maps (`core/rbac.py:5-61`, `api/v1/org.py:167-171`).

**Impact:** a same-tenant authenticated user with a known object ID may cross a domain boundary, while a legitimate CBO identity receives no coherent permissions.

**Closure gate:** every by-ID read/run/cancel/approve/KPI route invokes server-side scope, tenant, company, and domain authorization; wrong-domain tests return 403 and produce a denial audit event.

### P0-04 — Sensitive HR and business reasoning can be rehydrated into durable logs

**Evidence**

- The generic runner redacts before the LLM but rehydrates both output and reasoning trace (`core/langgraph/runner.py:165-202,282-305`).
- The API persists restored reasoning in AuditLog and HITL context (`api/v1/agents.py:2777-2823`).
- Audit signatures are nullable and no complete signature assignment path was found (`core/models/audit.py:31`).

**Impact:** candidate, employee, payroll, financial, legal, and customer PII may enter long-lived logs outside the intended user-visible response.

**Closure gate:** keep stored reasoning, traces, events, and approval context redacted by default; selectively rehydrate only authorized response fields; define field-level retention, access, deletion, legal hold, and cryptographic integrity controls.

### P0-05 — The KPI pipeline is not an authoritative business-data pipeline

**Evidence**

- CFO/CHRO/COO/CBO dashboards render agent count, tasks, success, HITL, cost, and domain totals rather than advertised role KPIs.
- `api/v1/kpis.py:67-220` derives generic metrics from `agent_task_results`.
- No non-test writer to `AgentTaskResult` was found; `core/seed_demo_data.py:10,50-79` seeds it to make dashboards nonzero.
- COO maps to `operations/it/support/facilities`, while registered agents use `ops` and Facilities uses `backoffice`, so real activity can be excluded.
- CBO maps to `legal/risk/corporate/comms`, while Legal and Risk use `backoffice`.
- CMO is more honest, but its KPI facts can be read from stored ConnectorConfig metadata rather than a durable vendor ingestion stream (`core/marketing/kpi_schema.py:407-413,1072-1080`).

**Impact:** dashboards can be empty, demo-driven, or structurally unable to see their own agents; public financial, people, operations, and CBO KPI promises are not implemented.

**Closure gate:** persist every run once; create domain systems of record and fact tables; normalize domain taxonomy; ingest vendor data durably; attach source/formula/entity/period/unit/freshness/reconciliation/confidence to every KPI; remove all silent demo substitution in strict runtimes.

### P0-06 — Workflow catalog and runtime do not enforce executable truth

**Evidence**

- The template catalog is metadata only (`core/workflows/template_catalog.py:9`).
- UI template selection still initializes a fixed AP-style definition (`ui/src/pages/WorkflowCreate.tsx:26-34,61-71,115`).
- `workflows/examples/employee_onboarding.yaml` uses `onboarding-agent` while the registry key is `onboarding_agent`; unknown keys silently fall back to BaseAgent.
- Notification examples use Slack shapes unsupported by the executor.
- The CA pack installer synthesizes a short agent chain and places human review last (`core/agents/packs/installer.py:352-418`) instead of installing safe, versioned workflow definitions.
- Marketing has a strong linter, but `/workflows/{id}/run` does not invoke it before run creation (`api/v1/workflows.py:753-829`). The checked-in campaign template fails its own production lint in `tests/unit/test_cmo_marketing_workflow_linter.py:332-345`.

**Impact:** a template may appear selectable and complete while executing the wrong agent, a generic base agent, a malformed notification, or a policy-incomplete production flow.

**Closure gate:** compile and validate versioned workflow definitions at install/save/run; reject undefined agents/actions/connectors, unsupported step shapes, missing policy/approval/idempotency/confirmation metadata, and unsafe maturity states before a WorkflowRun is inserted.

### P0-07 — CA filing authorization and provider evidence are incomplete

**Evidence**

- Filing approval changes database status but is not consumed as an exact submission authorization (`api/v1/companies.py:1523-1675`).
- GST credential verification proves local decryption rather than provider authentication (`api/v1/companies.py:2282-2340`).
- GST submission can fall back to an unsigned POST when DSC is absent (`connectors/finance/gstn.py:225-246`), and a mocked test accepts that path.
- The TDS example pays a challan before partner review (`workflows/examples/tds_quarterly_filing.yaml:97-123`).
- Compliance alerts are logged and marked sent despite an email TODO; the beat schedule does not include the advertised compliance job.

**Impact:** approval, signing, submission, receipt, and notification states can disagree with actual provider state.

**Closure gate:** production filing refuses missing/invalid DSC; approval snapshot is immutable and single-use; provider preflight and submission state machine persist accepted/rejected/unknown outcomes, ARN/challan/receipt, polling, reconciliation, and retry safety; qualified CA owner approves effective-dated statutory rules.

### P0-08 — Employment and destructive Operations decisions are not safely bounded

**Evidence**

- Talent Acquisition can update shortlisted/rejected state before HITL (`core/agents/hr/talent_acquisition.py:131-236`).
- Offboarding can change status and revoke access before HITL (`core/agents/hr/offboarding.py:65-118,266-318`).
- IT Operations contains destructive restart/rollback/delete runbooks and can invoke them before HITL (`core/agents/ops/it_operations.py:25-44,83-141,216-275`).
- Support Triage may update or reply before its approval section (`core/agents/ops/support_triage.py:134-284`).

**Impact:** adverse employment action, access removal, customer communication, or destructive remediation can occur without the promised human/change authority.

**Closure gate:** no automated final employment decision; record consent, rubric, explanation, human decision, fairness/adverse-impact review, appeal, and retention. Mutating operational runbooks are typed, allowlisted, approved before action, idempotent, verified, and compensating/rollback-aware.

### P0-09 — Public claims and structured data exceed evidence

**Evidence**

- Solution pages hardcode outcome figures including 99.7% matching, 11-second invoice processing, a 90-day cash forecast, four-hour close, zero-manual DSC filing, zero-error payroll, 3.2x ROAS, 42% CAC reduction, 88% triage, 4-hour-to-15-minute MTTR, and 30-to-2-day contract review.
- Repository and public copy also use certification/completeness language such as SOC 2 compliant, zero stubs, every connector official, and production-tested templates without a versioned evidence record proving the exact scope.
- Solution-page dashboard cards use static values while the authenticated dashboards expose generic telemetry.
- `ui/index.html` contains unverified aggregate ratings, tool counts, retention, production-tested template, certification/readiness, and feature claims.
- Static components label rotating fixture records as live activity.
- Internal CMO documentation says the domain is not ready for a fully agent-run claim and records no sandbox proof.

**Impact:** buyers, crawlers, evaluators, and sales material receive a stronger story than the product evidence supports.

**Closure gate:** create the claim/evidence registry defined in [LANDING_AND_DOCUMENTATION_BLUEPRINT.md](LANDING_AND_DOCUMENTATION_BLUEPRINT.md); remove or label unsupported claims; route all public facts and structured data through approved evidence; add CI drift tests.

### P0-10 — Release enforcement and production topology are incomplete

**Evidence**

- The GitHub production job is hard-disabled in `.github/workflows/deploy.yml:531-540`.
- Post-deploy E2E depends on the disabled deploy job.
- The official helper deploys API, UI, and migrations, not the background worker and beat services required for schedules and durable operations.
- Repository settings have no branch protection or ruleset enforcing reviews/checks.
- Backup/DR documentation promises quarterly drill reports in `docs/dr-drills/`, but that directory/evidence is absent.

**Impact:** green CI does not prove deployment, background processors can drift, required checks are procedural, and recovery promises lack retained exercise evidence.

**Closure gate:** protected main with required checks and approvals; one release manifest deploys API/UI/worker/beat/migrations by immutable digest; staged health and rollback tests; post-deploy E2E; scheduled security scan; backup restore and DR exercise with measured RPO/RTO evidence.

## Domain gap register

### Finance / CFO

**Implemented foundation**

- AP, AR, reconciliation, tax, FP&A, and close have concrete registered classes under `core/agents/finance/`.
- Oracle, SAP, Tally, GSTN, Stripe, QuickBooks, Zoho Books, Banking AA, Income Tax, Pine Labs, NetSuite, TRACES, and PT connector clients exist.
- Generic company isolation, approvals, reports, workflows, audit, secrets, and telemetry foundations exist.

**Material gaps**

- The advertised Treasury, Expense, Revenue Recognition, and Fixed Assets roles are not equivalent first-class finance engines.
- CFO UI does not implement cash runway, DSO/DPO, AP/AR aging, P&L/BS/CF, forecast, close progress, tax status, or financial lineage.
- No canonical ledger/fact model, chart-of-accounts mapping, fiscal/calendar/currency normalization, financial reconciliation service, or board-pack evidence model exists.
- Provider sandbox and controlled-pilot evidence is absent for complete post/payment/file paths.
- Finance-specific SLOs and alerts are missing.

**Priority:** close shared P0 controls; then build the finance data model and read-only command center before enabling any posting/payment autonomy.

### CA firms

**Implemented foundation**

- Tenant-scoped company/client operations, durable pack installation, encrypted GST credentials, approval records, client portal, PT draft/manual flow, TRACES offline reconciliation, CA billing ledger, and multi-client UI exist.
- Capability status APIs candidly distinguish several draft/manual/unwired paths.

**Material gaps**

- Pack install topology differs from the advertised workflow definitions.
- GST/TDS/PT/Income Tax/MCA coverage is not a certified, effective-dated statutory rules platform.
- Approval, signing, submission, receipt, polling, reconciliation, notification, client billing delivery, and payment collection are not one state machine.
- Live provider tests are mocked; release sign-off already notes live filing/DSC proof is missing.
- Filing SLOs, cutoff runbooks, unknown-outcome incident handling, and provider-auth monitoring are missing.

**Priority:** ship only workspace + draft/reconcile/review/manual handoff until the P0 filing boundary is closed and a qualified CA approves supported scope.

### Marketing / CMO

**Implemented foundation**

- Eight first-class core Marketing agents plus a separate Email LangGraph path exist.
- Connector setup/contracts, mapping/backfill, activation, policy, escalation, audit, KPI lineage/reconciliation, report gates, work queue, approval review, and proof projections are substantial.
- The CMO KPI/readiness response suppresses demo/mock proof in strict mode and exposes blockers; workflow-run creation does not yet enforce that protection.
- Signed SendGrid, Mailchimp, and MoEngage webhook handlers exist.

**Material gaps**

- Only Campaign Pilot has a production contract; eight other CMO contracts are beta/test-oriented.
- Workflow-run creation does not enforce the CMO readiness projection.
- CMO capability metadata in the UI is stale relative to current beta contracts.
- KPI facts can come from connector config metadata instead of durable vendor sync.
- ABM intent can fall back to seeded placeholder scores, and ABM launch updates a database status without a vendor write.
- A/B engine and YAML are not wired to the Email action/runtime/vendor confirmation path.
- Sandbox setup can mark credentials healthy without a real vendor health/scope check.
- No real sandbox or real-vendor proof exists.

**Priority:** enforce readiness before run creation, replace placeholder actions with confirmed vendor operations, build durable ingestion/campaign/experiment ledgers, and complete a vendor-sandbox pilot before any production outcome claim.

### HR / CHRO

**Implemented foundation**

- Six registered HR classes and Darwinbox, Keka, Greenhouse, Okta, DocuSign, LinkedIn Talent, Zoom, and EPFO connector modules exist.
- Generic tenant, workflow, approvals, secrets, audit, and RLS foundations exist.

**Material gaps**

- No Employee, Candidate, Job, PayrollRun/Line, Leave, Performance, Training, or Offboarding domain models/API exist.
- Handwritten agents call mismatched or nonexistent GreytHR, workspace, Slack, Freshdesk, and EPFO tool methods.
- Payroll uses hardcoded statutory/tax assumptions rather than an effective-dated governed rules service.
- Performance and L&D are prompt wrappers; golden datasets use registry keys that do not match the runtime.
- No consent, fairness, adverse-impact, appeal, or HR-log retention control supports recruiting.
- CHRO UI has no headcount, vacancy, hiring, attrition, payroll, leave, performance, skills, engagement, case, or compliance KPIs.
- Tests are predominantly AsyncMock/fake-mode structural tests.

**Priority:** build the HR system of record and privacy/employment decision controls first; implement recruiting/onboarding/offboarding read-only and approval-gated pilots before payroll or statutory writes.

### COO

**Implemented foundation**

- Jira, Zendesk, ServiceNow, PagerDuty, Confluence, sanctions, and MCA clients exist.
- Support Triage, Support Deflector, Vendor Manager, IT Operations, Compliance Guard, and Contract Intelligence are registered under `ops`.

**Material gaps**

- No Ticket, Incident, SLA, Vendor, Contract, Asset, Facility, Change, Problem, Inventory, Order, Quality/CAPA, or continuity domain models exist.
- Support and vendor agents call nonexistent/mismatched tools and can invent fallback scores or fixture metrics.
- Support Deflector uses process-local metrics; IT Operations measures function elapsed time as MTTR rather than detection-to-resolution.
- Facilities is under Back Office, not a coherent COO taxonomy.
- Template selection does not produce executable HR/COO graphs.
- COO dashboard has no incident, MTTR, SLA, CSAT, backlog, vendor, facility, supply-chain, quality, continuity, capacity, or cost facts.
- No real HR/Operations connector contract gate exists; manual production probes cover Jira/HubSpot/GitHub only.

**Priority:** implement the service/incident/vendor systems of record, webhook dedupe and SLA clocks, allowlisted read-only diagnostics, then approval-gated remediations with verification and rollback.

### CBO

**Implemented foundation**

- Public and authenticated routes exist.
- Legal Ops and Risk Sentinel are registered Back Office classes; DocuSign, Confluence, ServiceNow, and MCA connector clients can be reused. Facilities is another Back Office registry artifact, but it belongs to the proposed COO scope and is not CBO readiness evidence.
- Generic audit, approval, task, connector, and workflow infrastructure exists.

**Material gaps**

- No coherent CBO identity, domain, invite role, scopes, or navigation contract exists.
- Legal/Risk are wrappers; advertised contract, legal, risk, corporate secretary, data governance, communications, and fraud workflows do not exist end to end.
- No contract/clause/redline/obligation, risk/control/finding, board/resolution/filing, fraud/case, data-classification/lineage, partnership/deal, or portfolio-economics system of record exists.
- `/kpis/cbo` returns generic telemetry and its domain filter cannot see the Back Office agents.
- Public claims, integrations, dashboard values, and customer outcomes are static.
- No `docs/cbo_guide.md` or substantive CBO test suite exists.

**Priority:** ratify the working Chief Business Officer scope, ownership boundaries, and supported/out-of-scope matrix; add RBAC/taxonomy and domain models; then deliver contract/deal/risk/board read-only workflows before any filing, signature, disclosure, or fraud response action.

## Cross-platform shared gaps

| Area | Required work |
|---|---|
| Data platform | Canonical domain models, effective dating, event ingestion, source lineage, reconciliation, schema registry, retention, and company/entity/currency/time semantics. |
| Domain taxonomy and migration | `ops`/Operations/IT/support/facilities/Back Office and other runtime tags disagree across registry, RBAC, KPI filters, workflows, and historical telemetry; version, backfill, reconcile, cut over, rollback, and remove silent BaseAgent/global-connector fallbacks. |
| Capability evidence control | At the baseline, no persisted row-level readiness ledger existed. A local ledger/model/API/migration now exists, but it has not been migrated, seeded with reviewed domain records, merged, deployed, or backed by sandbox/pilot evidence. |
| Connector control plane | Real health/scope/account tests, tenant-bound IDs, secret rotation, sync/backfill, rate limits, retry/idempotency, webhook replay protection, degraded mode, certification matrix. |
| Policy and approvals | Central risk taxonomy, pre-dispatch authorization, dual control, payload snapshots, expiry, delegated authority, override/change/reject, and immutable audit evidence. |
| Workflow engine | Versioned executable definitions, compile-time lint, durable waits, resume, retries, sagas/compensation, migration, and state replay. |
| Observability | Domain SLIs, business-event metrics, source freshness, approval SLA, connector auth, unknown outcomes, reconciliation breaks, run lineage, and PII-safe traces. Existing Grafana queries expect a `tenant_id` label that the Prometheus metrics intentionally do not emit, so dashboard queries require contract tests against the actual label schema. |
| AI/model governance | Versioned model/prompt/tool/retrieval/policy inventory, domain eval thresholds, hallucination/citation rules, prompt-injection and tool-output-poisoning tests, drift, fallback/rollback, provider-change review, and model incident response are not one enforceable release system. |
| Test strategy | Production-like config with fake flags off, sandbox contracts, golden business cases, failure injection, idempotency/replay, authorization, accessibility, performance, rollback, and pilot evidence. |
| Security/privacy | Field classification, purpose/consent, least privilege, retention/deletion, subject rights, key/DSC lifecycle, audit integrity, supply-chain/security scanning, and incident evidence. Employment, confidential HR cases, support records, physical-access/visitor data, monitoring, marketing consent, contracts, and fraud each need a domain privacy impact/threat model. |
| Release/SRE | Protected branches, immutable release manifests, all service types deployed together, migration compatibility, canary/staged rollout, post-deploy E2E, DR drills, capacity and cost gates. |
| Support | Entitlements, SLO/SLA, severity model, on-call ownership, customer communications, escalation, known limitations, and evidence expiry review. |

## Documentation and landing-page gaps

The documentation corpus is extensive but fragmented and date-bound. `docs/PRD_CxO_v5.0.md` still describes CHRO/COO/CBO pages and KPI cache as nonexistent even though routes now exist; `docs/AGENT_MATURITY_MATRIX.md` marks several capabilities GA/99.9% without current production proof; `docs/agents.md`, README, landing pages, internal CMO backlog, and runtime registries disagree on agent count and maturity.

At the baseline, HR/CHRO, COO, and CBO role guides were missing, while CFO and CA guides overstated business outcomes and filing readiness. Local bounded CHRO/COO/CBO evaluation guides and warning/correction passes now exist; they remain provisional until review. The root `ROADMAP.md` now marks its older v2.4 material as historical and points to the canonical roadmap.

The canonical remediation is [LANDING_AND_DOCUMENTATION_BLUEPRINT.md](LANDING_AND_DOCUMENTATION_BLUEPRINT.md): a route and docs source of truth, evidence-controlled claims, role-specific guide set, trust center, prerendered metadata, shared visual workflow system, and CI checks for routes, links, schema, claims, accessibility, screenshots, and freshness.

## Unknowns requiring external evidence

The repository alone cannot prove these items. They remain open until an owner attaches evidence:

- vendor contracts, supported API terms, and production credentials for statutory, HR, marketing, operations, legal, fraud, and payment providers;
- qualified CA/legal/HR/compliance review of current rules and supported jurisdiction scope;
- real customer/pilot outcome data for all public benchmarks;
- security certifications, penetration-test closure, data-processing agreements, subprocessor review, and retention implementation;
- branch protection, environment protections, cloud IAM, secret rotation, alert routing, budget limits, and audit-log exports in the live account;
- backup success, restore tests, RPO/RTO measurements, background worker/beat revision parity, and full production topology;
- visual, accessibility, cross-browser, and mobile behavior in a working browser session.

## Overall recommendation

Do not execute the roadmap as six independent feature lists. Close the shared authorization, executor, persistence, domain-access, PII, workflow, connector, evidence, and release foundations first. Then promote one thin, read-only, reconciled vertical per domain; add approved external writes only after sandbox evidence; and enable public claims only after controlled-pilot evidence and business-owner sign-off.
