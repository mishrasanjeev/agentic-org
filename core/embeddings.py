"""Self-hosted embedding service backed by fastembed (ONNX).

Default model: `BAAI/bge-small-en-v1.5` (384 dim, MIT, ~66 MB ONNX).

Operators can flip to a different model via `AGENTICORG_EMBEDDING_MODEL`.
Known supported choices:

    BAAI/bge-small-en-v1.5    384-dim, English+ (default, smallest)
    BAAI/bge-base-en-v1.5     768-dim, English+
    BAAI/bge-m3               1024-dim, multilingual (100+ languages, 2.3 GB)

Flipping the model means the pgvector column dimensionality and the
IVFFlat index must match. Rotating is a three-step operation:

    1. `ALTER TABLE knowledge_documents ADD COLUMN embedding_new vector(N)`
       where N = dim of new model.
    2. Re-run seed / re-embed every document, writing `embedding_new`.
    3. `DROP COLUMN embedding; ALTER COLUMN embedding_new RENAME TO embedding`
       and recreate the IVFFlat index.

Until that's wired the default bge-small is the only model that's
guaranteed to round-trip against the current `vector(384)` column.

The model is loaded lazily on first call and cached for the process
lifetime. Set `FASTEMBED_CACHE_DIR` to pin weights to a known directory
(useful in CI to skip the download on subsequent runs).
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = structlog.get_logger()

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Dimensionality per known model. Add new entries here when validating
# a new fastembed-supported model.
_MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
}


def _configured_model_name() -> str:
    return os.getenv("AGENTICORG_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL


EMBEDDING_MODEL_NAME = _configured_model_name()
EMBEDDING_DIM = _MODEL_DIMS.get(EMBEDDING_MODEL_NAME, 384)

_model_lock = threading.Lock()
_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    """Return a singleton TextEmbedding, loading on first call."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from fastembed import TextEmbedding

        cache_dir = os.getenv("FASTEMBED_CACHE_DIR") or None
        logger.info(
            "embedding_model_loading",
            model=EMBEDDING_MODEL_NAME,
            dim=EMBEDDING_DIM,
            cache_dir=cache_dir,
        )
        _model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME, cache_dir=cache_dir)
        return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings, returning one vector per input."""
    if not texts:
        return []
    model = _get_model()
    vectors = [v.tolist() for v in model.embed(texts)]
    return vectors


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for single-string embedding."""
    return embed([text])[0]
