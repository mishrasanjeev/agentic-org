# SPDX-License-Identifier: Apache-2.0
"""Obligation and deadline extraction: who must do what by when, each item quoting the text it comes from.

An item whose quote is not in its source is dropped and counted, so nothing
is reported that the documents do not say. Deadlines are ISO dates or null
with the basis the document gives ("within 30 days of notice").
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.drafting import SourceIn
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source, by_id, combined_sources

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExtractIn(BaseModel):
    model_config = {"extra": "forbid"}

    documents: list[SourceIn] = Field(default_factory=list, max_length=10)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=10)
    reference_date: date | None = None
    scope: Literal["all", "customer", "bank", "vendor"] = "all"


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["obligations"],
    "properties": {
        "obligations": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["party", "obligation", "source_id", "quote"],
                "properties": {
                    "party": {"type": "string", "minLength": 1, "maxLength": 120},
                    "obligation": {"type": "string", "minLength": 1, "maxLength": 1_000},
                    "deadline": {"type": ["string", "null"], "maxLength": 40},
                    "deadline_basis": {"type": ["string", "null"], "maxLength": 300},
                    "source_id": {"type": "string", "maxLength": 64},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 400},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            },
        }
    },
}


def messages(payload: ExtractIn, sources: list[Source]) -> list[dict[str, str]]:
    system = (
        "You extract obligations and deadlines from documents for a bank. For each obligation give the party that "
        "owes it, what must be done, the deadline as an ISO date when the document states one (else null) and the "
        "basis the document gives for it, the source id, a short exact quote copied from the source, and a "
        "severity (low, medium, high). Never invent an obligation; if unsure, leave it out. Answer with one JSON "
        "object and nothing else: {obligations: [{party, obligation, deadline, deadline_basis, source_id, quote, "
        "severity}]}."
    )
    reference = f"Today is {payload.reference_date.isoformat()}.\n" if payload.reference_date else ""
    scope = f"Only obligations owed by the {payload.scope}.\n" if payload.scope != "all" else ""
    user = f"{reference}{scope}Documents:\n{services.source_block(sources)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _deadline(value: Any) -> str | None:
    if not isinstance(value, str) or not _ISO_RE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def finish(payload: ExtractIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Keep only items whose quote is in their source; normalise deadlines; sort by deadline."""
    known = by_id(sources)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in answer.get("obligations") or []:
        source = known.get(str(item.get("source_id")))
        quote = str(item.get("quote") or "")
        if source is None or not services.quote_in(quote, source.text):
            dropped += 1
            continue
        kept.append(
            {
                "party": str(item.get("party") or ""),
                "obligation": str(item.get("obligation") or ""),
                "deadline": _deadline(item.get("deadline")),
                "deadline_basis": str(item.get("deadline_basis") or "") or None,
                "source_id": source.id,
                "quote": quote[:400],
                "severity": item.get("severity") if item.get("severity") in ("low", "medium", "high") else "medium",
            }
        )
    kept.sort(key=lambda o: (o["deadline"] is None, o["deadline"] or "", o["party"]))
    return {
        "obligations": kept,
        "dropped": dropped,
        "sources_used": sorted({o["source_id"] for o in kept}),
        "reference_date": payload.reference_date.isoformat() if payload.reference_date else None,
    }


def rendered(output: dict[str, Any]) -> str:
    return "\n".join(f"{o['party']}: {o['obligation']}" for o in output.get("obligations") or [])


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    lines = text.split("\n")
    items = list(output.get("obligations") or [])
    if len(lines) != len(items):
        return output
    updated = []
    for item, line in zip(items, lines, strict=True):
        party, _, obligation = line.partition(": ")
        updated.append({**item, "party": party or item["party"], "obligation": obligation or item["obligation"]})
    return {**output, "obligations": updated}


async def resolve_sources(tenant_id: uuid.UUID, payload: ExtractIn, domains: list[str] | None) -> list[Source]:
    sources = await combined_sources(tenant_id, payload.documents, payload.knowledge_document_ids, domains)
    if not sources:
        raise services.ContentError(422, "no_documents", "Give at least one document to extract from")
    return sources


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "vendor-sla",
        "input": "Extract obligations: The vendor shall deliver the monthly report within 5 working days of month "
        "end. The bank shall pay undisputed invoices within 30 days of receipt.",
        "contains": ["vendor", "30 days"],
    },
    {
        "id": "customer-kyc",
        "input": "Extract obligations: The customer must submit updated address proof by 2026-12-31, failing "
        "which the account may be restricted.",
        "contains": ["2026-12-31"],
    },
    {
        "id": "nothing-binding",
        "input": "Extract obligations: This brochure describes the features of the savings account.",
        "not_contains": ["shall"],
    },
]

SERVICE = services.register(
    Service(
        name="extract",
        title="Obligation and deadline extraction",
        description="Who must do what by when, each item quoting the text it comes from; items the documents "
        "do not support are dropped and counted.",
        input_model=ExtractIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=True),
        dataset_name="content: obligation extraction",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
