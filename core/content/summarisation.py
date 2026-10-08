# SPDX-License-Identifier: Apache-2.0
"""Structured summarisation across documents: a summary, key points that cite their documents, per-document notes."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.drafting import SourceIn
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source, by_id, combined_sources


class SummariseIn(BaseModel):
    model_config = {"extra": "forbid"}

    documents: list[SourceIn] = Field(default_factory=list, max_length=10)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=10)
    focus: str = Field("", max_length=500)
    length: Literal["brief", "standard", "detailed"] = "standard"
    language: str = Field("en", max_length=16)


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "key_points", "per_document"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 8_000},
        "key_points": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "sources"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "sources": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 64}},
                },
            },
        },
        "per_document": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "summary"],
                "properties": {
                    "id": {"type": "string", "maxLength": 64},
                    "summary": {"type": "string", "maxLength": 3_000},
                },
            },
        },
        "open_questions": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
    },
}

_WORDS = {"brief": "about 80 words", "standard": "about 200 words", "detailed": "about 500 words"}


def messages(payload: SummariseIn, sources: list[Source]) -> list[dict[str, str]]:
    system = (
        "You summarise documents for a bank. Use only what the documents say; never add outside facts. Every key "
        "point must cite the ids of the documents it comes from. Note as open questions what the documents leave "
        "unclear or contradict. Answer with one JSON object and nothing else, matching this schema: {summary, "
        "key_points: [{text, sources: [document ids]}], per_document: [{id, summary}], open_questions: [..]}."
    )
    focus = f"Focus on: {payload.focus}\n" if payload.focus else ""
    user = (
        f"Summarise these {len(sources)} document(s) in {payload.language}, {_WORDS[payload.length]} for the summary.\n"
        f"{focus}\nDocuments:\n{services.source_block(sources)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def finish(payload: SummariseIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Citations must name given documents; a point that cites none is kept but counted as ungrounded."""
    known = by_id(sources)
    key_points = []
    ungrounded = 0
    for point in answer.get("key_points") or []:
        cited = [str(s) for s in (point.get("sources") or []) if str(s) in known]
        if not cited:
            ungrounded += 1
        key_points.append({"text": str(point.get("text") or ""), "sources": cited, "grounded": bool(cited)})
    per_document = [
        {"id": str(item.get("id")), "summary": str(item.get("summary") or "")}
        for item in (answer.get("per_document") or [])
        if str(item.get("id")) in known
    ]
    covered = {item["id"] for item in per_document}
    return {
        "summary": str(answer.get("summary") or ""),
        "key_points": key_points,
        "per_document": per_document,
        "open_questions": [str(q) for q in (answer.get("open_questions") or [])],
        "sources_used": sorted({s for point in key_points for s in point["sources"]} | covered),
        "ungrounded_points": ungrounded,
        "documents_not_covered": [source.id for source in sources if source.id not in covered],
    }


def rendered(output: dict[str, Any]) -> str:
    return str(output.get("summary") or "")


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    return {**output, "summary": text}


async def resolve_sources(tenant_id: uuid.UUID, payload: SummariseIn, domains: list[str] | None) -> list[Source]:
    sources = await combined_sources(tenant_id, payload.documents, payload.knowledge_document_ids, domains)
    if not sources:
        raise services.ContentError(422, "no_documents", "Give at least one document to summarise")
    return sources


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "two-policies",
        "input": "Summarise: Document A says the daily transfer limit is 2 lakh for new accounts. Document B says "
        "the limit rises to 5 lakh after 90 days.",
        "contains": ["2 lakh", "5 lakh"],
    },
    {
        "id": "contradiction",
        "input": "Summarise: Document A says the lock-in period is 12 months. Document B says the lock-in period "
        "is 6 months for the same product.",
        "contains": ["12 months", "6 months"],
    },
    {
        "id": "single-note",
        "input": "Summarise: The branch will remain closed on the second Saturday; ATMs stay open.",
        "contains": ["second Saturday"],
    },
]

SERVICE = services.register(
    Service(
        name="summarise",
        title="Structured summarisation",
        description="A summary across documents with key points that cite their documents, per-document notes "
        "and open questions.",
        input_model=SummariseIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=True),
        dataset_name="content: structured summarisation",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
