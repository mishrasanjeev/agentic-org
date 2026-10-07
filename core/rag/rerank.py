# SPDX-License-Identifier: Apache-2.0
"""A re-ranking stage over fused candidates: the query's own words decide the final order.

Hybrid retrieval fuses a dense and a sparse ranking by reciprocal rank, which
rewards a document for appearing in both lists but says little about how
well it answers the query. This stage re-scores the fused candidates with
what can be read from the texts alone, with no model call:

* **coverage**: the share of the query's terms the chunk contains;
* **phrase**: whether the query appears as a phrase;
* **proximity**: how close together the query's terms sit;
* **title**: whether the document title carries a query term;
* **fusion**: the fused score, kept so ties fall back to it.

The final score is a weighted sum in 0 to 1. Chunks that share no term with
the query keep their fused order below those that do. Behind
``AGENTICORG_KNOWLEDGE_RERANK_ENABLED`` (off by default); off, the fused
order is returned as it was. A model-graded re-ranker is not part of this
stage: the scores are deterministic and explainable, and the limits (no
synonyms, no meaning) are the limits of term matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.config import settings

WEIGHTS = {"coverage": 0.45, "phrase": 0.2, "proximity": 0.15, "title": 0.1, "fusion": 0.1}
MAX_CANDIDATES = 200
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "what", "how", "does", "do"}
)


def enabled() -> bool:
    return bool(settings.knowledge_rerank_enabled)


def terms(text: str) -> list[str]:
    found = [token for token in _TOKEN.findall((text or "").lower()) if token not in _STOP]
    return found or _TOKEN.findall((text or "").lower())


@dataclass(frozen=True)
class Candidate:
    key: str
    title: str
    text: str
    fused: float


def _proximity(query_terms: list[str], doc_tokens: list[str]) -> float:
    """1 when every query term sits within a short window, falling towards 0 as they spread."""
    positions = {term: [i for i, token in enumerate(doc_tokens) if token == term] for term in query_terms}
    present = [p for p in positions.values() if p]
    if len(present) < 2:
        return 1.0 if present else 0.0
    best = None
    for start in present[0]:
        span = 0
        for other in present[1:]:
            nearest = min(abs(p - start) for p in other)
            span = max(span, nearest)
        best = span if best is None else min(best, span)
    window = max(len(query_terms) * 4, 8)
    # Adjacent terms span len - 1 tokens: that is full proximity; the slack beyond it lowers the score.
    slack = max(0, (best or 0) - (len(query_terms) - 1))
    return max(0.0, 1.0 - slack / window)


def score(query: str, candidate: Candidate, *, max_fused: float) -> dict[str, float]:
    query_terms = list(dict.fromkeys(terms(query)))
    doc_tokens = _TOKEN.findall(candidate.text.lower())
    title_tokens = set(_TOKEN.findall(candidate.title.lower()))
    present = [term for term in query_terms if term in set(doc_tokens)]
    coverage = len(present) / len(query_terms) if query_terms else 0.0
    phrase = 1.0 if query_terms and " ".join(query_terms) in " ".join(doc_tokens) else 0.0
    proximity = _proximity(present, doc_tokens) if present else 0.0
    title = 1.0 if any(term in title_tokens for term in query_terms) else 0.0
    fusion = candidate.fused / max_fused if max_fused > 0 else 0.0
    parts = {"coverage": coverage, "phrase": phrase, "proximity": proximity, "title": title, "fusion": fusion}
    total = sum(WEIGHTS[name] * value for name, value in parts.items())
    return {**parts, "score": round(min(1.0, total), 4)}


def rerank(query: str, candidates: list[Candidate], top_k: int) -> list[tuple[Candidate, float]]:
    """The candidates in their re-ranked order with the new score; the fused order breaks ties."""
    if not candidates:
        return []
    pool = candidates[:MAX_CANDIDATES]
    max_fused = max(c.fused for c in pool)
    scored = [(c, score(query, c, max_fused=max_fused)["score"], index) for index, c in enumerate(pool)]
    scored.sort(key=lambda item: (-item[1], item[2]))
    return [(c, s) for c, s, _index in scored[:top_k]]


def explain(query: str, candidate: Candidate, *, max_fused: float) -> dict[str, Any]:
    """The parts of a candidate's score, for a debugging view; no content beyond the numbers."""
    return score(query, candidate, max_fused=max_fused)
