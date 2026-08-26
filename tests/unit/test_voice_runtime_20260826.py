"""Production voice runtime regression coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.voice.runtime import (
    build_conversation_twiml,
    build_twilio_signature,
    create_twilio_call,
    verify_twilio_signature,
)

REPO = Path(__file__).resolve().parents[2]


def test_twilio_webhook_signature_verification() -> None:
    url = "https://api.agenticorg.ai/api/v1/voice/webhooks/twilio/t/a/incoming"
    params = {"CallSid": "CA123", "SpeechResult": "show my balance"}
    token = "test-auth-token"
    signature = build_twilio_signature(url=url, params=params, auth_token=token)

    assert signature == "g4nuiqRQpOsYrEJB8MbrifdjX6E="

    assert verify_twilio_signature(url=url, params=params, signature=signature, auth_token=token)
    assert not verify_twilio_signature(url=url, params=params, signature="tampered", auth_token=token)
    assert not verify_twilio_signature(
        url=url.replace("https://", "http://"),
        params=params,
        signature=signature,
        auth_token=token,
    )


def test_twilio_webhooks_reach_signature_boundary_without_jwt() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from auth.middleware import AuthMiddleware

    prefix = "/api/v1/voice/webhooks/twilio/"
    assert prefix in GrantexAuthMiddleware.EXEMPT_PREFIXES
    assert prefix in AuthMiddleware.EXEMPT_PREFIXES


def test_twiml_escapes_agent_text_and_collects_speech() -> None:
    xml = build_conversation_twiml(
        action_url="https://api.agenticorg.ai/voice?next=1&safe=true",
        prompt='A < B & "safe"',
    )

    assert 'input="speech"' in xml
    assert 'speechTimeout="auto"' in xml
    assert 'A &lt; B &amp; "safe"' in xml
    assert "&amp;safe=true" in xml


@pytest.mark.asyncio
async def test_outbound_call_uses_real_twilio_contract(monkeypatch) -> None:
    client = AsyncMock()
    client.post.return_value = SimpleNamespace(
        status_code=201,
        json=lambda: {"sid": "CA456", "status": "queued"},
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = False
    monkeypatch.setattr("core.voice.runtime.httpx.AsyncClient", lambda **_kwargs: context)

    result = await create_twilio_call(
        account_sid="AC123",
        auth_token="token",
        to_number="+919999999999",
        from_number="+918888888888",
        voice_url="https://api.agenticorg.ai/incoming",
        status_callback_url="https://api.agenticorg.ai/status",
    )

    assert result["sid"] == "CA456"
    call = client.post.await_args
    assert call.args[0] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls.json"
    assert call.kwargs["auth"] == ("AC123", "token")
    assert call.kwargs["data"]["To"] == "+919999999999"
    assert call.kwargs["data"]["StatusCallback"] == "https://api.agenticorg.ai/status"


@pytest.mark.asyncio
async def test_outbound_call_never_reports_provider_failure_as_success(monkeypatch) -> None:
    client = AsyncMock()
    client.post.return_value = SimpleNamespace(status_code=401)
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = False
    monkeypatch.setattr("core.voice.runtime.httpx.AsyncClient", lambda **_kwargs: context)

    with pytest.raises(RuntimeError, match="HTTP 401"):
        await create_twilio_call(
            account_sid="AC123",
            auth_token="wrong",
            to_number="+919999999999",
            from_number="+918888888888",
            voice_url="https://api.agenticorg.ai/incoming",
            status_callback_url="https://api.agenticorg.ai/status",
        )


def test_voice_call_migration_is_tenant_scoped_and_encrypted() -> None:
    migration = (REPO / "migrations/versions/v6_z12_voice_runtime.py").read_text()
    model = (REPO / "core/models/voice_call.py").read_text()
    runtime = (REPO / "api/v1/voice_runtime.py").read_text()

    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "agenticorg.tenant_id" in migration
    assert "transcript_encrypted" in model
    assert "encrypt_for_tenant" in runtime
    assert 'audio_stored": False' in runtime
    assert "transcript" not in runtime.split("class VoiceCallOut", 1)[1].split("def _masked_number", 1)[0]


def test_voice_transcript_is_registered_for_key_lifecycle_scanning() -> None:
    verifier = (REPO / "core/crypto/verify_all.py").read_text(encoding="utf-8")

    assert "voice_calls.transcript_encrypted" in verifier
    assert "core.models.voice_call:VoiceCall:transcript_encrypted" in verifier
