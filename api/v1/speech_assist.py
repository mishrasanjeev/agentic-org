# SPDX-License-Identifier: Apache-2.0
"""Agent assist and disclosure tracking: the checklist on a kept recording, and a live call turn by turn.

``GET /speech/disclosures`` lists the scripts and what this tenant
requires (the business console's ``speech.required_disclosures``).
``GET /speech/recordings/{id}/disclosures`` checks a kept transcript.
``POST /speech/live/sessions`` opens a call; each turn posted to it
returns the checklist, the mood, the intent, the knowledge that answers
the customer and the next step, and raises a flag the moment a
disclosure is overdue; closing it gives the compliance report.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.speech import assist, disclosures, store
from core.speech.audio import SpeechError
from core.workbench import console

router = APIRouter(prefix="/speech", tags=["Speech"])


class SessionIn(BaseModel):
    model_config = {"extra": "forbid"}

    call_ref: str = Field("", max_length=128)
    call_type: str = Field("service", max_length=32)
    agent_id: str | None = Field(None, max_length=128)


class TurnIn(BaseModel):
    model_config = {"extra": "forbid"}

    speaker: str = Field("customer", max_length=32)
    text: str = Field(..., min_length=1, max_length=assist.MAX_TEXT)
    at: float | None = Field(None, ge=0, le=7200)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "speech_disabled",
            "message": "Speech intelligence is off for this deployment (AGENTICORG_SPEECH_INTELLIGENCE_ENABLED).",
        },
    )


def _refused(exc: SpeechError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


async def _required(tenant_id: str) -> list[str]:
    found = await console.value(tenant_id, "speech.required_disclosures")
    return [str(k) for k in (found or [])]


@router.get("/disclosures")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.disclosures.list",
)
async def list_disclosures(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The disclosure scripts, the call types, and the ones this tenant requires."""
    return {
        "enabled": store.enabled(),
        "disclosures": disclosures.catalogue(),
        "call_types": list(disclosures.CALL_TYPES),
        "required": await _required(tenant_id),
    }


@router.get("/recordings/{recording_id}/disclosures")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.recordings.disclosures",
)
async def recording_disclosures(
    recording_id: uuid.UUID,
    call_type: Annotated[str, Query(max_length=32)] = "service",
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The disclosure checklist against a kept transcript: said where, late, or missing."""
    if not store.enabled():
        raise _off()
    if call_type.lower() not in disclosures.CALL_TYPES:
        raise HTTPException(
            422,
            detail={
                "error": "call_type_unknown",
                "message": f"call_type is one of {', '.join(disclosures.CALL_TYPES)}",
            },
        )
    found = await store.get_recording(uuid.UUID(tenant_id), recording_id)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such recording"})
    transcript = found.get("transcript") or {}
    turns = list(transcript.get("turns") or [])
    roles = [str(r) for r in found.get("channel_roles") or []]
    agent = roles[0] if roles else ("agent" if any(t.get("speaker") == "agent" for t in turns) else None)
    needed = disclosures.required_for(call_type, await _required(tenant_id))
    return {"id": found["id"], "call_type": call_type.lower(), **disclosures.check(turns, required=needed, agent=agent)}


@router.post("/live/sessions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.live.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="speech.live.start",
)
async def start_session(
    body: SessionIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Open a live session for a call; the disclosures its type requires come back as the checklist."""
    if not store.enabled():
        raise _off()
    try:
        return await assist.start(
            uuid.UUID(tenant_id),
            call_ref=body.call_ref,
            call_type=body.call_type,
            agent_id=body.agent_id or _user_id(request) or None,
            required=await _required(tenant_id),
        )
    except SpeechError as exc:
        raise _refused(exc) from None


@router.post("/live/sessions/{session_id}/turns")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.live.sensitive.write",
    rate_limit="chat-query",
    idempotency="not-idempotent-appends-a-turn",
    audit_event="speech.live.turn",
)
async def post_turn(
    session_id: uuid.UUID, body: TurnIn, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """One transcribed turn of the call; the answer is what the agent should see now."""
    if not store.enabled():
        raise _off()
    try:
        return await assist.append_turn(
            uuid.UUID(tenant_id), session_id, speaker=body.speaker, text=body.text, at=body.at
        )
    except SpeechError as exc:
        raise _refused(exc) from None


@router.post("/live/sessions/{session_id}/close")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.live.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="speech.live.close",
)
async def close_session(session_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Close the call with its compliance report."""
    if not store.enabled():
        raise _off()
    try:
        return await assist.close(uuid.UUID(tenant_id), session_id)
    except SpeechError as exc:
        raise _refused(exc) from None


@router.get("/live/sessions")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.live.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.live.list",
)
async def list_sessions(
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The live sessions, newest first, without their turns."""
    if not store.enabled():
        raise _off()
    if status is not None and status not in assist.STATUSES:
        raise HTTPException(422, detail={"error": "status_unknown", "message": f"status is one of {assist.STATUSES}"})
    rows = await assist.list_sessions(uuid.UUID(tenant_id), status=status, limit=limit)
    return {"sessions": rows, "total": len(rows)}


@router.get("/live/sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.live.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.live.read",
)
async def get_session(session_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One live session with its turns, flags and report."""
    if not store.enabled():
        raise _off()
    found = await assist.get(uuid.UUID(tenant_id), session_id, with_turns=True)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such live session"})
    return found
