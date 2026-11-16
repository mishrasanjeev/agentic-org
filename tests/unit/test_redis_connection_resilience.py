"""Opt-in Redis connection resilience: keepalive, idle health checks, health-probe retry.

Production reaches Memorystore through a Serverless VPC Access connector, and
idle pooled sockets are occasionally dropped on that path. Every option here
is off by default, so behaviour only changes when a deploy turns it on.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import redis.exceptions as redis_exceptions

from core.redis_resilience import (
    HEALTH_CHECK_INTERVAL_ENV,
    KEEPALIVE_ENV,
    celery_redis_options,
    connection_kwargs,
)

REPO = Path(__file__).resolve().parents[2]

# Every module that builds a Redis client with the shared socket timeouts.
_CLIENT_MODULES = (
    "api/v1/auth.py",
    "api/v1/chat.py",
    "api/v1/health.py",
    "auth/one_time_codes.py",
    "auth/token_pool.py",
    "bridge/state.py",
    "core/async_redis.py",
    "core/auth_state.py",
    "core/billing/subscriptions.py",
    "core/billing/usage_tracker.py",
    "core/kpi_cache.py",
    "core/live_feed.py",
)


class _Settings:
    redis_socket_connect_timeout_seconds = 2.0
    redis_socket_timeout_seconds = 2.0
    redis_socket_keepalive = False
    redis_health_check_interval_seconds = 0
    redis_health_probe_retry = False


# ── Defaults stay off ─────────────────────────────────────────────────


def test_settings_defaults_leave_resilience_off() -> None:
    from core.config import Settings

    fields = Settings.model_fields
    assert fields["redis_socket_keepalive"].default is False
    assert fields["redis_health_check_interval_seconds"].default == 0
    assert fields["redis_health_probe_retry"].default is False


def test_resilience_kwargs_are_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config as config

    monkeypatch.setattr(config, "settings", _Settings())

    assert config.redis_connection_resilience_kwargs() == {}
    # The timeout helper keeps exactly its two keys: api/v1/auth.py sums its values.
    assert set(config.redis_socket_timeout_kwargs()) == {"socket_connect_timeout", "socket_timeout"}


def test_connection_kwargs_off_is_empty() -> None:
    assert connection_kwargs(keepalive=False, health_check_interval=0) == {}


def test_celery_options_are_empty_by_default() -> None:
    assert celery_redis_options({}) == {}
    assert celery_redis_options({KEEPALIVE_ENV: "false", HEALTH_CHECK_INTERVAL_ENV: "0"}) == {}


def test_celery_app_default_config_has_no_resilience_options() -> None:
    from core.tasks import celery_app

    transport = celery_app.app.conf.broker_transport_options or {}
    assert "socket_keepalive" not in transport
    assert not celery_app.app.conf.redis_socket_keepalive


# ── Enabled ───────────────────────────────────────────────────────────


def test_resilience_kwargs_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config as config

    enabled = _Settings()
    enabled.redis_socket_keepalive = True
    enabled.redis_health_check_interval_seconds = 15
    monkeypatch.setattr(config, "settings", enabled)

    kwargs = config.redis_connection_resilience_kwargs()

    assert kwargs["socket_keepalive"] is True
    assert kwargs["health_check_interval"] == 15
    expected_options = {
        getattr(socket, name): value
        for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3))
        if hasattr(socket, name)
    }
    assert kwargs.get("socket_keepalive_options", {}) == expected_options


def test_health_check_interval_alone_does_not_turn_on_keepalive() -> None:
    assert connection_kwargs(keepalive=False, health_check_interval=10) == {"health_check_interval": 10}


def test_celery_options_when_enabled() -> None:
    options = celery_redis_options({KEEPALIVE_ENV: "true", HEALTH_CHECK_INTERVAL_ENV: "10"})

    transport = options["broker_transport_options"]
    assert transport["socket_keepalive"] is True
    assert transport["health_check_interval"] == 10
    assert options["redis_socket_keepalive"] is True
    assert options["redis_backend_health_check_interval"] == 10


def test_celery_keepalive_only_keeps_the_kombu_health_check_default() -> None:
    options = celery_redis_options({KEEPALIVE_ENV: "1"})

    assert "health_check_interval" not in options["broker_transport_options"]
    assert "redis_backend_health_check_interval" not in options


@pytest.mark.parametrize(
    "env",
    [
        {KEEPALIVE_ENV: "maybe"},
        {HEALTH_CHECK_INTERVAL_ENV: "ten"},
        {HEALTH_CHECK_INTERVAL_ENV: "-1"},
        {HEALTH_CHECK_INTERVAL_ENV: "301"},
    ],
)
def test_celery_options_reject_invalid_values(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        celery_redis_options(env)


def test_every_shared_client_applies_the_resilience_kwargs() -> None:
    for rel in _CLIENT_MODULES:
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "redis_connection_resilience_kwargs" in src, rel


def test_token_pool_keeps_its_own_health_check_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config as config
    from auth import token_pool

    enabled = _Settings()
    enabled.redis_socket_keepalive = True
    enabled.redis_health_check_interval_seconds = 10
    monkeypatch.setattr(config, "settings", enabled)
    factory = MagicMock()
    monkeypatch.setattr(token_pool.aioredis, "from_url", factory)

    token_pool.TokenPool._new_redis_client()

    kwargs = factory.call_args.kwargs
    assert kwargs["health_check_interval"] == 30
    assert kwargs["socket_keepalive"] is True


# ── Health probe retry ────────────────────────────────────────────────


class _Pinger:
    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def ping(self) -> bool:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if outcome == "slow":
            await asyncio.sleep(10)
        if isinstance(outcome, BaseException):
            raise outcome
        return True


def _install(monkeypatch: pytest.MonkeyPatch, client: _Pinger, *, retry: bool) -> None:
    from api.v1 import health

    monkeypatch.setattr(health, "_get_health_redis_client", lambda: client)
    monkeypatch.setattr(health.settings, "redis_health_probe_retry", retry)


@pytest.mark.asyncio
async def test_health_probe_does_not_retry_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    client = _Pinger(redis_exceptions.ConnectionError("reset"), True)
    _install(monkeypatch, client, retry=False)

    assert await health._redis_health_status() == "unhealthy: ConnectionError"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_health_probe_retries_once_after_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    client = _Pinger(redis_exceptions.ConnectionError("reset"), True)
    _install(monkeypatch, client, retry=True)

    assert await health._redis_health_status() == "healthy"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_health_probe_stays_unhealthy_when_both_attempts_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    client = _Pinger(redis_exceptions.ConnectionError("reset"), redis_exceptions.ConnectionError("reset"))
    _install(monkeypatch, client, retry=True)

    assert await health._redis_health_status() == "unhealthy: ConnectionError"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_health_probe_does_not_retry_a_slow_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    monkeypatch.setattr(health, "_DEPENDENCY_HC_TIMEOUT", 0.05)
    client = _Pinger("slow", True)
    _install(monkeypatch, client, retry=True)

    assert await health._redis_health_status() == "unhealthy: TimeoutError"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_health_probe_does_not_retry_a_redis_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    client = _Pinger(redis_exceptions.TimeoutError("socket timeout"), True)
    _install(monkeypatch, client, retry=True)

    assert await health._redis_health_status() == "unhealthy: TimeoutError"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_health_probe_retry_stays_inside_the_dependency_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    monkeypatch.setattr(health, "_DEPENDENCY_HC_TIMEOUT", 0.2)

    class _SlowFailThenSlow(_Pinger):
        async def ping(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.15)
                raise redis_exceptions.ConnectionError("reset")
            await asyncio.sleep(10)
            return True

    client = _SlowFailThenSlow()
    _install(monkeypatch, client, retry=True)
    loop = asyncio.get_running_loop()
    started = loop.time()

    assert await health._redis_health_status() == "unhealthy: TimeoutError"
    assert loop.time() - started < 0.4
    assert client.calls == 2
