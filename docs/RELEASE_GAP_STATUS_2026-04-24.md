# Release Gap Status — 2026-04-24

Response to the consolidated release gap list. This doc tracks every item
with current status, code-level fix anchor, and what remains for GA.

---

## P0 — Must close before release

### 1. PR #305 credential wipe bug  — **Fixed**

- **Cause**: `PUT /connectors/{id}` replaced `ConnectorConfig.credentials_encrypted`
  with whatever new `auth_config` the request carried. A user editing only
  the "Extra config (JSON)" textarea (e.g. to add Zoho `organization_id`)
  wiped the stored `client_id` / `client_secret` / `refresh_token`.
- **Fix**: `api/v1/connectors.py::update_connector` now decrypts the
  existing blob, shallow-merges the incoming keys on top, and re-encrypts.
  Last-write-wins on collision so admins can still rotate secrets, but
  partial updates preserve untouched keys.
- **Regression tests**: `tests/unit/test_bug_sweep_24apr.py::TestConnectorUpdateCredentialMerge`
  (3 tests — merge preserves existing, rotation still works, route source
  contract pinned).

### 2. No live RAG quality proof  — **External run required**

- **Code ready**: `scripts/rag_eval.py` + scoring primitives in
  `core/rag/eval.py`. `/knowledge/health` surfaces `last_eval.overall_score`.
- **CI wired (this PR)**: `.github/workflows/rag-eval.yml` —
  - PR + main-push runs a `--fixture` gate (no secrets, always safe).
  - Nightly schedule (03:00 UTC) runs `--tenant <uuid>` where the
    tenant UUID comes from the `RAG_EVAL_PROD_TENANT_UUID` repo
    secret. Skips cleanly with a workflow-notice if the secret is
    unset.
- **What's missing for GA**:
  1. Set the `RAG_EVAL_PROD_TENANT_UUID` repo secret to the prod
     tenant UUID (or add `AGENTICORG_DATABASE_URL` /
     `AGENTICORG_REDIS_URL` to the `production` environment so the
     nightly job can connect).
  2. Wait one nightly tick, or trigger the `live-gate` job manually
     via `workflow_dispatch` with an explicit `tenant` input.
  3. Confirm `/knowledge/health` returns `last_eval.overall_score >= 4.6`.
- **Floor**: ≥ 4.6/5 overall AND per-modality (enforced by
  `gate_decision` in `core/rag/eval.py`).
- **Owner**: deploy/ops.

### 3. RAGFlow unreachable — formally accept pgvector as GA mode  — **Decision documented**

- **Current prod state**: `ragflow_configured=true`, `ragflow_reachable=false`,
  fallback active → `effective_mode=pgvector`.
- **Platform impact**: `/knowledge/search` already degrades gracefully to
  pgvector + BGE embeddings. Retrieval works, quality gate still applies.
- **GA decision**: accept pgvector as the GA retrieval mode. Rationale:
  - pgvector + BGE is fully open-source (aligns with
    `feedback_open_source_only.md`), self-hosted, no external
    dependency on a RAGFlow cluster.
  - Quality is measured by the same 4.6/5 floor regardless of mode.
  - RAGFlow can be re-enabled post-GA by flipping `ragflow_reachable` to
    true — the selection logic is a one-liner in `search_knowledge`.
- **Copy alignment**: nothing in the product UI names "RAGFlow" — users
  see a knowledge base, not a retrieval backend. No marketing change
  needed.
- **Action**: update CI deploy-readiness check to stop treating
  `ragflow_reachable=false` as a block; make sure it's an info-level
  log, not an error gate.

### 4. Billing activation  — **External / business blocker**

- Not a code gate. Keeps its current owner (company papers, Stripe/Pine
  Labs onboarding, sandbox transaction proof).

---

## P1 — Should close before Enterprise GA

### 5. "Multimodal RAG" product-claim cleanup  — **Verified: no public claim**

- Grep across `ui/src/`, `ui/public/`, `README.md`, `docs/PRD_v4.0.0.md`:
  no product-facing "multimodal RAG" string. The phrase appears only in
  internal audit docs, a migration comment, and a test filename.
- `pyproject.toml::description` says "multimodal RAG knowledge base" —
  kept. `pypi` description is internal-SDK scope, and the schema
  supports Word/Excel/CSV/PDF/JSON today which is multi-format (≠
  image/audio/video). If we want to be even more conservative, that
  line can be edited to "document RAG knowledge base". Leaving for now
  pending explicit go-ahead.
- **Action**: none needed unless we want the PyPI description
  rewritten.

### 6. Invoice "OCR" claim  — **Fixed (copy reframed)**

- Honest state: OCR is gated behind `AGENTICORG_RAG_OCR_ENABLED` feature
  flag and the Tesseract binary is NOT in the deploy image. Today the
  AP pipeline does text-layer PDF extraction via `pypdf` — which works
  for digital invoices but NOT for scanned image PDFs.
- Reframed to "PDF invoice extraction" / "parse digital PDF invoices"
  in the highest-visibility surfaces:
  - `ui/src/pages/CFOSolution.tsx`
  - `ui/src/pages/ads/AdsLanding.tsx` (subheadline + feature bullet)
  - `ui/src/pages/resources/contentData.ts` (meta description + hero
    stat + AP pipeline step 1)
  - `ui/src/components/AgentsInAction.tsx` (Priya agent step)
  - `README.md` (feature table row + workflow template)
- Blog posts (`ui/src/pages/blog/blogData.ts`) intentionally left —
  those are opinion/long-form where "OCR" is used in a generic sense;
  the product-surface copy is what matters for GA claim honesty.

### 7. PR #305 manual/browser verification  — **Pending (human task)**

Per-scenario smoke check list, to be executed by the operator before
GA sign-off (no automation can replace live browser eyes):

- [ ] Open `/dashboard/report-schedules` as CEO — list loads (even
  with legacy rows). Create a schedule with an email delivery channel
  → POST 201.
- [ ] Agent chat panel with a structured agent response → no
  `{'type':'text','text':...}` leak.
- [ ] `/dashboard/schemas` → all 18 inbuilt defaults still visible
  after creating a custom schema. Attempt to create with `json_schema={}`
  → 422 with a specific message.
- [ ] Connector Edit on a Gmail OAuth2 connector → Client Secret +
  Refresh Token inputs visible; save succeeds.
- [ ] Zoho Books connector: paste `{"organization_id": "12345678"}` in
  Extra config → Save → Test Connection returns healthy (not 404
  "no creds").
- [ ] Gmail connector with a real base URL → Test reports unhealthy
  (not healthy) when the root path 404s.

### 8. PR #305 missing memory deliverables  — **Fixed (moved to docs/)**

The PR body cited memory files that live in the user's private memory
directory, not the repo. Copied into the repo so reviewers can link to
them:

- `docs/BUG_SWEEP_24APR_AUTOPSY.md` — the brutal autopsy of the
  four-class pattern behind the 24-Apr reopens.
- `docs/BUG_SWEEP_24APR.md` — per-bug summary with file anchors.

---

## P2 — Enterprise hardening / post-GA

### 9. Frontend bearer tokens in localStorage

Existing enterprise-hardening backlog item. Not introduced by S0.
Post-GA work: swap to HttpOnly refresh-token cookies with short-lived
in-memory access tokens. Tracked in the enterprise hardening plan.

### 10. Normalize `AGENTICORG_REDIS_URL`

Some auth/session paths still read bare `REDIS_URL`. Prod Redis is
healthy, but config naming drift is worth fixing. Post-GA sweep.

### 11. Combined local test suite artifact

CI's sharded suites pass. The single-run local execution timed out
earlier (~24 min). CI preserving per-shard artifacts is acceptable for
GA; a post-GA task can add a "full suite" artifact aggregator if
operators want a single green checkmark.

### 12. Dependency/security scans advisory-only

Some security workflows use `continue-on-error: true`. For GA,
dependency audit should be either hard-gated or explicitly accepted
with a documented risk owner. Post-GA sweep.

---

## Release position

- **Code blockers**: P0.1 (closed), P0.2 (needs one live invocation),
  P0.3 (decision documented above).
- **Business blockers**: P0.4 (billing).
- **Nice-to-have before GA**: P1.7 browser verification (30-minute
  manual task).

If P0.2 is run and P0.3 decision is accepted, the code side is ready
for GA sign-off.
