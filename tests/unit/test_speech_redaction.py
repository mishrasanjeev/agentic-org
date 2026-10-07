# SPDX-License-Identifier: Apache-2.0
"""Speech, part 4: spoken card numbers, codes, CVVs and PINs found, cut from the transcript, silenced in the audio."""

from __future__ import annotations

import io
import json
import uuid
import wave
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import HTTPException

from core.config import settings
from core.speech import assist, audio, redaction, store, transcribe
from core.speech.audio import SpeechError
from core.workbench import console

TENANT = uuid.uuid4()
RATE = 16000


def _words(*items: tuple[str, float]) -> list[dict]:
    out = []
    for index, (text, start) in enumerate(items):
        out.append(
            {
                "text": text,
                "start": start,
                "end": start + 0.4,
                "confidence": 0.9,
                "speaker": "customer" if index % 2 else "agent",
            }
        )
    return out


class TestFinder:
    def test_spoken_digits_and_runs(self):
        assert (
            redaction.spoken_digits("4111") == "4111"
            and redaction.spoken_digits("four") == "4"
            and redaction.spoken_digits("oh") == "0"
        )
        assert redaction.spoken_digits("4111-1111") == "41111111" and redaction.spoken_digits("hello") is None
        assert redaction.spoken_digits("five", multiplier=2) == "55"
        words = _words(
            ("my", 0),
            ("card", 0.5),
            ("is", 1),
            ("four", 1.5),
            ("one", 2),
            ("double", 2.5),
            ("one", 3),
            ("1111", 3.5),
            ("1111", 4),
            ("1111", 4.5),
            ("ok", 5),
        )
        runs = redaction.digit_runs(words)
        assert runs == [(3, 9, "4111111111111111")]
        assert redaction.luhn("4111111111111111") is True and redaction.luhn("4111111111111112") is False
        assert redaction.digit_runs(_words(("triple", 0), ("seven", 0.5))) == [(0, 1, "777")]
        assert redaction.digit_runs(_words(("double", 0), ("trouble", 0.5))) == []
        spelled = _words(
            ("4111", 0),
            ("dash", 0.5),
            ("1111", 1),
            ("-", 1.5),
            ("1111", 2),
            ("hyphen", 2.5),
            ("1111", 3),
            ("dash", 3.5),
            ("ok", 4),
        )
        assert redaction.digit_runs(spelled) == [(0, 6, "4111111111111111")]  # separators inside a run, not after it
        assert [s.kind for s in redaction.find_spans(spelled)] == ["card"]

    def test_each_kind_is_judged_by_length_cue_and_check(self):
        words = _words(
            ("the", 0),
            ("card", 0.5),
            ("number", 1),
            ("is", 1.5),
            ("4111", 2),
            ("1111", 2.5),
            ("1111", 3),
            ("1111", 3.5),
            ("and", 4),
            ("the", 4.5),
            ("otp", 5),
            ("is", 5.5),
            ("four", 6),
            ("five", 6.5),
            ("six", 7),
            ("seven", 7.5),
            ("cvv", 8),
            ("is", 8.5),
            ("123", 9),
            ("my", 9.5),
            ("pin", 10),
            ("is", 10.5),
            ("9876", 11),
            ("i", 11.5),
            ("paid", 12),
            ("2500", 12.5),
            ("on", 13),
            ("the", 13.5),
            ("12th", 14),
        )
        spans = redaction.find_spans(words)
        assert [(s.kind, s.first, s.last, s.digits) for s in spans] == [
            ("card", 4, 7, 16),
            ("otp", 12, 15, 4),
            ("cvv", 18, 18, 3),
            ("pin", 22, 22, 4),
        ]
        assert spans[0].start == 2.0 and spans[0].end == 3.9 and spans[0].to_dict()["words"] == 4
        assert "digits" not in json.dumps([s.to_dict() for s in spans]).replace('"digits": ', "")  # counts only
        only = redaction.find_spans(words, kinds=["otp"])
        assert [s.kind for s in only] == ["otp"]
        assert (
            redaction.find_spans(_words(("4111", 0), ("1111", 0.5), ("1111", 1), ("1112", 1.5))) == []
        )  # no Luhn, no cue
        assert [
            s.kind
            for s in redaction.find_spans(
                _words(("card", 0), ("is", 0.5), ("4111", 1), ("1111", 1.5), ("1111", 2), ("1112", 2.5))
            )
        ] == ["card"]


class TestRedaction:
    def test_the_transcript_is_rewritten_with_markers(self):
        words = _words(
            ("card", 0), ("number", 0.5), ("4111", 1), ("1111", 1.5), ("1111", 2), ("1111", 2.5), ("thanks", 3)
        )
        transcript = transcribe.transcript_of([transcribe.Word(**w) for w in words], [])
        cleaned, spans = redaction.redact_transcript(transcript)
        assert len(spans) == 1 and cleaned["word_count"] == 4
        texts = [w["text"] for w in cleaned["words"]]
        assert texts == ["card", "number", "[CARD ****1111]", "thanks"] and cleaned["words"][2]["redacted"] == "card"
        assert cleaned["words"][2]["start"] == 1.0 and cleaned["words"][2]["end"] == 2.9
        assert "4111" not in cleaned["text"] and cleaned["redacted"][0]["kind"] == "card"
        same, none = redaction.redact_transcript({"words": [], "turns": []})
        assert none == [] and same == {"words": [], "turns": []}
        marker = redaction.marker(redaction.Span("otp", 0, 0, 0.0, 0.4, 4))
        assert marker == "[OTP REDACTED]"

    def test_plain_text_is_masked_for_live_turns(self):
        text, cut = redaction.redact_text("the otp is 4 5 6 7 please confirm")
        assert text == "the otp is [OTP REDACTED] please confirm" and cut == [{"kind": "otp", "digits": 4}]
        assert redaction.redact_text("nothing here") == ("nothing here", [])

    def test_the_audio_is_silenced_over_the_spans_with_padding(self):
        samples = np.ones(RATE * 3, dtype=np.float32) * 0.5
        recording = audio.Recording(sample_rate=RATE, channels=[samples, samples.copy()])
        spans = [redaction.Span("card", 0, 0, 1.0, 2.0, 16)]
        silenced = redaction.silence(recording, spans)
        for channel in silenced.channels:
            assert channel[int(0.5 * RATE)] == 0.5 and channel[int(1.5 * RATE)] == 0.0
            assert channel[int(0.9 * RATE)] == 0.0 and channel[int(0.8 * RATE)] == 0.5  # padded by 0.15 s
            assert channel[int(2.1 * RATE)] == 0.0 and channel[int(2.3 * RATE)] == 0.5
        assert recording.channels[0][int(1.5 * RATE)] == 0.5  # the original is untouched


def _wav(seconds: float = 3.0) -> bytes:
    samples = (0.5 * np.ones(int(RATE * seconds))).astype(np.float32)
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((samples * 32767).astype("<i2").tobytes())
    return out.getvalue()


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
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session, *, auto=False):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)
    monkeypatch.setattr(store, "encrypt_for_tenant", AsyncMock(side_effect=lambda text, tenant: "enc:" + text))
    monkeypatch.setattr(store, "decrypt_for_tenant", lambda text: text[4:])
    values = {"speech.redaction_kinds": list(redaction.KINDS), "speech.redact_on_transcription": auto}
    monkeypatch.setattr(console, "value", AsyncMock(side_effect=lambda tenant, key: values[key]))


TRANSCRIPT_WORDS = _words(
    ("card", 0.2), ("number", 0.6), ("4111", 1.0), ("1111", 1.4), ("1111", 1.8), ("1111", 2.2), ("thanks", 2.6)
)


def _row(**kw):
    from core.models.speech_recording import SpeechRecording

    transcript = transcribe.transcript_of([transcribe.Word(**w) for w in TRANSCRIPT_WORDS], [])
    base = {
        "tenant_id": TENANT,
        "filename": "call.wav",
        "mime_type": "audio/wav",
        "size_bytes": 10,
        "content": _wav(),
        "duration_seconds": 3.0,
        "sample_rate": RATE,
        "channels": 1,
        "channel_roles": ["agent", "customer"],
        "status": "transcribed",
        "engine": "supplied",
        "language": "en",
        "segments": [],
        "speakers": {"agent": {"turns": 1}},
        "transcript_encrypted": {"_encrypted": "enc:" + json.dumps(transcript)},
        "summary_encrypted": {"_encrypted": "enc:{}"},
        "analytics": {},
        "redactions": [],
        "redacted_at": None,
    }
    base.update(kw)
    row = SpeechRecording(**base)
    row.id = uuid.uuid4()
    return row


class TestStore:
    @pytest.mark.asyncio
    async def test_a_recording_is_redacted_in_audio_and_transcript_and_the_summary_dropped(self, monkeypatch):
        row = _row()
        session = _Session([row])
        _use(monkeypatch, session)
        preview = await store.redact(TENANT, row.id, dry_run=True)
        assert (
            preview["changed"] is False and [s["kind"] for s in preview["spans"]] == ["card"] and row.redactions == []
        )
        done = await store.redact(TENANT, row.id)
        assert done["changed"] is True and row.redactions[0]["kind"] == "card" and row.redacted_at is not None
        assert row.summary_encrypted == {} and "4111" not in row.transcript_encrypted["_encrypted"]
        assert "[CARD ****1111]" in row.transcript_encrypted["_encrypted"]
        silenced = audio.decode_wav(bytes(row.content))
        assert silenced.channels[0][int(1.5 * RATE)] == 0.0 and silenced.channels[0][int(0.3 * RATE)] > 0.4
        assert row.size_bytes == len(row.content)
        detail = await store.get_recording(TENANT, row.id)
        assert (
            detail["redactions"][0]["kind"] == "card"
            and detail["redacted_at"]
            and detail["transcript"]["redacted"][0]["kind"] == "card"
        )
        again = await store.redact(TENANT, row.id)
        assert again["changed"] is False and len(row.redactions) == 1
        with pytest.raises(SpeechError) as info:
            await store.redact(TENANT, row.id, kinds=["passport"])
        assert info.value.code == "kind_unknown"
        _use(monkeypatch, _Session([_row(transcript_encrypted={})]))
        with pytest.raises(SpeechError) as info:
            await store.redact(TENANT, session.rows[0].id)
        assert info.value.code == "not_transcribed"
        _use(monkeypatch, _Session([]))
        with pytest.raises(SpeechError) as info:
            await store.redact(TENANT, uuid.uuid4())
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_an_empty_kinds_list_cuts_nothing_and_a_concurrent_redaction_is_refused(self, monkeypatch):
        row = _row()
        session = _Session([row])
        _use(monkeypatch, session)
        nothing = await store.redact(TENANT, row.id, kinds=[])
        assert nothing["changed"] is False and nothing["kinds"] == [] and row.redactions == []
        real_execute = session.execute
        bumped = {"done": False}

        async def racing_execute(statement):
            result = await real_execute(statement)
            if "FOR UPDATE" in str(statement) and not bumped["done"]:
                bumped["done"] = True
                row.redactions = [{"kind": "otp"}]  # another redaction landed between the read and the lock
            return result

        session.execute = racing_execute
        with pytest.raises(SpeechError) as info:
            await store.redact(TENANT, row.id)
        assert info.value.code == "redaction_conflict" and "4111" in row.transcript_encrypted["_encrypted"]

    @pytest.mark.asyncio
    async def test_redaction_at_transcription_follows_the_console(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session, auto=True)
        words = [transcribe.Word(**w) for w in TRANSCRIPT_WORDS]
        monkeypatch.setattr(transcribe, "transcribe", AsyncMock(return_value=words))
        kept = await store.save(
            TENANT,
            filename="a.wav",
            mime_type="audio/wav",
            data=_wav(),
            channel_roles=[],
            language="en",
            engine="deepgram",
            created_by=None,
        )
        row = session.rows[-1]
        assert (
            kept["status"] == "transcribed"
            and row.redactions[0]["kind"] == "card"
            and "4111" not in row.transcript_encrypted["_encrypted"]
        )
        assert audio.decode_wav(bytes(row.content)).channels[0][int(1.5 * RATE)] == 0.0
        plain = _row(transcript_encrypted={}, status="received", content=_wav())
        session.rows = [plain]
        attached = await store.attach_transcript(TENANT, plain.id, [dict(w, speaker=None) for w in TRANSCRIPT_WORDS])
        assert (
            attached["redactions"][0]["kind"] == "card"
            and "[CARD ****1111]" in plain.transcript_encrypted["_encrypted"]
        )
        assert audio.decode_wav(bytes(plain.content)).channels[0][int(1.5 * RATE)] == 0.0
        _use(monkeypatch, session, auto=False)
        untouched = _row(transcript_encrypted={}, status="received", content=_wav())
        session.rows = [untouched]
        await store.attach_transcript(TENANT, untouched.id, [dict(w, speaker=None) for w in TRANSCRIPT_WORDS])
        assert untouched.redactions == [] and "4111" in untouched.transcript_encrypted["_encrypted"]


class TestLiveTurns:
    @pytest.mark.asyncio
    async def test_a_live_turn_is_masked_before_it_is_kept(self, monkeypatch):
        import core.database

        session = _Session()
        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)
        monkeypatch.setattr(assist, "encrypt_for_tenant", AsyncMock(side_effect=lambda text, tenant: "enc:" + text))
        monkeypatch.setattr(assist, "decrypt_for_tenant", lambda text: text[4:])
        opened = await assist.start(TENANT, call_ref="C-2", call_type="service", agent_id=None, required=[])
        row = session.rows[0]
        row.id = uuid.UUID(opened["id"])
        out = await assist.append_turn(
            TENANT, row.id, speaker="customer", text="the otp is 4 5 6 7", at=3.0, search=AsyncMock(return_value=[])
        )
        assert out["turn"]["redacted"] == [{"kind": "otp", "digits": 4}]
        assert (
            "4 5 6 7" not in row.turns_encrypted["_encrypted"] and "[OTP REDACTED]" in row.turns_encrypted["_encrypted"]
        )


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_redaction_routes(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", False)
        for call in (
            api.redact_recording(uuid.uuid4(), api.RedactIn(), tenant_id=str(TENANT)),
            api.get_redactions(uuid.uuid4(), tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404
        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        monkeypatch.setattr(
            store, "redact", AsyncMock(return_value={"id": "r", "changed": True, "spans": [{"kind": "otp"}]})
        )
        monkeypatch.setattr(
            store,
            "get_recording",
            AsyncMock(return_value={"id": "r", "redactions": [{"kind": "otp"}], "redacted_at": "t"}),
        )
        out = await api.redact_recording(uuid.uuid4(), api.RedactIn(kinds=["otp"], dry_run=True), tenant_id=str(TENANT))
        assert out["changed"] is True and store.redact.call_args.kwargs == {"kinds": ["otp"], "dry_run": True}
        assert (await api.redact_recording(uuid.uuid4(), None, tenant_id=str(TENANT)))["id"] == "r"
        found = await api.get_redactions(uuid.uuid4(), tenant_id=str(TENANT))
        assert found["redactions"] == [{"kind": "otp"}] and found["kinds"] == list(redaction.KINDS)
        monkeypatch.setattr(store, "redact", AsyncMock(side_effect=SpeechError(409, "not_transcribed", "no")))
        with pytest.raises(HTTPException) as info:
            await api.redact_recording(uuid.uuid4(), api.RedactIn(), tenant_id=str(TENANT))
        assert info.value.status_code == 409
        monkeypatch.setattr(store, "get_recording", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_redactions(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
        assert console.check("speech.redaction_kinds", ["card", "otp"]) == ["card", "otp"]
        assert console.check("speech.redact_on_transcription", True) is True
