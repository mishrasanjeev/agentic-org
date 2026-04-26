"""Self-hosted embedding service.

Default request-path model (PR-A): ``BAAI/bge-small-en-v1.5`` (384 dim,
MIT, ~66 MB ONNX), loaded via fastembed. PR-B will flip the request
path to ``BAAI/bge-m3`` once the backfill verifies no orphan rows.

A second loader path —
:func:`embed_bge_m3` — uses BAAI's official **FlagEmbedding** package
because fastembed does not ship bge-m3 in any released version.
FlagEmbedding loads the full PyTorch weights (~2.3 GB) lazily on
first call. Only the backfill job (PR-A) and PR-B's flipped request
path import it; request-path callers in PR-A still get the small
ONNX model.

Operators can flip the platform default via
``AGENTICORG_EMBEDDING_MODEL``. Known supported choices:

    BAAI/bge-small-en-v1.5    384-dim, English+ (default, smallest, fastembed)
    BAAI/bge-base-en-v1.5     768-dim, English+ (fastembed)
    BAAI/bge-large-en-v1.5    1024-dim, English+ (fastembed)
    BAAI/bge-m3               1024-dim, multilingual (~100), 8192 ctx
                              (FlagEmbedding only — see embed_bge_m3)

Flipping the default is **not safe** while existing rows are stored in
a column whose dimension does not match the new model. The cutover
sequence is:

    1. Migration adds ``embedding_bge_m3 vector(1024)`` (PR-A,
       v495_bge_m3_column).
    2. Backfill job ``python -m core.embeddings_backfill`` re-embeds
       every row with bge-m3 into ``embedding_bge_m3``. Idempotent —
       only writes where the target is NULL.
    3. PR-B drops ``embedding``, renames ``embedding_bge_m3`` to
       ``embedding``, recreates the IVFFlat index, and bumps
       ``DEFAULT_EMBEDDING_MODEL`` to ``BAAI/bge-m3``.

Until PR-B lands the default stays bge-small to guarantee round-trip
against ``vector(384)``.

Both loaders are cached for the process lifetime. Set
``FASTEMBED_CACHE_DIR`` (fastembed) or ``HF_HOME``
(FlagEmbedding/Hugging Face) to pin weights to a known directory —
useful in CI and on Cloud Run revisions to skip the download on
subsequent boots.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = structlog.get_logger()

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
BGE_M3_MODEL = "BAAI/bge-m3"


def rag_use_bge_m3() -> bool:
    """Return True when the request path should embed + query with bge-m3.

    Read at every call (not module-load) so operators can flip the
    flag in Cloud Run without a redeploy. Treated as a feature flag,
    not a setting — the value MUST stay False until the BGE-M3
    backfill (``python -m core.embeddings_backfill --verify``) reports
    zero orphan rows. Otherwise queries embed with bge-m3 (1024 dim)
    but read the legacy ``embedding`` column (384 dim) and every
    /knowledge/search call 500s with a vector dimension mismatch.

    Truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    """
    raw = (os.getenv("AGENTICORG_RAG_USE_BGE_M3") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Column names used by RAG ingest + search. Centralised here so the
# flag-flip rolls both paths atomically.
RAG_EMBEDDING_COLUMN_LEGACY = "embedding"
RAG_EMBEDDING_COLUMN_BGE_M3 = "embedding_bge_m3"


def rag_embedding_column() -> str:
    """Return the pgvector column name the request path should read/write."""
    return RAG_EMBEDDING_COLUMN_BGE_M3 if rag_use_bge_m3() else RAG_EMBEDDING_COLUMN_LEGACY

# Dimensionality per known model. Add new entries here when validating
# a new model against pgvector.
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


def model_dim(model_name: str) -> int:
    """Return the embedding dimension for ``model_name``.

    Unknown models default to 384 — the same fallback ``EMBEDDING_DIM``
    uses — but the caller almost always wants to fail loudly when the
    backfill points at an unrecognised model. Callers that need that
    contract should look up ``_MODEL_DIMS`` directly.
    """
    return _MODEL_DIMS.get(model_name, 384)


# ─── fastembed (default request-path) ───────────────────────────────

_model_lock = threading.Lock()
_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    """Return a singleton TextEmbedding for the configured default."""
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
            loader="fastembed",
        )
        _model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME, cache_dir=cache_dir)
        return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings, returning one vector per input.

    Routes to bge-m3 when ``AGENTICORG_RAG_USE_BGE_M3`` is set,
    otherwise to the configured fastembed default. Both paths are
    cached for the process lifetime by their respective loaders.
    """
    if not texts:
        return []
    if rag_use_bge_m3():
        return embed_bge_m3(texts)
    model = _get_model()
    vectors = [v.tolist() for v in model.embed(texts)]
    return vectors


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for single-string embedding."""
    return embed([text])[0]


# ─── FlagEmbedding (bge-m3) ─────────────────────────────────────────

_bge_m3_lock = threading.Lock()
_bge_m3_model: Any | None = None


def _get_bge_m3_model() -> Any:
    """Return the singleton BGEM3FlagModel.

    First call downloads ~2.3 GB of weights from Hugging Face and
    loads them into memory (fp16 ≈ 1.2 GB resident). Pin
    ``HF_HOME`` so subsequent process boots reuse the cached weights
    instead of re-downloading on every Cloud Run cold start.
    """
    global _bge_m3_model
    if _bge_m3_model is not None:
        return _bge_m3_model
    with _bge_m3_lock:
        if _bge_m3_model is not None:
            return _bge_m3_model
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:
            raise RuntimeError(
                "FlagEmbedding is required to load BAAI/bge-m3. "
                "Install with `pip install FlagEmbedding>=1.2.0`."
            ) from exc

        cache_dir = os.getenv("HF_HOME") or None
        logger.info(
            "embedding_model_loading",
            model=BGE_M3_MODEL,
            dim=_MODEL_DIMS[BGE_M3_MODEL],
            cache_dir=cache_dir,
            loader="FlagEmbedding",
        )
        _bge_m3_model = BGEM3FlagModel(BGE_M3_MODEL, use_fp16=True, cache_dir=cache_dir)
        return _bge_m3_model


def embed_bge_m3(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed ``texts`` with bge-m3 (1024-dim, multilingual, 8192 ctx).

    Routing:
      - When ``AGENTICORG_TEI_URL`` is set, POST to that TEI service
        (HuggingFace text-embeddings-inference) — recommended for the
        request path so the api container does NOT need to hold the
        2.3 GB weights in memory.
      - Otherwise load FlagEmbedding in-process — used by the backfill
        Cloud Run job (which can afford the 16 GiB peak memory) and
        by local development.

    Returns dense vectors only — bge-m3 also produces sparse and
    multi-vector outputs but the request path uses pgvector dense
    cosine search, so the other modes are not exposed here.
    """
    if not texts:
        return []
    if os.getenv("AGENTICORG_TEI_URL"):
        return _embed_via_tei(texts)
    model = _get_bge_m3_model()
    out = model.encode(
        texts,
        batch_size=batch_size,
        max_length=8192,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = out["dense_vecs"]
    return [list(map(float, v)) for v in dense]


def _embed_via_tei(texts: list[str]) -> list[list[float]]:
    """POST ``texts`` to the configured TEI service.

    The TEI service runs as a separate Cloud Run service
    (``agenticorg-embeddings``) at ``min-instances=1`` so the bge-m3
    weights live there permanently and the api container stays at
    2 GiB. Cloud Run's invoker IAM is enforced via an ID token signed
    by the api's service account audience-bound to the TEI service URL.

    The TEI ``/embed`` endpoint shape:
        request:  {"inputs": ["t1", "t2"], "normalize": true}
        response: [[v1...], [v2...]]
    """
    import google.auth
    import google.auth.transport.requests
    import httpx
    from google.oauth2 import id_token

    base = os.environ["AGENTICORG_TEI_URL"].rstrip("/")
    # Mint an audience-bound ID token. Falls back to anonymous if no
    # GCP creds are available (dev / test environments where TEI is
    # exposed via --allow-unauthenticated).
    headers = {"Content-Type": "application/json"}
    try:
        auth_req = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_req, base)
        headers["Authorization"] = f"Bearer {token}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("tei_id_token_fetch_skipped", error=str(exc))

    payload = {"inputs": texts, "normalize": True, "truncate": True}
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = client.post(f"{base}/embed", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    return [list(map(float, v)) for v in data]


# Compatibility shim for the backfill — keeps the call site model-name
# agnostic so a future swap (e.g. to e5-large via fastembed) doesn't
# require re-routing through a different function name.
def embed_with(model_name: str, texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` with the model identified by ``model_name``.

    Routes to ``embed_bge_m3`` for bge-m3 (FlagEmbedding) and to the
    fastembed default loader for everything else.
    """
    if model_name == BGE_M3_MODEL:
        return embed_bge_m3(texts)
    if model_name != EMBEDDING_MODEL_NAME:
        raise NotImplementedError(
            f"embed_with: model {model_name!r} is not loadable from this "
            f"process. Default request-path model is {EMBEDDING_MODEL_NAME!r}; "
            f"only {BGE_M3_MODEL!r} has a second loader. Add a loader path "
            "before calling embed_with with another model name."
        )
    return embed(texts)
