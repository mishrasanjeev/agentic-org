# SPDX-License-Identifier: Apache-2.0
"""Speech, part 1: WAV decoding, who spoke when, words to speakers and turns, the store, the engines, the routes."""

from __future__ import annotations

import io
import uuid
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import HTTPException

from core.config import settings
from core.speech import audio, segments, store, transcribe
from core.speech.audio import SpeechError

TENANT = uuid.uuid4()
RATE = 16000


def tone(frequency: float, seconds: float, *, amplitude: float = 0.5, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def silence(seconds: float, *, rate: int = RATE) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float32)


def wav_bytes(channels: list[np.ndarray], *, rate: int = RATE, width: int = 2) -> bytes:
    frames = np.stack(channels, axis=1)
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(len(channels))
        handle.setsampwidth(width)
        handle.setframerate(rate)
        if width == 2:
            handle.writeframes(np.clip(frames * 32767, -32768, 32767).astype("<i2").tobytes())
        else:
            handle.writeframes((np.clip(frames * 127, -128, 127) + 128).astype(np.uint8).tobytes())
    return out.getvalue()


def stereo_call() -> bytes:
    """Channel 1 (agent) speaks 0.2-1.0 s and 2.4-3.0 s; channel 2 (customer) speaks 1.3-2.1 s."""
    agent = np.concatenate([silence(0.2), tone(220, 0.8), silence(1.4), tone(220, 0.6), silence(0.4)])
    customer = np.concatenate([silence(1.3), tone(1800, 0.8), silence(1.3)])
    customer = np.concatenate([customer, silence(len(agent) / RATE - len(customer) / RATE)])
    return wav_bytes([agent, customer[: len(agent)]])


def mono_call() -> bytes:
    """One channel: a low voice, a high voice, the low voice again, separated by pauses."""
    samples = np.concatenate(
        [silence(0.3), tone(200, 0.8), silence(0.6), tone(2200, 0.7), silence(0.6), tone(200, 0.9), silence(0.3)]
    )
    return wav_bytes([samples])


class TestAudio:
    def test_a_wav_decodes_to_float_channels_and_mono_mixes(self):
        recording = audio.load(stereo_call(), "audio/wav")
        assert recording.sample_rate == RATE and len(recording.channels) == 2
        assert 3.3 < recording.duration < 3.5 and recording.mono.shape == recording.channels[0].shape
        assert recording.to_dict()["channels"] == 2
        eight_bit = audio.decode_wav(wav_bytes([tone(440, 0.5)], width=1))
        assert len(eight_bit.channels) == 1 and abs(float(np.max(eight_bit.channels[0])) - 0.5) < 0.05
        again = audio.decode_wav(audio.encode_wav(recording))
        assert np.allclose(again.channels[1], recording.channels[1], atol=1e-3)
        assert len(audio.resample(tone(440, 1.0), RATE, 8000)) == 8000

    def test_bad_recordings_are_refused_with_a_reason(self):
        for data, mime, code in (
            (b"", "audio/wav", "empty_file"),
            (b"RIFFxxxxWAVEbroken", "audio/wav", "wav_unreadable"),
            (b"ID3" + b"\x00" * 20, "audio/mpeg", "format_unsupported"),
            (b"x" * (audio.MAX_BYTES + 1), "audio/wav", "too_large"),
        ):
            with pytest.raises(SpeechError) as info:
                audio.load(data, mime)
            assert info.value.code == code, code
        three = wav_bytes([tone(440, 0.2), tone(440, 0.2), tone(440, 0.2)])
        with pytest.raises(SpeechError) as info:
            audio.decode_wav(three)
        assert info.value.code == "channels_unsupported"


class TestDiarisation:
    def test_a_stereo_call_takes_its_speakers_from_the_channels(self):
        recording = audio.load(stereo_call(), "audio/wav")
        found = segments.diarise(recording, channel_roles=["agent", "customer"])
        assert [(s.speaker, round(s.start, 1), round(s.end, 1)) for s in found] == [
            ("agent", 0.2, 1.0),
            ("customer", 1.3, 2.1),
            ("agent", 2.4, 3.0),
        ]
        speakers = segments.speakers_of(found)
        assert speakers["agent"]["turns"] == 2 and speakers["customer"]["channel"] == 1
        assert abs(speakers["agent"]["share"] + speakers["customer"]["share"] - 1.0) < 0.01
        unnamed = segments.diarise(recording)
        assert {s.speaker for s in unnamed} == {"speaker_1", "speaker_2"}

    def test_a_mono_call_is_split_by_sound_into_two_speakers(self):
        recording = audio.load(mono_call(), "audio/wav")
        found = segments.diarise(recording, channel_roles=["agent", "customer"])
        assert [s.speaker for s in found] == ["agent", "customer", "agent"]
        assert found[0].start < 0.4 and found[1].start > 1.5 and all(s.channel == 0 for s in found)
        one = segments.diarise(
            audio.decode_wav(wav_bytes([np.concatenate([silence(0.2), tone(300, 1.0), silence(0.2)])]))
        )
        assert [s.speaker for s in one] == ["speaker_1"]
        same = segments.diarise(
            audio.decode_wav(wav_bytes([np.concatenate([tone(300, 0.6), silence(0.6), tone(300, 0.6)])]))
        )
        assert {s.speaker for s in same} == {"speaker_1"}  # the same voice twice is one speaker
        assert segments.diarise(audio.decode_wav(wav_bytes([silence(1.0)]))) == []

    def test_segment_helpers(self):
        rms = segments.frame_rms(np.concatenate([silence(0.1), tone(440, 0.1)]), RATE)
        assert len(rms) == 10 and rms[0] == 0 and rms[-1] > 0.3
        assert segments.active_frames(np.zeros(0)).tolist() == []
        assert segments.cluster_speakers(np.zeros(10), RATE, []) == []
        assert segments.Segment("a", 1.0, 2.5).to_dict()["duration"] == 1.5


class TestTranscript:
    def test_words_take_the_speaker_of_their_segment_and_group_into_turns(self):
        found = [
            segments.Segment("agent", 0.2, 1.0),
            segments.Segment("customer", 1.3, 2.1),
            segments.Segment("agent", 2.4, 3.0),
        ]
        words = transcribe.check_words(
            [
                {"text": "Hello", "start": 0.25, "end": 0.5, "confidence": 0.9},
                {"text": "there", "start": 0.55, "end": 0.9},
                {"word": "Hi", "start": 1.4, "end": 1.6, "confidence": "0.8"},
                {"text": "balance", "start": 1.7, "end": 2.0},
                {"text": "Sure", "start": 2.15, "end": 2.3},  # between segments: nearest is the customer's end
                {"text": "one", "start": 2.5, "end": 2.7},
                {"text": "moment", "start": 2.75, "end": 2.95},
            ]
        )
        transcript = transcribe.transcript_of(words, found)
        assert [(t["speaker"], t["text"]) for t in transcript["turns"]] == [
            ("agent", "Hello there"),
            ("customer", "Hi balance Sure"),
            ("agent", "one moment"),
        ]
        assert transcript["text"].startswith("agent: Hello there\ncustomer:")
        assert transcript["word_count"] == 7 and 0.9 < transcript["confidence"] <= 1.0
        assert transcript["turns"][0]["confidence"] == 0.95
        assert transcribe.transcript_of([], found) == {
            "words": [],
            "turns": [],
            "text": "",
            "word_count": 0,
            "confidence": None,
        }
        assert transcribe.assign_speakers([transcribe.Word("x", 0, 1)], [])[0].speaker is None

    def test_a_long_pause_ends_a_turn_and_bad_words_are_refused(self):
        words = [transcribe.Word("a", 0.0, 0.2, speaker="s"), transcribe.Word("b", 2.0, 2.2, speaker="s")]
        assert len(transcribe.turns_of(words)) == 2
        for bad in (
            "x",
            [{"text": ""}],
            [{"text": "a", "start": 1, "end": 0.5}],
            [{"text": "a", "start": "x", "end": 1}],
            [1],
        ):
            with pytest.raises(SpeechError) as info:
                transcribe.check_words(bad)
            assert info.value.status == 422

    @pytest.mark.asyncio
    async def test_engines_answer_or_say_why_not(self, monkeypatch):
        recording = audio.load(mono_call(), "audio/wav")
        assert transcribe.engines_available()["supplied"] is True
        assert await transcribe.transcribe(TENANT, recording, engine="supplied") == []
        monkeypatch.setattr(transcribe, "whisper_available", lambda: False)
        with pytest.raises(SpeechError) as info:
            await transcribe.transcribe(TENANT, recording, engine="faster_whisper")
        assert info.value.code == "engine_unavailable"
        with pytest.raises(SpeechError) as info:
            await transcribe.transcribe(TENANT, recording, engine="nothing")
        assert info.value.code == "engine_unknown"

    @pytest.mark.asyncio
    async def test_the_deepgram_adapter_sends_the_audio_and_reads_the_words(self, monkeypatch):
        import httpx

        from core.ai_providers import resolver

        recording = audio.load(mono_call(), "audio/wav")
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            seen["params"] = dict(request.url.params)
            seen["length"] = len(request.content)
            payload = {
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {
                                    "words": [
                                        {
                                            "word": "hello",
                                            "punctuated_word": "Hello,",
                                            "start": 0.3,
                                            "end": 0.6,
                                            "confidence": 0.98,
                                            "speaker": 0,
                                        },
                                        {"word": "there", "start": 0.7, "end": 1.0, "confidence": 0.9, "speaker": 0},
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
        monkeypatch.setattr(
            resolver,
            "get_provider_credential",
            AsyncMock(
                return_value=resolver.ResolvedCredential(
                    secret="dg-key", provider="stt_deepgram", kind="stt", source="tenant"
                )
            ),
        )
        words = await transcribe.transcribe(TENANT, recording, engine="deepgram", language="en")
        assert [w.text for w in words] == ["Hello,", "there"] and words[0].speaker == "provider_0"
        assert seen["auth"] == "Token dg-key" and seen["params"]["diarize"] == "true" and seen["length"] > 1000

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw),
        )
        with pytest.raises(SpeechError) as info:
            await transcribe.transcribe(TENANT, recording, engine="deepgram")
        assert info.value.code == "engine_failed"
        monkeypatch.setattr(
            resolver, "get_provider_credential", AsyncMock(side_effect=resolver.ProviderNotConfigured("none"))
        )
        with pytest.raises(SpeechError) as info:
            await transcribe.transcribe(TENANT, recording, engine="deepgram")
        assert info.value.code == "engine_not_configured"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def execute(self, statement):
        return _Result(self.rows)

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)
    monkeypatch.setattr(store, "encrypt_for_tenant", AsyncMock(side_effect=lambda text, tenant: "enc:" + text))
    monkeypatch.setattr(store, "decrypt_for_tenant", lambda text: text[4:])


class TestStore:
    @pytest.mark.asyncio
    async def test_a_recording_is_kept_with_its_speakers_and_an_attached_transcript_is_encrypted(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        kept = await store.save(
            TENANT,
            filename="call.wav",
            mime_type="audio/wav",
            data=stereo_call(),
            channel_roles=["agent", "customer"],
            language="en",
            engine="supplied",
            created_by="u1",
        )
        assert (
            kept["status"] == "received"
            and kept["channels"] == 2
            and kept["segments"] == 3
            and kept["transcript"] is None
        )
        assert kept["speakers"]["agent"]["turns"] == 2 and kept["channel_roles"] == ["agent", "customer"]
        row = session.rows[0]
        assert row.transcript_encrypted == {} and row.content == stereo_call()
        attached = await store.attach_transcript(
            TENANT, row.id, [{"text": "Hello", "start": 0.3, "end": 0.6}, {"text": "Hi", "start": 1.5, "end": 1.7}]
        )
        assert attached["status"] == "transcribed" and attached["engine"] == "supplied"
        assert [t["speaker"] for t in attached["transcript"]["turns"]] == ["agent", "customer"]
        assert (
            row.transcript_encrypted["_encrypted"].startswith("enc:")
            and "Hello" in row.transcript_encrypted["_encrypted"]
        )
        listed = await store.list_recordings(TENANT, limit=5)
        assert listed[0]["id"] == str(row.id) and "transcript" not in listed[0]
        assert (await store.get_recording(TENANT, row.id))["transcript"]["word_count"] == 2
        data, mime = await store.audio_of(TENANT, row.id)
        assert data == stereo_call() and mime == "audio/wav"
        session.rows = []
        assert await store.get_recording(TENANT, uuid.uuid4()) is None
        with pytest.raises(SpeechError):
            await store.attach_transcript(TENANT, uuid.uuid4(), [{"text": "x", "start": 0, "end": 1}])
        with pytest.raises(SpeechError):
            await store.audio_of(TENANT, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_an_engine_that_fails_leaves_the_recording_kept_and_the_failure_named(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        monkeypatch.setattr(
            transcribe, "transcribe", AsyncMock(side_effect=SpeechError(503, "engine_unavailable", "not installed"))
        )
        kept = await store.save(
            TENANT,
            filename="a.wav",
            mime_type="audio/wav",
            data=mono_call(),
            channel_roles=[],
            language="en",
            engine="faster_whisper",
            created_by=None,
        )
        assert (
            kept["status"] == "failed" and kept["last_error"].startswith("engine_unavailable") and kept["segments"] == 3
        )
        monkeypatch.setattr(transcribe, "transcribe", AsyncMock(return_value=[transcribe.Word("hello", 0.4, 0.8, 0.9)]))
        kept = await store.save(
            TENANT,
            filename="b.wav",
            mime_type="audio/wav",
            data=mono_call(),
            channel_roles=["agent", "customer"],
            language="en",
            engine="deepgram",
            created_by=None,
        )
        assert kept["status"] == "transcribed" and kept["transcript"]["turns"][0]["speaker"] == "agent"
        row = session.rows[-1]
        assert transcribe.transcribe.call_args.kwargs["engine"] == "deepgram"
        row.transcript_encrypted = {"_encrypted": "enc:not json"}
        assert store.transcript_of_row(row) is None


class TestEnforcement:
    def test_the_speech_routes_map_onto_enforced_scopes(self):
        from api.route_enforcement import GRANTABLE_ROUTE_SCOPES, SCOPE_FAMILIES, required_scopes_for

        assert SCOPE_FAMILIES["speech"] == ("audit:read", "approvals:write")
        assert required_scopes_for("speech.recordings.sensitive.read", "GET") == ("audit:read",)
        assert required_scopes_for("speech.recordings.sensitive.write", "POST") == ("approvals:write",)
        assert {"audit:read", "approvals:write"} <= GRANTABLE_ROUTE_SCOPES

    @pytest.mark.asyncio
    async def test_the_upload_is_bounded_before_it_is_read(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        monkeypatch.setattr(store, "save", AsyncMock(return_value={"id": "r"}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        read = AsyncMock(return_value=b"x" * (audio.MAX_BYTES + 1))
        upload = SimpleNamespace(filename="a.wav", content_type="audio/wav", read=read)
        with pytest.raises(HTTPException) as info:
            await api.upload_recording(
                upload, request, channel_roles="", language="en", engine=None, tenant_id=str(TENANT)
            )
        assert info.value.status_code == 413 and read.call_args.args == (audio.MAX_BYTES + 1,)
        assert store.save.call_count == 0

    @pytest.mark.asyncio
    async def test_local_inference_runs_in_a_worker_thread(self, monkeypatch):
        import threading

        seen: dict[str, object] = {}

        def fake_whisper(recording, *, language="en"):
            seen["thread"] = threading.current_thread() is not threading.main_thread()
            return [transcribe.Word("hello", 0.1, 0.4)]

        monkeypatch.setattr(transcribe, "whisper_available", lambda: True)
        monkeypatch.setattr(transcribe, "transcribe_whisper", fake_whisper)
        words = await transcribe.transcribe(TENANT, audio.load(mono_call(), "audio/wav"), engine="faster_whisper")
        assert [w.text for w in words] == ["hello"] and seen["thread"] is True


class TestRoutes:
    @pytest.mark.asyncio
    async def test_status_answers_off_and_the_rest_is_not_found(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", False)
        answer = await api.status(tenant_id=str(TENANT))
        assert answer["enabled"] is False and answer["limits"]["formats"] == ["audio/wav"]
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        upload = SimpleNamespace(filename="a.wav", content_type="audio/wav", read=AsyncMock(return_value=b""))
        for call in (
            api.upload_recording(upload, request, channel_roles="", language="en", engine=None, tenant_id=str(TENANT)),
            api.list_recordings(status=None, limit=10, tenant_id=str(TENANT)),
            api.get_recording(uuid.uuid4(), tenant_id=str(TENANT)),
            api.get_audio(uuid.uuid4(), tenant_id=str(TENANT)),
            api.attach_transcript(
                uuid.uuid4(), api.WordsIn(words=[{"text": "a", "start": 0, "end": 1}]), tenant_id=str(TENANT)
            ),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_routes_serve_the_store(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        monkeypatch.setattr(store, "save", AsyncMock(return_value={"id": "r", "status": "received"}))
        monkeypatch.setattr(store, "list_recordings", AsyncMock(return_value=[{"id": "r"}]))
        monkeypatch.setattr(store, "get_recording", AsyncMock(return_value={"id": "r"}))
        monkeypatch.setattr(store, "audio_of", AsyncMock(return_value=(b"RIFF", "audio/wav")))
        monkeypatch.setattr(store, "attach_transcript", AsyncMock(return_value={"id": "r", "status": "transcribed"}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        upload = SimpleNamespace(filename="a.wav", content_type="audio/wav", read=AsyncMock(return_value=b"RIFF"))
        out = await api.upload_recording(
            upload, request, channel_roles="agent, customer", language="hi", engine="supplied", tenant_id=str(TENANT)
        )
        assert out["id"] == "r" and store.save.call_args.kwargs["channel_roles"] == ["agent", "customer"]
        assert store.save.call_args.kwargs["language"] == "hi" and store.save.call_args.kwargs["created_by"] == "u1"
        with pytest.raises(HTTPException) as info:
            await api.upload_recording(
                upload, request, channel_roles="", language="en", engine="bogus", tenant_id=str(TENANT)
            )
        assert info.value.status_code == 422
        assert (await api.list_recordings(status="received", limit=10, tenant_id=str(TENANT)))["total"] == 1
        with pytest.raises(HTTPException) as info:
            await api.list_recordings(status="lost", limit=10, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        assert (await api.get_recording(uuid.uuid4(), tenant_id=str(TENANT)))["id"] == "r"
        response = await api.get_audio(uuid.uuid4(), tenant_id=str(TENANT))
        assert response.media_type == "audio/wav" and response.body == b"RIFF"
        assert (
            await api.attach_transcript(
                uuid.uuid4(), api.WordsIn(words=[{"text": "a", "start": 0, "end": 1}]), tenant_id=str(TENANT)
            )
        )["status"] == "transcribed"
        monkeypatch.setattr(store, "get_recording", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_recording(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(store, "save", AsyncMock(side_effect=SpeechError(415, "format_unsupported", "wav only")))
        with pytest.raises(HTTPException) as info:
            await api.upload_recording(
                upload, request, channel_roles="", language="en", engine=None, tenant_id=str(TENANT)
            )
        assert info.value.status_code == 415 and info.value.detail["error"] == "format_unsupported"
