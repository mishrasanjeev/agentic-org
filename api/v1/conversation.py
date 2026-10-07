# SPDX-License-Identifier: Apache-2.0
"""Conversational services: banking turns with slot filling and confirmation, the intent catalogue, and the session."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.conversation import intents as catalogue
from core.conversation import runtime
from core.database import get_tenant_session
from core.governance.agent_status import refusal_for as agent_status_refusal
from core.models.agent import Agent
from core.ownership import agent_ownership_fields, caller_from_request, require_agent_visible

logger = structlog.get_logger()
router = APIRouter(prefix="/conversation", tags=["Conversation"])

_CHANNELS = ("web", "voice", "whatsapp", "teams", "api")


class FeedbackIn(BaseModel):
    model_config = {"extra": "forbid"}

    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=500)
    company_id: str = ""
    agent_id: str = ""
    channel: str = Field("web", max_length=16)


class TurnIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=2000)
    company_id: str = ""
    agent_id: str = ""
    channel: str = Field("web", max_length=16)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "conversation_disabled",
            "message": "Conversational services are off for this deployment (AGENTICORG_CONVERSATION_V2_ENABLED).",
        },
    )


def _channel(value: str) -> str:
    channel = (value or "web").lower()
    if channel not in _CHANNELS:
        raise HTTPException(422, detail={"error": "channel_unknown", "message": f"channel must be one of {_CHANNELS}"})
    return channel


def _user_id(request: Request) -> str:
    from api.v1.chat import _session_user_id

    return _session_user_id(request)


async def _execution_context(
    request: Request, tenant_id: str, company_id: str, agent_id: str
) -> runtime.ExecutionContext | None:
    """What a confirmed action needs from the chosen agent: its tools, connectors and grant, resolved as chat does."""
    if not agent_id:
        return None
    from api.v1.agents import (
        _assert_connectors_ready_for_dispatch,
        _require_company_for_tenant,
        _resolve_connector_configs,
    )
    from auth.run_grants import resolve_run_grant

    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(404, "Agent not found") from None
    company_uuid = await _require_company_for_tenant(tenant_id, company_id)
    tid = uuid.UUID(tenant_id)
    caller = caller_from_request(request)
    async with get_tenant_session(tid, company_uuid) as session:
        agent = (
            await session.execute(
                select(Agent).where(Agent.id == aid, Agent.tenant_id == tid, Agent.company_id == company_uuid)
            )
        ).scalar_one_or_none()
        if agent is None:
            raise HTTPException(404, "Agent not found")
        require_agent_visible(agent, caller)
        refusal = agent_status_refusal(agent.status)
        if refusal is not None:
            raise HTTPException(409, refusal)
        connector_ids = list(agent.connector_ids or [])
        ownership = agent_ownership_fields(agent)
        if connector_ids:
            await _assert_connectors_ready_for_dispatch(
                session,
                tid,
                connector_ids,
                company_uuid,
                agent_visibility=ownership["visibility"],
                agent_owner_user_id=getattr(agent, "owner_user_id", None),
                linked_connector_ids=connector_ids,
            )
        context = runtime.ExecutionContext(
            tenant_id=tenant_id,
            agent_id=str(agent.id),
            agent_type=str(agent.agent_type or ""),
            domain=str(agent.domain or ""),
            authorized_tools=list(agent.authorized_tools or []),
            company_id=str(company_uuid),
            bindings=runtime.bindings_of(agent.config),
        )
    if connector_ids:
        context.connector_config, context.connector_names = await _resolve_connector_configs(
            tenant_id=tenant_id, connector_ids=connector_ids, agent_level_config=None, company_id=company_uuid
        )
    context.run_grant = await resolve_run_grant(
        tenant_id=tenant_id,
        agent_id=context.agent_id,
        caller_token=getattr(request.state, "grant_token", None),
        caller_agent_id=str(getattr(request.state, "agent_id", "") or ""),
        runtime=runtime.RUNTIME,
    )
    return context


@router.post("/turns")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.agent_execution.external_tool_sensitive.write",
    rate_limit="chat-query",
    idempotency="not_idempotent-advances-the-dialogue-and-may-run-a-confirmed-action",
    audit_event="conversation.turn",
)
async def post_turn(body: TurnIn, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One turn of a banking conversation: recognise, fill slots, clarify, confirm, and run a confirmed action."""
    if not runtime.enabled():
        raise _off()
    channel = _channel(body.channel)
    user_id = _user_id(request)
    context = await _execution_context(request, tenant_id, body.company_id, body.agent_id)
    tid = uuid.UUID(tenant_id)
    key = runtime.session_key(channel, body.company_id, body.agent_id, user_id)
    dialogue = await runtime.load_dialogue(tid, key)
    held = await runtime.held_turn(tid, key, body.text, dialogue)
    if held is not None:
        return held
    from core.conversation import dialogue as engine

    outcome = engine.advance(dialogue, body.text)
    execution: dict[str, Any] | None = None
    if outcome.kind == "execute":
        if context is None:
            execution = {"status": "unbound", "intent": outcome.intent, "message": "Choose an agent to run this."}
        else:
            execution = await runtime.execute(outcome, context)
    return await runtime.finish_turn(
        tid,
        key,
        dialogue,
        outcome,
        execution,
        text=body.text,
        user_id=user_id,
        agent_id=body.agent_id,
        channel=channel,
        context=context,
    )


@router.get("/intents")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="conversation.intents.list",
)
async def list_intents(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The banking intents the runtime recognises, their slots, and whether the runtime is on."""
    return {
        "enabled": runtime.enabled(),
        "intents": [intent.to_dict() for intent in catalogue.CATALOGUE],
        "actions": {action: list(aliases) for action, aliases in runtime.ACTIONS.items()},
        "min_confidence": catalogue.MIN_CONFIDENCE,
    }


@router.post("/feedback")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.write",
    rate_limit="standard",
    idempotency="idempotent-full-replace",
    audit_event="conversation.feedback",
)
async def post_feedback(
    body: FeedbackIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """A rating from 1 to 5 for the caller's conversation, kept on the session and with the agent's feedback."""
    from core.conversation import feedback

    if not runtime.enabled():
        raise _off()
    channel = _channel(body.channel)
    user_id = _user_id(request)
    tid = uuid.UUID(tenant_id)
    key = runtime.session_key(channel, body.company_id, body.agent_id, user_id)
    dialogue = await runtime.load_dialogue(tid, key)
    dialogue.rating = body.rating
    dialogue.rating_asked = True
    record = await feedback.record_rating(
        tid,
        session_key=key,
        agent_id=body.agent_id or None,
        user_id=user_id,
        rating=body.rating,
        comment=body.comment,
        channel=channel,
        intent=dialogue.last_intent,
        sentiment_label=feedback.latest_label(dialogue.sentiment),
    )
    await runtime.save_dialogue(tid, key, dialogue, user_id=user_id, agent_id=body.agent_id or None, channel=channel)
    return {"session_key": key, **record}


@router.get("/session/summary")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="conversation.session.summary",
)
async def get_summary(
    request: Request,
    company_id: Annotated[str, Query(max_length=64)] = "",
    agent_id: Annotated[str, Query(max_length=64)] = "",
    channel: Annotated[str, Query(max_length=16)] = "web",
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A summary of the caller's own conversation: requests, actions, what is pending, rating and sentiment."""
    from core.conversation import summary as conversation_summary

    if not runtime.enabled():
        raise _off()
    key = runtime.session_key(_channel(channel), company_id, agent_id, _user_id(request))
    dialogue = await runtime.load_dialogue(uuid.UUID(tenant_id), key)
    return {"session_key": key, "summary": conversation_summary.summarise(dialogue)}


@router.get("/session")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="conversation.session.read",
)
async def get_session(
    request: Request,
    company_id: Annotated[str, Query(max_length=64)] = "",
    agent_id: Annotated[str, Query(max_length=64)] = "",
    channel: Annotated[str, Query(max_length=16)] = "web",
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The caller's own dialogue for a channel, company and agent: stage, intent, slots and what is missing."""
    if not runtime.enabled():
        raise _off()
    key = runtime.session_key(_channel(channel), company_id, agent_id, _user_id(request))
    dialogue = await runtime.load_dialogue(uuid.UUID(tenant_id), key)
    return {"session_key": key, "dialogue": runtime.dialogue_view(dialogue)}


@router.delete("/session")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="chat.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="conversation.session.reset",
)
async def reset_session(
    request: Request,
    company_id: Annotated[str, Query(max_length=64)] = "",
    agent_id: Annotated[str, Query(max_length=64)] = "",
    channel: Annotated[str, Query(max_length=16)] = "web",
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Start the caller's dialogue over; nothing in progress is run."""
    if not runtime.enabled():
        raise _off()
    key = runtime.session_key(_channel(channel), company_id, agent_id, _user_id(request))
    reset = await runtime.reset_dialogue(uuid.UUID(tenant_id), key)
    return {"session_key": key, "reset": reset}
