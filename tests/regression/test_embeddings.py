"""PR-B4 regression - native embeddings.

Asserts that:
  1. core.embeddings.embed returns 384-dim vectors for arbitrary text.
  2. Semantically similar queries score higher than unrelated ones.

No DB / RAGFlow required. The tests intentionally stub the model loader
so unit CI does not depend on live Hugging Face/fastembed downloads or
runner-local model caches. Separate smoke/backfill checks cover real
model availability in environments that pre-provision the weights.
"""

from __future__ import annotations

import math

import pytest


class _Vector(list[float]):
    def tolist(self) -> list[float]:
        return list(self)


class _FakeEmbeddingModel:
    """Tiny deterministic stand-in for fastembed.TextEmbedding."""

    def embed(self, texts: list[str]) -> list[_Vector]:
        return [_Vector(_fake_embedding(text)) for text in texts]


def _fake_embedding(text: str) -> list[float]:
    lowered = text.lower()
    vector = [0.0] * 384
    if any(token in lowered for token in ("gst", "gstr", "itc", "return")):
        vector[0] = 1.0
    if any(token in lowered for token in ("tds", "compliance", "filing")):
        vector[1] = 1.0
    if any(token in lowered for token in ("roc", "mgt")):
        vector[2] = 1.0
    if any(token in lowered for token in ("cookie", "recipe", "sugar")):
        vector[3] = 1.0
    if not any(vector):
        vector[4] = 1.0
    return vector


@pytest.fixture(autouse=True)
def _use_fake_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import embeddings

    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setattr(embeddings, "_get_model", lambda: _FakeEmbeddingModel())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def test_embed_returns_384_dim_vectors() -> None:
    from core.embeddings import EMBEDDING_DIM, embed

    vectors = embed([
        "GST return filing due date",
        "TDS compliance quarterly filing",
        "Annual ROC filing MGT-7",
    ])
    assert len(vectors) == 3
    for v in vectors:
        assert len(v) == EMBEDDING_DIM, (
            f"expected {EMBEDDING_DIM} dims, got {len(v)}"
        )
        assert all(isinstance(x, float) for x in v)


def test_embed_semantic_similarity_ordering() -> None:
    from core.embeddings import embed

    # Related pair (both GST-specific) should score higher than
    # cross-domain control (a food recipe).
    anchor, related, unrelated = embed([
        "GST return filing and ITC reconciliation",
        "GSTR-3B summary due on 20th of following month",
        "Chocolate chip cookie recipe with brown sugar",
    ])
    sim_related = _cosine(anchor, related)
    sim_unrelated = _cosine(anchor, unrelated)
    assert sim_related > sim_unrelated, (
        f"expected related > unrelated, got "
        f"related={sim_related:.4f} unrelated={sim_unrelated:.4f}"
    )
