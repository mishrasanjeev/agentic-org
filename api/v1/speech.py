# SPDX-License-Identifier: Apache-2.0
"""Speech intelligence: recordings uploaded, split by speaker, transcribed and read back.

``POST /speech/recordings`` takes a PCM WAV, finds who spoke when (by
channel for a stereo call, by sound for a mono one), transcribes it
through the engine named where one is available, and keeps it with the
transcript encrypted. ``POST /speech/recordings/{id}/transcript`` attaches
words a caller transcribed elsewhere, aligned to the speakers. Off,
``GET /speech/status`` says so and the rest is not found.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.speech import store, transcribe
from core.speech.audio import MAX_BYTES, MAX_SECONDS, SpeechError

router = APIRouter(prefix="/speech", tags=["Speech"])


class WordsIn(BaseModel):
    model_config = {"extra": "forbid"}

    words: list[dict[str, Any]] = Field(..., min_length=1, max_length=transcribe.MAX_WORDS)


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


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether speech intelligence is on, which engines this deployment can use, and the limits."""
    return {
        "enabled": store.enabled(),
        "engines": transcribe.engines_available(),
        "limits": {"max_bytes": MAX_BYTES, "max_seconds": MAX_SECONDS, "formats": ["audio/wav"]},
    }


@router.post("/recordings")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.write",
    rate_limit="chat-query",
    idempotency="not-idempotent-create",
    audit_event="speech.recordings.create",
)
async def upload_recording(
    file: UploadFile,
    request: Request,
    channel_roles: Annotated[str, Query(max_length=80)] = "",
    language: Annotated[str, Query(max_length=16)] = "en",
    engine: Annotated[str | None, Query(max_length=32)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Keep a recording with its speakers and, where an engine is named, its transcript.

    ``channel_roles`` names the parties by channel, for example ``agent,customer``; ``engine`` is
    ``faster_whisper``, ``deepgram`` or ``supplied`` (words attached later).
    """
    if not store.enabled():
        raise _off()
    if engine is not None and engine not in transcribe.ENGINES:
        raise HTTPException(
            422, detail={"error": "engine_unknown", "message": f"engine is one of {', '.join(transcribe.ENGINES)}"}
        )
    roles = [r.strip() for r in channel_roles.split(",") if r.strip()][:2]
    # Read one byte past the limit at most, so an oversized body is refused without being held whole.
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(
            413,
            detail={"error": "too_large", "message": f"The recording is larger than {MAX_BYTES // (1024 * 1024)} MB"},
        )
    try:
        return await store.save(
            uuid.UUID(tenant_id),
            filename=file.filename or "",
            mime_type=file.content_type or "",
            data=data,
            channel_roles=roles,
            language=language,
            engine=engine,
            created_by=_user_id(request) or None,
        )
    except SpeechError as exc:
        raise _refused(exc) from None


@router.get("/recordings")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.recordings.list",
)
async def list_recordings(
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=store.MAX_LIST)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The kept recordings, newest first, without their transcripts."""
    if not store.enabled():
        raise _off()
    if status is not None and status not in store.STATUSES:
        raise HTTPException(422, detail={"error": "status_unknown", "message": f"status is one of {store.STATUSES}"})
    rows = await store.list_recordings(uuid.UUID(tenant_id), status=status, limit=limit)
    return {"recordings": rows, "total": len(rows)}


@router.get("/recordings/{recording_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.recordings.read",
)
async def get_recording(recording_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One recording with its segments, speakers and transcript."""
    if not store.enabled():
        raise _off()
    found = await store.get_recording(uuid.UUID(tenant_id), recording_id)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such recording"})
    return found


@router.get("/recordings/{recording_id}/audio")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.recordings.audio",
)
async def get_audio(recording_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> Response:
    """The recording's audio as kept."""
    if not store.enabled():
        raise _off()
    try:
        data, mime = await store.audio_of(uuid.UUID(tenant_id), recording_id)
    except SpeechError as exc:
        raise _refused(exc) from None
    return Response(content=data, media_type=mime or "audio/wav")


@router.post("/recordings/{recording_id}/transcript")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="speech.recordings.transcript",
)
async def attach_transcript(
    recording_id: uuid.UUID, body: WordsIn, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Words transcribed elsewhere (text, start, end, optional confidence), aligned to the recording's speakers."""
    if not store.enabled():
        raise _off()
    try:
        return await store.attach_transcript(uuid.UUID(tenant_id), recording_id, body.words)
    except SpeechError as exc:
        raise _refused(exc) from None


@router.post("/recordings/{recording_id}/summary")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-lifecycle-state",
    audit_event="speech.recordings.summarise",
)
async def summarise_recording(
    recording_id: uuid.UUID,
    method: Annotated[str, Query(max_length=16)] = "auto",
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Summarise a transcribed recording (intent, key points, next actions, outcome) and compute its analytics.

    ``method`` is ``auto`` (the model, the words when it does not answer), ``model`` or ``extractive``.
    """
    if not store.enabled():
        raise _off()
    try:
        return await store.summarise(uuid.UUID(tenant_id), recording_id, method=method)
    except SpeechError as exc:
        raise _refused(exc) from None


@router.get("/recordings/{recording_id}/summary")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.recordings.summary",
)
async def get_summary(recording_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The kept summary and analytics of a recording."""
    if not store.enabled():
        raise _off()
    found = await store.get_recording(uuid.UUID(tenant_id), recording_id)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such recording"})
    return {"id": found["id"], "summary": found.get("summary"), "analytics": found.get("analytics") or {}}


@router.get("/analytics")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="speech.recordings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="speech.analytics",
)
async def analytics_overview(
    limit: Annotated[int, Query(ge=1, le=store.MAX_LIST)] = 200, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Sentiment, empathy, talk share and escalation signals averaged over the latest summarised recordings."""
    if not store.enabled():
        raise _off()
    return await store.overview(uuid.UUID(tenant_id), limit=limit)
