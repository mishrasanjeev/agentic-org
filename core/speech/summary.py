# SPDX-License-Identifier: Apache-2.0
"""Call summaries: intent, key points, next actions and outcome, from a model or from the words.

The model path goes through the content services' JSON call (schema
checked, one retry) so a summary is a fixed shape whoever produced it.
The extractive path needs no model: the intent comes from the banking
intent catalogue over the customer's turns, the key points are the most
informative turns (amounts, dates, identifiers, length), the next actions
are the turns that commit to something, and the outcome is read from
the closing turns. Every summary says which path made it.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import structlog

from core.conversation import feedback
from core.conversation import intents as catalogue

logger = structlog.get_logger()

METHODS = ("auto", "model", "extractive")
MAX_POINTS = 5
MAX_ACTIONS = 5
TRANSCRIPT_LIMIT = 12_000
SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "key_points", "next_actions", "outcome"],
    "properties": {
        "intent": {"type": "string", "maxLength": 120},
        "key_points": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
        "next_actions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
        "outcome": {"type": "string", "enum": ["resolved", "unresolved", "escalated", "follow_up"]},
        "customer_mood": {"type": "string", "maxLength": 60},
        "notes": {"type": "string", "maxLength": 1000},
    },
}
_COMMIT_RE = re.compile(
    r"\b(i will|i'll|we will|we'll|will be|within \d+"
    r"|by (tomorrow|monday|tuesday|wednesday|thursday|friday|end of)"
    r"|please (send|share|visit|call)|you will receive|call you back|get back to you)\b",
    re.I,
)
_INFORMATIVE_RE = re.compile(
    r"(\d[\d,]{2,}|₹|rs\.?|account|card|loan|emi|statement|transfer|dispute|charge|refund|branch|otp|reference)", re.I
)
_RESOLVED_RE = re.compile(r"\b(resolved|sorted|done|processed|completed|fixed|thank you|thanks)\b", re.I)
_ESCALATED_RE = re.compile(r"\b(manager|supervisor|complaint|escalat)", re.I)


def _turns(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (transcript.get("turns") or []) if str(t.get("text") or "").strip()]


def intent_of(turns: list[dict[str, Any]], customer: str | None) -> dict[str, Any]:
    """The banking intent the customer's turns match best, with the catalogue's confidence."""
    best: tuple[float, str, str] | None = None
    for turn in turns:
        if customer is not None and turn.get("speaker") != customer:
            continue
        for match in catalogue.recognise(str(turn.get("text") or ""))[:1]:
            if best is None or match.confidence > best[0]:
                best = (match.confidence, match.intent.name, match.intent.title)
    if best is None:
        return {"name": "unknown", "title": "Not recognised", "confidence": 0.0}
    return {"name": best[1], "title": best[2], "confidence": round(best[0], 3)}


def _informativeness(text: str) -> float:
    words = text.split()
    return len(_INFORMATIVE_RE.findall(text)) * 2.0 + min(len(words), 40) / 10.0


def key_points_of(turns: list[dict[str, Any]], *, limit: int = MAX_POINTS) -> list[str]:
    """The most informative turns, in the order they were said."""
    ranked = sorted(enumerate(turns), key=lambda item: -_informativeness(str(item[1].get("text") or "")))
    chosen = sorted(index for index, _ in ranked[:limit])
    return [f"{turns[i].get('speaker')}: {str(turns[i].get('text') or '').strip()[:300]}" for i in chosen]


def next_actions_of(turns: list[dict[str, Any]], *, agent: str | None = None, limit: int = MAX_ACTIONS) -> list[str]:
    """The turns that commit to something: a promise, a deadline, a request; the agent's where the agent is known."""
    out = []
    for turn in turns:
        if agent is not None and turn.get("speaker") != agent:
            continue
        text = str(turn.get("text") or "").strip()
        if _COMMIT_RE.search(text) and not _ESCALATED_RE.search(text):
            out.append(f"{turn.get('speaker')}: {text[:300]}")
        if len(out) >= limit:
            break
    return out


def outcome_of(turns: list[dict[str, Any]], customer: str | None, *, agent: str | None = None) -> str:
    """resolved, escalated, follow_up or unresolved, read from the closing turns and the commitments.

    A call that ends on a positive customer note and a closing word is resolved even if a complaint was
    raised earlier; one that ends on a request for a person or a complaint is escalated.
    """
    closing = turns[-4:]
    text = " ".join(str(t.get("text") or "") for t in closing)
    customer_closing = [t for t in closing if customer is None or t.get("speaker") == customer]
    last_customer = customer_closing[-1:] if customer_closing else []
    mood = (
        feedback.sentiment(" ".join(str(t.get("text") or "") for t in last_customer))
        if last_customer
        else {"label": "neutral"}
    )
    if _RESOLVED_RE.search(text) and mood["label"] == "positive":
        return "resolved"
    if _ESCALATED_RE.search(" ".join(str(t.get("text") or "") for t in turns[-2:])):
        return "escalated"
    if _RESOLVED_RE.search(text) and mood["label"] != "negative":
        return "resolved"
    if next_actions_of(turns, agent=agent, limit=1):
        return "follow_up"
    return "unresolved"


def extractive(transcript: dict[str, Any], *, agent: str | None = None, customer: str | None = None) -> dict[str, Any]:
    """A summary from the words alone."""
    turns = _turns(transcript)
    intent = intent_of(turns, customer)
    customer_turns = [t for t in turns if customer is None or t.get("speaker") == customer]
    mood = (
        feedback.sentiment(" ".join(str(t.get("text") or "") for t in customer_turns))
        if customer_turns
        else {"label": "neutral"}
    )
    return {
        "method": "extractive",
        "intent": intent["title"],
        "intent_name": intent["name"],
        "intent_confidence": intent["confidence"],
        "key_points": key_points_of(turns),
        "next_actions": next_actions_of(turns, agent=agent),
        "outcome": outcome_of(turns, customer, agent=agent),
        "customer_mood": mood["label"],
        "turns": len(turns),
        "roles": {"agent": agent, "customer": customer},
    }


def _messages(transcript: dict[str, Any], agent: str | None, customer: str | None) -> list[dict[str, str]]:
    text = str(transcript.get("text") or "")[:TRANSCRIPT_LIMIT]
    system = (
        "You summarise recorded bank customer calls for the people who handle them afterwards. Use only what "
        "was said; never add facts. Answer with one JSON object and nothing else: {intent: what the customer "
        "wanted in a few words, key_points: up to 8 short points in the order they arose, next_actions: up to 8 "
        "commitments either party made, outcome: one of resolved, unresolved, escalated, follow_up, "
        "customer_mood: a few words, notes: anything a reviewer must know}."
    )
    user = f"The agent is {agent or 'unknown'} and the customer is {customer or 'unknown'}.\n\nTranscript:\n{text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def model(
    tenant_id: uuid.UUID,
    transcript: dict[str, Any],
    *,
    agent: str | None = None,
    customer: str | None = None,
    complete: Any = None,
) -> dict[str, Any]:
    """A summary from the model through the content services' checked JSON call."""
    from core.content import services

    answer, usage = await services.ask_model(
        tenant_id, _messages(transcript, agent, customer), SCHEMA, complete=complete
    )
    return {
        "method": "model",
        "intent": str(answer.get("intent") or "")[:120],
        "key_points": [str(p)[:300] for p in answer.get("key_points") or []][:8],
        "next_actions": [str(a)[:300] for a in answer.get("next_actions") or []][:8],
        "outcome": str(answer.get("outcome") or "unresolved"),
        "customer_mood": str(answer.get("customer_mood") or "")[:60],
        "notes": str(answer.get("notes") or "")[:1000],
        "model": usage,
        "turns": len(_turns(transcript)),
        "roles": {"agent": agent, "customer": customer},
    }


async def summarise(
    tenant_id: uuid.UUID,
    transcript: dict[str, Any],
    *,
    method: str = "auto",
    agent: str | None = None,
    customer: str | None = None,
    complete: Any = None,
) -> dict[str, Any]:
    """The summary by the method asked: the model, the words, or the model with the words as the fallback."""
    if method not in METHODS:
        raise ValueError(f"method is one of {', '.join(METHODS)}")
    if not _turns(transcript):
        return {**extractive(transcript, agent=agent, customer=customer), "empty": True}
    if method == "extractive":
        return extractive(transcript, agent=agent, customer=customer)
    try:
        return await model(tenant_id, transcript, agent=agent, customer=customer, complete=complete)
    # enterprise-gate: broad-except-ok reason=model-boundary-falls-back-to-the-extractive-summary-and-says-so
    except Exception as exc:  # noqa: BLE001 - the model boundary; the extractive summary stands in and says so
        if method == "model":
            raise
        logger.warning("speech_summary_model_failed", error_type=type(exc).__name__)
        return {**extractive(transcript, agent=agent, customer=customer), "fallback_from": "model"}
