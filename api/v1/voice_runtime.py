"""Durable Twilio voice runtime with signed webhooks and provider STT/TTS."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.config import settings
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_for_tenant
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.voice_call import VoiceCall
from core.voice.livekit_agent import VoiceAgentWorker
from core.voice.runtime import (
    TERMINAL_CALL_STATUSES,
    build_conversation_twiml,
    create_twilio_call,
    verify_twilio_signature,
)

router = APIRouter()
_WELCOME = "Hello. You are connected to your AgenticOrg voice agent. How can I help?"
_ERROR = "I could not process that request. Please try again later."
_CALL_STATUSES = TERMINAL_CALL_STATUSES | {"queued", "ringing", "in_progress"}


class OutboundCallRequest(BaseModel):
    agent_id: uuid.UUID
    to_number: str = Field(pattern=r"^\+[1-9]\d{6,14}$")


class VoiceCallOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    provider: str
    provider_call_id: str
    direction: Literal["inbound", "outbound"]
    status: str
    from_number: str | None
    to_number: str | None
    turn_count: int
    duration_seconds: int | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


def _masked_number(value: str | None) -> str | None:
    if not value:
        return None
    return f"***{value[-4:]}" if len(value) > 4 else "***"


def _call_out(row: VoiceCall) -> VoiceCallOut:
    return VoiceCallOut(
        id=row.id,
        agent_id=row.agent_id,
        provider=row.provider,
        provider_call_id=row.provider_call_id,
        direction=row.direction,  # type: ignore[arg-type]
        status=row.status,
        from_number=_masked_number(row.from_number),
        to_number=_masked_number(row.to_number),
        turn_count=row.turn_count,
        duration_seconds=row.duration_seconds,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
    )


def _normalise_call_status(value: str | None) -> str:
    status = str(value or "failed").strip().lower().replace("-", "_")
    return status if status in _CALL_STATUSES else "failed"


def _public_api_base() -> str:
    base = str(settings.public_api_base_url or "").rstrip("/")
    if settings.env in {"production", "staging"} and not base.startswith("https://"):
        raise HTTPException(503, "AGENTICORG_PUBLIC_API_BASE_URL must be HTTPS")
    return base or "http://localhost:8000"


def _voice_url(tenant_id: uuid.UUID, agent_id: uuid.UUID, suffix: str) -> str:
    return f"{_public_api_base()}/api/v1/voice/webhooks/twilio/{tenant_id}/{agent_id}/{suffix}"


async def _twilio_config(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any]:
    from api.v1.voice import _load_voice_settings

    config = await _load_voice_settings(tenant_id, str(agent_id))
    if not config or config.get("sip_provider") != "twilio":
        raise HTTPException(404, "Twilio voice is not configured for this agent")
    credentials = config.get("credentials")
    if not isinstance(credentials, dict):
        raise HTTPException(503, "Twilio credentials are unavailable")
    if not credentials.get("account_sid") or not credentials.get("auth_token"):
        raise HTTPException(503, "Twilio credentials are incomplete")
    return config


async def _verified_form(
    request: Request,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    suffix: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    config = await _twilio_config(tenant_id, agent_id)
    form = await request.form()
    params = {str(key): str(value) for key, value in form.multi_items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    token = str(config["credentials"]["auth_token"])
    if not verify_twilio_signature(
        url=_voice_url(tenant_id, agent_id, suffix),
        params=params,
        signature=signature,
        auth_token=token,
    ):
        raise HTTPException(403, "Invalid Twilio webhook signature")
    return params, config


async def _load_agent(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
        agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Voice agent not found")
    if agent.status not in {"active", "shadow"}:
        raise HTTPException(409, "Voice agent is not active")
    return agent


async def _upsert_call(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    provider_call_id: str,
    direction: str,
    status: str,
    from_number: str | None,
    to_number: str | None,
) -> VoiceCall:
    now = datetime.now(UTC)
    safe_from_number = _masked_number(from_number)
    safe_to_number = _masked_number(to_number)
    async with get_tenant_session(tenant_id) as session:
        statement = (
            insert(VoiceCall)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                provider="twilio",
                provider_call_id=provider_call_id,
                direction=direction,
                status=status,
                from_number=safe_from_number,
                to_number=safe_to_number,
                transcript_encrypted={},
                turn_count=0,
                started_at=now if status in {"ringing", "in_progress"} else None,
                ended_at=now if status in TERMINAL_CALL_STATUSES else None,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "provider", "provider_call_id"],
                set_={
                    "status": case(
                        (VoiceCall.status.in_(TERMINAL_CALL_STATUSES), VoiceCall.status),
                        else_=status,
                    ),
                    "from_number": func.coalesce(VoiceCall.from_number, safe_from_number),
                    "to_number": func.coalesce(VoiceCall.to_number, safe_to_number),
                    "started_at": func.coalesce(
                        VoiceCall.started_at,
                        now if status in {"ringing", "in_progress"} else None,
                    ),
                    "ended_at": case(
                        (status in TERMINAL_CALL_STATUSES, now),
                        else_=VoiceCall.ended_at,
                    ),
                    "updated_at": now,
                },
            )
            .returning(VoiceCall)
        )
        result = await session.execute(statement)
        return result.scalar_one()


async def _append_turn(tenant_id: uuid.UUID, provider_call_id: str, user_text: str, agent_text: str) -> None:
    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(
            select(VoiceCall).where(
                VoiceCall.tenant_id == tenant_id,
                VoiceCall.provider == "twilio",
                VoiceCall.provider_call_id == provider_call_id,
            )
        )
        row = result.scalar_one()
        if row.status in TERMINAL_CALL_STATUSES:
            raise RuntimeError("Cannot append a turn to a terminal voice call")
        encrypted = row.transcript_encrypted or {}
        ciphertext = encrypted.get("_encrypted") if isinstance(encrypted, dict) else None
        turns: list[dict[str, str]] = []
        if ciphertext:
            try:
                loaded = json.loads(decrypt_for_tenant(str(ciphertext)))
                if isinstance(loaded, list):
                    turns = loaded[-98:]
            except (TypeError, ValueError, json.JSONDecodeError):
                turns = []
        timestamp = datetime.now(UTC).isoformat()
        turns.extend(
            [
                {"role": "caller", "text": user_text[:4000], "at": timestamp},
                {"role": "agent", "text": agent_text[:4000], "at": timestamp},
            ]
        )
        ciphertext = await encrypt_for_tenant(json.dumps(turns, separators=(",", ":")), tenant_id)
        row.transcript_encrypted = {"_encrypted": ciphertext}
        row.turn_count += 1
        row.status = "in_progress"


@router.post("/voice/webhooks/twilio/{tenant_id}/{agent_id}/incoming")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="voice.provider.twilio.webhook",
    rate_limit="provider-webhook",
    idempotency="provider-call-sid",
    audit_event="voice.call.turn",
)
async def twilio_incoming(tenant_id: uuid.UUID, agent_id: uuid.UUID, request: Request) -> Response:
    """Handle a signed Twilio speech turn and return provider STT/TTS TwiML."""
    params, _config = await _verified_form(request, tenant_id=tenant_id, agent_id=agent_id, suffix="incoming")
    call_sid = params.get("CallSid", "")
    if not call_sid:
        raise HTTPException(422, "CallSid is required")
    await _upsert_call(
        tenant_id=tenant_id,
        agent_id=agent_id,
        provider_call_id=call_sid,
        direction="inbound" if params.get("Direction") != "outbound-api" else "outbound",
        status="in_progress",
        from_number=params.get("From"),
        to_number=params.get("To"),
    )
    action_url = _voice_url(tenant_id, agent_id, "incoming")
    speech = params.get("SpeechResult", "").strip()[:4000]
    if not speech:
        return Response(
            build_conversation_twiml(
                action_url=action_url,
                prompt=_WELCOME,
                language=str(_config.get("language") or "en-IN"),
            ),
            media_type="application/xml",
        )
    agent = await _load_agent(tenant_id, agent_id)
    worker = VoiceAgentWorker(
        {
            "agent_id": str(agent.id),
            "agent_type": agent.agent_type,
            "domain": agent.domain,
            "tenant_id": str(tenant_id),
            "system_prompt": agent.system_prompt_text or agent.system_prompt_ref,
            "authorized_tools": list(agent.authorized_tools or []),
        },
        thread_id=f"voice:{call_sid}",
    )
    try:
        reply = await worker.handle_call(call_sid, speech)
        await _append_turn(tenant_id, call_sid, speech, reply)
    except Exception:  # enterprise-gate: broad-except-ok reason=voice-turn-failure-returns-safe-provider-response
        reply = _ERROR
    return Response(
        build_conversation_twiml(
            action_url=action_url,
            prompt=reply,
            language=str(_config.get("language") or "en-IN"),
        ),
        media_type="application/xml",
    )


@router.post("/voice/webhooks/twilio/{tenant_id}/{agent_id}/status")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="voice.provider.twilio.status_webhook",
    rate_limit="provider-webhook",
    idempotency="provider-call-sid-status",
    audit_event="voice.call.status",
)
async def twilio_status(tenant_id: uuid.UUID, agent_id: uuid.UUID, request: Request) -> Response:
    """Persist signed provider call lifecycle updates."""
    params, _config = await _verified_form(request, tenant_id=tenant_id, agent_id=agent_id, suffix="status")
    call_sid = params.get("CallSid", "")
    if not call_sid:
        raise HTTPException(422, "CallSid is required")
    status = _normalise_call_status(params.get("CallStatus"))
    row = await _upsert_call(
        tenant_id=tenant_id,
        agent_id=agent_id,
        provider_call_id=call_sid,
        direction="inbound" if params.get("Direction") != "outbound-api" else "outbound",
        status=status,
        from_number=params.get("From"),
        to_number=params.get("To"),
    )
    duration = params.get("CallDuration", "")
    if duration.isdigit():
        async with get_tenant_session(tenant_id) as session:
            stored = await session.get(VoiceCall, row.id)
            if stored:
                stored.duration_seconds = int(duration)
    return Response(status_code=204)


@router.post("/voice/calls/outbound", response_model=VoiceCallOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.calls.outbound.create",
    rate_limit="voice-call-create",
    idempotency="provider-call-sid",
    audit_event="voice.call.outbound.create",
)
async def create_outbound_call(body: OutboundCallRequest, tenant_id: str = Depends(get_current_tenant)) -> VoiceCallOut:
    """Create a real Twilio outbound call for a configured agent."""
    tid = uuid.UUID(tenant_id)
    await _load_agent(tid, body.agent_id)
    config = await _twilio_config(tid, body.agent_id)
    credentials = config["credentials"]
    try:
        payload = await create_twilio_call(
            account_sid=str(credentials["account_sid"]),
            auth_token=str(credentials["auth_token"]),
            to_number=body.to_number,
            from_number=str(config["phone_number"]),
            voice_url=_voice_url(tid, body.agent_id, "incoming"),
            status_callback_url=_voice_url(tid, body.agent_id, "status"),
        )
    except (httpx.RequestError, RuntimeError) as exc:
        raise HTTPException(502, str(exc)) from exc
    row = await _upsert_call(
        tenant_id=tid,
        agent_id=body.agent_id,
        provider_call_id=str(payload["sid"]),
        direction="outbound",
        status=_normalise_call_status(str(payload.get("status") or "queued")),
        from_number=str(config["phone_number"]),
        to_number=body.to_number,
    )
    return _call_out(row)


@router.get("/voice/calls", response_model=list[VoiceCallOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.calls.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="voice.calls.list",
)
async def list_voice_calls(
    agent_id: Annotated[uuid.UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    tenant_id: str = Depends(get_current_tenant),
) -> list[VoiceCallOut]:
    """List real calls without transcript or full phone numbers."""
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(VoiceCall)
            .where(VoiceCall.tenant_id == tid, VoiceCall.agent_id == agent_id)
            .order_by(VoiceCall.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
    return [_call_out(row) for row in rows]


@router.get("/voice/runtime/health")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.runtime.health.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="voice.runtime.health",
)
async def voice_runtime_health(
    agent_id: Annotated[uuid.UUID, Query()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return truthful provider/STT/TTS readiness for one agent."""
    tid = uuid.UUID(tenant_id)
    try:
        config = await _twilio_config(tid, agent_id)
    except HTTPException:
        return {"ready": False, "provider": None, "stt": None, "tts": None}
    return {
        "ready": True,
        "provider": "twilio",
        "stt": "twilio_speech_recognition",
        "tts": "twilio_say",
        "inbound_webhook_url": _voice_url(tid, agent_id, "incoming"),
        "status_webhook_url": _voice_url(tid, agent_id, "status"),
        "phone_number": _masked_number(str(config.get("phone_number") or "")),
        "audio_stored": False,
        "transcript_encrypted": True,
    }
