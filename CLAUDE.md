# AgenticOrg Engineering Guide

`AGENTS.md` and `CLAUDE.md` in this repository are the same document. Change both in the
same commit and keep them identical.

This repository is a multi-tenant enterprise AI platform. Optimize for correct, minimal,
verifiable, production-safe changes. The default bar is enterprise-grade, not demo-grade.
Keep changes small, explicit, and verifiable, and follow existing patterns before adding a
new abstraction.

## Hard rules

These apply to every change, in every file, commit, branch and pull request.

**No tool attribution anywhere.** Commit messages, author and committer fields, branch
names, pull request titles and descriptions, code comments, documentation, changelogs,
release notes, and this file contain no mention of any AI coding tool, assistant or model
provider as an author, contributor or source, and no `Co-Authored-By` or "Generated with"
line for a tool. Naming a model provider or model as a product integration the software
supports (an LLM provider id, a model name, a connector) is not attribution. Check
`git config user.name` and `git config user.email` before your first commit; if either
names a tool, stop. Name branches for the work (`feat/registry-attestations`,
`fix/enforce-audience`), never for a tool. Before every push, read
`git log --format='%an %ae %cn %ce%n%B' origin/main..HEAD` and confirm that no tool name,
tool co-author trailer or generated-by line appears.

**Vendor-neutral, always.** Do not name any specific identity-verification,
business-verification, KYC, KYB, AML or screening vendor anywhere: code, comments, commits,
branches, pull requests, docs, fixtures, example configuration, package metadata. Use
`mock` and `acme_kyb` for provider examples; commercial provider adapters belong in separate
packages. The mock issuer is `mock-issuer.example`; documentation examples use
`issuer.example`, `provider.example` and `merchant.example`; the example agent is
`shopper-01`; the example software is `Nimbus Shopper 2.4`. If a field or behaviour only
makes sense for one vendor's product, it does not belong in the interface. Public payments
and identity standards and their publishers (AP2, Verifiable Intent, ACP, UCP, Stripe Shared
Payment Tokens, Visa Trusted Agent Protocol, Web Bot Auth, OpenID Federation, IETF and W3C
documents) may be named, because this work renders into them. `scripts/check_denylist.py`
enforces the vendor list; it must pass on every pull request.

**House terminology.** Use the left term, never the right: *registry*, not directory;
*operator override*, not kill switch; *issuer-branded*, not white-label; *irregularity*, not
anomaly; *attestation*, not verification result; *accredited issuer*, not trust provider or
verification partner; *relying party*, not consumer; `software_name` / `software_version`,
not name / version; *Agent Passport* for the credential, *grant* for the delegation.

**No new public exposure.** No internal planning documents, customer or partner names, real
individuals' names, local filesystem paths, commercial terms or real secret values in this
public repository.

**Synthetic data only.** `example.com`, `example.test` and `.example` domains, reserved-range
identifiers, invented names and amounts.

**Secrets.** Placeholders only; real values come from the environment or a secret manager.
Never commit `.env` files, keys or certificates.

**Standards, not inventions.** Where a design names a standard (SD-JWT, SD-JWT VC, Token
Status List, Bitstring Status List, OpenID Federation, SSF/CAEP, RFC 9421, RFC 8693,
RFC 9396, RFC 7638/8037, RFC 9651, CIMD, Web Bot Auth), implement it as the current text
specifies and cite the section in a comment. Verify claim names, media types and `typ`
values against the specification text, not memory.

**Feature flags.** Every behaviour change on an existing path ships behind a flag that
defaults off. New endpoints may ship enabled behind authentication. A deliberate default
flip is recorded in `CHANGELOG.md` as a breaking change with an explicit opt-out.

**Local first, then production.** Everything runs locally with `make dev` (Docker) before it
is merged. A production deployment happens only after its exit criterion is green in CI and
the owner has approved the runbook for it.

## Mandatory for bug fixes and reopen analysis

Every bug fix, reopen triage, QA-driven PR, or bug-list engagement on this repo must use the
repo-authored skill `agenticorg-bug-fix-fail-closed` (in the repository's skills directory)
and follow the canonical checklist in [`docs/bug_triage_skill.md`](docs/bug_triage_skill.md).
`docs/bug_triage_skill.md` is the source of truth for the fail-closed verdict matrix,
symptom-grep rule, sibling-path sweep, test-replays-tester's-steps rule,
merged-vs-deployed honesty, release sign-off discipline, and Alembic safety gates. Producing
a bug-fix summary, reopen verdict, or release sign-off without walking that checklist is an
incorrect work product.

Reproduce a reported bug, inspect sibling paths, add a regression test that replays the
failure, and rerun it after the fix.

Forbidden verdicts: "should be fixed", "the code looks correct", "probably a cache issue",
"fixed in main" (without deploy confirmation).

## Mandatory for enterprise hardening, architecture review, and release sign-off

Any whole-codebase audit, enterprise hardening review, production-readiness assessment,
architecture-safety review, or release sign-off in this repo must use the repo-authored
skill `agenticorg-enterprise` (in the repository's skills directory). This includes reviews
of workflow durability, auth/session hardening, billing runtime behavior, connector secret
handling, async safety, startup DDL, multi-tenant isolation, and deployment readiness.
Producing an enterprise verdict or release sign-off without walking that skill is an
incorrect work product.

If the task is both bug-related and enterprise-hardening-related, use both repo-authored
skills together: `agenticorg-bug-fix-fail-closed` and `agenticorg-enterprise`.

## Mission

- Deliver the simplest correct change that satisfies the request.
- Never weaken security, tenancy isolation, secrets handling, or operability for convenience.
- Prefer clarity over cleverness, explicitness over magic, and small diffs over broad rewrites.

## Repo context

- Backend: FastAPI, async SQLAlchemy, Alembic, Redis, Celery, LangGraph.
- Frontend: React 19, TypeScript, Vite, Vitest, Playwright.
- Infra: Docker, Docker Compose, Cloud Run, GitHub Actions.
- Core directories:
  - `api/` request handlers and FastAPI app wiring
  - `auth/` authn/authz and middleware
  - `core/` business logic, models, tool gateway, billing, tasks
  - `migrations/` schema migrations
  - `ui/` frontend app
  - `docker-compose.yml`, `scripts/deploy_cloud_run.sh`, `.github/workflows/` deployment and delivery

## Operating principles

### 1. Think before coding

- Do not silently choose an interpretation when the request is ambiguous.
- State assumptions explicitly before making consequential changes.
- If there is a simpler or safer approach than the requested one, say so.
- If a behavior, schema, contract, or security boundary is unclear, inspect first and ask only if needed.

### 2. Simplicity first

- Build the minimum code that solves the actual problem.
- Do not add speculative abstractions, options, flags, or indirection.
- Do not introduce a framework pattern for a one-off use case.
- If a solution feels "future-proof" but the future is hypothetical, cut it.

### 3. Surgical diffs

- Touch only files and lines that are necessary.
- Match the existing style and local patterns unless the task is explicitly a refactor.
- Do not opportunistically rewrite adjacent code, comments, or formatting.
- Only remove dead code if your change made it dead or the user asked for cleanup.

### 4. Goal-driven execution

- Translate requests into explicit success criteria.
- Prefer verifiable outcomes over vague implementation work.
- For non-trivial work, form a short plan with checks.
- Keep going until the change is implemented and verified, unless blocked by missing information or permissions.

## Non-negotiable enterprise rules

### Authority and tenancy

- Derive tenant, role, domain and privilege from authenticated server-side context. Never trust `tenant_id`, role, domain, or privilege claims from the client request body or query string when an authenticated server-side context exists.
- Bind tenant-scoped operations, and every database read and write, to the authenticated tenant on the server, and test cross-tenant attempts ("tenant A trying to affect tenant B").
- Check authorization at the backend action boundary. Any tenant-wide admin or control-plane action must enforce server-side authorization; a UI route guard alone is insufficient.
- Fail closed on missing user, missing tenant, incomplete session hydration, or ambiguous privilege state. A missing grant, failed policy load, invalid condition, or unverifiable webhook must not authorize an action.

### Secrets and sensitive data

- Do not store secrets in plaintext config, plaintext database fields, logs, metrics, or exceptions. Store persisted credentials encrypted or in a secret manager.
- Never put credentials or personal data in logs, metrics labels, fixtures, or public documentation.
- PII masking is for logs, traces, and audit artifacts, not for live execution payloads.
- Never leak tokens, API keys, email addresses, tenant identifiers, or connector credentials into telemetry labels.

### Database and schema discipline

- Every schema change must have an explicit, forward-only Alembic migration. Plan backfill, nullability, rollout order, rollback behavior and tenant isolation.
- Do not rely on startup-time DDL as the only delivery path for schema evolution.
- For tenant-scoped tables, require row-level security or an equally explicit isolation mechanism.

### Async and runtime safety

- Do not call synchronous Redis, HTTP, or database clients from async request handlers.
- Keep readiness checks cheap and local. Do not make production readiness depend on broad external fan-out.
- Public health endpoints must stay local and must not expose sensitive environment or connector details.
- Background worker entrypoints, queue names, and deployment commands must match real modules in the repo.

### API and domain behavior

- Validate inputs at the boundary with Pydantic or equivalent typed models.
- Preserve stable response shapes unless the user explicitly requests an API break.
- If you change auth, billing, org management, connectors, SSO, approvals, branding, or invoices, assume the blast radius is high and validate accordingly.

### Observability

- Metrics must stay low-cardinality. Do not use raw tenant IDs, user emails, request IDs, or arbitrary object IDs as metric labels.
- Use structured logs for detailed context and metrics for aggregate signals.
- Log enough to debug, but never enough to leak secrets or customer data.

### Frontend integrity

- Treat the backend as the source of truth for authorization and tenancy.
- Do not mark a user "authenticated enough" unless the session can be validated and hydrated safely.
- Prefer typed API helpers and explicit error states over silent fallbacks.
- If a frontend change affects auth, routing, billing, or admin workflows, run at least targeted UI tests and a build.
- **Design quality pass:** every UI-touching PR runs the `/audit`, `/critique` and `/polish` passes from the impeccable design skill pack against the changed components before push, and records the output summary in the PR description. See [`docs/frontend-design-workflow.md`](docs/frontend-design-workflow.md) for the playbook. The pass catches generic generated-UI patterns (overused fonts, gray-on-colored text, nested cards, bounce easing) that Playwright correctness tests don't.

### Delivery and release safety

- For changes in auth, billing, secrets, migrations, infra, workers, or deployment logic, verification is mandatory.
- Do not treat "degraded but maybe okay" as good enough without explicit reasoning.
- Keep deployment changes internally consistent across Docker, the Cloud Run deploy script, and CI if they touch the same runtime path.

## Default workflow

1. Inspect the current implementation and existing patterns before changing code.
2. Define the exact success criteria and identify the smallest safe diff.
3. Implement the change without drive-by refactors.
4. Verify with the smallest meaningful checks.
5. Report what changed, how it was verified, and any remaining risks.

## Required before every push

Keep the working branch off `main`. Before **any** `git push`, run the local preflight gate.
It mirrors every blocking check in CI so the push-and-pray loop ends.

```
bash scripts/preflight.sh
```

Fast backend-only iteration (set `SKIP_UI=1` only for a backend-only change):

```
SKIP_UI=1 bash scripts/preflight.sh
```

The gate runs the same checks as CI: branch safety (never main), `ruff check .` (whole tree),
`bandit -ll` on core/connectors/api/auth, alembic revision IDs ≤ 32 chars, `verify=False`
scan in production code,
`pytest tests/unit/ tests/contract/ tests/connector_harness/ tests/security/ tests/regression/` (CI adds `--cov-fail-under=55`),
`tsc --noEmit`, `npm run lint`, `vitest`, and `npm run build`.

Git hooks enforce this automatically — run once per clone:

```
bash scripts/install_hooks.sh
```

After that, `git commit` refuses direct-to-main commits, and `git push` runs the preflight.
Emergency bypass when absolutely required: `git push --no-verify`.

## Required verification by change type

Run `make check` and `make test` before claiming a release-ready result.

### Backend Python changes

- Run targeted tests first, and lint for Python changes.
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
- Keep Compose, the Cloud Run deploy script, and CI aligned if the same entrypoint or environment contract is affected.

## Practical commands

- Backend lint: `ruff check api auth core connectors workflows`
- Backend tests: `python -m pytest -q`
- Targeted backend tests without coverage fallback: `python -m pytest -q --no-cov <tests...>`
- Security scan: `python -m bandit -r api auth core -x migrations,tests -f json`
- Vendor denylist (what CI and `make check-denylist` run): `python scripts/check_denylist.py scan --base origin/main --head HEAD`.
  `audit` checks every tracked file and still fails on the three lines FINDINGS A-40 tracks.
- Frontend tests: `cd ui && npm test`
- Frontend build: `cd ui && npm run build`

## Style preferences for this repo

- Prefer typed, explicit Python over meta-programming.
- Prefer direct FastAPI dependencies over hidden middleware magic when enforcing route behavior.
- Prefer small helper functions close to use over global abstractions used once.
- Preserve existing comments unless they are now incorrect.
- Keep imports, naming, and file organization consistent with neighboring code.

## What good output looks like

- Small diff.
- No accidental API break.
- No new tenant-isolation hole.
- No new secret or PII leak.
- Clear verification.
- Clear residual risks when verification is partial.

## What bad output looks like

- Broad rewrites for a small request.
- Client-side-only auth or tenancy checks.
- Schema changes without migrations.
- Sync I/O inside async handlers.
- New telemetry cardinality explosions.
- "Fixed" code that was never actually run or tested.

## CI failure patterns (lessons learned)

These patterns caused CI failures during the April 2026 enterprise program. Check before pushing.

1. **TypeScript strict mode**: `noUnusedLocals` is enabled. Unused `const` declarations fail the build (TS6133).
2. **Regression tests that grep source code**: Some tests in `test_bugs_april06_2026.py` check that `Depends` appears on the `def` line. Multi-line signatures hide it. Keep `Depends` on the same line for short signatures.
3. **Integration tests with hardcoded versions**: `tests/integration/test_api_integration.py` asserts the version from `/health`. The API reads its version from `pyproject.toml` (`api/v1/product_facts.py`), so a version bump changes `pyproject.toml` and that test's expected value.
4. **Pydantic env prefix**: `core/config.py` uses `env_prefix = "AGENTICORG_"`. Field `foo_bar` maps to `AGENTICORG_FOO_BAR`, not `FOO_BAR`.
5. **Route collisions**: FastAPI silently registers both handlers for the same path — the first registered wins. Check for duplicates before adding routes.
6. **Health gate strictness**: Production health gate accepts only `"healthy"`. If you change the gate, update the regression test that asserts the expected value.
7. **Regression tests for old permissive behavior**: When tightening a gate, search for tests that assert the old permissive behavior. They will fail because they check the opposite of what you changed.
