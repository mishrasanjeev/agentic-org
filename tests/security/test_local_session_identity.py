"""Same-second session issuance and backward-compatible local token validation."""

import time

import jwt
import pytest

from auth import jwt as session_jwt


@pytest.mark.parametrize("caller_id", [None, "synthetic-caller-id"])
def test_same_second_tokens_get_distinct_server_generated_ids(monkeypatch, caller_id):
    now = int(time.time())
    monkeypatch.setattr(session_jwt.time, "time", lambda: now)
    data = {"sub": "synthetic@example.test", "role": "admin"}
    if caller_id is not None:
        data["jti"] = caller_id
    tokens = [session_jwt.create_access_token(data, expires_minutes=17) for _ in range(32)]
    payloads = [session_jwt.validate_local_token(token) for token in tokens]
    assert len(set(tokens)) == len(tokens)
    assert len({payload["jti"] for payload in payloads}) == len(tokens)
    for payload in payloads:
        assert isinstance(payload["jti"], str) and len(payload["jti"]) == 43
        assert payload["jti"] != caller_id
        assert payload["iss"] == session_jwt.local_issuer()
        assert payload["aud"] == "agenticorg-tool-gateway"
        assert payload["iat"] == now
        assert payload["exp"] == now + 17 * 60
        assert payload["sub"] == data["sub"] and payload["role"] == data["role"]


def test_legacy_session_without_id_remains_valid():
    now = int(time.time())
    payload = {
        "sub": "synthetic@example.test",
        "iss": session_jwt.local_issuer(),
        "aud": "agenticorg-tool-gateway",
        "iat": now,
        "exp": now + 60,
    }
    legacy = jwt.encode(payload, session_jwt.settings.secret_key, algorithm="HS256")
    assert session_jwt.validate_local_token(legacy) == payload


@pytest.mark.asyncio
async def test_single_local_session_revocation_does_not_revoke_same_second_sibling(monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(session_jwt.time, "time", lambda: now)
    monkeypatch.setattr(session_jwt, "_auth_state_strict", lambda: False)
    monkeypatch.setattr(session_jwt, "_get_redis", lambda: None)
    monkeypatch.setattr(session_jwt, "_blacklisted_tokens", {})
    data = {"sub": "synthetic@example.test", "role": "admin"}
    first, second = (session_jwt.create_access_token(data) for _ in range(2))
    assert first != second
    await session_jwt.blacklist_token(first)
    with pytest.raises(ValueError, match="revoked"):
        session_jwt.validate_local_token(first)
    assert session_jwt.validate_local_token(second)["sub"] == data["sub"]
