# Product Gap Review

Date: 2026-04-18
Repo: `agentic-org`
Reviewer stance: enterprise product, platform, SDK, and QA critic

## Executive Verdict

This product is ambitious and already has meaningful depth, but it is not yet at the level where I would call it "the best virtual employee platform" for small, medium, and large enterprises.

The most serious problem is not visual polish. It is trust. Multiple surfaces present enterprise-grade capability claims that are not fully backed by runtime behavior:

- SDK contracts do not match actual API responses.
- MCP and connector-tool claims do not match what the backend can execute.
- Settings and connector states present as real control-plane features while behaving like local UI state.
- The test baseline is not green, and measured backend coverage is 57%, not 100%.
- Public and in-product version / test / tool-count claims are inconsistent.

For enterprise adoption, this is the wrong failure mode. Enterprises will tolerate a few UX rough edges. They will not tolerate "looks real but is partially simulated."

## Review Method

- Static code review across backend, UI, SDKs, MCP server, and tests.
- Build validation for `ui`, `sdk-ts`, and `mcp-server`.
- Backend test run via `uv run pytest`.
- Frontend test run via `npm test` in `ui`.
- Live public-site inspection of `https://agenticorg.ai`.

## Critical Findings

### 1. SDK runtime contract is broken for agent-type execution

Severity: Critical

Evidence:

- `sdk/agenticorg/client.py:88-125` posts agent types to `/api/v1/a2a/tasks` and returns raw JSON.
- `sdk-ts/src/index.ts:26-35` defines `AgentResult` with top-level `task_id`, `agent_id`, `output`, `confidence`, and `reasoning_trace`.
- `sdk-ts/src/index.ts:113-127` also posts agent types to `/api/v1/a2a/tasks` and returns raw JSON as `AgentResult`.
- `api/v1/a2a.py:196-209` returns `{ "id", "status", "agent_type", "result": { "output", "confidence" } }`.
- `ui/src/pages/Integrations.tsx:57-58` teaches users to call `client.agents.run("ap_processor", ...)` and then read `result["output"]`, which is wrong for the current A2A shape.

Impact:

- Python SDK consumers using the documented example will hit the wrong response shape.
- TypeScript SDK consumers are given a compile-time contract that does not reflect runtime output.
- The in-app SDK quickstart reinforces the wrong behavior.

Why this matters:

If agent creation and execution are the core product, the SDK must be the most reliable surface in the platform. Right now it is not.

Required fix:

- Normalize A2A responses in both SDKs to a stable `AgentResult` shape.
- Or change SDK typings and examples to match the raw A2A payload.
- Add contract tests that pin SDK output against live API responses.

### 2. MCP server promises connector-tool execution that the backend cannot fulfill

Severity: Critical

Evidence:

- `api/v1/mcp.py:28-67` lists one MCP tool per agent type using `_AGENT_TYPE_DEFAULT_TOOLS`.
- `api/v1/mcp.py:98-104` rejects any tool name that does not start with `agenticorg_`.
- `mcp-server/src/index.ts:10` claims direct access to `340+ connector tools`.
- `mcp-server/src/index.ts:172-183` exposes `call_connector_tool` and encourages names like `jira_create_issue` and `slack_send_message`.
- `mcp-server/src/index.ts:190-195` claims `list_mcp_tools` returns `340+ available MCP tools across 54 connectors`.

Impact:

- Third-party MCP clients are told they can call connector tools that the backend will reject.
- This damages integrator trust immediately.
- Any enterprise evaluation of "agent interoperability" will fail at the exact layer that is supposed to impress them.

Required fix:

- Decide the real product model:
- Either MCP exposes agent wrappers only.
- Or MCP truly exposes connector-level tools.
- Then align backend, MCP server, docs, and marketing counts to one truthful model.

### 3. Enterprise settings UI contains non-persistent compliance controls

Severity: Critical

Evidence:

- `ui/src/pages/Settings.tsx:33-35` stores `piiMasking`, `dataRegion`, and `auditRetention` in component state.
- `ui/src/pages/Settings.tsx:54-61` only fetches `/config/fleet_limits`.
- `ui/src/pages/Settings.tsx:107-120` only saves `limits` back to `/config/fleet_limits`.
- `ui/src/pages/Settings.tsx:163-178` renders "Compliance & Data" controls for PII masking, data region, and audit retention.
- `ui/src/pages/Settings.tsx:302-318` renders Grantex with hardcoded `https://api.grantex.dev` and "Configured (stored in GCP Secret Manager)".

Impact:

- The UI presents governance settings as enterprise controls even though they are not persisted through the same save path.
- This is especially serious because the labels are compliance-sensitive.
- The Grantex section reads like a real integration panel but behaves like a static status card.

Required fix:

- Separate decorative product copy from real admin controls.
- Persist every rendered setting through a real config API.
- Show source-of-truth status from backend, not hardcoded "configured" state.

### 4. Connector connection state is mostly simulated

Severity: Critical

Evidence:

- `ui/src/pages/Connectors.tsx:13-68` contains a hardcoded native connector catalog.
- `ui/src/pages/Connectors.tsx:196-206` `handleConnect()` only toggles `connectedApps` in local UI state.
- `ui/src/pages/Connectors.tsx:366-370` the CTA changes between `Connect` and `Connected` based on that local state.

Impact:

- The marketplace gives the impression of operational integration management.
- In reality, the main connect action is not an integration flow, not persisted state, and not an auth flow.
- This is the exact kind of gap that breaks enterprise trust during a demo.

Required fix:

- Replace the local toggle with real connector onboarding states:
- `not_configured`
- `requires_auth`
- `configured`
- `degraded`
- `error`
- `syncing`
- Back these with API-driven status, audit events, secret provenance, and validation results.

### 5. Core dashboard uses hardcoded executive truth signals

Severity: High

Evidence:

- `ui/src/pages/Dashboard.tsx:119` hardcodes `Deflection Rate = 73%`.
- `ui/src/pages/Dashboard.tsx:171-182` shows `LangGraph`, `Connected`, and operational state that does not appear to be fetched from backend.
- `ui/src/pages/Dashboard.tsx:174` claims `50+ agents, 54 native connectors + 1000+ via Composio, 340+ tools`.

Impact:

- The first screen after login is supposed to establish credibility.
- Hardcoded executive metrics do the opposite.
- Enterprise buyers will assume other metrics may also be narrative rather than measured.

Required fix:

- Every executive KPI on the main dashboard should be either:
- backend-derived,
- explicitly labeled sample/demo,
- or removed.

## Major UI and UX Gaps by Feature

### Public product and trust surface

Findings:

- `README.md:13` shows version `4.8.0`.
- `README.md:41` shows version `4.3.0`.
- `ui/src/pages/Landing.tsx:437` and `ui/src/pages/Landing.tsx:502` show `v4.0.0`.
- `ui/src/pages/Landing.tsx:1539` claims `855+ tests passing`.
- The live public site also reflects these mixed trust signals.

Critique:

- Enterprise buyers notice inconsistency faster than missing features.
- Version drift across README, landing page, and build artifacts makes the product look unmanaged.
- Test-count claims on the marketing page are especially risky when the suite is not green.

Required UX correction:

- Establish one source of truth for version, connector count, tool count, and test-count.
- If the value is dynamic, render it dynamically.
- If it is not dynamic, stop surfacing it publicly.

### App shell and information architecture

Findings:

- `ui/src/components/Layout.tsx:19-51` defines a very large flat navigation tree.
- High-friction routes are all exposed side-by-side: dashboards, agents, workflows, approvals, connectors, A2A/MCP, sales, schemas, audit, knowledge, voice, RPA, packs, SLA, billing, settings.

Critique:

- The product behaves like a platform, but the navigation reads like an internal admin console.
- For SMB users, this is overwhelming.
- For enterprise users, it lacks role-appropriate progressive disclosure.

Required UX correction:

- Collapse the shell into role-based primary navigation.
- Move platform-admin features into a separate admin rail.
- Group integration, governance, and experimentation areas behind clear control-plane sections.

### Authorization fallback UX

Findings:

- `ui/src/components/ProtectedRoute.tsx:18` redirects unauthorized access to `/dashboard/audit`.

Critique:

- This is confusing and leaks the shape of the internal app.
- Unauthorized users should receive an access-denied state, not a redirect to a different product surface.

Required UX correction:

- Add a proper `403` experience with explanation, current role, and escalation path.

### Dashboard

Findings:

- Hardcoded metrics and integration status as described above.

Critique:

- The dashboard should answer:
- what is running,
- what is blocked,
- what is waiting for human approval,
- what is degraded,
- what is producing enterprise value.
- Right now it mixes some of that with static marketing telemetry.

Required UX correction:

- Reframe the dashboard around actual operational truth:
- active agent jobs,
- pending HITL queue,
- failed integrations,
- SLA breaches,
- trend over time,
- savings realized,
- top-risk automations.

### Connectors

Findings:

- `ui/src/pages/Connectors.tsx:13-68` hardcoded catalog.
- `ui/src/pages/Connectors.tsx:196-206` local connect toggle only.
- `ui/src/pages/Connectors.tsx:275` advertises "Browse All Native Connectors" with a fixed count.

Critique:

- The page currently feels more like a brochure plus local state than an enterprise integration hub.
- There is no clear lifecycle view: auth, health, permissions, scopes, secret origin, sync logs, last successful call, or rate-limit posture.

Required UX correction:

- Treat connectors as production infrastructure, not app store tiles.
- Add per-connector operational health, auth method, secret location, scope grants, test action, and audit trail.

### Settings and compliance

Findings:

- Compliance controls are not persisted through the visible save path.
- Grantex configuration state is effectively static.

Critique:

- This is one of the highest-risk UX gaps in the product because it affects buyer confidence, not only operator convenience.
- A compliance panel must never be partially decorative.

Required UX correction:

- Every toggle must show:
- current backend value,
- who changed it,
- when it changed,
- whether it is enforced,
- what systems it affects.

### Integrations / A2A / MCP page

Findings:

- `ui/src/pages/Integrations.tsx:12-13` only fetches `/api/v1/a2a/agent-card` and `/api/v1/mcp/tools`.
- `ui/src/pages/Integrations.tsx:57-58` includes the broken SDK example noted earlier.

Critique:

- This page looks like an enterprise integration console, but it is really a documentation page with badges and snippets.
- That is fine for docs, but not fine if positioned as part of the operating product.

Required UX correction:

- Split this into:
- a real integration control plane,
- and separate developer docs or quickstarts.

### Agents and agent detail

Findings:

- `ui/src/pages/AgentDetail.tsx:459` explicitly says "Load mock explanation until real API is wired".
- The explanation drawer then shows fabricated explanation bullets and confidence values.

Critique:

- Agent explainability is a core enterprise feature.
- Mocking it in the primary agent detail experience is not acceptable if the product claims production-grade governance.

Required UX correction:

- Remove mocked explanation content.
- If explanation APIs are not ready, mark the section as unavailable.
- When available, source explanation, cited tools, confidence basis, and HITL reason from actual run traces.

### Workflows

Findings:

- `ui/src/pages/Workflows.tsx:18-40` ships a large hardcoded workflow template library.
- `ui/src/pages/WorkflowDetail.tsx:78-85` presents steps with minimal semantics, often using raw `step.id` and `step.agent`.

Critique:

- The workflow surface is promising but not yet strong enough for non-technical enterprise operators.
- It needs clearer visualization of branching, approvals, retries, connectors used, and failure points.

Required UX correction:

- Move templates to backend-managed assets with versioning.
- Upgrade workflow detail into an execution graph with:
- node state,
- retries,
- latency,
- human approvals,
- connector calls,
- error remediation.

### Playground and demo access

Findings:

- `ui/src/pages/Playground.tsx:247-256` falls back to a seeded demo login and throws if the demo user is missing.
- `ui/src/pages/Playground.tsx:253` uses `ceo@agenticorg.local` and `ceo123!`.
- `ui/src/pages/Login.tsx:195-206` exposes multiple demo roles and passwords in the UI.

Critique:

- Demo environments are fine.
- But the line between public demo mechanics and product mechanics is too blurry.
- That weakens the perception of tenant isolation and security discipline.

Required UX correction:

- Isolate demo mode explicitly.
- Never make seeded fallback credentials part of normal product logic.
- Make public playground traffic go through a separately named sandbox path and policy.

## SDK and Developer Platform Evaluation

### Python SDK

Verdict: not production-safe until the A2A contract mismatch is fixed.

Strengths:

- API surface is small and readable.
- The basic client structure is fine.

Gaps:

- `sdk/agenticorg/client.py:88-125` returns incompatible shapes depending on whether the caller uses agent ID or agent type.
- The docstring promises "Execution result with status, output, confidence, trace" but the A2A branch does not normalize to that shape.

### TypeScript SDK

Verdict: typed experience is misleading today.

Strengths:

- Clean surface area.
- Build passes.

Gaps:

- `sdk-ts/src/index.ts:26-35` publishes a strong contract that the runtime does not honor for `/api/v1/a2a/tasks`.
- Consumers will trust the type system and then discover the mismatch only at runtime.

### MCP server

Verdict: promising packaging, but currently mis-specified against backend capability.

Strengths:

- Clear server surface and good intent.

Gaps:

- Claims direct connector-tool execution not supported by backend.
- Overstates tool inventory relative to the backend tool-list contract.

### Developer docs inside product

Verdict: currently unsafe because the examples inherit SDK contract bugs.

Evidence:

- `ui/src/pages/Integrations.tsx:57-58` demonstrates a broken response access pattern.

Required platform standard:

- Every quickstart snippet must be executed in CI against the current backend shape.

## Test and Coverage Reality Check

## Backend test run

Command run:

```text
uv run pytest
```

Observed result:

```text
1 failed, 3187 passed, 154 skipped, 8 errors
TOTAL coverage: 57%
```

Implications:

- Test coverage is not 100%.
- Test pass rate is not 100%.
- E2E readiness is not 100%.

Notable failures:

- `tests/unit/test_negative_cases.py::TestAuthLogin::test_login_rate_limit` failed.
- `tests/e2e/test_cxo_flows.py` errored because it expects a live DB path from `_db.settings.db_url` and local DB setup was not present for the run. See `tests/e2e/test_cxo_flows.py:46`.

## Frontend test run

Command run:

```text
cd ui
npm test
```

Observed result:

```text
1 failed | 73 passed | 74 total
```

Notable failure:

- `ui/src/__tests__/CompanyDetail.test.tsx:171-181` uses `getByText("success")`, which is ambiguous in the rendered page state.

## UI E2E state

Findings:

- `ui/playwright.config.ts:9` defaults `baseURL` to `https://app.agenticorg.ai`.
- `ui/e2e` contains a broad suite including `security-tests.spec.ts`, but I did not certify this suite as passing end-to-end in this review because the local environment was not provisioned for a safe, deterministic run.

Implications:

- There is evidence that the team cares about functional and security E2E coverage.
- There is not evidence that the total E2E baseline is green and stable in a reproducible local review run.

## Security test posture

Findings:

- There are meaningful security-focused tests in:
- `tests/security/test_auth_security_full.py`
- `tests/security/test_data_infra_security.py`
- `tests/unit/test_scope_enforcement.py`
- `ui/e2e/security-tests.spec.ts`

Verdict:

- Security testing exists.
- That is not the same as "all functional, security, UI/UX test cases are 100% completed."
- Given the measured 57% backend coverage and failing suites, that claim is false today.

## Enterprise Readiness Assessment

Current state by dimension:

- Product truthfulness: weak
- Core platform ambition: strong
- SDK reliability: weak
- Connector operating model: weak
- Governance UX credibility: weak
- Test rigor: moderate effort, insufficient outcome
- Enterprise adoption readiness: not yet

## Priority Fix Order

1. Fix truth mismatches first.
   Align SDKs, MCP server, docs, landing page, dashboard counts, and settings behavior with actual backend capability.

2. Remove simulated enterprise controls.
   Any compliance, connector, health, or explainability UI that is not backed by the backend should be disabled or clearly labeled unavailable.

3. Make the control plane operationally honest.
   Connectors, agents, workflows, and approvals need real state, health, and audit provenance.

4. Get the QA baseline genuinely green.
   No public "tests passing" claim should remain until backend, frontend, and E2E baselines pass in CI.

5. Raise coverage where the product promise is highest.
   Focus first on SDK contracts, MCP execution, tenant isolation, HITL policy paths, connector auth flows, and workflow execution traces.

6. Simplify the UI for role-based adoption.
   The platform needs separate experiences for operator, admin, buyer, and developer.

## Final Bottom Line

This product has enough substance to become strong. It does not yet have the consistency, operational honesty, or verified reliability required to be the default enterprise choice for "agents as virtual employees."

The biggest upgrade is not adding more features. It is making every visible claim, every SDK example, every dashboard metric, and every control-plane state provably true.
