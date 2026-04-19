# AgenticOrg Test Plan

**Scope:** end-to-end test coverage for the AgenticOrg platform.
**Audience:** engineers adding features, reviewers gating PRs, QA
auditors tracking coverage.

This is the feature matrix. Every shipped feature maps to at least one
Playwright spec + at least one backend test. If a row has no spec, it
isn't shippable under `feedback_no_skip_full_coverage.md` — don't
close the ticket without one.

---

## Coverage matrix

Grouped by product area. `spec` paths are relative to `ui/e2e/` and
`tests/` for Python.

### Auth + session

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| Login page renders + validates | `login-e2e.spec.ts` | `tests/unit/test_negative_cases.py::TestAuthLogin` | `@auth` |
| Invalid credentials surface an error | `login-e2e.spec.ts` | `tests/regression/test_pr_fixes_april2026.py::TestGrantexMiddlewareFailureClearing` | `@auth` |
| Rate-limit (429) after 5 failed attempts | — (backend-only) | `test_negative_cases.py::test_login_rate_limit` | `@auth` |
| Bearer token round-trips | `login-e2e.spec.ts` | `tests/integration/test_api_integration.py` | `@auth` |

### Tenancy + authorization

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| Non-admin hitting admin-gated route → `/access-denied` | `dashboard-403.spec.ts` | `test_company_isolation.py` (boundary tests) | `@tenancy` |
| `/governance/config` 403s without admin scope | — | `test_governance_api.py` | `@tenancy` |
| Cross-tenant report-schedule invisible | — | `tests/integration/test_db_api_endpoints.py::TestReportSchedulesIntegration::test_schedule_not_visible_across_tenants` | `@tenancy` |
| Cross-tenant delete returns 404 | — | same file | `@tenancy` |

### Governance (per-tenant)

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| PII-masking toggle persists across reload | `settings-governance.spec.ts` | `test_governance_api.py` | `@governance` |
| Data region IN → EU round-trip | `settings-governance.spec.ts` | `test_governance_api.py` | `@governance` |
| Audit retention CHECK-rejected outside 1–10 | `settings-governance.spec.ts` | `test_governance_api.py` + migration DDL | `@governance` |
| Every governance write creates audit row | `settings-governance.spec.ts` | `test_governance_api.py` | `@audit` |

### Truth & public claims

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| Landing counts match `/product-facts` | `product-claims.spec.ts`, `landing.spec.ts` | `scripts/consistency_sweep.py` | — |
| Dashboard KPIs real-not-hardcoded | `dashboard-403.spec.ts` | `test_cfo_cmo_kpis.py` | — |
| No stale `"340+ tools"` / `"54 native connectors"` | drift-guard in `product-claims.spec.ts` | `scripts/consistency_sweep.py` (scans 9 surfaces) | — |

### Decorative-state freeze (P1.2)

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| Grantex badge reflects `/integrations/status` | `decorative-state.spec.ts` | — | `@connector` |
| Marketplace Connect button labelled "Demo" | `decorative-state.spec.ts` | — | `@connector` |
| 403 page renders attempted_path + role | `dashboard-403.spec.ts` | — | `@tenancy` |

### SDK + MCP

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| SDK snippet on Settings page references `AgentRunResult` | `sdk-examples.spec.ts` | — | `@sdk` |
| Canonical `AgentRunResult` round-trip | — | `tests/regression/test_agent_run_contract.py` | `@sdk` |
| MCP unknown tool returns 404 with structured body | `sdk-examples.spec.ts` | `tests/unit/test_a2a_mcp.py::test_call_unknown_tool_returns_404` | `@mcp` |
| MCP agent-wrapper tool names prefixed `agenticorg_` | — | `test_a2a_mcp.py::test_tool_names_prefixed` | `@mcp` |

### Connectors

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| `/connectors/registry` returns canonical shape | `connectors-catalog.spec.ts` | — | `@connector` |
| UI card count matches backend registry | `connectors-catalog.spec.ts` | — | `@connector` |
| Edit button navigates to detail page | `connector-edit.spec.ts` | — | `@connector` |
| Detail page exposes auth-type control | `connector-edit.spec.ts` | — | `@connector` |

### Workflows

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| `/workflows/templates` returns backend catalog | `workflow-templates.spec.ts` | — | — |
| Create workflow from template | `workflow-templates.spec.ts` | `tests/unit/test_ca_workflows.py` | — |
| Approval (HITL) appears in Filing Approvals | `ca-firms.spec.ts` (Filing Approvals) | `test_ca_regression.py` | `@hitl` |

### Explainability

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| Agent detail shows real trace bullets | `explainer-real.spec.ts` | — | — |
| Empty state when no run yet | `explainer-real.spec.ts` | — | — |

### Knowledge base

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| pgvector 384-dim embedding produced | — | `tests/regression/test_embeddings.py` | — |
| Semantic ordering (related > unrelated) | — | `test_embeddings.py` | — |
| `/knowledge/search` fallback returns results | — | `test_embeddings.py` (via native path) | — |

### Platform health

| Feature | Playwright | Backend | Tag |
|---|---|---|---|
| `/health` returns `healthy` | `full-app.spec.ts` | `tests/regression/test_bugs_april06_2026.py` | — |
| `/product-facts` exempt from auth | `landing.spec.ts` | — | — |

---

## Non-functional coverage

### Consistency

- `scripts/consistency_sweep.py` — version agreement, runtime-registry
  alignment, no stale public claims across 9 surfaces, MCP-version
  lockstep, MCP vs LangGraph tool-index sanity. **Preflight gate #8.**

### Critical-path tags

- `scripts/check_critical_path_tags.py` — asserts `@auth`, `@tenancy`,
  `@sdk`, `@mcp`, `@hitl`, `@connector`, `@governance`, `@audit` each
  appear in ≥1 Playwright spec. **Preflight gate #10.**

### Coverage floor

- Global: `--cov-fail-under=55` in the unit-tests CI step.
- Per-module: `scripts/check_module_coverage.py` enforces
  `auth/*` ≥ 70, `api/v1/auth.py` ≥ 50, `api/v1/governance.py` ≥ 35,
  `api/v1/mcp.py` ≥ 45, `core/database.py` ≥ 30. **Preflight gate #9.**

### Design quality (aesthetic coverage)

- Every UI-touching PR runs the impeccable skill pack:
  `/audit <area>` → `/critique <area>` → `/polish <area>` and pastes
  the summary into the PR body. See
  `docs/frontend-design-workflow.md`. Complements Playwright
  (correctness) with aesthetic quality (overused fonts, gray-on-
  colored text, nested cards, bounce easing).

---

## Running the full gate

Before push:

```bash
bash scripts/preflight.sh
```

That runs, in order: branch safety → ruff → bandit → alembic IDs →
`verify=False` scan → pytest regression+unit → tsc → vite build →
consistency sweep → module-coverage floor → critical-path tags.

For local Playwright against a full docker stack:

```bash
bash scripts/local_e2e.sh ui/e2e/<spec>
```

---

## When to add to this plan

If a PR adds a new feature, append a row to the matching section
**before** merging. If a section doesn't exist, add one. If the PR
removes a feature, delete the row — out-of-date rows mislead reviewers
more than missing rows do.
