# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Provider setup may enable only an owned, confirmed binding."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.v1 import channels
from core.channels.providers import ChannelError


@pytest.mark.parametrize("environment,production", [("development", False), ("staging", False), ("production", True)])
def test_default_callback_origin_does_not_register_local_bindings_on_production(monkeypatch, environment, production):
    monkeypatch.delenv("AGENTICORG_BASE_URL", raising=False)
    monkeypatch.setenv("AGENTICORG_ENV", environment)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Test bot",
        provider="telegram",
        target_kind="agent",
        target_id=uuid.uuid4(),
        enabled=False,
        auto_reply=False,
    )
    callback = channels._out(row)["callback_url"]
    assert callback.startswith("https://app.agenticorg.ai/") is production
    if not production:
        assert callback.startswith("http://localhost:8000/")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["confirmed", "conflict", "timeout", "missing", "wrong_provider"])
async def test_telegram_registration_keeps_owner_scope_and_fails_closed(monkeypatch, outcome):
    tenant_id, binding_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        id=binding_id,
        tenant_id=tenant_id,
        owner_user_id=owner_id,
        name="Test bot",
        provider="mailgun" if outcome == "wrong_provider" else "telegram",
        target_kind="agent",
        target_id=uuid.uuid4(),
        enabled=False,
        auto_reply=False,
        credentials_ciphertext="encrypted-test-material",
    )
    queries = []
    calls = []

    class Session:
        async def scalar(self, query):
            queries.append(query)
            return None if outcome == "missing" else row

    @asynccontextmanager
    async def session(tid):
        assert tid == tenant_id
        yield Session()

    async def actor(request, tid):
        assert tid == tenant_id
        return SimpleNamespace(id=owner_id)

    async def unseal(value):
        assert value == "encrypted-test-material"
        return {"access_token": "123:synthetic", "webhook_secret": "synthetic-secret"}

    async def connect(credentials, callback):
        calls.append(callback)
        if outcome == "conflict":
            raise ChannelError("telegram_bot_connected_elsewhere")
        if outcome == "timeout":
            raise TimeoutError()
        return "example_test_bot"

    monkeypatch.setattr(channels, "get_tenant_session", session)
    monkeypatch.setattr(channels, "_actor", actor)
    monkeypatch.setattr(channels, "unseal", unseal)
    monkeypatch.setattr(channels, "connect_telegram", connect)
    monkeypatch.setenv("AGENTICORG_BASE_URL", "https://app.example.test")
    request = Request({"type": "http", "headers": []})
    if outcome == "confirmed":
        result = await channels.register_telegram(binding_id, request, str(tenant_id))
        assert result["binding"]["enabled"] is True
        assert result["bot_username"] == "example_test_bot"
        assert "credentials_ciphertext" not in result["binding"]
    else:
        with pytest.raises(HTTPException) as caught:
            await channels.register_telegram(binding_id, request, str(tenant_id))
        assert (
            caught.value.status_code
            == {"conflict": 409, "timeout": 504, "missing": 404, "wrong_provider": 422}[outcome]
        )
        assert row.enabled is False
    assert {tenant_id, binding_id, owner_id}.issubset(set(queries[0].compile().params.values()))
    if outcome in {"missing", "wrong_provider"}:
        assert calls == []
