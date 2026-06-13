"""Regression coverage for production incidents found on 2026-06-13."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Text

TENANT_ID = "00000000-0000-0000-0000-000000000001"


class _Headers:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


def _request(auth_header: str | None = None, client_ip: str = "203.0.113.10"):
    request = MagicMock()
    request.url.path = "/api/v1/auth/me"
    request.client.host = client_ip
    request.cookies.get.return_value = ""
    request.headers = _Headers(
        {"Authorization": auth_header} if auth_header is not None else {}
    )
    request.path_params = {}
    request.state = SimpleNamespace()
    return request


def test_audit_action_accepts_full_agent_create_contract() -> None:
    """Allowed agent names/types must not overflow audit_log.action."""
    from core.models.audit import AuditLog
    from core.schemas.api import AgentCreate

    body = AgentCreate(
        name="n" * 255,
        agent_type="t" * 100,
        domain="sales",
    )
    action = f"Created agent '{body.name}' ({body.agent_type})"

    assert len(action) > 100
    assert isinstance(AuditLog.__table__.c.action.type, Text)


@pytest.mark.asyncio
async def test_legacy_auth_missing_credentials_do_not_increment_lockout() -> None:
    from auth.middleware import AuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = AuthMiddleware(app=MagicMock())
    call_next = AsyncMock()
    client_ip = "203.0.113.11"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
        response = await middleware.dispatch(
            _request(auth_header=None, client_ip=client_ip),
            call_next,
        )

    assert response.status_code == 401
    assert client_ip not in _mem_failures
    call_next.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_auth_invalid_credentials_still_increment_lockout() -> None:
    from auth.middleware import AuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = AuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.12"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None), \
         patch(
             "auth.middleware.validate_token",
             new_callable=AsyncMock,
             side_effect=ValueError("bad token"),
         ):
        response = await middleware.dispatch(
            _request(auth_header="Bearer bad-token", client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert len(_mem_failures[client_ip]) == 1


@pytest.mark.asyncio
async def test_legacy_auth_malformed_authorization_increments_lockout() -> None:
    from auth.middleware import AuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = AuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.15"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
        response = await middleware.dispatch(
            _request(auth_header="Basic bad-token", client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert len(_mem_failures[client_ip]) == 1


@pytest.mark.asyncio
async def test_grantex_auth_missing_credentials_do_not_increment_lockout() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = GrantexAuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.13"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
        response = await middleware.dispatch(
            _request(auth_header=None, client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert client_ip not in _mem_failures


@pytest.mark.asyncio
async def test_grantex_auth_invalid_credentials_still_increment_lockout() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = GrantexAuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.14"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None), \
         patch("auth.grantex_middleware._is_grantex_token", return_value=False), \
         patch(
             "auth.grantex_middleware.validate_token",
             new_callable=AsyncMock,
             side_effect=ValueError("bad token"),
         ):
        response = await middleware.dispatch(
            _request(auth_header="Bearer bad-token", client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert len(_mem_failures[client_ip]) == 1


@pytest.mark.asyncio
async def test_grantex_auth_empty_bearer_increments_lockout() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = GrantexAuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.16"

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
        response = await middleware.dispatch(
            _request(auth_header="Bearer ", client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert len(_mem_failures[client_ip]) == 1
