"""PostgreSQL/ASGI end-to-end coverage for the signed voice runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from core.crypto.tenant_secrets import decrypt_for_tenant
from core.models.agent import Agent
from core.models.voice_call import VoiceCall


def _twilio_signature(url: str, params: dict[str, str], token: str) -> str:
    canonical = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    # Twilio's webhook protocol mandates HMAC-SHA1. This helper verifies
    # protocol compatibility; it is not password hashing or key derivation.
    digest = hmac.new(
        token.encode(),
        canonical.encode(),  # lgtm[py/weak-sensitive-data-hashing]
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


@pytest.mark.asyncio
async def test_signed_voice_runtime_persists_encrypted_tenant_safe_calls(
    client,
    make_auth_headers,
    tenant_id,
    monkeypatch,
) -> None:
    import core.database as db_mod
    from api.v1 import voice_runtime

    agent_id = uuid.uuid4()
    auth_token = "local-twilio-signature-token"
    headers = make_auth_headers(scopes=["agenticorg:admin"], agent_id=str(agent_id))

    async with db_mod.async_session_factory() as session:
        session.add(
            Agent(
                id=agent_id,
                tenant_id=uuid.UUID(tenant_id),
                company_id=None,
                name="Local Voice Agent",
                agent_type="customer_support",
                domain="operations",
                description="Local signed voice integration test",
                system_prompt_ref="inline://voice-e2e",
                prompt_variables={},
                llm_model="test-model",
                llm_fallback=None,
                llm_config={},
                hitl_condition="always",
                authorized_tools=[],
                connector_ids=[],
                output_schema=None,
                status="active",
                version="1.0.0",
                parent_agent_id=None,
                shadow_comparison_agent_id=None,
                cost_controls={},
                scaling={},
                tags=[],
                ttl_hours=None,
                expires_at=None,
                config={},
                employee_name="Voice Agent",
                avatar_url=None,
                designation="Support",
                specialization="Voice",
                routing_filter={},
                is_builtin=False,
                cost_center_id=None,
                system_prompt_text="Answer customer questions accurately.",
                reporting_to=None,
            )
        )
        await session.commit()

    config_response = await client.post(
        "/api/v1/voice/config",
        headers=headers,
        json={
            "agent_id": str(agent_id),
            "sip_provider": "twilio",
            "credentials": {"account_sid": "ACLOCAL", "auth_token": auth_token},
            "phone_number": "+919876543210",
            "language": "en-IN",
            "stt_engine": "provider_managed",
            "tts_engine": "provider_managed",
            "runtime_status": "ready",
        },
    )
    assert config_response.status_code == 200, config_response.text
    assert config_response.json()["credentials"]["auth_token"] == "***oken"

    monkeypatch.setattr(
        voice_runtime,
        "settings",
        SimpleNamespace(public_api_base_url="https://testserver", env="test"),
    )

    async def _reply(_worker, _call_sid: str, speech: str) -> str:
        assert speech == "Where is my order?"
        return "Your order is ready for pickup."

    monkeypatch.setattr(voice_runtime.VoiceAgentWorker, "handle_call", _reply)

    incoming_url = f"https://testserver/api/v1/voice/webhooks/twilio/{tenant_id}/{agent_id}/incoming"
    params = {
        "CallSid": "CA-LOCAL-INBOUND",
        "Direction": "inbound",
        "From": "+919111111111",
        "To": "+919876543210",
        "SpeechResult": "Where is my order?",
    }
    webhook_response = await client.post(
        incoming_url.replace("https://testserver", ""),
        data=params,
        headers={"X-Twilio-Signature": _twilio_signature(incoming_url, params, auth_token)},
    )
    assert webhook_response.status_code == 200, webhook_response.text
    assert "Your order is ready for pickup." in webhook_response.text
    assert 'input="speech"' in webhook_response.text

    rejected = await client.post(
        incoming_url.replace("https://testserver", ""),
        data=params,
        headers={"X-Twilio-Signature": "tampered"},
    )
    assert rejected.status_code == 403

    status_url = f"https://testserver/api/v1/voice/webhooks/twilio/{tenant_id}/{agent_id}/status"
    status_params = {
        "CallSid": "CA-LOCAL-INBOUND",
        "CallStatus": "completed",
        "CallDuration": "42",
        "Direction": "inbound",
        "From": "+919111111111",
        "To": "+919876543210",
    }
    status_response = await client.post(
        status_url.replace("https://testserver", ""),
        data=status_params,
        headers={"X-Twilio-Signature": _twilio_signature(status_url, status_params, auth_token)},
    )
    assert status_response.status_code == 204, status_response.text

    async def _fake_create_twilio_call(**kwargs):
        assert kwargs["to_number"] == "+919222222222"
        assert kwargs["from_number"] == "+919876543210"
        return {"sid": "CA-LOCAL-OUTBOUND", "status": "queued"}

    monkeypatch.setattr(voice_runtime, "create_twilio_call", _fake_create_twilio_call)
    outbound = await client.post(
        "/api/v1/voice/calls/outbound",
        headers=headers,
        json={"agent_id": str(agent_id), "to_number": "+919222222222"},
    )
    assert outbound.status_code == 200, outbound.text
    assert outbound.json()["to_number"] == "***2222"

    calls = await client.get(
        f"/api/v1/voice/calls?agent_id={agent_id}",
        headers=headers,
    )
    assert calls.status_code == 200, calls.text
    assert {item["provider_call_id"] for item in calls.json()} == {
        "CA-LOCAL-INBOUND",
        "CA-LOCAL-OUTBOUND",
    }
    assert all(item["from_number"].startswith("***") for item in calls.json())
    assert all(item["to_number"].startswith("***") for item in calls.json())

    async with db_mod.async_session_factory() as session:
        result = await session.execute(select(VoiceCall).where(VoiceCall.provider_call_id == "CA-LOCAL-INBOUND"))
        stored = result.scalar_one()
        assert stored.status == "completed"
        assert stored.duration_seconds == 42
        assert stored.turn_count == 1
        assert stored.from_number == "***1111"
        assert stored.to_number == "***3210"
        ciphertext = str(stored.transcript_encrypted["_encrypted"])
        assert "Where is my order?" not in ciphertext
        turns = json.loads(decrypt_for_tenant(ciphertext))
        assert [turn["role"] for turn in turns] == ["caller", "agent"]

    other_tenant = str(uuid.uuid4())
    async with db_mod.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                "VALUES (:id, 'other', :slug, 'enterprise', 'IN', '{}')"
            ),
            {"id": other_tenant, "slug": f"other-{other_tenant[:8]}"},
        )
    isolated = await client.get(
        f"/api/v1/voice/calls?agent_id={agent_id}",
        headers=make_auth_headers(
            tenant_id=other_tenant,
            scopes=["agenticorg:admin"],
            agent_id=str(agent_id),
        ),
    )
    assert isolated.status_code == 200, isolated.text
    assert isolated.json() == []
