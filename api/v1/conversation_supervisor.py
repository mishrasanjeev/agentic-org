# SPDX-License-Identifier: Apache-2.0
"""The supervisor console: live conversations, a transcript, takeover, replies and release. Tenant-admin only."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.conversation import runtime, supervisor

router = APIRouter(prefix="/conversation/supervisor", tags=["Conversation"], dependencies=[require_tenant_admin])


class ReplyIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=2000)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "conversation_disabled",
            "message": "Conversational services are off for this deployment (AGENTICORG_CONVERSATION_V2_ENABLED).",
        },
    )


def _supervisor_id(request: Request) -> str:
    from api.v1.chat import _session_user_id

    return _session_user_id(request)


def _not_found() -> HTTPException:
    return HTTPException(404, detail={"error": "not_found", "message": "No such conversation"})


@router.get("/sessions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="conversation.supervisor.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="conversation.supervisor.sessions.list",
)
async def list_sessions(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_idle: bool = False,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Conversations in progress or escalated, newest activity first, with who holds each one."""
    if not runtime.enabled():
        raise _off()
    sessions = await supervisor.list_live(uuid.UUID(tenant_id), limit=limit, include_idle=include_idle)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="conversation.supervisor.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="conversation.supervisor.session.read",
)
async def get_session(session_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One conversation: its state, recent turns, slots and hand-off record."""
    if not runtime.enabled():
        raise _off()
    view = await supervisor.transcript(uuid.UUID(tenant_id), session_id)
    if view is None:
        raise _not_found()
    return view


@router.post("/sessions/{session_id}/takeover")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="conversation.supervisor.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="conversation.supervisor.takeover",
)
async def take_over(
    session_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Take the conversation: the assistant stops answering and the user's messages come here."""
    if not runtime.enabled():
        raise _off()
    view = await supervisor.takeover(uuid.UUID(tenant_id), session_id, _supervisor_id(request))
    if view is None:
        raise _not_found()
    return view


@router.post("/sessions/{session_id}/reply")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="conversation.supervisor.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="conversation.supervisor.reply",
)
async def send_reply(
    session_id: uuid.UUID, body: ReplyIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """A message to the user, delivered to their chat through the live feed. Only the holder may reply."""
    if not runtime.enabled():
        raise _off()
    view = await supervisor.reply(uuid.UUID(tenant_id), session_id, _supervisor_id(request), body.text)
    if view is None:
        raise _not_found()
    if view.get("refused"):
        raise HTTPException(
            409, detail={"error": view["refused"], "message": "Take the conversation over before replying"}
        )
    return view


@router.post("/sessions/{session_id}/release")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="conversation.supervisor.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="conversation.supervisor.release",
)
async def release_session(
    session_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Hand the conversation back to the assistant."""
    if not runtime.enabled():
        raise _off()
    view = await supervisor.release(uuid.UUID(tenant_id), session_id, _supervisor_id(request))
    if view is None:
        raise _not_found()
    return view
