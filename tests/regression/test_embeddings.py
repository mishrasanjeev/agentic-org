"""PR-B4 regression — native embeddings.

Asserts that:
  1. core.embeddings.embed returns 384-dim vectors for arbitrary text.
  2. Semantically similar queries score higher than unrelated ones
     (BGE clusters "GST filing" near "tax return" etc.).

No DB / RAGFlow required — this is a model-layer contract test that
protects the embedding dimensionality and rough semantic behavior.

The zero-skip rule applies: this test asserts hard. If fastembed is
missing or its weights cannot be fetched, the suite fails so the
environment gets fixed rather than silently green.
"""

from __future__ import annotations

import math


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
