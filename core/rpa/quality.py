"""Vector-embedded data quality gate for RPA-produced chunks.

Target: ``>= 4.8/5`` per the 2026-04-23 RPA spec. Chunks that score
below 4.5 are rejected; chunks between 4.5 and 4.8 are published but
flagged for review.

The rubric deliberately uses cheap, deterministic heuristics so the
quality gate runs inline during ingestion (no extra LLM call per
chunk). Each dimension is binary (1.0 or 0.0) — a chunk earns a
5.0 only when all five pass.

Dimensions (each worth 1.0):

1. **Length sanity** — 200 ≤ chars ≤ 2000.
   Short chunks carry too little context for retrieval; long chunks
   waste embedding-model tokens (BAAI/bge-small is 512-token).

2. **Sentence completeness** — the chunk ends with a terminal
   punctuation mark (``.`` / ``!`` / ``?``). Truncated mid-sentence
   chunks retrieve poorly because the tail is a dangling phrase.

3. **Non-boilerplate** — the chunk doesn't begin with or consist
   primarily of navigation / footer / cookie-banner text (we detect
   common RBI site-nav boilerplate like "Home › Press Release").

4. **Informative density** — at least two out of three content
   markers present: a number (date / figure / percentage), a
   capitalised noun (regulatory body / policy name), and a minimum
   word count (>= 40).

5. **Embedding-friendliness** — no excessive whitespace / no
   control characters / no obvious encoding garbage (e.g.
   "â€™" mojibake from Latin-1 → UTF-8 misencoding).

The five dimensions can be tuned without changing the storage
contract: ``score_chunk`` always returns a float in [0, 5].
"""

from __future__ import annotations

import re
from typing import Any

# Target thresholds
QUALITY_TARGET = 4.8
QUALITY_REJECT_BELOW = 4.5

# Boilerplate prefixes commonly observed on RBI site navigation
_BOILERPLATE_PATTERNS = (
    re.compile(r"^\s*home\s*[›>]", re.IGNORECASE),
    re.compile(r"^\s*skip\s+to\s+main\s+content", re.IGNORECASE),
    re.compile(r"^\s*loading\.\.\.", re.IGNORECASE),
    re.compile(r"reserved\s+bank\s+of\s+india\s+all\s+rights", re.IGNORECASE),
)

_MOJIBAKE_PATTERNS = (
    "â€™",
    "â€œ",
    "â€�",
    "Ã©",
    "Ã ",
)

_TERMINAL_PUNCTUATION = (".", "!", "?", '"', "'")


def _length_ok(content: str) -> bool:
    n = len(content or "")
    return 200 <= n <= 2000


def _completes_sentence(content: str) -> bool:
    stripped = (content or "").rstrip()
    if not stripped:
        return False
    # Accept common sentence-ending marks even when a closing quote or
    # bracket trails them: "foo." or 'foo?'
    return any(stripped.endswith(mark) for mark in _TERMINAL_PUNCTUATION)


def _non_boilerplate(content: str) -> bool:
    if not content:
        return False
    for pat in _BOILERPLATE_PATTERNS:
        if pat.search(content):
            return False
    return True


def _informative(content: str) -> bool:
    if not content:
        return False
    words = content.split()
    has_enough_words = len(words) >= 40
    has_number = bool(re.search(r"\d", content))
    has_capitalised_noun = bool(
        re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", content)
    )
    passes = sum(
        int(flag)
        for flag in (has_enough_words, has_number, has_capitalised_noun)
    )
    return passes >= 2


def _embedding_friendly(content: str) -> bool:
    if not content:
        return False
    # Reject excessive whitespace (more than 20% spaces indicates a
    # malformed extraction)
    if content.count("  ") / max(len(content), 1) > 0.05:
        return False
    # Reject control characters other than newline / tab
    if any(ord(c) < 32 and c not in "\n\t" for c in content):
        return False
    # Reject common mojibake
    for pat in _MOJIBAKE_PATTERNS:
        if pat in content:
            return False
    return True


def score_chunk(chunk: dict[str, Any] | str) -> dict[str, Any]:
    """Return a quality breakdown for a single chunk.

    Accepts either a raw content string or a chunk dict with a
    ``content`` field.

    Returns
    -------
    dict
        ``{score: float, max: 5, dimensions: {name: 0|1}, passes: bool,
        flagged: bool, reasons: [str]}``.
        ``passes`` is True when ``score >= QUALITY_TARGET``.
        ``flagged`` is True when ``QUALITY_REJECT_BELOW <= score < QUALITY_TARGET``
        (publish but surface for review).
    """
    content = chunk if isinstance(chunk, str) else str(chunk.get("content") or "")

    dimensions = {
        "length_ok": 1.0 if _length_ok(content) else 0.0,
        "completes_sentence": 1.0 if _completes_sentence(content) else 0.0,
        "non_boilerplate": 1.0 if _non_boilerplate(content) else 0.0,
        "informative": 1.0 if _informative(content) else 0.0,
        "embedding_friendly": 1.0 if _embedding_friendly(content) else 0.0,
    }
    score = sum(dimensions.values())
    reasons = [name for name, val in dimensions.items() if val == 0.0]
    return {
        "score": round(score, 2),
        "max": 5,
        "dimensions": dimensions,
        "passes": score >= QUALITY_TARGET,
        "flagged": QUALITY_REJECT_BELOW <= score < QUALITY_TARGET,
        "reasons": reasons,
    }


def filter_chunks(
    chunks: list[dict[str, Any]],
    target: float = QUALITY_TARGET,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition chunks into ``(published, flagged, rejected)`` lists.

    - ``published`` — score >= ``target`` (default 4.8). Published
      chunks get a ``quality`` field merged in.
    - ``flagged`` — score between QUALITY_REJECT_BELOW (4.5) and
      ``target``. Published with a warning so the ops dashboard can
      sample them; useful when RBI page structure shifts slightly.
    - ``rejected`` — score below QUALITY_REJECT_BELOW. Dropped before
      hitting the vector store to protect downstream retrieval.
    """
    published: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for chunk in chunks:
        q = score_chunk(chunk)
        enriched = {**chunk, "quality": q}
        if q["score"] >= target:
            published.append(enriched)
        elif q["score"] >= QUALITY_REJECT_BELOW:
            flagged.append(enriched)
        else:
            rejected.append(enriched)
    return published, flagged, rejected
