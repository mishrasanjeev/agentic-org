# SPDX-License-Identifier: Apache-2.0
"""Live agent assist: a call as it happens, turn by turn, with the checklist, the mood, the knowledge and the next step.

A live session is opened when a call starts and takes each turn as it
is transcribed. On every customer turn it recognises the banking intent,
scores the mood, surfaces the knowledge articles that answer the turn and
suggests the next question from the intent's slots; on every turn it
re-checks the disclosure checklist and raises a flag the moment a
disclosure is overdue or a customer asks for a person. The turns are
kept encrypted like a transcript; the flags hold no words. Closing the
session gives the compliance report.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.conversation import feedback
from core.conversation import intents as catalogue
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_for_tenant
from core.speech import analytics, disclosures
from core.speech.audio import SpeechError

logger = structlog.get_logger()

MAX_TURNS = 2000
MAX_TEXT = 2000
MAX_SUGGESTIONS = 3
STATUSES = ("open", "closed")


async def default_search(tenant_id: uuid.UUID, text: str, limit: int) -> list[dict[str, Any]]:
    """The tenant's knowledge for a customer turn, through the knowledge search; nothing when it cannot answer."""
    try:
        from api.v1.knowledge import _native_semantic_search

        results = await _native_semantic_search(str(tenant_id), text, limit)
    # enterprise-gate: broad-except-ok reason=knowledge-search-boundary-degrades-to-no-suggestions-logging-the-failure
    except Exception as exc:  # noqa: BLE001 - the retrieval boundary; the agent gets no suggestion, not an error
        logger.warning("speech_assist_search_failed", error_type=type(exc).__name__)
        return []
    out = []
    for result in results[:limit]:
        out.append(
            {
                "document": getattr(result, "document_name", ""),
                "text": str(getattr(result, "chunk_text", ""))[:400],
                "score": round(float(getattr(result, "score", 0.0)), 3),
            }
        )
    return out


def next_question(intent_name: str | None, turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The next slot the intent still needs, as the question the catalogue asks for it."""
    intent = catalogue.INTENTS.get(intent_name or "")
    if intent is None or not intent.slots:
        return None
    text = " ".join(str(t.get("text") or "") for t in turns)
    entities = catalogue.extract_entities(text)
    for slot in intent.slots:
        if not slot.required:
            continue
        if slot.kind == "amount" and entities.get("amount") is not None:
            continue
        if slot.kind in ("account", "card") and (entities.get(slot.kind) or entities.get("ending")):
            continue
        if slot.kind == "payee" and entities.get("payee"):
            continue
        return {"slot": slot.name, "question": slot.prompt}
    return {"slot": None, "question": "Everything needed is known; confirm and proceed."}


def session_dict(row: Any, *, with_turns: bool = False) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "call_ref": row.call_ref,
        "agent_id": row.agent_id,
        "call_type": row.call_type,
        "required": list(row.required or []),
        "status": row.status,
        "turn_count": row.turn_count,
        "flags": list(row.flags or []),
        "report": dict(row.report or {}),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if with_turns:
        out["turns"] = turns_of_row(row)
    return out


def turns_of_row(row: Any) -> list[dict[str, Any]]:
    envelope = row.turns_encrypted if isinstance(row.turns_encrypted, dict) else {}
    ciphertext = envelope.get("_encrypted")
    if not ciphertext:
        return []
    try:
        return list(json.loads(decrypt_for_tenant(str(ciphertext))))
    except (ValueError, TypeError) as exc:
        logger.warning("speech_live_turns_unreadable", error_type=type(exc).__name__)
        return []


async def _encrypt_turns(tenant_id: uuid.UUID, turns: list[dict[str, Any]]) -> dict[str, Any]:
    return {"_encrypted": await encrypt_for_tenant(json.dumps(turns, ensure_ascii=False), tenant_id)}


async def start(
    tenant_id: uuid.UUID, *, call_ref: str, call_type: str, agent_id: str | None, required: list[str]
) -> dict[str, Any]:
    """Open a live session for a call with the disclosures its type requires."""
    from core.database import get_tenant_session
    from core.models.speech_live_session import SpeechLiveSession

    kind = (call_type or "service").lower()
    if kind not in disclosures.CALL_TYPES:
        raise SpeechError(422, "call_type_unknown", f"call_type is one of {', '.join(disclosures.CALL_TYPES)}")
    needed = [item.key for item in disclosures.required_for(kind, required)]
    now = datetime.now(UTC)
    row = SpeechLiveSession(
        tenant_id=tenant_id,
        call_ref=(call_ref or "")[:128],
        agent_id=(agent_id or None),
        call_type=kind,
        required=needed,
        status="open",
        turn_count=0,
        turns_encrypted={},
        flags=[],
        report={},
        started_at=now,
    )
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        answer = session_dict(row)
    logger.info("speech_live_started", call_type=kind, required=len(needed))
    return {
        **answer,
        "checklist": _checklist(needed, {"found": [], "missing": [{"key": k} for k in needed], "late": []}),
    }


def _checklist(required: list[str], status: dict[str, Any]) -> list[dict[str, Any]]:
    found = {f["key"]: f for f in status.get("found", [])}
    late = {item["key"] for item in status.get("late", [])}
    out = []
    for key in required:
        item = disclosures.DISCLOSURES.get(key)
        if item is None:
            continue
        state = "late" if key in late else "said" if key in found else "pending"
        out.append(
            {"key": key, "title": item.title, "script": item.script, "state": state, "at": found.get(key, {}).get("at")}
        )
    return out


def assess(
    turns: list[dict[str, Any]],
    *,
    required: list[str],
    agent: str | None,
    elapsed: float,
    previous_flags: list[dict[str, Any]],
) -> dict[str, Any]:
    """What the agent should see after a turn: the checklist, the mood, the intent, the next step, and any new flag."""
    needed = [disclosures.DISCLOSURES[k] for k in required if k in disclosures.DISCLOSURES]
    status = disclosures.check(turns, required=needed, agent=agent)
    raised = {(f.get("kind"), f.get("key")) for f in previous_flags}
    new_flags: list[dict[str, Any]] = []
    for item in disclosures.overdue(needed, status, elapsed):
        if ("disclosure_overdue", item["key"]) not in raised:
            new_flags.append(
                {"kind": "disclosure_overdue", "key": item["key"], "title": item["title"], "at": round(elapsed, 3)}
            )
    customer_turns = [t for t in turns if agent is None or t.get("speaker") != agent]
    last_customer = customer_turns[-1] if customer_turns else None
    mood = (
        feedback.sentiment(str(last_customer.get("text") or ""))
        if last_customer
        else {"score": 0.0, "label": "neutral"}
    )
    sentiment = analytics.sentiment_of_turns(customer_turns, None)
    if sentiment["longest_negative_streak"] >= 2 and ("negative_streak", None) not in raised:
        new_flags.append(
            {"kind": "negative_streak", "key": None, "title": "Two negative turns in a row", "at": round(elapsed, 3)}
        )
    for turn in customer_turns[-1:]:
        lowered = str(turn.get("text") or "").lower()
        phrase = next((p for p in analytics.ESCALATION_PHRASES if p in lowered), None)
        if phrase and ("escalation_phrase", phrase) not in raised:
            new_flags.append(
                {
                    "kind": "escalation_phrase",
                    "key": phrase,
                    "title": f"The customer said: {phrase}",
                    "at": round(elapsed, 3),
                }
            )
    best = None
    for turn in customer_turns:
        for match in catalogue.recognise(str(turn.get("text") or ""))[:1]:
            if best is None or match.confidence > best.confidence:
                best = match
    intent = (
        {"name": best.intent.name, "title": best.intent.title, "confidence": round(best.confidence, 3)}
        if best
        else None
    )
    return {
        "checklist": _checklist(required, status),
        "compliant_so_far": not status["missing"] and not status["late"],
        "mood": mood,
        "sentiment": {"overall": sentiment["overall"], "negative_streak": sentiment["longest_negative_streak"]},
        "intent": intent,
        "next_step": next_question(best.intent.name, customer_turns) if best else None,
        "new_flags": new_flags,
    }


async def append_turn(
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    speaker: str,
    text: str,
    at: float | None,
    search: Any = None,
) -> dict[str, Any]:
    """Take a turn, re-check the call and tell the agent what to do next; knowledge is surfaced for a customer turn."""
    from core.database import get_tenant_session
    from core.models.speech_live_session import SpeechLiveSession

    clean = str(text or "").strip()[:MAX_TEXT]
    if not clean:
        raise SpeechError(422, "text_empty", "A turn needs text")
    who = (speaker or "customer").strip().lower()[:32]
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(SpeechLiveSession).where(
                    SpeechLiveSession.tenant_id == tenant_id, SpeechLiveSession.id == session_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SpeechError(404, "not_found", "No such live session")
        if row.status != "open":
            raise SpeechError(409, "closed", "The live session is closed")
        turns = turns_of_row(row)
        if len(turns) >= MAX_TURNS:
            raise SpeechError(413, "too_many_turns", f"A live session holds at most {MAX_TURNS} turns")
        started = row.started_at
        required = list(row.required or [])
        previous_flags = list(row.flags or [])
    now = datetime.now(UTC)
    elapsed = float(at) if at is not None else (now - started).total_seconds() if started else 0.0
    turns.append({"speaker": who, "text": clean, "start": round(max(0.0, elapsed), 3)})
    agent_name = (
        analytics.roles_of(sorted({t["speaker"] for t in turns}))[0]
        if "agent" not in {t["speaker"] for t in turns}
        else "agent"
    )
    view = assess(turns, required=required, agent=agent_name, elapsed=elapsed, previous_flags=previous_flags)
    suggestions: list[dict[str, Any]] = []
    if who != agent_name:
        suggestions = await (search or default_search)(tenant_id, clean, MAX_SUGGESTIONS)
    envelope = await _encrypt_turns(tenant_id, turns)
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(SpeechLiveSession)
                .where(SpeechLiveSession.tenant_id == tenant_id, SpeechLiveSession.id == session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise SpeechError(404, "not_found", "No such live session")
        row.turns_encrypted = envelope
        row.turn_count = len(turns)
        row.flags = previous_flags + view["new_flags"]
        row.updated_at = now
        answer = session_dict(row)
    if view["new_flags"]:
        logger.info("speech_live_flags", kinds=[f["kind"] for f in view["new_flags"]])
    return {
        **answer,
        **view,
        "suggestions": suggestions,
        "turn": {"speaker": who, "start": round(max(0.0, elapsed), 3)},
    }


async def close(tenant_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any]:
    """Close the session with the compliance report: what was said, late or missing, and the flags raised."""
    from core.database import get_tenant_session
    from core.models.speech_live_session import SpeechLiveSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(SpeechLiveSession)
                .where(SpeechLiveSession.tenant_id == tenant_id, SpeechLiveSession.id == session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise SpeechError(404, "not_found", "No such live session")
        turns = turns_of_row(row)
        needed = [disclosures.DISCLOSURES[k] for k in (row.required or []) if k in disclosures.DISCLOSURES]
        agent_name = (
            "agent"
            if any(t.get("speaker") == "agent" for t in turns)
            else analytics.roles_of(sorted({t["speaker"] for t in turns}))[0]
        )
        status = disclosures.check(turns, required=needed, agent=agent_name)
        row.report = {**status, "flags": list(row.flags or []), "turns": len(turns)}
        row.status = "closed"
        row.closed_at = datetime.now(UTC)
        row.updated_at = row.closed_at
        answer = session_dict(row)
    logger.info("speech_live_closed", compliant=status["compliant"], missing=len(status["missing"]))
    return answer


async def get(tenant_id: uuid.UUID, session_id: uuid.UUID, *, with_turns: bool = False) -> dict[str, Any] | None:
    from core.database import get_tenant_session
    from core.models.speech_live_session import SpeechLiveSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(SpeechLiveSession).where(
                    SpeechLiveSession.tenant_id == tenant_id, SpeechLiveSession.id == session_id
                )
            )
        ).scalar_one_or_none()
        return session_dict(row, with_turns=with_turns) if row is not None else None


async def list_sessions(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.speech_live_session import SpeechLiveSession

    statement = select(SpeechLiveSession).where(SpeechLiveSession.tenant_id == tenant_id)
    if status:
        statement = statement.where(SpeechLiveSession.status == status)
    statement = statement.order_by(SpeechLiveSession.started_at.desc()).limit(max(1, min(limit, 200)))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return [session_dict(row) for row in rows]
