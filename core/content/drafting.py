# SPDX-License-Identifier: Apache-2.0
"""Governed drafting: notices, circulars, letters, emails, memos and FAQs from points and approved sources.

The draft names the sources it used (only ones it was given), lists the
placeholders a person must fill, and is kept as a record. A notice or a
circular, or any draft the caller marks, goes to the drafts queue for a
second person to approve before it is final.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source, by_id, combined_sources

KINDS = ("notice", "circular", "letter", "email", "memo", "faq")
APPROVAL_KINDS = ("notice", "circular")
TONES = ("formal", "neutral", "friendly", "plain")
_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9 _/-]{1,40})\]")


class SourceIn(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field("", max_length=200)
    text: str = Field(..., min_length=1, max_length=30_000)


class DraftIn(BaseModel):
    model_config = {"extra": "forbid"}

    kind: Literal["notice", "circular", "letter", "email", "memo", "faq"]
    subject: str = Field(..., min_length=1, max_length=200)
    points: list[str] = Field(..., min_length=1, max_length=20)
    audience: str = Field("customers", max_length=100)
    tone: Literal["formal", "neutral", "friendly", "plain"] = "formal"
    language: str = Field("en", max_length=16)
    constraints: list[str] = Field(default_factory=list, max_length=10)
    sources: list[SourceIn] = Field(default_factory=list, max_length=10)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=10)
    require_approval: bool | None = None


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "body", "sections", "sources_used"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "body": {"type": "string", "minLength": 1, "maxLength": 20_000},
        "sections": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "text"],
                "properties": {
                    "heading": {"type": "string", "maxLength": 200},
                    "text": {"type": "string", "maxLength": 8_000},
                },
            },
        },
        "sources_used": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 64}},
        "placeholders": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 64}},
        "notes": {"type": "string", "maxLength": 1_000},
    },
}


def requires_approval(payload: DraftIn) -> bool:
    """Policy: notices and circulars always, anything the caller marks, nothing else."""
    if payload.require_approval is not None:
        return bool(payload.require_approval) or payload.kind in APPROVAL_KINDS
    return payload.kind in APPROVAL_KINDS


def messages(payload: DraftIn, sources: list[Source]) -> list[dict[str, str]]:
    points = "\n".join(f"- {point}" for point in payload.points)
    constraints = "\n".join(f"- {item}" for item in payload.constraints) or "- none"
    system = (
        "You draft documents for a bank. Write only from the points and the sources given; never add facts, "
        "figures, names or dates that are not in them. Where a fact is needed but missing, write a placeholder in "
        "square brackets in capitals, such as [DATE] or [BRANCH NAME]. Answer with one JSON object and nothing "
        "else, matching this schema: {title, body, sections: [{heading, text}], sources_used: [source ids you "
        "relied on], placeholders: [the placeholders you wrote], notes}."
    )
    user = (
        f"Draft a {payload.kind} in {payload.language} for {payload.audience} in a {payload.tone} tone.\n"
        f"Subject: {payload.subject}\nPoints to cover:\n{points}\nConstraints:\n{constraints}\n"
    )
    if sources:
        user += "\nApproved sources (cite their ids in sources_used):\n" + services.source_block(sources)
    else:
        user += "\nNo sources were given: sources_used must be an empty list."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def finish(payload: DraftIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Only given sources count as used; placeholders are what the body actually contains."""
    known = by_id(sources)
    claimed = [str(s) for s in answer.get("sources_used") or []]
    used = [s for s in claimed if s in known]
    unknown = [s for s in claimed if s not in known]
    body = str(answer.get("body") or "")
    placeholders = sorted({match.group(0) for match in _PLACEHOLDER_RE.finditer(body)})
    notes = str(answer.get("notes") or "")
    if unknown:
        notes = (notes + " " if notes else "") + f"Dropped {len(unknown)} source reference(s) that were not given."
    return {
        "kind": payload.kind,
        "title": str(answer.get("title") or payload.subject)[:300],
        "body": body,
        "sections": [
            {"heading": str(s.get("heading") or ""), "text": str(s.get("text") or "")}
            for s in (answer.get("sections") or [])
        ],
        "sources_used": used,
        "placeholders": placeholders,
        "notes": notes.strip(),
        "requires_approval": requires_approval(payload),
    }


def rendered(output: dict[str, Any]) -> str:
    return str(output.get("body") or "")


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    return {**output, "body": text}


async def resolve_sources(tenant_id: uuid.UUID, payload: DraftIn, domains: list[str] | None) -> list[Source]:
    return await combined_sources(tenant_id, payload.sources, payload.knowledge_document_ids, domains)


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "notice-branch-timings",
        "input": "Draft a notice for customers: branch timings change to 10 am to 4 pm from the first of next month; "
        "digital channels unaffected; contact the branch for appointments.",
        "contains": ["10 am", "4 pm"],
        "not_contains": ["[DATE]"],
    },
    {
        "id": "circular-kyc-refresh",
        "input": "Draft a circular for branch staff: periodic KYC refresh is due for accounts opened before 2020; "
        "use the approved form; escalate exceptions to the compliance desk.",
        "contains": ["KYC", "compliance"],
    },
    {
        "id": "email-missing-fact",
        "input": "Draft an email to a customer confirming their new debit card has been dispatched; the courier "
        "reference is not known yet.",
        "contains": ["["],
    },
]

SERVICE = services.register(
    Service(
        name="draft",
        title="Governed drafting",
        description="A notice, circular, letter, email, memo or FAQ drafted from points and approved sources, "
        "with placeholders for what is not known; notices and circulars go to the drafts queue for approval.",
        input_model=DraftIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=False),
        dataset_name="content: governed drafting",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
