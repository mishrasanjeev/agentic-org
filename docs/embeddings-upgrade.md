# Upgrading the embedding model

The knowledge-base native embedding path ships with
`BAAI/bge-small-en-v1.5` (384-dim, English+). Operators can flip to a
different fastembed-supported model via the `AGENTICORG_EMBEDDING_MODEL`
environment variable.

| Model                         | Dim  | Languages        | Weights  | Notes                  |
|-------------------------------|------|------------------|----------|------------------------|
| `BAAI/bge-small-en-v1.5`      | 384  | English+         | ~66 MB   | Default, CI-friendly   |
| `BAAI/bge-base-en-v1.5`       | 768  | English+         | ~130 MB  | Better accuracy, 2x    |
| `BAAI/bge-m3`                 | 1024 | 100+ multilingual| ~2.3 GB  | Hindi, Tamil, etc.     |
| `BAAI/bge-large-en-v1.5`      | 1024 | English+         | ~1.3 GB  | Best English quality   |

`core/embeddings.py::_MODEL_DIMS` is the source of truth; add new rows
there when validating a fastembed-supported model.

## Rotation procedure

Changing the model means the pgvector column dimensionality and the
IVFFlat index have to match. This is a schema migration, not just a
config flip.

1. **Stage the new column.** Add an Alembic migration:

   ```sql
   ALTER TABLE knowledge_documents
       ADD COLUMN IF NOT EXISTS embedding_new vector(<N>);
   CREATE INDEX IF NOT EXISTS ix_kd_embedding_new
       ON knowledge_documents
       USING ivfflat (embedding_new vector_cosine_ops)
       WITH (lists = 100);
   ```

   where `<N>` is the new model's dimensionality
   (see `_MODEL_DIMS`).

2. **Re-embed every document.** Set the env var and run the seed /
   backfill script so `embedding_new` is populated:

   ```bash
   export AGENTICORG_EMBEDDING_MODEL=BAAI/bge-m3
   python -m scripts.backfill_embeddings
   ```

   `scripts/backfill_embeddings.py` (not yet implemented — file a
   follow-up) should read every `knowledge_documents` row, call
   `core.embeddings.embed()`, and `UPDATE … SET embedding_new = …`.

3. **Swap.** Second Alembic migration:

   ```sql
   DROP INDEX IF EXISTS ix_knowledge_documents_embedding;
   ALTER TABLE knowledge_documents DROP COLUMN embedding;
   ALTER TABLE knowledge_documents
       RENAME COLUMN embedding_new TO embedding;
   ALTER INDEX ix_kd_embedding_new
       RENAME TO ix_knowledge_documents_embedding;
   ```

4. **Ship.** Deploy the new `AGENTICORG_EMBEDDING_MODEL` env var +
   the migrations together so the schema and the serving code agree.

## Why not flip the default?

- CI image size: bge-m3 is 2.3 GB. Pulling that on every test run
  would slow the matrix meaningfully.
- Memory: bge-m3 uses ~4 GB RAM per process. Most small-tier tenants
  don't need 100-language retrieval.
- Rollback: the column dimensionality is a one-way door without the
  above rotation. A default that forces a mandatory backfill is a
  breaking change.

Keep bge-small as the default; opt in to bge-m3 per deployment when
the customer actually serves multilingual KB content.
