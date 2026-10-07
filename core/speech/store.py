# SPDX-License-Identifier: Apache-2.0
"""Kept recordings: the audio, its segments and speakers, and the transcript encrypted at rest.

A recording row keeps the WAV, what the diariser found (segments and
speakers, plain, as they hold no words) and the transcript under the
tenant's key as the voice runtime keeps call transcripts
(``{"_encrypted": ...}``), so a database read never yields speech in
clear. Tenant scoped under row-level security.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_for_tenant
from core.speech import analytics as call_analytics
from core.speech import segments as diarisation
from core.speech import summary as summaries
from core.speech import transcribe as engines
from core.speech.audio import Recording, SpeechError, load

logger = structlog.get_logger()

STATUSES = ("received", "transcribed", "failed")
MAX_LIST = 200


def enabled() -> bool:
    return bool(getattr(settings, "speech_intelligence_enabled", False))


def summary_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "duration_seconds": row.duration_seconds,
        "sample_rate": row.sample_rate,
        "channels": row.channels,
        "channel_roles": list(row.channel_roles or []),
        "status": row.status,
        "engine": row.engine,
        "language": row.language,
        "speakers": dict(row.speakers or {}),
        "segments": len(row.segments or []),
        "summarised": bool(row.summary_encrypted),
        "scores": dict((row.analytics or {}).get("scores") or {}),
        "last_error": row.last_error,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def transcript_of_row(row: Any) -> dict[str, Any] | None:
    """The transcript in clear, from its encrypted envelope; None when there is none or it cannot be read."""
    envelope = row.transcript_encrypted if isinstance(row.transcript_encrypted, dict) else {}
    ciphertext = envelope.get("_encrypted")
    if not ciphertext:
        return None
    try:
        return json.loads(decrypt_for_tenant(str(ciphertext)))
    except (ValueError, TypeError) as exc:
        logger.warning("speech_transcript_unreadable", error_type=type(exc).__name__)
        return None


def _decrypt(envelope: Any, what: str) -> dict[str, Any] | None:
    ciphertext = envelope.get("_encrypted") if isinstance(envelope, dict) else None
    if not ciphertext:
        return None
    try:
        return json.loads(decrypt_for_tenant(str(ciphertext)))
    except (ValueError, TypeError) as exc:
        logger.warning("speech_envelope_unreadable", what=what, error_type=type(exc).__name__)
        return None


def summary_of_row(row: Any) -> dict[str, Any] | None:
    return _decrypt(getattr(row, "summary_encrypted", None), "summary")


def detail_dict(row: Any) -> dict[str, Any]:
    return {
        **summary_dict(row),
        "segments_detail": list(row.segments or []),
        "transcript": transcript_of_row(row),
        "summary": summary_of_row(row),
        "analytics": dict(row.analytics or {}),
    }


async def _encrypt(tenant_id: uuid.UUID, transcript: dict[str, Any]) -> dict[str, Any]:
    return {"_encrypted": await encrypt_for_tenant(json.dumps(transcript, ensure_ascii=False), tenant_id)}


async def save(
    tenant_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
    channel_roles: list[str],
    language: str,
    engine: str | None,
    created_by: str | None,
) -> dict[str, Any]:
    """Decode, diarise, transcribe where an engine is named and available, and keep the recording."""
    from core.database import get_tenant_session
    from core.models.speech_recording import SpeechRecording

    # Decoding and the signal work over a two-hour recording are CPU bound: off the event loop.
    recording: Recording = await asyncio.to_thread(load, data, mime_type)
    found = await asyncio.to_thread(diarisation.diarise, recording, channel_roles=channel_roles)
    row = SpeechRecording(
        tenant_id=tenant_id,
        filename=(filename or "recording.wav")[:255],
        mime_type=(mime_type or "audio/wav")[:100],
        size_bytes=len(data),
        content=data,
        duration_seconds=round(recording.duration, 3),
        sample_rate=recording.sample_rate,
        channels=len(recording.channels),
        channel_roles=[r[:32] for r in channel_roles][:2],
        status="received",
        engine=engine,
        language=(language or "en")[:16],
        segments=[s.to_dict() for s in found],
        speakers=diarisation.speakers_of(found),
        transcript_encrypted={},
        summary_encrypted={},
        analytics={},
        created_by=(created_by or None),
    )
    if engine and engine != "supplied":
        try:
            words = await engines.transcribe(tenant_id, recording, engine=engine, language=language)
            row.transcript_encrypted = await _encrypt(tenant_id, engines.transcript_of(words, found))
            row.status = "transcribed"
        except SpeechError as exc:
            row.status = "failed"
            row.last_error = f"{exc.code}: {exc.message}"[:500]
            logger.warning("speech_transcription_failed", engine=engine, code=exc.code)
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        answer = detail_dict(row)
    logger.info(
        "speech_recording_kept", status=answer["status"], channels=answer["channels"], segments=answer["segments"]
    )
    return answer


async def _row(session: Any, tenant_id: uuid.UUID, recording_id: uuid.UUID, *, lock: bool = False) -> Any:
    from core.models.speech_recording import SpeechRecording

    statement = select(SpeechRecording).where(
        SpeechRecording.tenant_id == tenant_id, SpeechRecording.id == recording_id
    )
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def list_recordings(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.speech_recording import SpeechRecording

    statement = select(SpeechRecording).where(SpeechRecording.tenant_id == tenant_id)
    if status:
        statement = statement.where(SpeechRecording.status == status)
    statement = statement.order_by(SpeechRecording.created_at.desc()).limit(max(1, min(limit, MAX_LIST)))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return [summary_dict(row) for row in rows]


async def get_recording(tenant_id: uuid.UUID, recording_id: uuid.UUID) -> dict[str, Any] | None:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id)
        return detail_dict(row) if row is not None else None


async def attach_transcript(tenant_id: uuid.UUID, recording_id: uuid.UUID, raw_words: Any) -> dict[str, Any]:
    """Words a caller supplies, aligned to the kept segments and stored as the recording's transcript.

    The segments are read and the transcript encrypted before the row is locked, so the tenant key
    lookup never runs while a connection holds a lock.
    """
    from core.database import get_tenant_session

    words = engines.check_words(raw_words)
    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id)
        if row is None:
            raise SpeechError(404, "not_found", "No such recording")
        kept_segments = list(row.segments or [])
    found = [
        diarisation.Segment(
            speaker=s["speaker"], start=float(s["start"]), end=float(s["end"]), channel=int(s.get("channel", 0))
        )
        for s in kept_segments
    ]
    envelope = await _encrypt(tenant_id, engines.transcript_of(words, found))
    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id, lock=True)
        if row is None:
            raise SpeechError(404, "not_found", "No such recording")
        row.transcript_encrypted = envelope
        row.status = "transcribed"
        row.engine = "supplied"
        row.last_error = None
        # A new transcript makes the summary and the analytics of the old one stale: they go with it.
        row.summary_encrypted = {}
        row.analytics = {}
        row.updated_at = datetime.now(UTC)
        answer = detail_dict(row)
    logger.info("speech_transcript_attached", words=len(words))
    return answer


async def audio_of(tenant_id: uuid.UUID, recording_id: uuid.UUID) -> tuple[bytes, str]:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id)
        if row is None:
            raise SpeechError(404, "not_found", "No such recording")
        return bytes(row.content), row.mime_type or "audio/wav"


def _roles(row: Any) -> tuple[str | None, str | None]:
    """The agent and the customer among the speakers: by a recognisable label first, by channel position only when
    neither label says which is which, so ``channel_roles=customer,agent`` is read as it was meant."""
    names = list((row.speakers or {}).keys())
    roles = [str(r) for r in (row.channel_roles or [])]
    labelled = any(r.lower() in call_analytics.AGENT_NAMES or r.lower() in call_analytics.CUSTOMER_NAMES for r in roles)
    if labelled:
        return call_analytics.roles_of(names)
    return call_analytics.roles_of(
        names, agent=roles[0] if roles else None, customer=roles[1] if len(roles) > 1 else None
    )


async def summarise(
    tenant_id: uuid.UUID, recording_id: uuid.UUID, *, method: str = "auto", complete: Any = None
) -> dict[str, Any]:
    """Summarise a transcribed recording and compute its analytics; both are kept, the summary encrypted.

    The transcript is read, the summary made and encrypted before the row is locked, so neither the
    model call nor the tenant key lookup runs while a connection holds a lock.
    """
    from core.database import get_tenant_session

    if method not in summaries.METHODS:
        raise SpeechError(422, "method_unknown", f"method is one of {', '.join(summaries.METHODS)}")
    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id)
        if row is None:
            raise SpeechError(404, "not_found", "No such recording")
        transcript = transcript_of_row(row)
        kept_segments = list(row.segments or [])
        duration = float(row.duration_seconds or 0.0)
        agent, customer = _roles(row)
    if not transcript or not transcript.get("turns"):
        raise SpeechError(409, "not_transcribed", "The recording has no transcript to summarise yet")
    try:
        summary = await summaries.summarise(
            tenant_id, transcript, method=method, agent=agent, customer=customer, complete=complete
        )
    except ValueError as exc:
        raise SpeechError(422, "method_unknown", str(exc)) from None
    # enterprise-gate: broad-except-ok reason=model-boundary-refused-with-an-explicit-error-nothing-kept
    except Exception as exc:  # noqa: BLE001 - the model boundary when the caller insisted on the model
        logger.warning("speech_summary_failed", error_type=type(exc).__name__)
        raise SpeechError(502, "summary_failed", "The model did not produce a summary") from None
    analytics = call_analytics.analyse(transcript, kept_segments, duration, agent=agent, customer=customer)
    envelope = await _encrypt(tenant_id, summary)
    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, recording_id, lock=True)
        if row is None:
            raise SpeechError(404, "not_found", "No such recording")
        row.summary_encrypted = envelope
        row.analytics = analytics
        row.updated_at = datetime.now(UTC)
        answer = {"id": str(row.id), "summary": summary, "analytics": analytics}
    logger.info("speech_recording_summarised", method=summary.get("method"), outcome=summary.get("outcome"))
    return answer


async def overview(tenant_id: uuid.UUID, *, limit: int = 200) -> dict[str, Any]:
    """The analytics of the latest summarised recordings, averaged: sentiment, empathy, talk share and signals."""
    from core.database import get_tenant_session
    from core.models.speech_recording import SpeechRecording

    statement = (
        select(SpeechRecording)
        .where(SpeechRecording.tenant_id == tenant_id)
        .order_by(SpeechRecording.created_at.desc())
        .limit(max(1, min(limit, MAX_LIST)))
    )
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    scored = [dict(r.analytics or {}) for r in rows if (r.analytics or {}).get("scores")]
    if not scored:
        return {"recordings": len(rows), "analysed": 0, "averages": {}, "escalation_risk": {}, "signals": {}}

    def mean(key: str) -> float | None:
        values = [float(a["scores"][key]) for a in scored if a["scores"].get(key) is not None]
        return round(sum(values) / len(values), 3) if values else None

    risk: dict[str, int] = {}
    signals: dict[str, int] = {}
    for item in scored:
        risk[item["scores"].get("escalation_risk", "low")] = (
            risk.get(item["scores"].get("escalation_risk", "low"), 0) + 1
        )
        for signal in item.get("signals") or []:
            signals[signal["kind"]] = signals.get(signal["kind"], 0) + 1
    return {
        "recordings": len(rows),
        "analysed": len(scored),
        "averages": {
            "customer_sentiment": mean("customer_sentiment"),
            "empathy": mean("empathy"),
            "customer_talk_share": mean("customer_talk_share"),
        },
        "escalation_risk": risk,
        "signals": signals,
    }
