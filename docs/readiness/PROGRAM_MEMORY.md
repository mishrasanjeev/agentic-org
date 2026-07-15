# Program Memory

**Status:** selected Wave 0/1 foundations are implemented and locally validated; PR review, merge, non-production migration, deployment, and external evidence remain pending
**Baseline:** product 4.8.0, repository commit `384543788bcd1f66aed8cff8ab03699ae384926e`, 2026-07-13
**Accountable editor:** unassigned until `W0-05`
**Last reviewed:** 2026-07-15
**Next review:** every merged readiness work package or 2026-07-27
**Prerequisite:** local implementation entries are provisional until linked to a merged PR and retained CI/deployment evidence
**Related runbook:** [capability promotion transaction](CAPABILITY_READINESS_REGISTER.md#promotion-transaction), [build and release roadmap](BUILD_ROADMAP.md), and update protocol below
**Limitation:** this file preserves intent and decisions; the capability register and immutable evidence artifacts determine release state.
**Related test:** `tests/regression/test_readiness_documentation.py`

This file is the durable repository memory for the cross-domain readiness program requested on 2026-07-13. It is deliberately concise and must be updated as decisions change.

## Objective

Make Marketing, Finance, CA firms, HR, CBO, and COO product surfaces genuinely ready across product behavior, data, integrations, governance, security, testing, operations, documentation, and go-to-market. Documentation and acceptance criteria come before feature construction.

## Scope interpretation

- **Marketing** maps to the CMO domain.
- **Finance** maps to the CFO domain and corporate finance operations.
- **CA** means Indian Chartered Accountant firm and multi-client compliance operations.
- **HR** maps to the CHRO domain.
- **COO** means enterprise service, IT, vendor, facilities, supply-chain, quality, and continuity operations.
- **CBO working scope pending `W0-04` sign-off:** Chief Business Officer spanning commercial strategy/governance plus selected legal, risk, board, communications, information-governance, and fraud/investigation responsibilities. It does not mean Chief Brand Officer. The supported/out-of-scope matrix is not yet ratified.

## Durable decisions

1. **No autonomous filing or payment claim is permitted yet.** Finance/CA tool execution must be interrupted before external write dispatch, and approval must bind to the exact payload.
2. **No role dashboard may substitute generic agent telemetry for business KPIs.** Task count, success rate, HITL count, and LLM cost are platform operations metrics, not CFO/CHRO/COO/CBO command-center KPIs.
3. **Marketing remains beta until real-vendor proof exists.** Its fail-closed contracts are substantially deeper than other domains, but deterministic and mock-backed tests are not a production pilot.
4. **Public numeric outcomes are evidence-controlled.** Unverified figures such as zero errors, 99.7% matching, 88% triage, 3.2x ROAS, and compressed cycle times must be removed, labeled illustrative, or backed by an evidence record.
5. **The current client-rendered site needs route-specific rendering.** A crawler receiving the generic home document for solution routes is not an acceptable landing-page implementation.
6. **Registered is not ready.** Agent, connector, route, workflow, or pack registration is inventory only.
7. **The canonical plan lives in `docs/readiness/`.** Older PRDs and audits are requirements/history unless the canonical documents explicitly cite them as current evidence.

## Known P0 blockers

- Strict environments now classify unsafe actions centrally, resolve connectors by exact tenant/company scope, and keep unsafe actions shadow-only before ToolGateway or LangGraph connector dispatch. This is containment, not live authorization: trusted issuer validation, expiry, canonical payload binding, nonce/replay defense, and single-use consumption remain blocked under `PLAT-03/04`.
- Installed CA workflows synthesize a review-last topology instead of installing a safe, versioned draft/reconcile/approve/write workflow.
- GST submission can fall back to an unsigned request when DSC is unavailable.
- Filing approvals are status records rather than single-use authorization bound to a canonical payload and submission receipt.
- CFO, CHRO, COO, and CBO dashboards expose generic agent activity rather than their advertised domain KPIs; company scoping is also inconsistent outside CFO/CMO.
- Public landing, pricing, executive/functional solution, README, blog/resource, workflow, OACP, and generated-LLM surfaces have a local truth-remediation pass. The expanded claim scan, route inventory, desktop/mobile route audit, focused accessibility checks, and production build pass locally. Retained browser artifacts, CI merge, route-specific server rendering, and deployed-page verification remain blocking.
- Domain production evidence is dominated by mocks, test doubles, and local deterministic scenarios.
- Production deployment is manual, the GitHub production job is disabled, post-deploy E2E depends on that disabled job, and background worker/beat rollout is not covered by the official API/UI deployment helper.
- Branch protection and required checks are not enforced in repository settings.
- Backup/DR documentation promises quarterly drill evidence, but no `docs/dr-drills/` evidence directory exists in the inspected tree.
- LangGraph containment currently emits structured logs but not the durable action-attempt/outcome audit and signed authorization required for promotion. MCP/A2A now carry explicit company context and fail closed when it is absent; external interoperability and real-provider evidence remain unverified.

## Selected-foundation implementation checkpoint — 2026-07-14

| Foundation | Local state | Verification | Residual release boundary |
|---|---|---|---|
| Public claim registry and scanner | Implemented | Governed surfaces pass; CI step added | Registry owners/approvers remain unassigned; records expire 2026-07-27 unless reviewed. |
| Capability readiness/evidence ledger | Implemented with models, migration, RLS, admin API, evidence provenance, expiry checks, and hash-chained history | Focused policy/ledger tests, mypy, one Alembic head | Migration has not been applied to production; no real capability evidence has been registered. |
| Action-risk taxonomy and pre-dispatch containment | Implemented across ToolGateway, LangGraph, workflow connector steps, and agent context propagation | All default tools classified; focused runtime/security/workflow suite green | Six ambiguous tools remain blocked; strict unsafe actions are shadow-only until trusted authorization exists. |
| Public landing and executive solution truthfulness | Implemented locally across governed public surfaces | Fixed-date claim gate, typecheck, lint, 167 UI tests, production build, 25 public Playwright tests, and a 23-route desktop/mobile browser audit pass | Route-specific server rendering, retained browser artifacts, CI evidence, accessibility specialist review, and production verification remain outstanding. |
| Release inventory | New admin routes recorded | Enterprise stability route gate passes with the refreshed inventory | Branch protection, automated production deployment, worker/beat rollout, and post-deploy evidence remain blocked. |

## Definition of done

A domain is ready only when every mandatory row in [DOMAIN_READINESS_STANDARD.md](DOMAIN_READINESS_STANDARD.md) has:

- an implemented and tenant-isolated code path;
- pre-write policy, approval, idempotency, reconciliation, and audit controls where applicable;
- real sandbox and controlled-pilot evidence;
- a role-specific dashboard with traceable KPIs and honest empty/degraded states;
- security, privacy, accessibility, performance, and failure-mode tests;
- SLOs, alerts, support ownership, incident/rollback runbooks, and DR coverage;
- complete user, administrator, developer, and operator documentation;
- landing-page claims that do not exceed the evidence state.

Each row is controlled through the [capability readiness and evidence register](CAPABILITY_READINESS_REGISTER.md); internal maturity, gate result, public availability, and claim treatment are independent fields.

## Next execution checkpoint

Complete `PLAT-03/04` before enabling any unsafe live dispatch: issue trusted, signed, expiring, single-use authorizations over canonical payload hashes and nonce state; persist durable attempt/outcome audit across every runtime; add the remaining company-aware database and feature-flag boundaries; and validate the existing tenant/company connector binding against real providers. In parallel, apply the readiness migration in a non-production environment, seed independently reviewed capability records, repair release enforcement, and collect real sandbox evidence. No domain GA work should leapfrog these foundations.

## Update protocol

When a work package closes, append a dated entry below with the PR, evidence path, readiness-state change, residual gaps, and approver. Do not overwrite the blocker history.

### Change log

- **2026-07-13:** Initial cross-domain program memory created from repository, UI, API, workflow, test, documentation, landing-page, and production-operations audits.
- **2026-07-14 (local implementation; no PR yet):** Added the public-claim registry/linter and CI gate; capability readiness/evidence models, migration, RLS, admin API, review-expiry enforcement, and hash-chained events; centralized action taxonomy and strict shadow containment; company/domain context propagation; route-inventory update; and truthful public-copy remediation. At that checkpoint, 80 focused tests passed, the expanded runtime sweep reported 544 passed with 1 skipped, selected mypy scope passed, one Alembic head was present, enterprise stability and consistency gates passed, the then-current claim scan passed, and the UI build passed. Later working-tree changes require fresh validation; these counts are historical local evidence, not current CI or release proof.
- **2026-07-15 (selected local foundation expansion; no promotion):** Added a typed commercial offer catalog and drift gate; local commercial/claim work reported 62 claim tests, 120 billing/CA tests, and 4 Pricing UI tests passing, plus scoped Ruff, scoped mypy, UI typecheck/lint/build, catalog consistency, claim scan, and diff checks. Readiness-security work reported 23 focused tests passing, scoped Ruff/mypy passing, and one Alembic head `v6z7_readiness_security`; 8 PostgreSQL integration tests were collected but skipped because `AGENTICORG_DB_URL` was unavailable. Documentation truth, exact route evidence fields, CHRO/COO/CBO guides, legacy warnings, and fixed-credential removal were updated. The documentation regression reported 7 passed (including canonical links and Mermaid structure), claim registry `2026-07-14.1` passed at the recorded review time, and `git diff --check` reported no whitespace errors; fixed-value README/QA credentials were removed. Pytest emitted a non-test cache-permission warning. **PR:** pending. **Merge SHA:** pending. **Required CI run/URL:** pending. **Browser matrix and screenshot URI/checksum:** pending. **Release manifest/image digests:** pending. **Migration execution:** pending. **Production revisions and health commit:** pending. **Post-deploy E2E/rollback/DR evidence:** pending. **Owners and approvers:** unassigned. No capability maturity, mandatory gate, public availability, claim treatment, or unsafe live action was promoted by this local work.
- **2026-07-15 (final local acceptance for the current working tree; no promotion):** The PR-equivalent Python command completed with 5,801 passed, 7 skipped, 5 expected failures, and zero failures; protected generated artifacts were restored byte-for-byte after test side effects. Real PostgreSQL acceptance completed 6/6 Alembic scenarios, including empty/legacy/managed bootstrap, downgrade/re-upgrade, append-only and promotion-chain triggers, RLS, and least privilege; readiness API integration completed 2/2 and the broader database boundary completed 108 passed with 2 live-provider skips. UI acceptance completed typecheck, lint with zero errors and 20 pre-existing warnings, 25 files/167 tests, zero production dependency vulnerabilities, and a 711-module production build; focused public Playwright completed 25/25. Browser QA covered 23 public routes at desktop and mobile widths: all 46 route/viewport observations returned 200, exactly one H1, no horizontal overflow, no page exceptions, and no failed static assets. Scroll-triggered sections and representative home, pricing, executive, and legal pages were visually inspected; the only console signal was the expected local API-proxy 502 with the backend intentionally absent, and pricing visibly failed closed. Temporary screenshots were not retained as release evidence. Claim registry `2026-07-14.1`, billing catalog `2026-07-15.1`, Ruff, mypy across 989 files, the 350-route enterprise stability gate, documentation regression, generated-LLM parity, and whitespace checks passed. **PR:** pending. **Merge SHA:** pending. **Required CI run/URL:** pending. **Retained browser matrix and screenshot URI/checksum:** pending. **Release manifest/image digests:** pending. **Non-production and production migration execution:** pending. **Production revisions and health commit:** pending. **Post-deploy E2E/rollback/DR evidence:** pending. **Owners and approvers:** unassigned. These results validate the local implementation only; they do not promote capability maturity, mandatory gates, public availability, claim treatment, or unsafe live actions.
