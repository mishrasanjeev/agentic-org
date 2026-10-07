# SPDX-License-Identifier: Apache-2.0
"""The supervisor's view: live sessions, a transcript, takeover, replies and release.

A supervisor sees every conversation in progress or escalated, opens its
transcript, and can take it over: from then on the user's messages are not
handled by the runtime but shown to the supervisor, whose replies reach the
user's chat through the live feed (``api/websocket/feed.py``). Releasing a
session hands it back to the runtime. Every turn and change is announced on
the feed so the view updates without polling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.conversation.dialogue import Dialogue

logger = structlog.get_logger()

LIVE_STATUSES = ("active", "escalated")
EXCERPT = 240
HISTORY_KEEP = 40


def session_view(row: Any) -> dict[str, Any]:
    state = dict(row.state or {})
    escalation = dict(row.escalation or {}) if getattr(row, "escalation", None) else None
    return {
        "id": str(row.id),
        "session_key": row.session_key,
        "user_id": row.user_id,
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "channel": row.channel,
        "status": row.status,
        "intent": row.intent,
        "stage": state.get("stage"),
        "turns": int(row.turns or 0),
        "taken_over_by": getattr(row, "taken_over_by", None),
        "taken_over_at": row.taken_over_at.isoformat() if getattr(row, "taken_over_at", None) else None,
        "escalation": {k: escalation.get(k) for k in ("reason", "intent", "at", "hitl_id", "ticket")}
        if escalation
        else None,
        "rating": state.get("rating"),
        "sentiment": (state.get("sentiment") or [{}])[-1].get("label") if state.get("sentiment") else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def announce(tenant_id: uuid.UUID, session_key: str, *, event: str, **fields: Any) -> None:
    """Tell the live feed about a conversation change; a feed failure never fails the turn."""
    from api.websocket.feed import broadcast_to_tenant

    payload = {"type": event, "source": "conversation", "session_key": session_key, **fields}
    try:
        await broadcast_to_tenant(str(tenant_id), payload)
    # enterprise-gate: broad-except-ok reason=live-feed-failure-never-fails-a-conversation-turn
    except Exception as exc:
        logger.warning("conversation_feed_announce_failed", feed_event=event, error_type=type(exc).__name__)


async def announce_turn(
    tenant_id: uuid.UUID, session_key: str, *, role: str, text: str, intent: str | None, stage: str
) -> None:
    await announce(
        tenant_id,
        session_key,
        event="conversation.turn",
        role=role,
        text=text[:EXCERPT],
        intent=intent,
        stage=stage,
    )


async def _row(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    key: str | None = None,
    lock: bool = False,
):
    from core.models.conversation_session import ConversationSession

    query = select(ConversationSession).where(ConversationSession.tenant_id == tenant_id)
    query = (
        query.where(ConversationSession.id == session_id)
        if session_id is not None
        else query.where(ConversationSession.session_key == key)
    )
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def list_live(tenant_id: uuid.UUID, *, limit: int = 50, include_idle: bool = False) -> list[dict[str, Any]]:
    """Sessions in progress or escalated (and idle ones on request), newest activity first."""
    from core.database import get_tenant_session
    from core.models.conversation_session import ConversationSession

    async with get_tenant_session(tenant_id) as session:
        query = select(ConversationSession).where(ConversationSession.tenant_id == tenant_id)
        if not include_idle:
            query = query.where(ConversationSession.status.in_(LIVE_STATUSES))
        rows = (
            (await session.execute(query.order_by(ConversationSession.updated_at.desc()).limit(limit))).scalars().all()
        )
    return [session_view(row) for row in rows]


async def transcript(tenant_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any] | None:
    """One session with its recent turns and hand-off record, or None."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, session_id=session_id)
        if row is None:
            return None
        view = session_view(row)
        state = dict(row.state or {})
        view["history"] = list(state.get("history") or [])
        view["slots"] = dict(state.get("slots") or {})
        view["escalation_summary"] = (row.escalation or {}).get("summary") if getattr(row, "escalation", None) else None
        from core.conversation import summary as conversation_summary

        view["summary"] = conversation_summary.summarise(
            Dialogue.from_dict(state),
            escalation=dict(row.escalation or {}) if getattr(row, "escalation", None) else None,
        )
    return view


async def taken_over(tenant_id: uuid.UUID, key: str) -> str | None:
    """Who has taken a session over, by its key, or None."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, key=key)
        return getattr(row, "taken_over_by", None) if row is not None else None


async def _append(
    tenant_id: uuid.UUID, *, session_id: uuid.UUID | None, key: str | None, role: str, text: str
) -> dict[str, Any] | None:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, session_id=session_id, key=key, lock=True)
        if row is None:
            return None
        state = dict(row.state or Dialogue().to_dict())
        history = list(state.get("history") or [])
        history.append({"role": role, "text": text[:500]})
        state["history"] = history[-HISTORY_KEEP:]
        row.state = state
        row.updated_at = datetime.now(UTC)
        return session_view(row)


async def user_message(tenant_id: uuid.UUID, key: str, text: str) -> dict[str, Any] | None:
    """A user's message while a supervisor holds the session: stored and announced, not handled by the runtime."""
    view = await _append(tenant_id, session_id=None, key=key, role="user", text=text)
    if view is not None:
        await announce(tenant_id, key, event="conversation.message", role="user", text=text[:EXCERPT])
    return view


async def takeover(tenant_id: uuid.UUID, session_id: uuid.UUID, supervisor_id: str) -> dict[str, Any] | None:
    """A supervisor takes the session: the runtime stops answering until it is released."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, session_id=session_id, lock=True)
        if row is None:
            return None
        row.taken_over_by = str(supervisor_id)[:128]
        row.taken_over_at = datetime.now(UTC)
        if row.status == "idle":
            row.status = "active"
        row.updated_at = datetime.now(UTC)
        key = row.session_key
        view = session_view(row)
    await _append(
        tenant_id, session_id=session_id, key=None, role="system", text="A supervisor has joined the conversation."
    )
    await announce(tenant_id, key, event="conversation.takeover", supervisor=str(supervisor_id)[:128])
    return view


async def reply(tenant_id: uuid.UUID, session_id: uuid.UUID, supervisor_id: str, text: str) -> dict[str, Any] | None:
    """A supervisor's message to the user: stored on the transcript and delivered through the live feed."""
    view = await _append(tenant_id, session_id=session_id, key=None, role="supervisor", text=text)
    if view is None:
        return None
    if view.get("taken_over_by") != str(supervisor_id)[:128]:
        return {**view, "refused": "not_taken_over"}
    await announce(tenant_id, view["session_key"], event="conversation.message", role="supervisor", text=text[:EXCERPT])
    return view


async def release(tenant_id: uuid.UUID, session_id: uuid.UUID, supervisor_id: str) -> dict[str, Any] | None:
    """Hand the session back to the runtime."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, session_id=session_id, lock=True)
        if row is None:
            return None
        row.taken_over_by = None
        row.taken_over_at = None
        row.updated_at = datetime.now(UTC)
        key = row.session_key
        view = session_view(row)
    await _append(
        tenant_id,
        session_id=session_id,
        key=None,
        role="system",
        text="The supervisor has left; the assistant is back.",
    )
    await announce(tenant_id, key, event="conversation.release", supervisor=str(supervisor_id)[:128])
    return view


async def mark_escalated(tenant_id: uuid.UUID, key: str, record: dict[str, Any]) -> None:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, key=key, lock=True)
        if row is None:
            return
        row.status = "escalated"
        row.escalation = dict(record)
        row.updated_at = datetime.now(UTC)
