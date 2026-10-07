# SPDX-License-Identifier: Apache-2.0
"""Chunking strategies: how extracted spans become the chunks that are embedded and retrieved.

Three strategies, chosen per tenant (``chunk_strategy`` in the tenant AI
settings) with the tenant's ``chunk_size`` (tokens; four characters a token
here) as the size band:

``sentence``
    The platform's original behaviour: spans are cut at sentence
    boundaries into a 120 to ``max_chars`` band and short neighbours are
    merged. Layout is not consulted. This is the default, so a tenant that
    has set nothing is chunked exactly as before.
``paragraph``
    One chunk per extracted paragraph, table row or list item; short
    consecutive paragraphs under the same heading are merged up to the band
    and a long paragraph is cut at sentence boundaries. A chunk never
    crosses a heading.
``heading``
    Spans are grouped under their nearest heading until the band is full,
    and each chunk starts with the heading's text, so a retrieved chunk says
    which section it came from. A chunk never crosses a heading.

Every chunk keeps the provenance of its first span (page, paragraph,
heading, sheet, cell range, frame), so a citation can name the place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.rag.extractors import ExtractedSpan

STRATEGIES: tuple[str, ...] = ("sentence", "paragraph", "heading")
DEFAULT_STRATEGY = "sentence"
DEFAULT_MAX_CHARS = 1500
MIN_CHARS = 120
MIN_MAX_CHARS = 400
MAX_MAX_CHARS = 6000
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ChunkPlan:
    strategy: str = DEFAULT_STRATEGY
    max_chars: int = DEFAULT_MAX_CHARS

    @classmethod
    def from_settings(cls, strategy: Any, chunk_size_tokens: Any) -> ChunkPlan:
        """The plan a tenant's settings describe; anything unusable falls back to the default."""
        chosen = strategy if isinstance(strategy, str) and strategy in STRATEGIES else DEFAULT_STRATEGY
        max_chars = DEFAULT_MAX_CHARS
        if isinstance(chunk_size_tokens, int) and not isinstance(chunk_size_tokens, bool) and chunk_size_tokens > 0:
            max_chars = max(MIN_MAX_CHARS, min(MAX_MAX_CHARS, chunk_size_tokens * CHARS_PER_TOKEN))
        return cls(strategy=chosen, max_chars=max_chars)


def validate_strategy(value: Any) -> str:
    if value not in STRATEGIES:
        raise ValueError(f"chunk_strategy must be one of {', '.join(STRATEGIES)}")
    return str(value)


def _cut_long(text: str, max_chars: int, min_chars: int = MIN_CHARS) -> list[str]:
    """Cut a long text at sentence boundaries into pieces of at most ``max_chars``."""
    pieces: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(". ", 0, max_chars)
        if cut < min_chars:
            cut = remaining.rfind("\n", 0, max_chars)
        if cut < min_chars:
            cut = max_chars
        pieces.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].lstrip()
    if remaining.strip():
        pieces.append(remaining.strip())
    return [piece for piece in pieces if piece]


def chunk_sentence(spans: list[ExtractedSpan], max_chars: int = DEFAULT_MAX_CHARS) -> list[tuple[str, ExtractedSpan]]:
    """The original behaviour, unchanged: ``core.rag.ingest._chunk_spans``."""
    from core.rag.ingest import _chunk_spans

    return _chunk_spans(spans, max_chars=max_chars, min_chars=MIN_CHARS)


def _groups_by_heading(spans: list[ExtractedSpan]) -> list[tuple[ExtractedSpan | None, list[ExtractedSpan]]]:
    """Consecutive spans under the same heading, in order; a heading span opens its own group."""
    groups: list[tuple[ExtractedSpan | None, list[ExtractedSpan]]] = []
    current_heading: ExtractedSpan | None = None
    current: list[ExtractedSpan] = []
    for span in spans:
        if not span.text:
            continue
        if span.kind == "heading":
            if current:
                groups.append((current_heading, current))
            current_heading, current = span, []
            continue
        current.append(span)
    if current or current_heading is not None:
        groups.append((current_heading, current))
    return groups


def chunk_paragraph(spans: list[ExtractedSpan], max_chars: int = DEFAULT_MAX_CHARS) -> list[tuple[str, ExtractedSpan]]:
    out: list[tuple[str, ExtractedSpan]] = []
    for heading, members in _groups_by_heading(spans):
        buffer = ""
        first: ExtractedSpan | None = None
        for span in members:
            pieces = _cut_long(span.text, max_chars)
            for piece in pieces:
                if buffer and len(buffer) + 2 + len(piece) > max_chars:
                    out.append((buffer, first or span))
                    buffer, first = "", None
                if not buffer:
                    buffer, first = piece, span
                elif len(buffer) < MIN_CHARS:
                    buffer = f"{buffer}\n\n{piece}"
                else:
                    out.append((buffer, first or span))
                    buffer, first = piece, span
        if buffer and first is not None:
            out.append((buffer, first))
        if not members and heading is not None and len(heading.text) >= MIN_CHARS:
            out.append((heading.text, heading))
    return out


def chunk_heading(spans: list[ExtractedSpan], max_chars: int = DEFAULT_MAX_CHARS) -> list[tuple[str, ExtractedSpan]]:
    out: list[tuple[str, ExtractedSpan]] = []
    for heading, members in _groups_by_heading(spans):
        prefix = f"{heading.text}\n\n" if heading is not None else ""
        budget = max(MIN_CHARS, max_chars - len(prefix))
        buffer = ""
        first: ExtractedSpan | None = None
        for span in members:
            for piece in _cut_long(span.text, budget):
                if buffer and len(buffer) + 2 + len(piece) > budget:
                    out.append((prefix + buffer, first or span))
                    buffer, first = "", None
                buffer = piece if not buffer else f"{buffer}\n\n{piece}"
                first = first or span
        if buffer and first is not None:
            out.append((prefix + buffer, first))
        elif heading is not None and not members:
            out.append((heading.text, heading))
    return out


def chunk(spans: list[ExtractedSpan], plan: ChunkPlan | None = None) -> list[tuple[str, ExtractedSpan]]:
    """The chunks of the spans under the plan; the default plan reproduces the original behaviour."""
    plan = plan or ChunkPlan()
    if plan.strategy == "paragraph":
        return chunk_paragraph(spans, plan.max_chars)
    if plan.strategy == "heading":
        return chunk_heading(spans, plan.max_chars)
    return chunk_sentence(spans, plan.max_chars)
