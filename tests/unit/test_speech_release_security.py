# SPDX-License-Identifier: Apache-2.0
"""Replay speech storage, stale-summary and async redaction release findings."""

import threading

import pytest

from core.speech import store
from core.speech.audio import SpeechError
from tests.unit.test_speech_redaction import TENANT, TRANSCRIPT_WORDS, _row, _Session, _use, _wav


@pytest.mark.asyncio
async def test_received_audio_is_not_persisted_as_plain_wav(monkeypatch):
    session = _Session()
    _use(monkeypatch, session)
    raw = _wav()
    await store.save(
        TENANT, filename="fixture.wav", mime_type="audio/wav", data=raw,
        channel_roles=[], language="en", engine="supplied", created_by=None,
    )
    assert session.rows[0].content != raw
    assert not bytes(session.rows[0].content).startswith(b"RIFF")
    assert await store.audio_of(TENANT, session.rows[0].id) == (raw, "audio/wav")


@pytest.mark.asyncio
async def test_summary_cannot_restore_evidence_from_replaced_transcript(monkeypatch):
    row = _row()
    _use(monkeypatch, _Session([row]))

    async def replaced(*args, **kwargs):
        row.transcript_encrypted = {"_encrypted": 'enc:{"turns": []}'}
        row.summary_encrypted = {}
        return {"method": "extractive", "outcome": "unresolved"}

    monkeypatch.setattr(store.summaries, "summarise", replaced)
    with pytest.raises(SpeechError) as caught:
        await store.summarise(TENANT, row.id, method="extractive")
    assert caught.value.status == 409
    assert caught.value.code == "transcript_conflict"
    assert row.summary_encrypted == {}


@pytest.mark.asyncio
async def test_supplied_transcript_audio_work_runs_off_loop_before_write_lock(monkeypatch):
    row = _row(transcript_encrypted={}, status="received")
    session = _Session([row])
    _use(monkeypatch, session, auto=True)
    loop_thread = threading.get_ident()
    seen = []
    original_load = store.load
    original_execute = session.execute
    locked = False

    async def execute(statement):
        nonlocal locked
        if "FOR UPDATE" in str(statement):
            locked = True
        return await original_execute(statement)

    def load(*args):
        seen.append((threading.get_ident(), locked))
        return original_load(*args)

    session.execute = execute
    monkeypatch.setattr(store, "load", load)
    await store.attach_transcript(TENANT, row.id, [dict(w, speaker=None) for w in TRANSCRIPT_WORDS])
    assert seen and all(thread != loop_thread and not lock for thread, lock in seen)


@pytest.mark.asyncio
async def test_redaction_refuses_a_concurrent_transcript_replacement(monkeypatch):
    row = _row()
    session = _Session([row])
    _use(monkeypatch, session)
    original_execute = session.execute

    async def execute(statement):
        if "FOR UPDATE" in str(statement):
            row.transcript_encrypted = {"_encrypted": 'enc:{"turns": []}'}
        return await original_execute(statement)

    session.execute = execute
    with pytest.raises(SpeechError) as caught:
        await store.redact(TENANT, row.id)
    assert caught.value.status == 409
    assert row.transcript_encrypted == {"_encrypted": 'enc:{"turns": []}'}


@pytest.mark.asyncio
async def test_redaction_invalidates_derived_analytics_and_keeps_audio_encrypted(monkeypatch):
    row = _row()
    row.analytics = {"scores": {"customer_sentiment": 0.5}, "signals": [{"kind": "old"}]}
    _use(monkeypatch, _Session([row]))
    result = await store.redact(TENANT, row.id)
    assert result["changed"] is True
    assert row.summary_encrypted == {} and row.analytics == {}
    assert not bytes(row.content).startswith(b"RIFF")
    playback, mime = await store.audio_of(TENANT, row.id)
    assert playback.startswith(b"RIFF") and mime == "audio/wav"
