# SPDX-License-Identifier: Apache-2.0
"""Escalation hand-off: a person gets the summary, the intent tag and the transcript, not a blank slate.

A hand-off records a review-queue item (``hitl_queue``, trigger
``conversation_escalation``) carrying the summary, the intent tag, the slots
collected so far and the recent turns; when the agent is authorised for a
ticketing tool (``create_ticket``, ``create_incident``) a ticket is created
through the governed tool path with the same summary and tag, and its
reference is kept on the session. The session is marked escalated and the
live feed is told, so a supervisor sees it at once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.conversation import dialogue as dialogue_engine
from core.conversation.dialogue import Dialogue
from core.conversation.intents import INTENTS

logger = structlog.get_logger()

TRIGGER = "conversation_escalation"
TICKET_TOOLS: tuple[str, ...] = ("create_ticket", "create_incident")
REASON_REQUESTED = "requested"  # the user asked for a person
REASON_FALLBACKS = "fallbacks"  # the runtime could not help after repeated turns
REASON_SLOTS = "slots"  # a slot could not be collected
EXPIRES_HOURS = 24
TRANSCRIPT_TURNS = 12


def intent_tag(dialogue: Dialogue, intent: str | None = None) -> str:
    """The intent the hand-off is about: the outcome's, else the one in progress, else the last that ran."""
    name = intent or dialogue.intent or dialogue.last_intent or ""
    return name if name in INTENTS else "general"


def summary_text(dialogue: Dialogue, *, reason: str, intent: str | None = None) -> str:
    """One paragraph a person reads first: what the user wanted, what was collected, why it is here."""
    tag = intent_tag(dialogue, intent)
    title = INTENTS[tag].title if tag in INTENTS else "General enquiry"
    slots = dialogue.slots or dialogue.last_slots or {}
    parts = [f"Hand-off from chat: {title.lower()}"]
    if slots:
        parts.append(
            "with "
            + ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in slots.items() if value not in (None, ""))
        )
    reasons = {
        REASON_REQUESTED: "the user asked for a person",
        REASON_FALLBACKS: "the assistant could not help after repeated turns",
        REASON_SLOTS: "a required detail could not be collected",
    }
    parts.append(f"because {reasons.get(reason, reason)}.")
    last_user = next((turn["text"] for turn in reversed(dialogue.history) if turn.get("role") == "user"), "")
    text = " ".join(parts)
    if last_user:
        text += f' Last message: "{last_user[:200]}".'
    return text


def transcript(dialogue: Dialogue, *, turns: int = TRANSCRIPT_TURNS) -> list[dict[str, str]]:
    return [dict(turn) for turn in dialogue.history[-turns:]]


def ticket_params(
    tool_name: str, *, summary: str, tag: str, lines: list[dict[str, str]], reason: str
) -> dict[str, Any]:
    """The ticket a ticketing tool creates: subject, description with the transcript, priority and the intent tag."""
    body = summary + "\n\nTranscript:\n" + "\n".join(f"{t.get('role', '')}: {t.get('text', '')}" for t in lines)
    urgent = reason in (REASON_FALLBACKS, REASON_SLOTS)
    if tool_name == "create_incident":
        return {
            "short_description": f"Chat hand-off: {tag}",
            "description": body,
            "urgency": "2" if urgent else "3",
            "category": "conversation",
        }
    return {
        "subject": f"Chat hand-off: {tag}",
        "description": body,
        "priority": "high" if urgent else "normal",
        "type": "question",
        "tags": ["chat_handoff", tag],
    }


def ticket_tool(authorized_tools: list[str] | None) -> str | None:
    """The first authorised ticketing tool ref, by the order of ``TICKET_TOOLS``."""
    from core.conversation.runtime import _bare

    refs = [str(t) for t in (authorized_tools or [])]
    for name in TICKET_TOOLS:
        match = next((ref for ref in refs if _bare(ref) == name), None)
        if match:
            return match
    return None


def _requested_by(user_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return None


async def _review_item(
    tenant_id: uuid.UUID,
    *,
    agent_id: str,
    title: str,
    reason: str,
    context: dict[str, Any],
    requested_by: uuid.UUID | None,
) -> str | None:
    """The review-queue item a hand-off leaves; None when no agent owns the conversation."""
    from core.database import get_tenant_session
    from core.models.hitl import HITLQueue

    try:
        agent_uuid = uuid.UUID(str(agent_id))
    except (ValueError, TypeError):
        return None
    async with get_tenant_session(tenant_id) as session:
        item = HITLQueue(
            tenant_id=tenant_id,
            agent_id=agent_uuid,
            workflow_run_id=None,
            requested_by_user_id=requested_by,
            title=title,
            trigger_type=TRIGGER,
            priority="high" if reason in (REASON_FALLBACKS, REASON_SLOTS) else "normal",
            assignee_role="support",
            decision_options={"options": ["acknowledge", "resolve"]},
            context=context,
            expires_at=datetime.now(UTC) + timedelta(hours=EXPIRES_HOURS),
        )
        session.add(item)
        await session.flush()
        return str(item.id)


async def handoff(
    tenant_id: uuid.UUID,
    *,
    session_key: str,
    dialogue: Dialogue,
    user_id: str,
    agent_id: str,
    channel: str,
    reason: str,
    context: Any = None,
    intent: str | None = None,
) -> dict[str, Any]:
    """Hand a conversation to a person: the review item, the ticket when a tool is bound, the session marked."""
    from core.conversation import runtime, supervisor

    tag = intent_tag(dialogue, intent)
    summary = summary_text(dialogue, reason=reason, intent=intent)
    lines = transcript(dialogue)
    record: dict[str, Any] = {
        "reason": reason,
        "intent": tag,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(),
        "hitl_id": None,
        "ticket": None,
    }
    context_payload = {
        "summary": summary,
        "intent": tag,
        "slots": dict(dialogue.slots or dialogue.last_slots or {}),
        "transcript": lines,
        "session_key": session_key,
        "channel": channel,
        "reason": reason,
        "handoff": dialogue_engine.handoff_summary(dialogue),
    }
    try:
        record["hitl_id"] = await _review_item(
            tenant_id,
            agent_id=agent_id,
            title=f"Hand-off: {INTENTS[tag].title if tag in INTENTS else 'conversation'}",
            reason=reason,
            context=context_payload,
            requested_by=_requested_by(user_id),
        )
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        logger.warning("conversation_handoff_review_item_failed", error_type=type(exc).__name__)
    ref = ticket_tool(getattr(context, "authorized_tools", None)) if context is not None else None
    if ref is not None:
        tool_name = runtime._bare(ref)
        result = await runtime.run_tool(
            context,
            ref,
            ticket_params(tool_name, summary=summary, tag=tag, lines=lines, reason=reason),
            label="handoff",
        )
        ticket = result.get("result") if isinstance(result.get("result"), dict) else {}
        record["ticket"] = {
            "status": result.get("status"),
            "tool": tool_name,
            "reference": str(
                (ticket.get("ticket") or {}).get("id")
                or (ticket.get("result") or {}).get("number")
                or ticket.get("id")
                or ticket.get("number")
                or ""
            )
            or None,
        }
    await supervisor.mark_escalated(tenant_id, session_key, record)
    await supervisor.announce(
        tenant_id,
        session_key,
        event="conversation.escalated",
        intent=tag,
        reason=reason,
        hitl_id=record["hitl_id"],
        ticket=(record["ticket"] or {}).get("reference"),
    )
    logger.info(
        "conversation_handoff", intent=tag, reason=reason, ticket=bool(record["ticket"]), review=bool(record["hitl_id"])
    )
    return record


def handoff_answer(record: dict[str, Any]) -> str:
    """What the user is told once the hand-off is recorded."""
    ticket = record.get("ticket") or {}
    reference = ticket.get("reference")
    if reference:
        return f"I have handed this over to a person with a summary. Your reference is {reference}."
    if ticket and ticket.get("status") not in (None, "executed"):
        return (
            "I have handed this over to a person with a summary; the ticket could not be raised, "
            "so the team will pick it up from the review queue."
        )
    return "I have handed this over to a person with a summary. Someone will pick this up shortly."
