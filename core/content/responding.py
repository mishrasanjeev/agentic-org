# SPDX-License-Identifier: Apache-2.0
"""Policy-grounded response drafts: an answer written only from an approved source set, cited, or an honest no.

Every claim of the response cites a source by id with a quote that must be
in that source; a response with no valid citation, or with any claim that no
verified citation covers, is not a response: the service says the approved
sources do not cover the question and names the gaps, so nothing is answered
from memory.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.drafting import SourceIn
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source, by_id, combined_sources

NOT_COVERED = "The approved sources do not cover this question, so no answer is given from them."


class RespondIn(BaseModel):
    model_config = {"extra": "forbid"}

    message: str = Field(..., min_length=1, max_length=4_000)
    sources: list[SourceIn] = Field(default_factory=list, max_length=10)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=10)
    audience: Literal["customer", "relationship_manager", "internal", "regulator", "vulnerable_customer"] = "customer"
    tone: Literal["formal", "neutral", "friendly", "plain"] = "neutral"
    language: str = Field("en", max_length=16)


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answerable", "response", "citations"],
    "properties": {
        "answerable": {"type": "boolean"},
        "response": {"type": "string", "maxLength": 8_000},
        "citations": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_id", "quote"],
                "properties": {
                    "source_id": {"type": "string", "maxLength": 64},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 400},
                    "claim": {"type": "string", "maxLength": 500},
                },
            },
        },
        "gaps": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 300}},
    },
}


def messages(payload: RespondIn, sources: list[Source]) -> list[dict[str, str]]:
    system = (
        "You answer a question for a bank using only the approved sources given. If the sources do not answer "
        "it, set answerable to false, leave the response empty and name the gaps. Every claim in the response "
        "must have a citation: the source id and a short exact quote copied from that source. Never use outside "
        "knowledge. Answer with one JSON object and nothing else: {answerable, response, citations: [{source_id, "
        "quote, claim}], gaps: [..]}."
    )
    user = (
        f"Audience: {payload.audience}. Tone: {payload.tone}. Language: {payload.language}.\n"
        f"Question:\n{payload.message}\n\nApproved sources:\n{services.source_block(sources)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+")
_STOPWORDS = frozenset(
    "the and for are was were has have had not but you your our its this that these those with from into onto "
    "can may will shall would could should must per any all each also than then there their them they who whom "
    "which what when where why how about over under after before during within without only just very more most "
    "some such been being does did doing yes please thank thanks hello dear regards happy help glad sorry".split()
)
#: The share of a claim's content words that the cited passage must hold for the claim to count as covered.
CLAIM_COVERAGE = 0.6


def _tokens(text: str) -> tuple[set[str], set[str]]:
    """The content words (lightly stemmed) and the figures of a text."""
    words: set[str] = set()
    figures: set[str] = set()
    for token in _TOKEN_RE.findall(text.lower()):
        if token[0].isdigit():
            figures.add(token.replace(",", ""))
        elif len(token) >= 3 and token not in _STOPWORDS:
            words.add(token[:-1] if len(token) > 3 and token.endswith("s") else token)
    return words, figures


def _passage(quote: str, text: str) -> str:
    """The sentences of ``text`` that hold ``quote``: the context a verified citation vouches for."""
    haystack = services.normalise(text)
    needle = services.normalise(quote)
    start = haystack.find(needle) if needle else -1
    if start < 0:
        return needle
    end = start + len(needle)
    left = max(haystack.rfind(mark, 0, start) for mark in (". ", "! ", "? ", "; "))
    rights = [i for i in (haystack.find(mark, end) for mark in (". ", "! ", "? ", "; ")) if i >= 0]
    right = min(rights) if rights else len(haystack)
    return haystack[left + 1 if left >= 0 else 0 : right + 1]


def claims_of(response: str) -> list[str]:
    """The claims of a response: its sentences that state something (a figure, or at least two content words)."""
    out = []
    for sentence in _SENTENCE_RE.split(response):
        sentence = sentence.strip()
        words, figures = _tokens(sentence)
        if figures or len(words) >= 2:
            out.append(sentence)
    return out


def unsupported_claims(response: str, citations: list[dict[str, Any]], known: dict[str, Source]) -> list[str]:
    """Every claim of the response that no verified citation covers.

    A claim is covered when one cited passage (the source sentences holding a verified quote) holds every
    figure of the claim and at least ``CLAIM_COVERAGE`` of its content words. Anything else is unsupported.
    """
    passages = []
    for citation in citations:
        source = known.get(citation["source_id"])
        if source is not None:
            passages.append(_tokens(_passage(citation["quote"], source.text)))
    missing = []
    for claim in claims_of(response):
        words, figures = _tokens(claim)
        covered = any(
            figures <= cited_figures and (not words or len(words & cited_words) / len(words) >= CLAIM_COVERAGE)
            for cited_words, cited_figures in passages
        )
        if not covered:
            missing.append(claim[:300])
    return missing


def finish(payload: RespondIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Citations must quote their sources; with none left, the response is withheld and the gaps are named."""
    known = by_id(sources)
    citations = []
    dropped = 0
    for item in answer.get("citations") or []:
        source = known.get(str(item.get("source_id")))
        quote = str(item.get("quote") or "")
        if source is None or not services.quote_in(quote, source.text):
            dropped += 1
            continue
        citations.append({"source_id": source.id, "quote": quote[:400], "claim": str(item.get("claim") or "")})
    gaps = [str(g) for g in (answer.get("gaps") or [])]
    response = str(answer.get("response") or "")
    answerable = bool(answer.get("answerable")) and bool(citations) and bool(response.strip())
    unsupported = unsupported_claims(response, citations, known) if answerable else []
    if unsupported:
        # One uncited claim withholds the whole response: a partly grounded answer is not grounded.
        answerable = False
        gaps = gaps + [f"Not supported by a cited source: {claim}"[:300] for claim in unsupported]
    if not answerable:
        if not gaps:
            gaps = ["The approved sources do not address the question as asked."]
        return {
            "answerable": False,
            "response": NOT_COVERED,
            "citations": [],
            "gaps": gaps,
            "sources_used": [],
            "dropped_citations": dropped,
            "unsupported_claims": unsupported,
        }
    return {
        "answerable": True,
        "response": response,
        "citations": citations,
        "gaps": gaps,
        "sources_used": sorted({c["source_id"] for c in citations}),
        "dropped_citations": dropped,
        "unsupported_claims": [],
    }


def rendered(output: dict[str, Any]) -> str:
    return str(output.get("response") or "")


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    return {**output, "response": text}


async def resolve_sources(tenant_id: uuid.UUID, payload: RespondIn, domains: list[str] | None) -> list[Source]:
    sources = await combined_sources(tenant_id, payload.sources, payload.knowledge_document_ids, domains)
    if not sources:
        raise services.ContentError(422, "no_sources", "Give an approved source set to answer from")
    return sources


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "covered",
        "input": "Question: what is the daily transfer limit for a new account? Source: new accounts may transfer "
        "up to 2 lakh a day for the first 90 days.",
        "contains": ["2 lakh"],
    },
    {
        "id": "not-covered",
        "input": "Question: can I open a joint account online? Source: new accounts may transfer up to 2 lakh a "
        "day for the first 90 days.",
        "contains": ["do not cover"],
    },
]

SERVICE = services.register(
    Service(
        name="respond",
        title="Policy-grounded response",
        description="An answer written only from an approved source set with a cited quote per claim, or an "
        "honest statement that the sources do not cover the question.",
        input_model=RespondIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=True),
        dataset_name="content: policy-grounded response",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
