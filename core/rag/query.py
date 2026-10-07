# SPDX-License-Identifier: Apache-2.0
"""Query transformation and agentic retrieval with a visible trace.

A knowledge search starts from the words a person typed. While
``AGENTICORG_KNOWLEDGE_QUERY_TRANSFORM_ENABLED`` is on, the query is planned
before it is run (``plan``):

* **normalised**: whitespace collapsed, wrapping quotes removed, bounded;
* **decomposed**: a compound question becomes its parts (two questions,
  ``difference between A and B``, ``A versus B``, ``A and B`` under one
  question word);
* **rewritten**: a keyword form with the lead-in (``can you tell me``,
  ``what is``) and the stop words removed, for the sparse ranking.

Every rule that fired is named in the plan. A model may add variants
(``model_variants``) when ``AGENTICORG_KNOWLEDGE_QUERY_REWRITE_MODEL`` names
one; a model that fails or answers badly adds nothing, and the trace says so.

Retrieval is then **agentic** (``retrieve``): the normalised query is
searched first; when the first pass is weak (fewer hits than asked for, or
the best score below ``WEAK_SCORE``) and the plan has variants, each variant
is searched and the lists are fused by reciprocal rank; a strong first pass
is returned as it is. Each step (plan, search, decision, fuse) is recorded
with its counts and elapsed time in a trace the response can carry, so a
reviewer sees why a chunk was retrieved. The trace holds queries and counts,
never tenant or user identifiers.

Off, nothing here runs and a search is exactly what it was.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings
from core.rag.rerank import terms

logger = structlog.get_logger()

MAX_QUERY_CHARS = 1000
MAX_VARIANTS = 4
MIN_VARIANT_TERMS = 2
WEAK_SCORE = 0.35
RRF_K = 60
MODEL_MAX_TOKENS = 200

_QUESTION_WORDS = ("what", "which", "who", "when", "where", "why", "how", "is", "are", "do", "does", "can", "should")
_LEAD_INS = (
    "can you tell me",
    "could you tell me",
    "please tell me",
    "i want to know",
    "i need to know",
    "i would like to know",
    "tell me",
    "show me",
    "give me",
    "explain",
    "describe",
    "what is the",
    "what are the",
    "what is",
    "what are",
    "whats",
    "how do i",
    "how does",
    "how to",
    "is there",
    "do we have",
    "where is",
    "where are",
    "who is",
    "when does",
    "please",
)
_DIFFERENCE = re.compile(r"\bdifference\s+between\s+(.+?)\s+and\s+(.+?)\??$", re.I)
_COMPARE = re.compile(r"\b(?:compare|contrast)\s+(.+?)\s+(?:and|with|to|against)\s+(.+?)\??$", re.I)
_VERSUS_TOKENS = frozenset({"vs", "vs.", "versus"})
_VERSUS = re.compile(r"^(.+?)\s+(?:vs\.?|versus)\s+(.+?)\??$", re.I)


def enabled() -> bool:
    return bool(settings.knowledge_query_transform_enabled)


@dataclass
class Plan:
    """What will be searched for a query, and the rules that decided it."""

    original: str
    normalised: str
    variants: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def add_variant(self, text: str, rule: str) -> bool:
        candidate = normalise(text)
        if not candidate or len(terms(candidate)) < MIN_VARIANT_TERMS:
            return False
        known = {self.normalised.lower(), *(v.lower() for v in self.variants)}
        if candidate.lower() in known or len(self.variants) >= MAX_VARIANTS:
            return False
        self.variants.append(candidate)
        self.rules.append(rule)
        return True

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.normalised, "variants": list(self.variants), "rules": list(self.rules)}


def normalise(text: Any) -> str:
    """Whitespace collapsed, wrapping quotes removed, bounded to ``MAX_QUERY_CHARS``."""
    value = " ".join(str(text or "").split())[:MAX_QUERY_CHARS]
    while len(value) > 1 and value[0] == value[-1] and value[0] in "\"'“”‘’":
        value = value[1:-1].strip()
    return value.strip()


def keyword_form(text: str) -> str:
    """The query without its lead-in and stop words: the form the sparse ranking matches best."""
    lowered = normalise(text).lower().rstrip("?.! ")
    lowered = lowered.replace("'", "")
    for lead in _LEAD_INS:
        if lowered.startswith(lead + " "):
            lowered = lowered[len(lead) + 1 :]
            break
    return " ".join(t for t in terms(lowered) if t not in _VERSUS_TOKENS)


def decompose(text: str) -> list[tuple[str, str]]:
    """The parts of a compound query, each with the rule that split it; empty when the query is one question."""
    value = normalise(text)
    sentences = [s.strip() for s in re.split(r"[?;]\s*", value) if s.strip()]
    if len(sentences) > 1:
        return [(s, "sentence") for s in sentences]
    for pattern, rule in ((_DIFFERENCE, "difference"), (_COMPARE, "compare"), (_VERSUS, "versus")):
        match = pattern.search(value)
        if match:
            return [(match.group(1), rule), (match.group(2), rule)]
    lowered = value.lower()
    if lowered.split(" ", 1)[0] in _QUESTION_WORDS and " and " in lowered:
        left, right = value.split(" and ", 1) if " and " in value else value.lower().split(" and ", 1)
        if len(terms(left)) >= MIN_VARIANT_TERMS and len(terms(right)) >= MIN_VARIANT_TERMS:
            return [(left, "conjunction"), (right, "conjunction")]
    return []


def plan(text: str) -> Plan:
    """The query as it will be searched: normalised, with its decomposed parts and keyword form as variants."""
    normalised = normalise(text)
    planned = Plan(original=str(text or "")[:MAX_QUERY_CHARS], normalised=normalised)
    for part, rule in decompose(normalised):
        planned.add_variant(part, rule)
    keywords = keyword_form(normalised)
    if keywords and keywords != normalised.lower():
        planned.add_variant(keywords, "keywords")
    return planned


async def model_variants(tenant_id: uuid.UUID, query: str, *, complete: Any = None) -> tuple[list[str], str | None]:
    """Up to three search queries a model proposes for the query, or none with the reason it gave nothing."""
    model = str(settings.knowledge_query_rewrite_model or "").strip()
    if not model:
        return [], "no_model"
    messages = [
        {
            "role": "system",
            "content": (
                "You rewrite a search query for a document search engine. Answer with a JSON array of at most "
                "three short alternative queries that keep the meaning, and nothing else."
            ),
        },
        {"role": "user", "content": normalise(query)},
    ]
    try:
        if complete is None:
            from core.prompts.compare import _complete

            complete = _complete
        response = await complete(tenant_id, model, messages, MODEL_MAX_TOKENS)
        text = response.get("content", "") if isinstance(response, dict) else getattr(response, "content", "")
        start, end = str(text).find("["), str(text).rfind("]")
        parsed = json.loads(str(text)[start : end + 1]) if start >= 0 and end > start else None
    # enterprise-gate: broad-except-ok reason=a-failed-rewrite-falls-back-to-the-deterministic-plan
    except Exception as exc:
        logger.warning("knowledge_query_rewrite_failed", model=model, error=type(exc).__name__)
        return [], "model_failed"
    if not isinstance(parsed, list):
        return [], "model_unusable"
    proposals = [normalise(p) for p in parsed if isinstance(p, str) and normalise(p)]
    return proposals[:3], None


@dataclass
class Trace:
    """The steps of one retrieval, each with counts and elapsed time, and nothing that identifies a tenant or user."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def add(self, stage: str, **detail: Any) -> None:
        self.steps.append({"stage": stage, "elapsed_ms": int((time.monotonic() - self.started) * 1000), **detail})

    def as_dict(self) -> dict[str, Any]:
        return {"steps": [dict(step) for step in self.steps]}


def is_weak(hits: list[Any], top_k: int, score_of: Callable[[Any], float]) -> tuple[bool, str]:
    """A first pass that returned fewer hits than asked, or whose best score is below the floor."""
    if len(hits) < top_k:
        return True, f"{len(hits)} of {top_k} hits"
    best = max((float(score_of(h) or 0.0) for h in hits), default=0.0)
    if best < WEAK_SCORE:
        return True, f"best score {best:.2f} below {WEAK_SCORE:.2f}"
    return False, f"{len(hits)} hits, best score {best:.2f}"


def fuse(
    lists: list[list[Any]], top_k: int, *, key: Callable[[Any], Any], rescore: Callable[[Any, float], Any]
) -> list[Any]:
    """Reciprocal-rank fusion of several result lists; a hit at the top of every list scores 1."""
    scores: dict[Any, float] = {}
    first: dict[Any, Any] = {}
    for hits in lists:
        for rank, hit in enumerate(hits, start=1):
            k = key(hit)
            scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
            first.setdefault(k, hit)
    ceiling = len(lists) / (RRF_K + 1)
    ordered = sorted(scores, key=lambda k: (-scores[k], str(k)))[:top_k]
    return [rescore(first[k], round(scores[k] / ceiling, 4)) for k in ordered]


async def retrieve(
    planned: Plan,
    top_k: int,
    search: Callable[[str], Awaitable[list[Any]]],
    *,
    key: Callable[[Any], Any],
    score_of: Callable[[Any], float],
    rescore: Callable[[Any, float], Any],
    trace: Trace | None = None,
) -> tuple[list[Any], Trace]:
    """Search the planned query; expand to its variants and fuse when the first pass is weak. Every step is traced."""
    trace = trace or Trace()
    trace.add("plan", query=planned.normalised, variants=list(planned.variants), rules=list(planned.rules))
    first = list(await search(planned.normalised))
    trace.add("search", query=planned.normalised, hits=len(first), best=_best(first, score_of))
    weak, reason = is_weak(first, top_k, score_of)
    if not weak or not planned.variants:
        trace.add("decision", action="accept", reason=reason if not weak else f"{reason}; no variants")
        return first, trace
    trace.add("decision", action="expand", reason=reason)
    lists = [first]
    for variant in planned.variants:
        hits = list(await search(variant))
        trace.add("search", query=variant, hits=len(hits), best=_best(hits, score_of))
        lists.append(hits)
    fused = fuse(lists, top_k, key=key, rescore=rescore)
    trace.add("fuse", lists=len(lists), candidates=len({key(h) for hits in lists for h in hits}), returned=len(fused))
    return fused, trace


def _best(hits: list[Any], score_of: Callable[[Any], float]) -> float:
    return round(max((float(score_of(h) or 0.0) for h in hits), default=0.0), 4)
