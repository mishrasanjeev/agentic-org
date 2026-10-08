# SPDX-License-Identifier: Apache-2.0
"""The supervisor's view: live sessions, a transcript, takeover, replies and release.

A supervisor sees every conversation in progress or escalated, opens its
transcript, and can take it over: from then on the user's messages are not
handled by the runtime but shown to the supervisor, whose replies reach the
user's chat. Releasing a session hands it back to the runtime. Every turn and
change is announced on the live feed (``api/websocket/feed.py``) so the views
update without polling, but the feed is tenant-wide, so an announcement never
carries what was said: the console reads the text through its tenant-admin
transcript route, and the user's chat reads the supervisor's messages from the
user's own session (``GET /conversation/session``), which also replays any that
arrived while the chat was closed.
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
HISTORY_KEEP = 40
REPLAY_ROLES = ("supervisor", "system")  # what the user's chat replays from the session's transcript
NOT_HOLDER = "not_taken_over"


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


async def announce_turn(tenant_id: uuid.UUID, session_key: str, *, role: str, intent: str | None, stage: str) -> None:
    """A turn happened: who spoke and where the dialogue is, never the text (the feed reaches every tenant user)."""
    await announce(tenant_id, session_key, event="conversation.turn", role=role, intent=intent, stage=stage)


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
    return view


async def taken_over(tenant_id: uuid.UUID, key: str) -> str | None:
    """Who has taken a session over, by its key, or None."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, key=key)
        return getattr(row, "taken_over_by", None) if row is not None else None


async def replay(tenant_id: uuid.UUID, key: str) -> list[dict[str, Any]]:
    """The supervisor's messages and notices on one session, oldest first, for the user's own chat to show.

    ``key`` is the caller's own session key (the route derives it from the
    authenticated user), so a user only ever reads their own conversation.
    """
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, key=key)
        if row is None:
            return []
        history = list(dict(row.state or {}).get("history") or [])
    return [
        {"role": turn.get("role"), "text": str(turn.get("text") or ""), "at": turn.get("at")}
        for turn in history
        if isinstance(turn, dict) and turn.get("role") in REPLAY_ROLES
    ]


async def _append(
    tenant_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None,
    key: str | None,
    role: str,
    text: str,
    holder: str | None = None,
) -> dict[str, Any] | None:
    """Add a turn to the session's transcript under a row lock.

    With ``holder``, the session must be held by that supervisor: the check is
    made on the locked row in the same transaction, before anything is
    written, so a refused message leaves the transcript as it was.
    """
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, session_id=session_id, key=key, lock=True)
        if row is None:
            return None
        if holder is not None and getattr(row, "taken_over_by", None) != holder:
            return {**session_view(row), "refused": NOT_HOLDER}
        state = dict(row.state or Dialogue().to_dict())
        history = list(state.get("history") or [])
        history.append({"role": role, "text": text[:500], "at": datetime.now(UTC).isoformat()})
        state["history"] = history[-HISTORY_KEEP:]
        row.state = state
        row.updated_at = datetime.now(UTC)
        return session_view(row)


async def user_message(tenant_id: uuid.UUID, key: str, text: str) -> dict[str, Any] | None:
    """A user's message while a supervisor holds the session: stored and announced, not handled by the runtime."""
    view = await _append(tenant_id, session_id=None, key=key, role="user", text=text)
    if view is not None:
        await announce(tenant_id, key, event="conversation.message", role="user")
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
    """A supervisor's message to the user, stored on the transcript; only the holder may send one.

    The feed is told that a message arrived (not what it says); the user's chat
    then reads it from the user's own session.
    """
    view = await _append(
        tenant_id, session_id=session_id, key=None, role="supervisor", text=text, holder=str(supervisor_id)[:128]
    )
    if view is None or view.get("refused"):
        return view
    await announce(tenant_id, view["session_key"], event="conversation.message", role="supervisor")
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
