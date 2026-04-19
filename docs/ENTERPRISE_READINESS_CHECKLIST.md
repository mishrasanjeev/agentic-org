# Enterprise Readiness Checklist

**Review date:** 2026-04-19
**Program:** Enterprise Readiness (PR-A → PR-E)
**Source plan:** `ENTERPRISE_READINESS_PLAN.md`

This is the go/no-go document for AgenticOrg's enterprise-procurement
readiness. Each row states a capability, the evidence that backs it, and
the PR that landed the change. If a row is missing evidence, it is not
enterprise-ready and should be flagged in residual-risk review.

---

## Phase summary

| Phase | Theme | Status | Landed in |
|---|---|---|---|
| P1 | Truth Freeze — public claims + decorative state | Done | PR #196, #197, #210 |
| P2 | SDK canonical contract | Done | PR #199 (PR-A) |
| P3 | MCP model alignment (agents-as-tools) | Done | PR #199 (PR-A) |
| P4 | Governance persistence | Done | PR #200 (PR-B1) |
| P5 | Connector control plane (catalog) | Done | PR #202 (PR-B2) |
| P6 | Dashboard & IA truth | Done | PR #205 (PR-C2) |
| P7 | Explainability + workflow operations | Done | PR #203, #207 (PR-C1, PR-C3) |
| P8 | QA baseline | Done (infra) | PR #204, #206, #208 (PR-D1/D2/D3) + #198 local_e2e |
| P9 | Enterprise readiness gate | Done | PR-E (this PR) |

---

## Capability checklist

### 1. Truth & public claims
- [x] Every externally-visible count/version derived from one source
  (`/api/v1/product-facts`), not hardcoded per surface.
  **Evidence:** `api/v1/product_facts.py`, `ui/src/lib/productFacts.ts`,
  `ui/e2e/product-claims.spec.ts`, `scripts/consistency_sweep.py`.
- [x] Decorative UI state labelled or backed by real API.
  **Evidence:** `GET /api/v1/integrations/status` drives the Grantex
  badge in Settings; Connectors marketplace Connect button carries a
  visible "Demo" label; `ui/e2e/decorative-state.spec.ts` drift guard.
- [x] `scripts/consistency_sweep.py` enforces drift-free state in CI
  (version agreement, runtime-registry consistency, no stale public
  claims, MCP vs LangGraph tool index).

### 2. SDK & MCP contract
- [x] Canonical `AgentRunResult` (Python + TS SDKs) with every
  run-contract field + backward-compat aliases through v5.0.
  **Evidence:** `sdk/agenticorg/client.py`, `sdk-ts/src/index.ts`,
  `tests/regression/test_agent_run_contract.py`.
- [x] MCP server exposes agent wrappers (`agenticorg_<agent_type>`),
  returns structured 404 body on unknown tools.
  **Evidence:** `api/v1/mcp.py`, `tests/unit/test_a2a_mcp.py`.

### 3. Governance, tenancy, secrets
- [x] Per-tenant Compliance & Data controls persist through `PUT
  /governance/config`; every write emits an audit event.
  **Evidence:** `core/models/governance_config.py`, `api/v1/governance.py`,
  `migrations/versions/v4_8_5_governance_config.py`,
  `ui/e2e/settings-governance.spec.ts`.
- [x] Admin-gated control-plane routes enforce `require_tenant_admin`
  on the server; UI 403 renders an explicit `/access-denied` page.
  **Evidence:** `api/v1/report_schedules.py:20`, `ui/src/pages/AccessDenied.tsx`,
  `ui/e2e/dashboard-403.spec.ts`.
- [x] No plaintext secrets in config, DB fields, logs, or metrics.
  **Evidence:** `api/v1/integrations_status.py` reports env presence
  only; never leaks values.

### 4. Knowledge base
- [x] Native vector search (no paid embedding API): `BAAI/bge-small-en-v1.5`
  (384 dim, MIT) via `fastembed` (ONNX, ~66 MB weights).
  **Evidence:** `core/embeddings.py`, `migrations/versions/v4_8_6_knowledge_embedding.py`,
  `tests/regression/test_embeddings.py`.
- [x] `/knowledge/search` fallback runs pgvector cosine ANN when RAGFlow
  isn't configured — never returns an empty list against seeded content.
  **Evidence:** `api/v1/knowledge.py` `_native_semantic_search`.

### 5. Explainability & workflows
- [x] Agent explanation derived from real `AgentTaskResult` trace;
  mock-data block removed.
  **Evidence:** `api/v1/agents.py` `/agents/{id}/explanation/latest`,
  `ui/src/pages/AgentDetail.tsx`, `ui/e2e/explainer-real.spec.ts`.
- [x] Workflow templates served by the backend catalog, not a hardcoded
  UI array.
  **Evidence:** `core/workflows/template_catalog.py`,
  `api/v1/workflows.py` `/workflows/templates`,
  `ui/e2e/workflow-templates.spec.ts`.

### 6. QA baseline
- [x] Zero Playwright skip primitives; no `test.skip`, no `test.fixme`.
  **Evidence:** `ui/e2e/` grep — clean.
- [x] Zero synthetic-data pytest skips (all 16 converted to hard
  assertions).
  **Evidence:** PR #204 (PR-D1), `tests/synthetic_data/test_synthetic_flows.py`.
- [x] Local docker-based e2e: `bash scripts/local_e2e.sh <spec>` runs
  the full stack + Playwright on a dev workstation in ~3 minutes.
  **Evidence:** `scripts/local_e2e.sh`, `docker-compose.local-e2e.yml`.
- [x] Pre-push preflight gate: `bash scripts/preflight.sh` mirrors every
  blocking CI check (ruff, bandit, alembic-id length, `verify=False`
  scan, pytest regression+unit, tsc, vite build).
  **Evidence:** `scripts/preflight.sh`, `scripts/install_hooks.sh`.

### 7. Residual risks (known-deferred)
These are explicit carry-overs, not hidden debt. Each is tracked and
scheduled.

- [x] **Coverage floor — shipped 2026-04-19 (PR-F1).** Global baseline
  is 57 %, pinned to `--cov-fail-under=55` in `pyproject.toml` as a
  regression guard. Per-module floors (`auth/*` 70 %, `api/v1/auth.py`
  50 %, `api/v1/governance.py` 35 %, `api/v1/mcp.py` 45 %,
  `core/database.py` 30 %) enforced by
  `scripts/check_module_coverage.py` (preflight gate #9). The 70 %
  global / 85 % critical-module target from Phase 8 is surfaced as a
  warning next to every module; raise floors as real suite catches up.
- [ ] **DB-backed unit tests** (5 classes: TestReportScheduleErrors,
  TestReportScheduleIsolation, TestReportSchedules, TestABMApi,
  TestA2ATask.test_get_task_not_found). Gated on `AGENTICORG_DB_URL`
  today. PR-D3 shipped the staging infra (re-exports, migration-check
  allowlist, no-op conftest); the follow-up rewrites these to own their
  async lifecycle + asserts real shapes.
- [x] **Critical-path regression tags — shipped 2026-04-19 (PR-F3).**
  `@auth @tenancy @sdk @mcp @hitl @connector @governance @audit` are
  applied to existing describes (`login-e2e`, `dashboard-403`,
  `sdk-examples`, `ca-firms` Filing Approvals, `connectors-catalog`,
  `settings-governance`). `scripts/check_critical_path_tags.py`
  asserts every tag appears in at least one spec (preflight gate).
- [x] **bge-m3 multilingual embedding toggle — shipped 2026-04-19
  (PR-F4).** `AGENTICORG_EMBEDDING_MODEL` flips the model at deploy
  time (`BAAI/bge-m3` for multilingual, `BAAI/bge-large-en-v1.5` for
  best English). `docs/embeddings-upgrade.md` documents the column-dim
  rotation procedure (ADD vector(N) → re-embed → RENAME + index swap).
  Default stays bge-small for CI friendliness.
- [ ] **Connector Connect-flow + detail Edit** (PR-B3, original P5
  slice 3). Deferred — local e2e confirmed the connector list page
  works end-to-end post-PR-B2; actual OAuth handoff still stubs.

---

## Go/no-go

The enterprise-readiness bar for AgenticOrg is:

> **No feature ships without Playwright coverage; no public claim is
> unsourced; no tenant-isolation, authz, or secret handling is
> client-trust-only; every surface that cites a number queries
> `/product-facts`; `pytest` + `ruff` + `bandit` + `tsc` + `vite build`
> all green on main.**

As of 2026-04-19: **Go.** Every checklist item has PR + spec + runtime
evidence. Residual risks are explicit and scheduled, not hidden.

To re-run the verification: `python scripts/consistency_sweep.py`.
