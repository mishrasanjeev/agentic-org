# SPDX-License-Identifier: Apache-2.0
"""Speech, part 2: analytics from the words and timings, summaries from the model or the words, store, routes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.speech import analytics, store, summary
from core.speech.audio import SpeechError

TENANT = uuid.uuid4()

TURNS = [
    {
        "speaker": "agent",
        "start": 0.2,
        "end": 2.0,
        "text": "Good morning, thank you for calling. How can I help?",
        "confidence": 0.9,
        "words": 10,
    },
    {
        "speaker": "customer",
        "start": 2.4,
        "end": 5.0,
        "text": "I want to transfer 25,000 to Ravi but the app keeps failing, this is terrible",
        "confidence": 0.9,
        "words": 15,
    },
    {
        "speaker": "agent",
        "start": 5.2,
        "end": 8.0,
        "text": "I am sorry about that, I understand how frustrating it is. Let me help.",
        "confidence": 0.9,
        "words": 14,
    },
    {
        "speaker": "customer",
        "start": 8.3,
        "end": 10.0,
        "text": "It is useless, I will file a complaint if this is not fixed",
        "confidence": 0.9,
        "words": 13,
    },
    {
        "speaker": "agent",
        "start": 10.2,
        "end": 14.0,
        "text": "I will raise the transfer from account ending 1234 now and call you back within 2 hours.",
        "confidence": 0.9,
        "words": 20,
    },
    {
        "speaker": "customer",
        "start": 14.5,
        "end": 16.0,
        "text": "Alright, thank you, that is helpful",
        "confidence": 0.9,
        "words": 6,
    },
]
SEGMENTS = [
    {"speaker": "agent", "start": 0.2, "end": 2.0, "channel": 0, "duration": 1.8},
    {"speaker": "customer", "start": 2.4, "end": 5.0, "channel": 1, "duration": 2.6},
    {"speaker": "agent", "start": 4.6, "end": 8.0, "channel": 0, "duration": 3.4},  # starts before the customer ends
    {"speaker": "customer", "start": 8.3, "end": 10.0, "channel": 1, "duration": 1.7},
    {"speaker": "agent", "start": 10.2, "end": 14.0, "channel": 0, "duration": 3.8},
    {"speaker": "customer", "start": 17.5, "end": 19.0, "channel": 1, "duration": 1.5},  # after a long silence
]
TRANSCRIPT = {
    "turns": TURNS,
    "text": "\n".join(f"{t['speaker']}: {t['text']}" for t in TURNS),
    "word_count": 78,
    "confidence": 0.9,
}


class TestAnalytics:
    def test_roles_are_named_recognised_or_taken_in_order(self):
        assert analytics.roles_of(["agent", "customer"]) == ("agent", "customer")
        assert analytics.roles_of(["speaker_1", "speaker_2"]) == ("speaker_1", "speaker_2")
        assert analytics.roles_of(["b", "a"], agent="a", customer="b") == ("a", "b")
        assert analytics.roles_of(["Advisor", "Caller"]) == ("Advisor", "Caller")
        assert analytics.roles_of(["only"]) == ("only", None)
        assert analytics.roles_of([]) == (None, None)

    def test_the_call_is_scored_turn_by_turn(self):
        found = analytics.analyse(TRANSCRIPT, SEGMENTS, 19.0)
        assert found["roles"] == {"agent": "agent", "customer": "customer"}
        sentiment = found["sentiment"]
        assert [t["label"] for t in sentiment["turns"]] == ["negative", "negative", "positive"]
        assert sentiment["longest_negative_streak"] == 2 and sentiment["negative_share"] > 0.6
        assert sentiment["closing"] > sentiment["opening"]
        empathy = found["empathy"]
        assert (
            empathy["markers"]["apology"] == 1
            and empathy["markers"]["acknowledgement"] == 1
            and empathy["markers"]["reassurance"] >= 1
        )
        assert (
            empathy["negative_customer_turns"] == 2
            and empathy["answered_with_empathy"] == 1
            and 40 <= empathy["score"] <= 100
        )
        interaction = found["interaction"]
        assert interaction["turns"] == 6 and interaction["talk_ratio"]["agent"] > interaction["talk_ratio"]["customer"]
        assert interaction["interruptions"] == [{"at": 4.6, "by": "agent", "overlap": 0.4}]
        assert interaction["silences"] == [{"from": 14.0, "to": 17.5, "seconds": 3.5}]
        assert interaction["longest_monologue"] == {"speaker": "agent", "seconds": 3.8}
        assert interaction["words_per_minute"]["customer"] > 0
        kinds = [s["kind"] for s in found["signals"]]
        assert kinds == ["escalation_phrase", "negative_streak"]
        assert found["scores"]["escalation_risk"] == "high" and found["scores"]["empathy"] == empathy["score"]

    def test_an_empty_transcript_scores_nothing(self):
        found = analytics.analyse({"turns": []}, [], 0.0)
        assert found["sentiment"]["turns"] == [] and found["empathy"]["score"] == 0 and found["signals"] == []
        assert found["scores"]["escalation_risk"] == "low" and found["interaction"]["longest_monologue"] is None
        assert analytics.sentiment_of_turns([], "c")["overall"] == 0.0
        fell = analytics.signals_of([], {"opening": 0.8, "closing": 0.1, "longest_negative_streak": 0}, "c")
        assert fell == [{"kind": "mood_fell", "from": 0.8, "to": 0.1}]


class TestSummary:
    def test_the_extractive_summary_reads_intent_points_actions_and_outcome(self):
        found = summary.extractive(TRANSCRIPT, agent="agent", customer="customer")
        assert (
            found["method"] == "extractive"
            and found["intent_name"] == "fund_transfer"
            and found["intent_confidence"] > 0
        )
        assert any("25,000" in point for point in found["key_points"]) and len(found["key_points"]) <= 5
        assert found["next_actions"] == [
            "agent: I will raise the transfer from account ending 1234 now and call you back within 2 hours."
        ]
        assert found["outcome"] == "resolved" and found["turns"] == 6
        assert (
            summary.outcome_of([{"speaker": "customer", "text": "I want to speak to a manager"}], "customer")
            == "escalated"
        )
        assert summary.outcome_of([{"speaker": "customer", "text": "nothing works"}], "customer") == "unresolved"
        assert (
            summary.outcome_of([{"speaker": "agent", "text": "I will call you back"}], "customer", agent="agent")
            == "follow_up"
        )
        assert summary.next_actions_of(TURNS, agent="agent") == summary.next_actions_of(
            TURNS
        )  # the customer threat is not an action
        assert summary.intent_of([{"speaker": "customer", "text": "la la la"}], "customer")["name"] == "unknown"
        assert summary.key_points_of([]) == [] and summary.next_actions_of([]) == []

    @pytest.mark.asyncio
    async def test_the_model_summary_goes_through_the_checked_call_and_falls_back(self, monkeypatch):
        answers = iter(
            [
                SimpleNamespace(
                    content=(
                        '{"intent": "Fund transfer failure", "key_points": ["transfer of 25,000 failed"], '
                        '"next_actions": ["agent to call back"], "outcome": "follow_up", '
                        '"customer_mood": "frustrated then relieved"}'
                    ),
                    tokens_used=120,
                    model="m",
                )
            ]
        )

        async def complete(tenant_id, model, messages, max_tokens):
            assert messages[0]["role"] == "system" and "Transcript:" in messages[1]["content"]
            return next(answers)

        found = await summary.summarise(
            TENANT, TRANSCRIPT, method="model", agent="agent", customer="customer", complete=complete
        )
        assert found["method"] == "model" and found["outcome"] == "follow_up" and found["model"]["tokens"] == 120

        async def broken(tenant_id, model, messages, max_tokens):
            raise RuntimeError("no model")

        found = await summary.summarise(TENANT, TRANSCRIPT, method="auto", complete=broken)
        assert found["method"] == "extractive" and found["fallback_from"] == "model"
        with pytest.raises(Exception):  # noqa: B017 - the caller insisted on the model
            await summary.summarise(TENANT, TRANSCRIPT, method="model", complete=broken)
        with pytest.raises(ValueError):
            await summary.summarise(TENANT, TRANSCRIPT, method="magic")
        empty = await summary.summarise(TENANT, {"turns": []}, method="auto")
        assert empty["empty"] is True and empty["outcome"] == "unresolved"


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


def _row(**kw):
    import json

    from core.models.speech_recording import SpeechRecording

    base = {
        "tenant_id": TENANT,
        "filename": "call.wav",
        "mime_type": "audio/wav",
        "size_bytes": 10,
        "content": b"RIFF",
        "duration_seconds": 19.0,
        "sample_rate": 16000,
        "channels": 2,
        "channel_roles": ["agent", "customer"],
        "status": "transcribed",
        "engine": "supplied",
        "language": "en",
        "segments": SEGMENTS,
        "speakers": {"agent": {"turns": 3}, "customer": {"turns": 3}},
        "transcript_encrypted": {"_encrypted": "enc:" + json.dumps(TRANSCRIPT)},
        "summary_encrypted": {},
        "analytics": {},
    }
    base.update(kw)
    row = SpeechRecording(**base)
    row.id = uuid.uuid4()
    return row


class TestStore:
    @pytest.mark.asyncio
    async def test_summarising_keeps_the_summary_encrypted_and_the_analytics_in_clear(self, monkeypatch):
        row = _row()
        session = _Session([row])
        _use(monkeypatch, session)
        found = await store.summarise(TENANT, row.id, method="extractive")
        assert (
            found["summary"]["intent_name"] == "fund_transfer"
            and found["analytics"]["scores"]["escalation_risk"] == "high"
        )
        assert row.summary_encrypted["_encrypted"].startswith("enc:") and row.analytics["roles"] == {
            "agent": "agent",
            "customer": "customer",
        }
        detail = await store.get_recording(TENANT, row.id)
        assert (
            detail["summary"]["outcome"] == "resolved"
            and detail["summarised"] is True
            and detail["scores"]["empathy"] > 0
        )
        listed = await store.list_recordings(TENANT)
        assert listed[0]["summarised"] is True and "summary" not in listed[0]
        overview = await store.overview(TENANT)
        assert (
            overview["analysed"] == 1
            and overview["escalation_risk"] == {"high": 1}
            and overview["signals"]["escalation_phrase"] == 1
        )
        assert overview["averages"]["empathy"] == row.analytics["scores"]["empathy"]

    @pytest.mark.asyncio
    async def test_roles_follow_the_labels_not_the_channel_order(self, monkeypatch):
        row = _row(channel_roles=["customer", "agent"], speakers={"customer": {"turns": 3}, "agent": {"turns": 3}})
        assert store._roles(row) == ("agent", "customer")
        row = _row(channel_roles=["left", "right"], speakers={"left": {"turns": 1}, "right": {"turns": 1}})
        assert store._roles(row) == ("left", "right")

    @pytest.mark.asyncio
    async def test_a_replaced_transcript_drops_the_old_summary_and_analytics(self, monkeypatch):
        row = _row(summary_encrypted={"_encrypted": "enc:{}"}, analytics={"scores": {"empathy": 1}})
        session = _Session([row])
        _use(monkeypatch, session)
        await store.attach_transcript(TENANT, row.id, [{"text": "Hello", "start": 0.3, "end": 0.6}])
        assert row.summary_encrypted == {} and row.analytics == {} and row.status == "transcribed"

    @pytest.mark.asyncio
    async def test_a_recording_without_a_transcript_or_a_bad_method_is_refused(self, monkeypatch):
        row = _row(transcript_encrypted={}, status="received")
        _use(monkeypatch, _Session([row]))
        with pytest.raises(SpeechError) as info:
            await store.summarise(TENANT, row.id)
        assert info.value.code == "not_transcribed"
        with pytest.raises(SpeechError) as info:
            await store.summarise(TENANT, row.id, method="magic")
        assert info.value.code == "method_unknown"
        _use(monkeypatch, _Session([]))
        with pytest.raises(SpeechError) as info:
            await store.summarise(TENANT, uuid.uuid4())
        assert info.value.status == 404
        assert (await store.overview(TENANT))["analysed"] == 0

    @pytest.mark.asyncio
    async def test_a_model_that_fails_when_insisted_on_is_an_explicit_error(self, monkeypatch):
        row = _row()
        _use(monkeypatch, _Session([row]))

        async def broken(tenant_id, model, messages, max_tokens):
            raise RuntimeError("down")

        with pytest.raises(SpeechError) as info:
            await store.summarise(TENANT, row.id, method="model", complete=broken)
        assert info.value.code == "summary_failed" and row.summary_encrypted == {}


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_summary_routes_are_off_with_the_flag(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", False)
        for call in (
            api.summarise_recording(uuid.uuid4(), method="auto", tenant_id=str(TENANT)),
            api.get_summary(uuid.uuid4(), tenant_id=str(TENANT)),
            api.analytics_overview(limit=10, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_summary_routes_serve_the_store(self, monkeypatch):
        from api.v1 import speech as api

        monkeypatch.setattr(settings, "speech_intelligence_enabled", True)
        monkeypatch.setattr(
            store, "summarise", AsyncMock(return_value={"id": "r", "summary": {"outcome": "resolved"}, "analytics": {}})
        )
        monkeypatch.setattr(
            store,
            "get_recording",
            AsyncMock(return_value={"id": "r", "summary": {"outcome": "resolved"}, "analytics": {"scores": {}}}),
        )
        monkeypatch.setattr(store, "overview", AsyncMock(return_value={"analysed": 2}))
        out = await api.summarise_recording(uuid.uuid4(), method="extractive", tenant_id=str(TENANT))
        assert out["summary"]["outcome"] == "resolved" and store.summarise.call_args.kwargs["method"] == "extractive"
        assert (await api.get_summary(uuid.uuid4(), tenant_id=str(TENANT)))["analytics"] == {"scores": {}}
        assert (await api.analytics_overview(limit=10, tenant_id=str(TENANT)))["analysed"] == 2
        monkeypatch.setattr(store, "summarise", AsyncMock(side_effect=SpeechError(409, "not_transcribed", "no")))
        with pytest.raises(HTTPException) as info:
            await api.summarise_recording(uuid.uuid4(), method="auto", tenant_id=str(TENANT))
        assert info.value.status_code == 409
        monkeypatch.setattr(store, "get_recording", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_summary(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
