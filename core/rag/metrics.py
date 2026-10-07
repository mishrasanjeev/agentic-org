# SPDX-License-Identifier: Apache-2.0
"""Retrieval quality metrics and a grounding indicator.

While ``AGENTICORG_KNOWLEDGE_METRICS_ENABLED`` is on, every knowledge search
leaves one sample (``sample``, ``record``): the retrieval path, how many
chunks came back and were withheld, the best score, the **context
relevance** (the mean share of the query's terms each returned chunk
carries, and the share of chunks carrying at least one), the latency, and
whether the search expanded its query or consulted the graph. The sample
holds counts and scores only, never the query or a chunk. The same figures
feed the Prometheus series (``observe``) by path, and ``summary`` folds a
tenant's samples over a window for ``GET /knowledge/metrics``.

``grounding`` is the hallucination indicator: the share of an answer's
sentences that at least one retrieved chunk supports (half of the sentence's
terms in one chunk), with the unsupported sentences listed, so a caller that
turned chunks into an answer can say how much of it stands on the knowledge
base. It is deterministic and calls no model.

Off, nothing is recorded and a search is exactly what it was.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import structlog

from core.config import settings
from core.rag.rerank import terms

logger = structlog.get_logger()

PATHS: tuple[str, ...] = ("ragflow", "hybrid", "vector_keyword")
MAX_UNSUPPORTED = 5
MIN_SENTENCE_TERMS = 3
SUPPORT_SHARE = 0.5
MAX_WINDOW_HOURS = 24 * 30
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def enabled() -> bool:
    return bool(settings.knowledge_metrics_enabled)


def coverage(query_terms: list[str], text: str) -> float:
    """The share of the query's terms the text carries."""
    if not query_terms:
        return 0.0
    present = set(terms(text))
    return sum(1 for term in query_terms if term in present) / len(query_terms)


def relevance(query: str, texts: list[str]) -> tuple[float, float]:
    """Mean query-term coverage of the chunks, and the share of chunks carrying at least one query term."""
    query_terms = list(dict.fromkeys(terms(query)))
    if not query_terms or not texts:
        return 0.0, 0.0
    covers = [coverage(query_terms, text) for text in texts]
    return round(sum(covers) / len(covers), 4), round(sum(1 for c in covers if c > 0) / len(covers), 4)


def _risk(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.8:
        return "low"
    if score >= 0.5:
        return "medium"
    return "high"


def grounding(answer: str, chunks: list[str]) -> dict[str, Any]:
    """The share of the answer's sentences a chunk supports, the sentences none does, and the risk that follows."""
    sentences = [s.strip() for s in _SENTENCE.split(str(answer or "")) if s.strip()]
    chunk_terms = [set(terms(chunk)) for chunk in chunks]
    checked = supported = 0
    unsupported: list[str] = []
    for sentence in sentences:
        sentence_terms = list(dict.fromkeys(terms(sentence)))
        if len(sentence_terms) < MIN_SENTENCE_TERMS:
            continue
        checked += 1
        best = max((sum(1 for t in sentence_terms if t in ct) / len(sentence_terms) for ct in chunk_terms), default=0.0)
        if best >= SUPPORT_SHARE:
            supported += 1
        elif len(unsupported) < MAX_UNSUPPORTED:
            unsupported.append(sentence[:200])
    score = round(supported / checked, 4) if checked else None
    return {
        "sentences": checked,
        "supported": supported,
        "score": score,
        "unsupported": unsupported,
        "hallucination_risk": _risk(score),
    }


@dataclass(frozen=True)
class Sample:
    """One search, as figures: nothing here identifies the query, a chunk, a tenant or a user."""

    path: str
    results: int
    withheld: int
    top_score: float
    relevance: float
    covered_share: float
    latency_ms: int
    expanded: bool
    graph: bool
    query_terms: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sample(
    query: str,
    texts: list[str],
    scores: list[float],
    *,
    path: str,
    latency_ms: int,
    expanded: bool = False,
    graph: bool = False,
    withheld: int = 0,
) -> Sample:
    mean_relevance, covered = relevance(query, texts)
    return Sample(
        path=path if path in PATHS else "vector_keyword",
        results=len(texts),
        withheld=max(0, int(withheld)),
        top_score=round(max((float(s or 0.0) for s in scores), default=0.0), 4),
        relevance=mean_relevance,
        covered_share=covered,
        latency_ms=max(0, int(latency_ms)),
        expanded=bool(expanded),
        graph=bool(graph),
        query_terms=len(set(terms(query))),
    )


def observe(s: Sample) -> None:
    """The Prometheus series: searches by path and outcome, relevance and latency by path."""
    from observability import metrics as prom

    prom.knowledge_searches_total.labels(path=s.path, outcome="hits" if s.results else "empty").inc()
    prom.knowledge_search_relevance.labels(path=s.path).observe(s.relevance)
    prom.knowledge_search_latency_seconds.labels(path=s.path).observe(s.latency_ms / 1000.0)


async def record(session: Any, tenant_id: uuid.UUID, s: Sample) -> None:
    """One row per search in ``knowledge_retrieval_metrics``."""
    from sqlalchemy import text as sqltext

    await session.execute(
        sqltext(
            "INSERT INTO knowledge_retrieval_metrics "
            "(id, tenant_id, path, results, withheld, top_score, relevance, covered_share, latency_ms, "
            " expanded, graph, query_terms, created_at) "
            "VALUES (gen_random_uuid(), :tid, :path, :results, :withheld, :top_score, :relevance, "
            " :covered_share, :latency_ms, :expanded, :graph, :query_terms, now())"
        ),
        {"tid": str(tenant_id), **s.as_dict()},
    )


async def summary(session: Any, tenant_id: uuid.UUID, hours: int) -> dict[str, Any]:
    """A tenant's searches over the window: counts, relevance, latency percentiles and the path mix."""
    from sqlalchemy import text as sqltext

    window = max(1, min(int(hours), MAX_WINDOW_HOURS))
    params = {"tid": str(tenant_id), "hours": window}
    row = (
        await session.execute(
            sqltext(
                "SELECT COUNT(*), AVG(relevance), AVG(covered_share), "
                "SUM(CASE WHEN results = 0 THEN 1 ELSE 0 END), "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), "
                "SUM(CASE WHEN expanded THEN 1 ELSE 0 END), SUM(CASE WHEN graph THEN 1 ELSE 0 END), "
                "SUM(withheld) "
                "FROM knowledge_retrieval_metrics "
                "WHERE tenant_id = :tid AND created_at >= now() - make_interval(hours => :hours)"
            ),
            params,
        )
    ).fetchone()
    mix = (
        await session.execute(
            sqltext(
                "SELECT path, COUNT(*) FROM knowledge_retrieval_metrics "
                "WHERE tenant_id = :tid AND created_at >= now() - make_interval(hours => :hours) "
                "GROUP BY path ORDER BY path"
            ),
            params,
        )
    ).fetchall()
    searches = int((row[0] if row else 0) or 0)

    def share(value: Any) -> float | None:
        return round(float(value or 0) / searches, 4) if searches else None

    def number(value: Any) -> float | None:
        return round(float(value), 4) if value is not None else None

    return {
        "window_hours": window,
        "searches": searches,
        "empty_share": share(row[3] if row else 0),
        "mean_relevance": number(row[1] if row else None),
        "mean_covered_share": number(row[2] if row else None),
        "p50_latency_ms": number(row[4] if row else None),
        "p95_latency_ms": number(row[5] if row else None),
        "expanded_share": share(row[6] if row else 0),
        "graph_share": share(row[7] if row else 0),
        "withheld": int((row[8] if row else 0) or 0),
        "paths": {str(path): int(count or 0) for path, count in mix},
    }
