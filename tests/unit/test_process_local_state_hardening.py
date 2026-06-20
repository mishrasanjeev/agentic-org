from __future__ import annotations

import asyncio

import pytest


def test_jwt_blacklist_fails_closed_in_strict_runtime(monkeypatch) -> None:
    from auth import jwt as jwt_mod

    token = "strict-token"
    jwt_mod._blacklisted_tokens.clear()
    monkeypatch.delenv("AGENTICORG_AUTH_STATE_STRICT", raising=False)
    monkeypatch.setattr(jwt_mod.settings, "env", "production")
    monkeypatch.setattr(jwt_mod, "_get_redis", lambda: None)

    with pytest.raises(RuntimeError, match="Redis"):
        jwt_mod.blacklist_token(token)
    assert token not in jwt_mod._blacklisted_tokens

    with pytest.raises(RuntimeError, match="Redis"):
        asyncio.run(jwt_mod._is_blacklisted(token))


def test_jwt_blacklist_relaxed_runtime_uses_memory_fallback(monkeypatch) -> None:
    from auth import jwt as jwt_mod

    token = "relaxed-token"
    jwt_mod._blacklisted_tokens.clear()
    monkeypatch.delenv("AGENTICORG_AUTH_STATE_STRICT", raising=False)
    monkeypatch.setattr(jwt_mod.settings, "env", "development")
    monkeypatch.setattr(jwt_mod, "_get_redis", lambda: None)

    jwt_mod.blacklist_token(token)

    assert token in jwt_mod._blacklisted_tokens
    assert asyncio.run(jwt_mod._is_blacklisted(token)) is True
    jwt_mod._blacklisted_tokens.clear()


def test_pinelabs_order_mapping_fails_closed_in_strict_runtime(monkeypatch) -> None:
    from core.billing import pinelabs_client

    pinelabs_client._order_map.clear()
    pinelabs_client._order_id_map.clear()
    monkeypatch.setattr(pinelabs_client.settings, "env", "production")
    monkeypatch.setattr(pinelabs_client, "_redis_client", lambda: None)

    with pytest.raises(RuntimeError, match="Redis"):
        pinelabs_client.store_order_mapping("merchant-1", "order-1", "tenant-1", "pro")
    assert pinelabs_client._order_map == {}

    with pytest.raises(RuntimeError, match="Redis"):
        pinelabs_client.lookup_order_details("merchant-1")


def test_pinelabs_order_mapping_relaxed_runtime_uses_memory_fallback(monkeypatch) -> None:
    from core.billing import pinelabs_client

    pinelabs_client._order_map.clear()
    pinelabs_client._order_id_map.clear()
    monkeypatch.setattr(pinelabs_client.settings, "env", "development")
    monkeypatch.setattr(pinelabs_client, "_redis_client", lambda: None)

    pinelabs_client.store_order_mapping("merchant-2", "order-2", "tenant-2", "enterprise")

    assert pinelabs_client.lookup_order_details("merchant-2") == {
        "order_id": "order-2",
        "tenant_id": "tenant-2",
        "plan": "enterprise",
    }
    assert pinelabs_client.lookup_order_details_by_order_id("order-2") == {
        "merchant_order_reference": "merchant-2",
        "order_id": "order-2",
        "tenant_id": "tenant-2",
        "plan": "enterprise",
    }
    pinelabs_client._order_map.clear()
    pinelabs_client._order_id_map.clear()


def test_chat_sessions_fail_closed_in_strict_runtime_without_redis(monkeypatch) -> None:
    from api.v1 import chat

    chat._sessions.clear()
    monkeypatch.setattr(chat.settings, "env", "production")
    monkeypatch.setattr(chat, "_redis_client", None)

    with pytest.raises(RuntimeError, match="Redis"):
        asyncio.run(chat._load_session("tenant:company"))

    with pytest.raises(RuntimeError, match="Redis"):
        asyncio.run(chat._save_session("tenant:company", [{"role": "user"}]))
    assert chat._sessions == {}


def test_chat_sessions_relaxed_runtime_uses_memory_fallback(monkeypatch) -> None:
    from api.v1 import chat

    chat._sessions.clear()
    monkeypatch.setattr(chat.settings, "env", "development")
    monkeypatch.setattr(chat, "_redis_client", None)

    entries = [{"role": "user", "content": "hello"}]
    asyncio.run(chat._save_session("tenant:company", entries))

    assert asyncio.run(chat._load_session("tenant:company")) == entries
    chat._sessions.clear()
