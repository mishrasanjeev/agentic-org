# Scenario 5 — Knowledge base semantic search

**Persona:** knowledge owner / KB admin
**Goal:** verify that `/knowledge/search` returns relevant results
against seeded CA/GST compliance content even when RAGFlow isn't
configured — native `pgvector` + BGE embeddings handle the fallback
**Regression:** `tests/regression/test_embeddings.py`

## Steps

1. Stand up the stack locally with seeded KB content: `bash
   scripts/local_e2e.sh` runs the seed script that populates
   `knowledge_documents` with 6 curated CA/GST documents and writes
   a 384-dim BGE embedding for each.
2. Leave `RAGFLOW_API_URL` unset — native path is the one under test.
3. `POST /api/v1/knowledge/search` with body `{"query": "GSTR-3B due
   date", "top_k": 3}`. Expect a non-empty `results` array; the top
   hit's `document_name` references GST filing.
4. Compare scores: `{"query": "TDS quarterly Form 24Q"}` should
   surface the TDS compliance document above the MCA ROC document.
5. If BGE weights haven't been cached yet, the first request warms
   them (~10 s on a cold box). Subsequent requests return in <100 ms.
6. For a query with no semantic match (e.g. "cookie recipe"), the
   endpoint still returns the `top_k` closest docs with low similarity
   scores — the caller decides the threshold. It never returns `[]`
   against seeded content.

## Verification commands

```bash
# 384-dim shape + semantic ordering sanity check (no DB required)
pytest tests/regression/test_embeddings.py -v --no-cov

# End-to-end: seed + search against local postgres
bash scripts/local_e2e.sh ui/e2e/knowledge-search.spec.ts
```

## Expected outcome

- Semantic retrieval works without a paid embedding API.
- Fallback is real (pgvector cosine ANN), not a keyword-match
  approximation.
- Model: `BAAI/bge-small-en-v1.5`, MIT-licensed, ~66 MB quantized ONNX
  weights. bge-m3 multilingual upgrade tracked as a follow-up.

## Drift guards

- `tests/regression/test_embeddings.py` — asserts 384-dim output +
  semantic-ordering property (related > unrelated cosine similarity).
- `migrations/versions/v4_8_6_knowledge_embedding.py` — idempotent
  `ADD COLUMN IF NOT EXISTS embedding vector(384)` + ivfflat index.
