# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""API lifespans must not erase live JWT revocations or rewrite their expiry."""

from __future__ import annotations

import fnmatch
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


class _RedisMemory:
    def __init__(self, state=None):
        self.state = state if state is not None else {}

    async def setex(self, key, ttl, value):
        self.state[key] = (value, 1_000_000 + ttl * 1000)

    async def get(self, key):
        return self.state.get(key, (None, -2))[0]

    async def pexpiretime(self, key):
        return self.state.get(key, (None, -2))[1]

    async def dbsize(self):
        return len(self.state)

    async def scan(self, cursor=0, match="*", count=500):
        return 0, [key for key in self.state if fnmatch.fnmatch(key, match)]

    async def delete(self, *keys):
        for key in keys:
            self.state.pop(key, None)

    async def aclose(self):
        pass


def _track_redis_command(client, command):
    async def invoke(*args, **kwargs):
        return await getattr(client, command)(*args, **kwargs)

    return AsyncMock(side_effect=invoke)


async def _assert_lifespan_preserves_revocations(monkeypatch, client, observer):
    """Use the same Redis boundary for real JWT checks and real API startup."""
    from api import main
    from auth import jwt as tokens

    monkeypatch.setenv("AGENTICORG_AUTH_STATE_STRICT", "1")
    monkeypatch.setattr(tokens.settings, "env", "test")
    monkeypatch.setattr(tokens.settings, "secret_key", "synthetic-startup-revocation-signing-key")
    monkeypatch.setattr(tokens.settings, "jwt_public_key_url", "")
    monkeypatch.setattr(tokens, "_blacklisted_tokens", {})
    boundary = SimpleNamespace(
        **{name: _track_redis_command(client, name) for name in ("setex", "get", "scan", "delete", "aclose")}
    )
    monkeypatch.setattr(tokens, "_redis_client", boundary)
    monkeypatch.setattr("redis.asyncio.from_url", Mock(return_value=boundary))

    asynchronous = (
        "core.database.init_db",
        "core.database.close_db",
        "core.langgraph.checkpointer.open_checkpointer",
        "core.langgraph.checkpointer.close_checkpointer",
        "auth.token_pool.token_pool.init",
        "auth.token_pool.token_pool.close",
        "api.v1.health.close_health_resources",
        "core.spend.drain",
    )
    synchronous = (
        "core.crypto.credential_vault.assert_vault_key_configured",
        "observability.tracing.init_tracing_from_settings",
        "observability.tracing.shutdown_tracing",
        "connectors.plugins.load_configured_plugins",
        "observability.metrics_export.start_metrics_server",
        "observability.metrics_export.stop_metrics_server",
        "core.spend.prewarm",
    )
    async_stubs = [AsyncMock() for _ in asynchronous]
    sync_stubs = [Mock() for _ in synchronous]
    for target, stub in zip(asynchronous, async_stubs, strict=True):
        monkeypatch.setattr(target, stub)
    for target, stub in zip(synchronous, sync_stubs, strict=True):
        monkeypatch.setattr(target, stub)
    warmup = Mock()
    monkeypatch.setattr("core.langgraph.grantex_auth.get_grantex_client", lambda: SimpleNamespace(enforce=warmup))

    revoked = tokens.create_access_token({"sub": "revoked-user"})
    control = tokens.create_access_token({"sub": "active-user"})
    assert tokens.validate_local_token(revoked)["sub"] == "revoked-user"
    revoked_key = tokens._token_redis_key(revoked)
    assert re.fullmatch(r"token_blacklist:[0-9a-f]{64}", revoked_key)
    await tokens.blacklist_token(revoked)
    legacy_key = "token_blacklist:legacy-prefix-sentinel"
    unrelated_key = "synthetic:startup:sentinel"
    await observer.setex(legacy_key, 4800, "legacy-sentinel")
    await observer.setex(unrelated_key, 6100, "unrelated-sentinel")
    keys = (revoked_key, legacy_key, unrelated_key)

    async def snapshot():
        return {key: (await observer.get(key), await observer.pexpiretime(key)) for key in keys}

    expected = await snapshot()
    assert all(value is not None and expiry > 0 for value, expiry in expected.values())
    expected_size = await observer.dbsize()
    assert expected_size == 3
    phases = []

    async def assert_preserved(phase):
        with pytest.raises(ValueError, match="Token has been revoked"):
            await tokens.validate_token(revoked)
        assert (await tokens.validate_token(control))["sub"] == "active-user"
        assert await snapshot() == expected, "Startup changed shared keys or absolute expiry times"
        assert await observer.dbsize() == expected_size
        assert await observer.get(tokens._token_redis_key(control)) is None
        phases.append(phase)

    await assert_preserved("before")
    for restart in (1, 2):
        async with main.lifespan(main.app):
            await assert_preserved(f"ready-{restart}")
        await assert_preserved(f"stopped-{restart}")

    boundary.scan.assert_not_awaited()
    boundary.delete.assert_not_awaited()
    boundary.setex.assert_awaited_once()
    assert not tokens._blacklisted_tokens, "The Redis-only revocation must not be masked by local fallback"
    for stub in async_stubs:
        assert stub.await_count == 2
    for stub in sync_stubs:
        assert stub.call_count == 2
    assert warmup.call_count == 2
    return phases


@pytest.mark.asyncio
async def test_api_lifespan_preserves_active_revocations(monkeypatch):
    state = {}
    phases = await _assert_lifespan_preserves_revocations(monkeypatch, _RedisMemory(state), _RedisMemory(state))
    assert phases == ["before", "ready-1", "stopped-1", "ready-2", "stopped-2"]
