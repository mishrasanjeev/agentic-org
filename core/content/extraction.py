# SPDX-License-Identifier: Apache-2.0
"""Obligation and deadline extraction: who must do what by when, each item quoting the text it comes from.

An item is kept only when its quote is a meaningful span of its source (at
least MIN_QUOTE_CHARS characters and MIN_QUOTE_WORDS words, found in the
source) and the quote supports the obligation (most of the obligation's
content words appear in it); anything else is dropped and counted, so nothing
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
_WORD_RE = re.compile(r"[a-z0-9]+")
MIN_QUOTE_CHARS = 12
MIN_QUOTE_WORDS = 3
# Share of the obligation's content words its quote must contain.
MIN_SUPPORT = 0.5
_STOPWORDS = frozenset(
    "a an the and or of to in on at by for from with within into upon as is are be been being was were will "
    "shall must should may can could would has have had its it this that these those their there any all each "
    "every such not no nor than then per under over after before".split()
)


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
                    # A short quote drops its item in finish() rather than failing the whole answer here.
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
        "basis the document gives for it, the source id, the exact sentence or clause from the source that "
        "states the obligation (copied word for word, at least a few words), and a "
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


def _stems(text: str) -> set[str]:
    """Content words, cut to a common stem so "invoices" meets "invoice" and "delivery" meets "deliver"."""
    return {word[:5] for word in _WORD_RE.findall(str(text or "").lower()) if word not in _STOPWORDS}


def meaningful_quote(quote: str) -> bool:
    """A quote long enough to be evidence: not a character or a stray word."""
    normalised = services.normalise(quote)
    return len(normalised) >= MIN_QUOTE_CHARS and len(_WORD_RE.findall(normalised)) >= MIN_QUOTE_WORDS


def supports(quote: str, obligation: str) -> bool:
    """Whether ``quote`` states ``obligation``: most of the obligation's content words are in the quote."""
    wanted = _stems(obligation)
    if not wanted:
        return False
    return len(wanted & _stems(quote)) / len(wanted) >= MIN_SUPPORT


def finish(payload: ExtractIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    """Keep only items with a meaningful quote that is in their source and supports the obligation;
    normalise deadlines; sort by deadline."""
    known = by_id(sources)
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in answer.get("obligations") or []:
        source = known.get(str(item.get("source_id")))
        quote = str(item.get("quote") or "")
        obligation = str(item.get("obligation") or "")
        if (
            source is None
            or not meaningful_quote(quote)
            or not services.quote_in(quote, source.text)
            or not supports(quote, obligation)
        ):
            dropped += 1
            continue
        kept.append(
            {
                "party": str(item.get("party") or ""),
                "obligation": obligation,
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
