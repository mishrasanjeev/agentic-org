"""Self-hosted embedding service backed by fastembed (ONNX) + BGE small.

Model: `BAAI/bge-small-en-v1.5`
- Dimensionality: 384
- License: MIT
- Quantized ONNX weights (~66 MB) pulled on first use via fastembed.

The model is loaded lazily on first call and cached for the process lifetime.
Call `embed(["text"])` to get a `list[list[float]]` (one 384-dim vector per
input string). Inputs are always processed in batches.

No external API calls, no token costs. Safe to run in CI and offline envs
once the weights are cached. To keep the CI image small the fastembed cache
can be pinned to a local path via `FASTEMBED_CACHE_DIR`.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = structlog.get_logger()

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

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
    """Embed a batch of strings, returning one 384-dim vector per input."""
    if not texts:
        return []
    model = _get_model()
    vectors = [v.tolist() for v in model.embed(texts)]
    return vectors


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for single-string embedding."""
    return embed([text])[0]
