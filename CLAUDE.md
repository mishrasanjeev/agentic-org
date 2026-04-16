# AgenticOrg Claude Guide

This repository is a multi-tenant enterprise AI platform. Optimize for correct, minimal, verifiable, production-safe changes. The default bar is enterprise-grade, not demo-grade.

## Mission

- Deliver the simplest correct change that satisfies the request.
- Never weaken security, tenancy isolation, secrets handling, or operability for convenience.
- Prefer clarity over cleverness, explicitness over magic, and small diffs over broad rewrites.

## Repo Context

- Backend: FastAPI, async SQLAlchemy, Alembic, Redis, Celery, LangGraph.
- Frontend: React 19, TypeScript, Vite, Vitest, Playwright.
- Infra: Docker, Docker Compose, Helm, GitHub Actions.
- Core directories:
  - `api/` request handlers and FastAPI app wiring
  - `auth/` authn/authz and middleware
  - `core/` business logic, models, tool gateway, billing, tasks
  - `migrations/` schema migrations
  - `ui/` frontend app
  - `helm/`, `docker-compose.yml`, `.github/workflows/` deployment and delivery

## Operating Principles

### 1. Think Before Coding

- Do not silently choose an interpretation when the request is ambiguous.
- State assumptions explicitly before making consequential changes.
- If there is a simpler or safer approach than the requested one, say so.
- If a behavior, schema, contract, or security boundary is unclear, inspect first and ask only if needed.

### 2. Simplicity First

- Build the minimum code that solves the actual problem.
- Do not add speculative abstractions, options, flags, or indirection.
- Do not introduce a framework pattern for a one-off use case.
- If a solution feels "future-proof" but the future is hypothetical, cut it.

### 3. Surgical Diffs

- Touch only files and lines that are necessary.
- Match the existing style and local patterns unless the task is explicitly a refactor.
- Do not opportunistically rewrite adjacent code, comments, or formatting.
- Only remove dead code if your change made it dead or the user asked for cleanup.

### 4. Goal-Driven Execution

- Translate requests into explicit success criteria.
- Prefer verifiable outcomes over vague implementation work.
- For non-trivial work, form a short plan with checks.
- Keep going until the change is implemented and verified, unless blocked by missing information or permissions.

## Non-Negotiable Enterprise Rules

### Authz and Tenancy

- Never trust `tenant_id`, role, domain, or privilege claims from the client request body or query string when an authenticated server-side context exists.
- Bind tenant-scoped operations to authenticated tenant context on the server.
- Any tenant-wide admin or control-plane action must enforce server-side authorization, not only frontend role gating.
- UI route guards are convenience only. Backend authorization is the real control boundary.
- Fail closed on missing user, missing tenant, incomplete session hydration, or ambiguous privilege state.
- For multi-tenant code, actively test the "tenant A trying to affect tenant B" case in your head before shipping.

### Secrets and Sensitive Data

- Do not store secrets in plaintext config, plaintext database fields, logs, metrics, or exceptions.
- If a secret must be persisted, prefer encrypted storage or a real secret manager path.
- PII masking is for logs, traces, and audit artifacts, not for live execution payloads.
- Never leak tokens, API keys, email addresses, tenant identifiers, or connector credentials into telemetry labels.

### Database and Schema Discipline

- Every schema change must have an explicit Alembic migration.
- Do not rely on startup-time DDL as the only delivery path for schema evolution.
- For tenant-scoped tables, require row-level security or an equally explicit isolation mechanism.
- Schema changes must consider backfill, nullability, rollout order, and rollback behavior.

### Async and Runtime Safety

- Do not call synchronous Redis, HTTP, or database clients from async request handlers.
- Keep readiness checks cheap and local. Do not make production readiness depend on broad external fan-out.
- Public health endpoints must not expose sensitive environment or connector details.
- Background worker entrypoints, queue names, and deployment commands must match real modules in the repo.

### API and Domain Behavior

- Validate inputs at the boundary with Pydantic or equivalent typed models.
- Preserve stable response shapes unless the user explicitly requests an API break.
- If you change auth, billing, org management, connectors, SSO, approvals, branding, or invoices, assume the blast radius is high and validate accordingly.

### Observability

- Metrics must stay low-cardinality. Do not use raw tenant IDs, user emails, request IDs, or arbitrary object IDs as metric labels.
- Use structured logs for detailed context and metrics for aggregate signals.
- Log enough to debug, but never enough to leak secrets or customer data.

### Frontend Integrity

- Treat the backend as the source of truth for authorization and tenancy.
- Do not mark a user "authenticated enough" unless the session can be validated and hydrated safely.
- Prefer typed API helpers and explicit error states over silent fallbacks.
- If a frontend change affects auth, routing, billing, or admin workflows, run at least targeted UI tests and a build.

### Delivery and Release Safety

- For changes in auth, billing, secrets, migrations, infra, workers, or deployment logic, verification is mandatory.
- Do not treat "degraded but maybe okay" as good enough without explicit reasoning.
- Keep deployment changes internally consistent across Docker, Helm, and CI if they touch the same runtime path.

## Default Workflow

1. Inspect the current implementation and existing patterns before changing code.
2. Define the exact success criteria and identify the smallest safe diff.
3. Implement the change without drive-by refactors.
4. Verify with the smallest meaningful checks.
5. Report what changed, how it was verified, and any remaining risks.

## Required Before Every Push

Before **any** `git push`, run the local preflight gate. It mirrors every blocking check in CI so the push-and-pray loop ends.

```
bash scripts/preflight.sh
```

Fast backend-only iteration:

```
SKIP_UI=1 bash scripts/preflight.sh
```

The gate checks: branch safety (never main), `ruff check .` (whole tree), `bandit -ll -iii` on api/auth/core, alembic revision IDs ≤ 32 chars, `verify=False` scan in production code, `pytest tests/regression/ tests/unit/`, `tsc --noEmit`, and `npm run build`.

Git hooks enforce this automatically — run once per clone:

```
bash scripts/install_hooks.sh
```

After that, `git commit` refuses direct-to-main commits, and `git push` runs the preflight. Emergency bypass when absolutely required: `git push --no-verify`.

## Required Verification by Change Type

### Backend Python changes

- Run targeted tests first.
- Run `ruff check` on the touched backend areas.
- If pytest fails only because coverage output is locked or unavailable, rerun the targeted suite with `--no-cov` and say so explicitly.

### Auth, billing, org, connector, or tenancy changes

- Add or run boundary tests for authz, tenant isolation, and negative cases.
- Verify no client-controlled identifier can cross tenant boundaries.

### Schema or migration changes

- Add an Alembic migration.
- Sanity-check upgrade behavior and any runtime assumptions that depend on the new schema.

### Frontend changes

- Run targeted `vitest` coverage for the touched flow when feasible.
- Run `ui` build when routes, types, auth flows, or shared API helpers changed.

### Infra and worker changes

- Verify commands point to real modules, scripts, or binaries in the repo.
- Keep Compose, Helm, and CI aligned if the same entrypoint or environment contract is affected.

## Practical Commands

- Backend lint: `ruff check api auth core connectors workflows`
- Backend tests: `python -m pytest -q`
- Targeted backend tests without coverage fallback: `python -m pytest -q --no-cov <tests...>`
- Security scan: `python -m bandit -r api auth core -x migrations,tests -f json`
- Frontend tests: `cd ui && npm test`
- Frontend build: `cd ui && npm run build`

## Style Preferences for This Repo

- Prefer typed, explicit Python over meta-programming.
- Prefer direct FastAPI dependencies over hidden middleware magic when enforcing route behavior.
- Prefer small helper functions close to use over global abstractions used once.
- Preserve existing comments unless they are now incorrect.
- Keep imports, naming, and file organization consistent with neighboring code.

## What Good Output Looks Like

- Small diff.
- No accidental API break.
- No new tenant-isolation hole.
- No new secret or PII leak.
- Clear verification.
- Clear residual risks when verification is partial.

## What Bad Output Looks Like

- Broad rewrites for a small request.
- Client-side-only auth or tenancy checks.
- Schema changes without migrations.
- Sync I/O inside async handlers.
- New telemetry cardinality explosions.
- "Fixed" code that was never actually run or tested.

## CI Failure Patterns (Lessons Learned)

These patterns caused CI failures during the April 2026 enterprise program. Check before pushing.

1. **TypeScript strict mode**: `noUnusedLocals` is enabled. Unused `const` declarations fail the build (TS6133).
2. **Regression tests that grep source code**: Some tests in `test_bugs_april06_2026.py` check that `Depends` appears on the `def` line. Multi-line signatures hide it. Keep `Depends` on the same line for short signatures.
3. **Integration tests with hardcoded versions**: `test_api_integration.py` asserts the version from `/health`. When bumping version, update: `pyproject.toml`, `api/main.py`, `api/v1/health.py`, and the integration test.
4. **Pydantic env prefix**: `core/config.py` uses `env_prefix = "AGENTICORG_"`. Field `foo_bar` maps to `AGENTICORG_FOO_BAR`, not `FOO_BAR`.
5. **Route collisions**: FastAPI silently registers both handlers for the same path — the first registered wins. Check for duplicates before adding routes.
6. **Health gate strictness**: Production health gate accepts only `"healthy"`. If you change the gate, update the regression test that asserts the expected value.
7. **Regression tests for OLD permissive behavior**: When tightening a gate, search for tests that assert the old permissive behavior. They will fail because they check the opposite of what you changed.

## Source Inspiration

This file adopts the core behavioral model from Forrest Chang's Karpathy-inspired Claude guidelines and specializes it for AgenticOrg's enterprise multi-tenant product and codebase.
