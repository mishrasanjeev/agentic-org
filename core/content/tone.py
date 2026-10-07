# SPDX-License-Identifier: Apache-2.0
"""Audience-adaptive tone: the same facts rewritten for an audience, a tone and a reading level.

A rewrite must keep every number, amount, date and percentage of the
original; the service checks that deterministically and reports what went
missing, so a friendlier text never loses the figures that matter.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.content import services
from core.content.services import GuardrailProfile, Service
from core.content.sources import Source

_FACT_RE = re.compile(
    r"(?P<date>\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b)"
    r"|(?:(?P<cur>[₹$€£]|\b(?:rs\.?|inr|usd|eur|gbp)(?=\s?\d))\s?)?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<unit>%|per\s?cent\b|percent\b|lakhs?\b|lacs?\b|crores?\b|cr\b|thousand\b|k\b"
    r"|millions?\b|mn\b|billions?\b|bn\b))?"
    r"(?:\s?(?P<cur_after>rupees?\b|dollars?\b|euros?\b|pounds?\b|inr\b|usd\b|eur\b|gbp\b))?",
    re.I,
)
# Every spelling of a currency maps to one symbol, so a rewrite may spell it differently but not change it.
_CURRENCIES = {
    "₹": "₹",
    "rs": "₹",
    "rs.": "₹",
    "inr": "₹",
    "rupee": "₹",
    "rupees": "₹",
    "$": "$",
    "usd": "$",
    "dollar": "$",
    "dollars": "$",
    "€": "€",
    "eur": "€",
    "euro": "€",
    "euros": "€",
    "£": "£",
    "gbp": "£",
    "pound": "£",
    "pounds": "£",
}
# Magnitude words scale the figure, so 10 lakh and 10,00,000 are the same amount and 10 lakh and 10 are not.
_MAGNITUDES = {
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "thousand": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "mn": 1_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
    "bn": 1_000_000_000,
}


class AdaptIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=20_000)
    audience: Literal["customer", "relationship_manager", "internal", "regulator", "vulnerable_customer", "partner"] = (
        "customer"
    )
    tone: Literal["formal", "neutral", "friendly", "plain", "empathetic"] = "neutral"
    reading_level: Literal["plain", "standard", "expert"] = "standard"
    language: str = Field("en", max_length=16)
    keep: list[str] = Field(default_factory=list, max_length=20)


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 24_000},
        "changes": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 300}},
    },
}

_LEVELS = {
    "plain": "short sentences, everyday words, no jargon; explain any term a first-time customer may not know",
    "standard": "clear sentences for a general reader; keep common banking terms",
    "expert": "precise terms for a professional reader; no simplification of regulatory wording",
}


def _fact_key(match: re.Match[str]) -> str:
    """One figure as a comparable key: currency symbol, value with its magnitude applied, and percent."""
    if match.group("date"):
        return match.group("date")
    value = Decimal(match.group("num").replace(",", ""))
    unit = re.sub(r"\s", "", (match.group("unit") or "").lower())
    percent = unit in ("%", "percent")
    if unit in _MAGNITUDES:
        value *= _MAGNITUDES[unit]
    currency = _CURRENCIES.get((match.group("cur") or match.group("cur_after") or "").lower(), "")
    number = format(value.normalize(), "f")
    return f"{currency}{number}{'%' if percent else ''}"


def fact_map(text: str) -> dict[str, str]:
    """The figures a rewrite must keep, by comparable key, each with the text it was found as."""
    found: dict[str, str] = {}
    for match in _FACT_RE.finditer(text):
        key = _fact_key(match)
        found.setdefault(key, re.sub(r"\s+", " ", match.group(0).strip()))
    return found


def facts_in(text: str) -> list[str]:
    """The figures a rewrite must keep: numbers, amounts, dates and percentages, with currency and magnitude."""
    return list(fact_map(text))


def messages(payload: AdaptIn, sources: list[Source]) -> list[dict[str, str]]:
    keep = ("\nKeep these terms exactly: " + ", ".join(payload.keep)) if payload.keep else ""
    system = (
        "You rewrite bank communications for an audience without changing their meaning. Keep every number, "
        "amount, date, percentage, name and condition exactly; do not add facts, promises or advice. Answer with "
        "one JSON object and nothing else: {text, changes: [what you changed and why]}."
    )
    user = (
        f"Rewrite for the audience '{payload.audience}' in a {payload.tone} tone, {_LEVELS[payload.reading_level]}, "
        f"in {payload.language}.{keep}\n\nOriginal:\n{payload.text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def finish(payload: AdaptIn, sources: list[Source], answer: dict[str, Any]) -> dict[str, Any]:
    text = str(answer.get("text") or "")
    original = fact_map(payload.text)
    rewritten = fact_map(text)
    missing = [found_as for key, found_as in original.items() if key not in rewritten]
    kept_terms_missing = [term for term in payload.keep if term.lower() not in text.lower()]
    return {
        "text": text,
        "changes": [str(c) for c in (answer.get("changes") or [])],
        "audience": payload.audience,
        "tone": payload.tone,
        "reading_level": payload.reading_level,
        "facts_preserved": not missing and not kept_terms_missing,
        "missing_facts": missing + kept_terms_missing,
    }


def rendered(output: dict[str, Any]) -> str:
    return str(output.get("text") or "")


def apply_text(output: dict[str, Any], text: str) -> dict[str, Any]:
    return {**output, "text": text}


async def resolve_sources(tenant_id: uuid.UUID, payload: AdaptIn, domains: list[str] | None) -> list[Source]:
    return [Source(id="original", title="Original text", text=payload.text, origin="inline")]


DATASET_CASES: list[dict[str, Any]] = [
    {
        "id": "plain-for-customer",
        "input": "Rewrite for a customer in plain language: Pursuant to the revised schedule of charges effective "
        "1 January 2026, the quarterly average balance requirement is ₹10,000, failing which a charge of ₹150 "
        "per quarter applies.",
        "contains": ["10,000", "150", "2026"],
    },
    {
        "id": "empathetic-decline",
        "input": "Rewrite empathetically for a vulnerable customer: Your loan application dated 12/03/2026 has "
        "been declined due to insufficient income documentation.",
        "contains": ["12/03/2026"],
    },
]

SERVICE = services.register(
    Service(
        name="adapt",
        title="Audience-adaptive tone",
        description="The same facts rewritten for an audience, a tone and a reading level, with every figure "
        "checked to be still there.",
        input_model=AdaptIn,
        output_schema=OUTPUT_SCHEMA,
        guardrails=GuardrailProfile(input=True, output=True, grounded=True),
        dataset_name="content: audience-adaptive tone",
        dataset_cases=DATASET_CASES,
        messages=messages,
        finish=finish,
        rendered=rendered,
        apply_text=apply_text,
        resolve_sources=resolve_sources,
    )
)
