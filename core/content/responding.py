# SPDX-License-Identifier: Apache-2.0
"""Policy-grounded response drafts: an answer written only from an approved source set, cited, or an honest no.

Every paragraph of the response cites a source by id with a quote that must
be in that source; a response with no valid citation is not a response: the
service says the approved sources do not cover the question and names the
gaps, so nothing is answered from memory.
"""

from __future__ import annotations

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
    answerable = bool(answer.get("answerable")) and bool(citations) and bool(str(answer.get("response") or "").strip())
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
        }
    return {
        "answerable": True,
        "response": str(answer.get("response") or ""),
        "citations": citations,
        "gaps": gaps,
        "sources_used": sorted({c["source_id"] for c in citations}),
        "dropped_citations": dropped,
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
