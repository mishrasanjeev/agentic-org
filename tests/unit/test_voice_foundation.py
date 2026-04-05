"""Tests for voice agent foundation (PRD v4.0.0 Section 10).

Covers:
  1. SIP config validation — valid config passes
  2. SIP config validation — missing fields caught
  3. SIP config validation — invalid provider rejected
  4. Voice pipeline builder — returns correct config
  5. Mock call handling — VoiceAgentWorker processes a turn
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.voice.livekit_agent import VoiceAgentWorker
from core.voice.pipeline import (
    STTConfig,
    TTSConfig,
    VoicePipelineConfig,
    build_voice_pipeline,
)
from core.voice.sip_config import (
    PROVIDER_CUSTOM,
    PROVIDER_TWILIO,
    PROVIDER_VONAGE,
    SIPConfig,
    validate_sip_config,
)
from core.voice.sip_config import (
    test_sip_connection as sip_connection_test,
)


# ── 1. SIP config validation — valid config passes ──────────────────────
class TestSIPValidation:
    def test_valid_twilio_config(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_TWILIO,
            credentials={"account_sid": "AC123", "auth_token": "tok456"},
            phone_number="+919876543210",
        )
        errors = validate_sip_config(config)
        assert errors == []

    def test_valid_vonage_config(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_VONAGE,
            credentials={"api_key": "key123", "api_secret": "sec456"},
            phone_number="+14155551234",
        )
        errors = validate_sip_config(config)
        assert errors == []

    # ── 2. SIP config validation — missing fields caught ─────────────
    def test_missing_credentials(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_TWILIO,
            credentials={},
            phone_number="+919876543210",
        )
        errors = validate_sip_config(config)
        assert len(errors) == 2  # account_sid + auth_token
        assert any("account_sid" in e for e in errors)
        assert any("auth_token" in e for e in errors)

    def test_missing_phone_number(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_TWILIO,
            credentials={"account_sid": "AC123", "auth_token": "tok456"},
            phone_number="",
        )
        errors = validate_sip_config(config)
        assert len(errors) == 1
        assert "Phone number is required" in errors[0]

    def test_invalid_phone_format(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_TWILIO,
            credentials={"account_sid": "AC123", "auth_token": "tok456"},
            phone_number="9876543210",  # missing +
        )
        errors = validate_sip_config(config)
        assert len(errors) == 1
        assert "E.164" in errors[0]

    # ── 3. SIP config validation — invalid provider rejected ─────────
    def test_invalid_provider(self) -> None:
        config = SIPConfig(
            provider="invalid_provider",
            credentials={},
            phone_number="+919876543210",
        )
        errors = validate_sip_config(config)
        assert len(errors) == 1
        assert "Invalid provider" in errors[0]


# ── 4. Voice pipeline builder — returns correct config ───────────────────
class TestVoicePipeline:
    def test_default_pipeline(self) -> None:
        agent_cfg = {"agent_id": "test-voice-1", "system_prompt": "Hello"}
        pipeline = build_voice_pipeline(agent_cfg)

        assert isinstance(pipeline, VoicePipelineConfig)
        assert pipeline.stt.engine == "faster-whisper"
        assert pipeline.tts.engine == "piper"
        assert pipeline.agent_config["agent_id"] == "test-voice-1"

    def test_custom_stt_tts(self) -> None:
        agent_cfg = {"agent_id": "test-voice-2"}
        stt = STTConfig(engine="google", language="hi")
        tts = TTSConfig(engine="google", language="hi-IN")

        pipeline = build_voice_pipeline(agent_cfg, stt_config=stt, tts_config=tts)

        assert pipeline.stt.engine == "google"
        assert pipeline.stt.language == "hi"
        assert pipeline.tts.engine == "google"
        assert pipeline.tts.language == "hi-IN"

    def test_pipeline_with_sip(self) -> None:
        agent_cfg = {"agent_id": "test-voice-3"}
        sip = {"provider": "twilio", "phone_number": "+919876543210"}

        pipeline = build_voice_pipeline(agent_cfg, sip_config=sip)

        assert pipeline.sip_config["provider"] == "twilio"


# ── 5. Mock call handling — VoiceAgentWorker processes a turn ────────────
class TestVoiceAgentWorker:
    @pytest.mark.asyncio
    async def test_handle_call_success(self) -> None:
        agent_cfg = {
            "agent_id": "voice-test",
            "agent_type": "voice",
            "domain": "general",
            "tenant_id": "t-123",
            "system_prompt": "You are a test assistant.",
            "authorized_tools": [],
        }

        mock_result = {
            "status": "completed",
            "output": {"response": "The answer is 42."},
            "confidence": 0.95,
        }

        worker = VoiceAgentWorker(agent_cfg, grant_token="jwt-test")
        mock_session = object()

        with patch("core.langgraph.runner.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            response = await worker.handle_call(mock_session, "What is the answer?")

        assert response == "The answer is 42."

    @pytest.mark.asyncio
    async def test_handle_call_error_graceful(self) -> None:
        agent_cfg = {"agent_id": "voice-err", "authorized_tools": []}

        mock_result = {
            "status": "failed",
            "error": "LLM timeout",
            "output": {},
        }

        worker = VoiceAgentWorker(agent_cfg)

        with patch("core.langgraph.runner.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            response = await worker.handle_call(object(), "Hello?")

        assert "sorry" in response.lower() or "try again" in response.lower()


# ── SIP connection test (mock-safe) ──────────────────────────────────────
class TestSIPConnection:
    @pytest.mark.asyncio
    async def test_connection_test_validation_fail(self) -> None:
        config = SIPConfig(provider="invalid", credentials={}, phone_number="")
        result = await sip_connection_test(config)
        assert result["success"] is False
        assert "Validation failed" in result["message"]

    @pytest.mark.asyncio
    async def test_connection_test_custom_valid(self) -> None:
        config = SIPConfig(
            provider=PROVIDER_CUSTOM,
            credentials={"sip_uri": "sip:test@example.com"},
            phone_number="+919876543210",
        )
        result = await sip_connection_test(config)
        assert result["success"] is True
