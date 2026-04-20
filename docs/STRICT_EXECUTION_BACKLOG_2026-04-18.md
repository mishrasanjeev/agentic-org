# Strict Execution Backlog

Date: 2026-04-18
Source review: [PRODUCT_GAP_REVIEW_2026-04-18.md](./PRODUCT_GAP_REVIEW_2026-04-18.md)
Purpose: convert the review into a strict, sequenced execution backlog without starting implementation
Status: not started

## Program Rules

These rules apply to every item below.

1. No feature may ship with decorative enterprise state.
   If the UI says `Connected`, `Configured`, `Healthy`, `Compliant`, `Tests Passing`, or similar, that state must come from a backend source of truth.

2. No public or in-product claim may remain unverified.
   Version, connector count, MCP tool count, test count, and security/compliance claims must be derived from maintained sources or removed.

3. SDK examples are release-blocking.
   Every SDK snippet surfaced in docs, README, UI, or package examples must be executable against the current API contract.

4. Governance UX must be real.
   Compliance, data-region, retention, RBAC, audit, HITL, and explainability surfaces must be backed by persisted system state and audit trails.

5. No "100%" claim may be used loosely.
   "100% completed", "100% coverage", or "all tests complete" may only be used where there is measurable proof.

6. Every completed backlog item must include proof.
   Proof means code change, test coverage, passing validation, and updated docs if behavior changed.

## Global Definition Of Done

An item is only done when all of the following are true:

- Behavior is implemented in backend and UI or SDK as required.
- Tests exist for the happy path, failure path, and authorization path where relevant.
- Existing tests pass.
- New or changed examples/docs are accurate.
- Auditability is addressed for enterprise-facing admin/governance features.
- Marketing or UI copy is aligned with actual capability.

## Release Gates

These are program-level gates, not single-task gates.

### Gate A: Trust Restoration

- No known mismatch remains between API behavior, SDK types, UI examples, and MCP docs.
- No hardcoded enterprise health/compliance state remains in production UI.
- No inconsistent version / tool-count / test-count claim remains in README, landing, or app shell.

### Gate B: Operational Truth

- Connectors, settings, dashboard metrics, and explainability surfaces are backed by APIs.
- Unauthorized access shows explicit `403` behavior, not confusing redirects.
- Demo/sandbox behavior is isolated from normal product flows.

### Gate C: QA Credibility

- Required backend, frontend, and E2E suites are green in CI.
- Coverage thresholds are enforced by policy.
- Security, RBAC, HITL, SDK, and MCP critical paths have explicit regression coverage.

## Sequenced Workstreams

Execution order is mandatory unless dependencies are formally reworked.

| Workstream | Priority | Why it must happen in this order |
| --- | --- | --- |
| WS-0 Truth Freeze | P0 | Stops further trust erosion while deeper fixes are built |
| WS-1 SDK and API Contract Alignment | P0 | Core developer and platform entry point is currently unsafe |
| WS-2 MCP and Connector Execution Model Alignment | P0 | External agent interoperability claims are mismatched |
| WS-3 Settings and Governance Persistence | P0 | Compliance/admin surfaces must be made real |
| WS-4 Connector Control Plane | P1 | Moves connectors from brochure state to production state |
| WS-5 Dashboard and Information Architecture | P1 | Fixes executive trust and operator usability |
| WS-6 Agent Explainability and Workflow Operations | P1 | Core "virtual employee" trust layer needs real runtime evidence |
| WS-7 QA Baseline and Coverage Program | P0 | Current test posture is not releasable |
| WS-8 Release Consistency and Enterprise Readiness | P1 | Final consolidation before relaunch or enterprise push |

## WS-0 Truth Freeze

### BL-001 Remove or relabel unverified public claims

Priority: P0
Status: not started

Problem:

- README, landing page, and app surfaces show inconsistent version, test-count, and tool-count claims.

Primary evidence:

- `README.md:13`
- `README.md:41`
- `ui/src/pages/Landing.tsx:437`
- `ui/src/pages/Landing.tsx:502`
- `ui/src/pages/Landing.tsx:1539`

Scope:

- README
- landing page
- any UI badge, footer, hero, or pricing copy that asserts counts or version values

Deliverables:

- inventory of all externally visible numeric claims
- keep/remove/update decision for each claim
- replacement copy where a claim cannot be sourced automatically

Acceptance criteria:

- no inconsistent numeric/product-version claim remains
- every retained claim has an identified source of truth
- product copy does not promise more than the platform currently supports

Validation:

- manual copy audit
- PR checklist proving source for each retained claim

Dependencies:

- none

### BL-002 Freeze decorative enterprise state in production UI

Priority: P0
Status: not started

Problem:

- Multiple screens present enterprise-grade state that is local, static, or partially simulated.

Primary evidence:

- `ui/src/pages/Settings.tsx:163-178`
- `ui/src/pages/Settings.tsx:302-318`
- `ui/src/pages/Connectors.tsx:196-206`
- `ui/src/pages/Dashboard.tsx:119`
- `ui/src/pages/Dashboard.tsx:171-182`
- `ui/src/pages/AgentDetail.tsx:459`

Scope:

- Settings
- Connectors
- Dashboard
- Agent Detail explainability
- any similar "healthy/configured/connected" badges

Deliverables:

- state inventory of all simulated enterprise controls
- disposition for each: implement now, disable, or mark unavailable

Acceptance criteria:

- no production route shows fabricated operational/compliance state without clear labeling
- unavailable features are labeled explicitly instead of simulated

Validation:

- route-by-route UI review
- sign-off checklist against the inventory

Dependencies:

- none

## WS-1 SDK and API Contract Alignment

### BL-101 Define canonical agent run response contract

Priority: P0
Status: not started

Problem:

- Agent execution returns different shapes depending on route and caller path.

Primary evidence:

- `sdk/agenticorg/client.py:88-125`
- `sdk-ts/src/index.ts:26-35`
- `sdk-ts/src/index.ts:113-127`
- `api/v1/a2a.py:196-209`

Scope:

- `/api/v1/agents/{id}/run`
- `/api/v1/a2a/tasks`
- Python SDK response model
- TypeScript SDK response model
- docs/examples in product and README

Deliverables:

- written API contract for agent execution
- explicit compatibility policy for agent-id and agent-type execution
- migration note if the contract changes

Acceptance criteria:

- one canonical result shape is approved for SDK consumption
- backend endpoints either converge on that shape or SDKs normalize to it consistently
- there is no ambiguity in field names such as `id` vs `task_id` or nested `result.output` vs top-level `output`

Validation:

- contract tests covering both execution paths
- documentation examples compiled or executed in CI

Dependencies:

- none

### BL-102 Align Python SDK to canonical contract

Priority: P0
Status: not started

Problem:

- The Python SDK currently returns raw A2A payloads for agent-type execution.

Primary evidence:

- `sdk/agenticorg/client.py:88-125`

Scope:

- Python SDK run path
- Python SDK docs/examples
- package README and any usage snippets

Deliverables:

- normalized Python SDK return model
- updated examples
- regression tests

Acceptance criteria:

- the same conceptual `run()` call yields a stable documented shape for both agent-id and agent-type usage
- Python examples execute successfully against the current backend

Validation:

- Python SDK unit tests
- end-to-end smoke test calling real API contract in CI

Dependencies:

- BL-101

### BL-103 Align TypeScript SDK to canonical contract

Priority: P0
Status: not started

Problem:

- TypeScript types currently promise fields the A2A endpoint does not return.

Primary evidence:

- `sdk-ts/src/index.ts:26-35`
- `sdk-ts/src/index.ts:113-127`

Scope:

- TypeScript SDK types
- runtime normalization
- example code

Deliverables:

- corrected `AgentResult` model or normalized runtime mapping
- updated examples
- test coverage for both code paths

Acceptance criteria:

- no mismatch exists between declared types and runtime values
- TypeScript users can rely on the types without hidden response-shape caveats

Validation:

- TS type tests or compile assertions
- runtime tests against mocked and real endpoint shapes

Dependencies:

- BL-101

### BL-104 Fix in-product developer examples and docs

Priority: P0
Status: not started

Problem:

- Product UI teaches a broken SDK usage pattern.

Primary evidence:

- `ui/src/pages/Integrations.tsx:57-58`

Scope:

- integrations page
- README
- SDK package docs
- any sample snippets in the repo

Deliverables:

- corrected snippets
- source-of-truth snippet location
- CI validation for sample execution or snapshot consistency

Acceptance criteria:

- every published sample matches the final SDK contract
- there is one maintained source for shared snippets

Validation:

- snippet tests in CI

Dependencies:

- BL-102
- BL-103

## WS-2 MCP and Connector Execution Model Alignment

### BL-201 Decide and document the actual MCP product model

Priority: P0
Status: not started

Problem:

- The platform currently mixes two incompatible stories:
- MCP exposes agents as tools
- MCP exposes connector tools directly

Primary evidence:

- `api/v1/mcp.py:28-67`
- `api/v1/mcp.py:98-104`
- `mcp-server/src/index.ts:10`
- `mcp-server/src/index.ts:172-183`
- `mcp-server/src/index.ts:190-195`

Scope:

- backend MCP contract
- MCP server surface
- developer docs
- product marketing copy

Deliverables:

- decision document selecting one supported model
- allowed tool naming standard
- discovery semantics
- backward compatibility plan

Acceptance criteria:

- one unambiguous external model is approved
- unsupported MCP claims are removed

Validation:

- architecture sign-off

Dependencies:

- none

### BL-202 Align backend MCP endpoints to selected model

Priority: P0
Status: not started

Problem:

- Backend tool discovery and execution currently only support `agenticorg_` agent wrappers.

Primary evidence:

- `api/v1/mcp.py:28-67`
- `api/v1/mcp.py:98-117`

Scope:

- `/api/v1/mcp/tools`
- `/api/v1/mcp/call`
- auth and routing logic

Deliverables:

- backend implementation consistent with chosen MCP model
- tool schema coverage
- error model for unsupported/unknown tools

Acceptance criteria:

- discovery results match executable tool names
- every tool returned by discovery is callable
- unsupported names fail with intentional documented behavior

Validation:

- contract tests
- integration tests for discovery + invoke flow

Dependencies:

- BL-201

### BL-203 Align MCP server behavior and language

Priority: P0
Status: not started

Problem:

- The MCP server currently advertises direct connector-tool calls the backend rejects.

Primary evidence:

- `mcp-server/src/index.ts:172-195`

Scope:

- MCP server tool names
- descriptions
- example prompts
- docs/comments

Deliverables:

- corrected server tool catalog
- corrected descriptions
- compatibility notes if tool names change

Acceptance criteria:

- MCP server no longer overstates backend capability
- examples succeed against the selected product model

Validation:

- build passes
- integration tests against the backend

Dependencies:

- BL-201
- BL-202

### BL-204 Align tool counts and connector counts across product surfaces

Priority: P0
Status: not started

Problem:

- Tool and connector counts vary across marketing, UI, MCP, and build output.

Scope:

- landing
- dashboard
- connectors page
- MCP docs
- README

Deliverables:

- single count source or explicit removal of volatile counts

Acceptance criteria:

- there is one authoritative definition for connector and tool counts
- every retained count is automatically derived or centrally maintained

Validation:

- copy audit

Dependencies:

- BL-201

## WS-3 Settings and Governance Persistence

### BL-301 Define persisted governance configuration model

Priority: P0
Status: not started

Problem:

- Compliance/data settings are shown in UI without end-to-end persistence through the visible save path.

Primary evidence:

- `ui/src/pages/Settings.tsx:33-35`
- `ui/src/pages/Settings.tsx:54-61`
- `ui/src/pages/Settings.tsx:107-120`
- `ui/src/pages/Settings.tsx:163-178`

Scope:

- PII masking
- data region
- audit retention
- API key management visibility
- any additional admin-governance controls in Settings

Deliverables:

- backend config schema
- API read/write contract
- auditing requirements

Acceptance criteria:

- every governance control on the page maps to a persisted backend field
- each control has an owner, storage location, and audit model

Validation:

- schema review
- API contract review

Dependencies:

- none

### BL-302 Implement settings read/write parity across backend and UI

Priority: P0
Status: not started

Problem:

- The current settings screen saves only fleet limits while presenting broader admin controls.

Scope:

- Settings page
- config endpoints
- backend persistence
- audit log

Deliverables:

- full settings API parity
- UI load/save parity
- validation and error states

Acceptance criteria:

- every editable setting loads from backend
- every editable setting saves to backend
- save success and failure messages map to actual persistence outcome

Validation:

- backend API tests
- UI integration tests
- audit log verification

Dependencies:

- BL-301

### BL-303 Replace static Grantex panel with real health/config state

Priority: P0
Status: not started

Problem:

- Grantex status appears operational but is currently effectively static.

Primary evidence:

- `ui/src/pages/Settings.tsx:302-324`

Scope:

- base URL source
- API key status
- runtime registration state
- last sync / last error

Deliverables:

- backend status endpoint or consolidated config endpoint
- UI panel sourced from backend
- error/degraded states

Acceptance criteria:

- Grantex panel reflects actual environment/config state
- read-only hardcoded values are removed or replaced with sourced values

Validation:

- API tests
- UI tests for healthy/degraded/unconfigured variants

Dependencies:

- BL-301

### BL-304 Add admin/governance auditability coverage

Priority: P0
Status: not started

Problem:

- Enterprise settings changes must be attributable.

Scope:

- settings changes
- connector auth changes
- compliance controls
- data-region changes
- retention changes

Deliverables:

- audit event schema
- audit persistence
- UI visibility where appropriate

Acceptance criteria:

- every governance mutation writes an audit record with actor, time, old value, new value, and tenant context

Validation:

- security and audit tests

Dependencies:

- BL-302
- BL-303

## WS-4 Connector Control Plane

### BL-401 Define connector lifecycle model

Priority: P1
Status: not started

Problem:

- Connector state is too binary and too UI-local to serve enterprise operations.

Primary evidence:

- `ui/src/pages/Connectors.tsx:196-206`

Scope:

- connector catalog
- connector instance status
- auth lifecycle
- health lifecycle

Deliverables:

- connector status model with states such as:
- `not_configured`
- `requires_auth`
- `configured`
- `healthy`
- `degraded`
- `error`
- `syncing`

Acceptance criteria:

- the lifecycle is documented and maps cleanly to backend data and UI behavior

Validation:

- design review

Dependencies:

- none

### BL-402 Replace local connect toggle with real integration flow

Priority: P1
Status: not started

Problem:

- The main connector CTA only mutates local state.

Primary evidence:

- `ui/src/pages/Connectors.tsx:196-206`
- `ui/src/pages/Connectors.tsx:366-370`

Scope:

- connector onboarding
- secret capture
- OAuth flow where relevant
- validation flow

Deliverables:

- real connect action
- persisted status
- user-visible success/failure/error states

Acceptance criteria:

- pressing `Connect` results in an actual backend operation or auth handoff
- refreshed UI preserves connector state

Validation:

- UI integration tests
- E2E connector onboarding tests

Dependencies:

- BL-401
- BL-302

### BL-403 Make connector catalog data-driven

Priority: P1
Status: not started

Problem:

- Native connector catalog is hardcoded in UI.

Primary evidence:

- `ui/src/pages/Connectors.tsx:13-68`

Scope:

- connector metadata
- categories
- capabilities
- availability

Deliverables:

- backend-served connector catalog
- UI consuming backend data

Acceptance criteria:

- catalog contents do not require UI code edits for metadata-only changes

Validation:

- API tests
- UI tests

Dependencies:

- BL-401

### BL-404 Operationalize connector detail page

Priority: P1
Status: not started

Problem:

- Connector management needs health, scope, audit, and validation surfaces.

Scope:

- connector detail
- secret reference handling
- OAuth metadata
- last sync
- health checks
- audit trail

Deliverables:

- connector detail requirements
- operational state model in UI

Acceptance criteria:

- each configured connector has an inspectable operational record
- admin users can tell whether a connector is usable, degraded, mis-scoped, or failing

Validation:

- UI tests
- E2E operational scenarios

Dependencies:

- BL-402
- BL-403

## WS-5 Dashboard and Information Architecture

### BL-501 Replace hardcoded executive metrics and health cards

Priority: P1
Status: not started

Problem:

- Dashboard currently contains hardcoded business and infrastructure signals.

Primary evidence:

- `ui/src/pages/Dashboard.tsx:119`
- `ui/src/pages/Dashboard.tsx:171-182`

Scope:

- KPIs
- platform health cards
- integration counts
- agent/tool counts

Deliverables:

- dashboard metric inventory
- source mapping for each metric
- removal of unsourced metrics

Acceptance criteria:

- no dashboard KPI is hardcoded unless explicitly labeled demo/sample
- source and refresh model is known for each KPI

Validation:

- dashboard data contract tests
- UI integration tests

Dependencies:

- BL-001
- BL-002

### BL-502 Redesign app-shell navigation for role-based operation

Priority: P1
Status: not started

Problem:

- Navigation is too flat and too broad for enterprise users and smaller teams alike.

Primary evidence:

- `ui/src/components/Layout.tsx:19-51`

Scope:

- primary nav
- admin/control-plane grouping
- role-based visibility
- onboarding path

Deliverables:

- new IA proposal
- role-based navigation map
- migration plan for route discoverability

Acceptance criteria:

- primary navigation is understandable for admin, operator, and evaluator personas
- advanced/system surfaces are separated from daily operation surfaces

Validation:

- design review
- usability review

Dependencies:

- none

### BL-503 Replace redirect-based authorization fallback with explicit 403 UX

Priority: P1
Status: not started

Problem:

- Unauthorized access currently redirects to audit view.

Primary evidence:

- `ui/src/components/ProtectedRoute.tsx:18`

Scope:

- protected routes
- access denied page/state
- role mismatch messaging

Deliverables:

- explicit access denied screen
- role/context-aware messaging

Acceptance criteria:

- unauthorized users are not silently redirected to unrelated product surfaces
- the UI explains the restriction and next step

Validation:

- route-level tests
- E2E authz tests

Dependencies:

- none

## WS-6 Agent Explainability and Workflow Operations

### BL-601 Replace mock explainability with real run-trace explainability

Priority: P1
Status: not started

Problem:

- Agent detail currently shows mock explanation content.

Primary evidence:

- `ui/src/pages/AgentDetail.tsx:459`

Scope:

- agent detail
- explainability API
- cited tools
- confidence rationale
- HITL rationale

Deliverables:

- backend explainability contract
- UI backed by real trace data
- empty/unavailable state if trace data is absent

Acceptance criteria:

- no mock explanation remains in production agent detail
- every shown explanation element is sourced from a real run or trace derivation

Validation:

- backend tests
- UI integration tests
- E2E agent-run trace review

Dependencies:

- none

### BL-602 Move workflow templates to a managed backend source

Priority: P1
Status: not started

Problem:

- Workflow template library is hardcoded in the UI.

Primary evidence:

- `ui/src/pages/Workflows.tsx:18-40`

Scope:

- template storage
- template versioning
- template retrieval

Deliverables:

- backend template model
- API endpoints
- UI data source change

Acceptance criteria:

- workflow templates are not encoded as a static UI constant
- templates can be versioned and audited

Validation:

- API tests
- UI tests

Dependencies:

- none

### BL-603 Upgrade workflow detail into an operational execution view

Priority: P1
Status: not started

Problem:

- Workflow detail is too thin for enterprise execution monitoring.

Primary evidence:

- `ui/src/pages/WorkflowDetail.tsx:78-85`

Scope:

- workflow graph
- step status
- retries
- approvals
- connector usage
- errors

Deliverables:

- detailed workflow detail requirements
- data contract for step execution state

Acceptance criteria:

- operators can diagnose a workflow run from the detail view without reading raw IDs
- steps have human-readable labels, statuses, timestamps, and failure context

Validation:

- UI tests
- E2E workflow-run scenarios

Dependencies:

- BL-602

### BL-604 Isolate demo and sandbox mechanics from normal product flows

Priority: P1
Status: not started

Problem:

- Playground and login currently blend demo credentials and normal product paths too closely.

Primary evidence:

- `ui/src/pages/Playground.tsx:247-256`
- `ui/src/pages/Playground.tsx:253`
- `ui/src/pages/Login.tsx:195-206`

Scope:

- login demo mode
- playground sandbox mode
- seeded demo users
- public trial routing

Deliverables:

- sandbox policy
- explicit demo mode boundaries
- product-vs-demo separation plan

Acceptance criteria:

- seeded demo credentials are not part of normal operational product behavior
- demo experiences are explicitly named and isolated

Validation:

- auth flow review
- E2E sandbox flow tests

Dependencies:

- none

## WS-7 QA Baseline and Coverage Program

### BL-701 Restore backend test baseline to green

Priority: P0
Status: not started

Problem:

- Current backend suite is not green.

Observed review result:

- `uv run pytest`
- `1 failed, 3187 passed, 154 skipped, 8 errors`
- `57%` total coverage

Scope:

- failing unit tests
- errored E2E tests
- environment assumptions

Deliverables:

- failure inventory
- ownership assignment
- remediation order

Acceptance criteria:

- required backend suites pass in CI
- failures are not waived without explicit decision

Validation:

- green CI run

Dependencies:

- none

### BL-702 Restore frontend unit/integration baseline to green

Priority: P0
Status: not started

Problem:

- Current frontend suite is not green.

Observed review result:

- `cd ui && npm test`
- `1 failed | 73 passed | 74 total`

Primary evidence:

- `ui/src/__tests__/CompanyDetail.test.tsx:171-181`

Scope:

- failing tests
- brittle queries
- missing route coverage

Deliverables:

- frontend failure inventory
- test hardening plan

Acceptance criteria:

- required frontend unit/integration suites pass in CI
- ambiguous and flaky selectors are removed

Validation:

- green CI run

Dependencies:

- none

### BL-703 Create deterministic local and CI E2E environments

Priority: P0
Status: not started

Problem:

- E2E posture is broad, but environment assumptions are not yet deterministic enough for trustable certification.

Primary evidence:

- `tests/e2e/test_cxo_flows.py:46`
- `ui/playwright.config.ts:9`

Scope:

- local DB/bootstrap requirements
- Playwright base URL behavior
- tenant seeding
- demo data isolation

Deliverables:

- deterministic test environment spec
- local setup instructions
- CI environment wiring

Acceptance criteria:

- E2E tests do not rely on implicit prod URLs
- local and CI environments are reproducible
- environment-dependent tests declare prerequisites clearly

Validation:

- clean-environment E2E runs in CI

Dependencies:

- none

### BL-704 Define coverage policy and stop using "100%" ambiguously

Priority: P0
Status: not started

Problem:

- There is no credible basis for a current "100%" completion claim.

Scope:

- backend coverage policy
- frontend coverage policy
- critical-path coverage definition
- marketing/README language

Deliverables:

- written QA policy defining:
- pass-rate requirements
- coverage thresholds
- critical-path modules requiring stronger coverage
- when "100%" may be used

Acceptance criteria:

- repo-wide policy is explicit
- public copy no longer uses unqualified "100%" unless measured and true

Validation:

- policy review
- CI threshold enforcement

Dependencies:

- BL-701
- BL-702
- BL-703

### BL-705 Add explicit critical-path regression suites

Priority: P0
Status: not started

Problem:

- High-risk enterprise flows need direct regression coverage regardless of overall repo coverage.

Critical paths to cover:

- auth and RBAC
- cross-tenant isolation
- SDK contract parity
- MCP discovery/invocation
- HITL escalation
- connector auth and health
- governance config persistence
- audit logging

Deliverables:

- regression matrix
- ownership per critical path
- required-suite tags in CI

Acceptance criteria:

- each critical path has named tests and CI inclusion
- no enterprise-significant regression can slip without touching a required suite

Validation:

- regression matrix review
- CI enforcement

Dependencies:

- BL-101
- BL-202
- BL-302
- BL-402

## WS-8 Release Consistency and Enterprise Readiness

### BL-801 Run cross-surface consistency sweep before any relaunch

Priority: P1
Status: not started

Problem:

- Even after fixes, the product can still fail if docs, UI, SDKs, and MCP server drift again.

Scope:

- README
- landing
- app shell
- integrations page
- SDK package docs
- MCP docs/comments

Deliverables:

- consistency checklist
- release review template

Acceptance criteria:

- one sweep confirms that all surfaced product claims match implemented behavior

Validation:

- pre-release review

Dependencies:

- BL-104
- BL-204
- BL-501

### BL-802 Create an enterprise evaluation script

Priority: P1
Status: not started

Problem:

- The product should be able to survive structured enterprise evaluation, not only internal demo flows.

Scope:

- buyer evaluation
- admin evaluation
- developer evaluation
- operator evaluation

Deliverables:

- scripted evaluation scenarios for:
- SMB setup
- mid-market admin onboarding
- enterprise governance review
- developer integration review
- operations failure/recovery review

Acceptance criteria:

- the product can be evaluated through consistent scenario-based walkthroughs
- each scenario maps to verified product behavior, not aspirational copy

Validation:

- evaluation dry run

Dependencies:

- all prior workstreams

### BL-803 Final enterprise readiness review

Priority: P1
Status: not started

Problem:

- The platform needs a formal readiness checkpoint before being positioned as best-in-class for enterprise virtual employees.

Scope:

- product truthfulness
- operational readiness
- security and governance
- developer platform
- QA credibility

Deliverables:

- go/no-go review
- residual risk log
- blocked claims list

Acceptance criteria:

- unresolved critical trust issues are zero
- QA Gate C is satisfied
- leadership can point to evidence for every major enterprise claim

Validation:

- readiness review meeting
- signed release decision

Dependencies:

- all prior workstreams

## Dependency Summary

These items are hard blockers and should be treated as the critical path.

- BL-101 blocks BL-102 and BL-103.
- BL-102 and BL-103 block BL-104.
- BL-201 blocks BL-202 and BL-203.
- BL-301 blocks BL-302 and BL-303.
- BL-302 and BL-303 block BL-304.
- BL-401 blocks BL-402 and BL-403.
- BL-602 blocks BL-603.
- BL-701, BL-702, and BL-703 block BL-704.
- Core enterprise readiness depends on WS-1, WS-2, WS-3, and WS-7 completing successfully.

## Immediate Execution Order

If this backlog is executed strictly, start in this exact order:

1. BL-001
2. BL-002
3. BL-101
4. BL-201
5. BL-301
6. BL-701
7. BL-702
8. BL-703
9. BL-102
10. BL-103
11. BL-202
12. BL-203
13. BL-302
14. BL-303
15. BL-401
16. BL-402
17. BL-403
18. BL-501
19. BL-503
20. BL-601
21. BL-602
22. BL-603
23. BL-604
24. BL-704
25. BL-705
26. BL-204
27. BL-304
28. BL-502
29. BL-404
30. BL-801
31. BL-802
32. BL-803

## Non-Negotiable Exit Conditions

Before the product is positioned as enterprise-best or "virtual employees for any enterprise task", all of the following must be true:

- SDK contract mismatch is eliminated.
- MCP story is truthful and executable.
- Governance/configuration UX is backed by real persistence.
- Connector state is real, not local or decorative.
- Dashboard truth signals are sourced, not hardcoded.
- Mock explainability is removed.
- Demo mode is isolated from normal product operation.
- Required backend, frontend, and E2E suites are green.
- Coverage policy is explicit and enforced.
- Public product claims are consistent and evidence-backed.
