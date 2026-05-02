"""Regression pins for the May 2026 runtime hardening sweep."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import Response

REPO = Path(__file__).resolve().parents[2]


def test_strict_runtime_env_covers_aliases_and_unknown_values() -> None:
    from core.config import is_strict_runtime_env

    for env in ("production", "prod", "staging", "stage", "preview", "qa-cluster-3"):
        assert is_strict_runtime_env(env), f"{env} must receive production hardening"
    for env in ("local", "dev", "development", "test", "ci"):
        assert not is_strict_runtime_env(env), f"{env} should keep local-dev behavior"


def test_fastapi_disables_docs_redoc_and_openapi_in_strict_runtime_source() -> None:
    src = (REPO / "api" / "main.py").read_text(encoding="utf-8")
    assert "is_strict_runtime_env(settings.env)" in src
    assert "docs_url=None if _is_strict_runtime else" in src
    assert "redoc_url=None if _is_strict_runtime else" in src
    assert "openapi_url=None if _is_strict_runtime else" in src
    assert "settings.env == \"production\"" not in src


def test_session_cookie_secure_for_prod_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "prod")
    auth_mod = importlib.import_module("api.v1.auth")
    response = Response()

    auth_mod._set_session_cookie(response, "jwt-value", 60)

    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "agenticorg_session=" in cookie_header
    assert "secure" in cookie_header


def test_csrf_cookie_secure_for_stage_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "stage")
    from auth.csrf import set_csrf_cookie

    response = Response()
    set_csrf_cookie(response, "csrf-value", max_age_seconds=60)

    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "agenticorg_csrf=" in cookie_header
    assert "secure" in cookie_header


def test_redis_url_resolution_prefers_agenticorg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import redis_url_from_env

    monkeypatch.setenv("AGENTICORG_REDIS_URL", "redis://canonical:6379/9")
    monkeypatch.setenv("REDIS_URL", "redis://legacy:6379/0")

    assert redis_url_from_env(default_db=1) == "redis://canonical:6379/9"


def test_redis_url_resolution_uses_settings_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config as config

    class _Settings:
        redis_url = "redis://settings:6379/8"

    monkeypatch.delenv("AGENTICORG_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(config, "settings", _Settings())

    assert config.redis_url_from_env() == "redis://settings:6379/8"
    assert config.redis_url_from_env(default_db=1) == "redis://localhost:6379/1"


def test_redis_helpers_do_not_read_legacy_redis_url_directly() -> None:
    for rel in (
        "core/auth_state.py",
        "core/async_redis.py",
        "api/v1/auth.py",
        "api/v1/chat.py",
    ):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "redis_url_from_env" in src, f"{rel} must use canonical Redis resolution"
        assert 'os.environ.get("REDIS_URL"' not in src
        assert '__import__("os").environ.get("REDIS_URL"' not in src


def test_redis_clients_use_bounded_socket_timeouts() -> None:
    for rel in (
        "core/auth_state.py",
        "core/async_redis.py",
        "api/v1/auth.py",
        "api/v1/chat.py",
    ):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "socket_connect_timeout=0.5" in src
        assert "socket_timeout=0.5" in src


def test_reset_password_blacklists_the_resolved_token_value() -> None:
    src = (REPO / "api" / "v1" / "auth.py").read_text(encoding="utf-8")
    assert "blacklist_token(token_value)" in src
    assert "blacklist_token(body.token)" not in src
