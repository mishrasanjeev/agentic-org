# Strict Repo Audit — S0 Closure Plan (2026-04-24)

**Source audit:** `docs/STRICT_REPO_AUDIT_AND_TEST_MATRIX_2026-04-24.md`
**Stop-ship items in scope:** S0-06, S0-07, S0-08, S0-09.
**Verdict:** **Release blocked** until all four close with passing tests + a
measured 4.6+/5 retrieval quality gate.

This plan exists because the four S0 items are large and interdependent. Shipping
them as a single PR would be unreviewable and would break the Alembic/model
contract mid-flight. Each S0 is scoped to its own PR, ordered so earlier PRs
don't block on later ones and dependent work lands in the right sequence.

## Ordered PR sequence

| # | PR title | Closes | Depends on | Estimated diff | Acceptance criteria (all must be green to merge) |
| - | --- | --- | --- | --- | --- |
| 1 | **feat(tenant-ai-credentials): encrypted BYO provider tokens + admin API + UI** | S0-09 | — | backend + UI + migration + tests | See §PR-1 below |
| 2 | **feat(tenant-ai-config): tenant LLM / embedding / chunking settings with model allowlist** | S0-08 | PR-1 | backend + UI + migration + tests | See §PR-2 |
| 3 | **feat(rag-multimodal): unified ingestion service + PDF/Word/Excel/image/audio/video extraction + provenance** | S0-06 | PR-1, PR-2 | backend + migration + tests | See §PR-3 |
| 4 | **feat(rag-quality-gate): gold corpus + 0-5 scoring + 4.6/5 floor blocks ingestion and model rotation** | S0-07 | PR-1, PR-2, PR-3 | backend + tests + docs | See §PR-4 |

Each PR ships its own regression suite. Later PRs MUST NOT regress earlier acceptance criteria.

## PR-1 — BYO AI provider credentials (S0-09)

### Deliverables
- New table `tenant_ai_credentials` (tenant-scoped, encrypted):
  - `id`, `tenant_id`, `provider`, `credential_kind`, `credentials_encrypted` (JSONB, envelope via `core.crypto.encrypt_for_tenant`), `status`, `created_at`, `updated_at`, `last_health_check_at`, `last_used_at`, `rotated_at`, `display_prefix`, `display_suffix`.
  - `provider` allowlist: `gemini | openai | anthropic | azure_openai | openai_compatible | voyage | cohere | ragflow | stt_deepgram | stt_azure | tts_elevenlabs | tts_azure`.
  - `credential_kind`: `llm | embedding | rag | stt | tts`.
  - Unique `(tenant_id, provider, credential_kind)` — one BYO token per provider/kind per tenant.
- Alembic migration `v492_tenant_ai_credentials` idempotent.
- Admin API `api/v1/tenant_ai_credentials.py`: CRUD + `/{id}/test` + `/{id}/rotate`. Admin-gated at router level.
- Provider resolver `core/ai_providers/resolver.py`: `get_provider_credential(tenant_id, provider, kind) -> str | None`. Preferred over platform env. Platform fallback gated by tenant-setting `ai_fallback_policy` (`allow | deny`).
- Integration points updated:
  - `core/langgraph/llm_factory.py` lines 185/195/206 — call resolver.
  - `core/llm/router.py` lines 314/367/400 — call resolver.
  - `core/embeddings.py` — add cloud-provider embedding path (OpenAI) guarded by resolver.
  - `api/v1/voice.py` — persist STT/TTS keys through the same vault (retires the in-memory dict at line 336).
- Admin UI page `/dashboard/settings/ai-credentials` — list / create / test / rotate / delete. Never displays raw tokens (prefix + suffix only).

### Acceptance criteria
- [ ] `tests/regression/test_s009_byo_tokens.py` — ≥ 15 cases covering: tenant isolation (A can't read B's tokens), encryption-at-rest (no plaintext in DB dump), masking on list/show endpoints, masking in logs, non-admin rejection on every mutation, platform fallback disabled by policy, rotation updates `rotated_at` without restart, delete blocked while referenced by an active tenant-ai-config, `/test` endpoint actually calls the provider's identity probe.
- [ ] `grep -r 'raw_token\|api_key=.*plaintext' core/ api/ | grep -v 'tests/' | grep -v 'docstring' | wc -l` returns 0.
- [ ] `/api/v1/tenant-ai-credentials` requires admin; 401/403 tests assert.
- [ ] Preflight green (ruff + bandit + alembic ≤ 32 chars + pytest + tsc + vite build + consistency sweep).
- [ ] UI page passes `npx tsc --noEmit`; Playwright smoke test covers create + test + rotate.
- [ ] Audit log receives a `tenant_ai_credential.{created|updated|tested|rotated|deleted|used}` event for every mutation.

### Residual risk if merged alone
PR-1 adds the storage + resolver but PR-2 (model config) is required before the UI can pick a provider per-tenant. Until PR-2 lands, PR-1 is useful for STT/TTS persistence only.

## PR-2 — Tenant AI config (S0-08)

### Deliverables
- New table `tenant_ai_settings`:
  - `tenant_id` (PK), `llm_provider`, `llm_model`, `llm_fallback_model`, `llm_routing_policy`, `max_input_tokens`, `embedding_provider`, `embedding_model`, `embedding_dimensions`, `chunk_size`, `chunk_overlap`, `ai_fallback_policy`, `updated_at`, `updated_by`.
- Alembic migration `v493_tenant_ai_settings`.
- Admin API `api/v1/tenant_ai_settings.py`: GET / PUT. Admin-gated.
- Model allowlist in `core/ai_providers/catalog.py` — tuple of `(provider, model, capability, dimensions_if_embedding)`. Rejects unknown provider/model.
- `core/langgraph/llm_factory.py` + `core/llm/router.py` consult `tenant_ai_settings` before `external_keys`.
- `core/embeddings.py` reads the effective model/dimensions from the tenant's setting, validates against allowlist, and fails closed on mismatch.
- Embedding backfill script `scripts/embedding_rotate.py` — dim-safe rotation: creates shadow `knowledge_documents_v2` with new dims, dual-writes during rollout, atomically swaps, drops old. Gated by an operator flag + sign-off checklist.
- UI page `/dashboard/settings/ai-config` lists current setting + shows allowlist + renders the Rotate-Model wizard with migration status.

### Acceptance criteria
- [ ] `tests/regression/test_s008_tenant_ai_config.py` — ≥ 12 cases covering: admin-only mutation, unknown model rejection, embedding-dim mismatch rejection, BYO token required for paid providers when `ai_fallback_policy=deny`, existing agent runs still resolve prior model metadata until rotation completes, rotation script dry-run idempotent.
- [ ] `scripts/embedding_rotate.py --dry-run` returns an accurate plan.
- [ ] A deliberate rotation attempt with `ai_fallback_policy=deny` and no BYO token returns 409 with a clear message.
- [ ] Admin UI renders state, errors, and the Rotate wizard without localstorage-token dependency.
- [ ] Audit log emits `tenant_ai_setting.updated` with diff.

## PR-3 — Multimodal RAG ingestion (S0-06)

### Deliverables
- New module `core/rag/ingest.py` — single entrypoint `ingest_document(tenant_id, source, stream, mime_type, metadata)` that:
  - Routes to an extractor by MIME type: text / PDF (pdfminer) / Word (mammoth) / Excel (openpyxl with sheet+cell provenance) / image (Tesseract or platform OCR) / audio (Whisper via tenant BYO or platform) / video (ffmpeg → audio + keyframes → OCR).
  - Chunks at sentence/paragraph with `(source_id, chunk_index, start_char, end_char, sheet_name?, page_no?, frame_ts?)` provenance.
  - Embeds via tenant-configured embedding model.
  - Persists to `knowledge_documents` with the new provenance columns + a separate `knowledge_chunk_sources` FK table so a doc can retain multi-page/multi-sheet provenance.
- New columns + FK on `knowledge_documents`: `mime_type`, `extraction_quality`, `embedding_model`, `embedding_dimensions`, `token_count`, `source_object_id`, `source_object_type`.
- New table `knowledge_chunk_sources` (chunk_id → page/sheet/frame provenance).
- Alembic migration `v494_multimodal_rag`.
- Every upload surface calls `ingest_document`:
  - `/api/v1/knowledge/upload` — existing.
  - `/api/v1/sop/upload` + pasted SOP — opt-in via a `index_for_rag: bool` flag on the request body (audit logs the choice).
  - Voice session end hook — opt-in via tenant-setting `rag_index_voice_transcripts: bool`.
- Unsupported MIME types return 415 with a clear error (NOT fake-accept).
- Native pgvector search in `api/v1/knowledge.py` now covers user-uploaded documents (joined on the new columns), not just seeded rows.

### Acceptance criteria
- [ ] `tests/regression/test_s006_multimodal_rag.py` — ≥ 25 cases covering one happy path per modality + provenance round-trip + tenant isolation at document/chunk/embedding level + RAGFlow-down test confirms native pgvector search covers every modality + 415 on unsupported type + replace-file removes stale chunks.
- [ ] Playwright test uploads a small PDF + image + MP3 and asserts the resulting KB search returns the expected chunks with correct provenance.
- [ ] `curl /api/v1/knowledge/health` reports the new modality matrix in its response.

## PR-4 — RAG quality gate (S0-07)

### Deliverables
- New module `core/rag/eval.py` — gold-query corpus (seed file + per-modality queries) + a 5-dimension scoring rubric (semantic match / provenance fidelity / recall / no-hallucination / response length sanity).
- `scripts/rag_eval.py` — run the gold corpus against prod or a staging index; writes `rag_eval_<timestamp>.json`.
- CI wiring: `pytest tests/regression/test_s007_rag_quality_gate.py` runs the eval against a seeded test DB and blocks merge if score < 4.6 or per-modality < 4.6.
- Admin `/api/v1/knowledge/health` surfaces `last_eval_score`, `last_eval_ran_at`, `per_modality_scores`.
- `scripts/embedding_rotate.py` (from PR-2) now requires eval-passed on the new model before swap.

### Acceptance criteria
- [ ] Gold corpus has ≥ 8 queries per critical modality.
- [ ] Synthetic bad-embedding injection fails the gate.
- [ ] Keyword/filename fallback path is prohibited from scoring as high-quality vector retrieval (checker asserts retrieval_mode==pgvector or ragflow).
- [ ] `AGENTICORG_EMBEDDING_MODEL` change at runtime refuses to take effect without a passing eval run.
- [ ] `/knowledge/health` returns the eval metadata.

## Cross-cutting requirements

- **Tenant isolation**: every new table has `tenant_id`, indexed, and every list/get route filters on the caller's tenant.
- **Secret redaction**: no test, log, metric, or API response returns a raw provider token. Masking tests live in PR-1 and are re-run in every subsequent PR's CI.
- **Audit log**: every mutation in PRs 1–4 emits an event with `actor_id`, `tenant_id`, `action`, `target_id`, and a diff (excluding secrets).
- **Feature flag**: PR-3 and PR-4 ship behind `AGENTICORG_RAG_MULTIMODAL_ENABLED` and `AGENTICORG_RAG_QUALITY_GATE_ENABLED` so tenants can soak them before flipping on.

## Release sign-off gate

GA sign-off remains blocked until:
- [ ] All four PRs merged + deployed to production.
- [ ] Gold-corpus eval score in prod is ≥ 4.6/5 overall AND for each critical modality.
- [ ] `/api/v1/knowledge/health` in prod shows `effective_mode = "ragflow"` OR `effective_mode = "pgvector"` with `last_eval_score >= 4.6`.
- [ ] At least one tenant BYO OpenAI key passed `/test` in prod.
- [ ] Ops runbook items §1 (billing) + §2 (RAGFlow) from `docs/enterprise_release_ops_runbook.md` closed.

## Starting now

**PR-1** (BYO AI provider credentials) is the foundational item. Work begins immediately on branch `feat/tenant-ai-credentials`. This plan becomes the merge-gate checklist for each PR.
