"""Regression coverage for production incidents found on 2026-06-13."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Text

TENANT_ID = "00000000-0000-0000-0000-000000000001"
REPO = Path(__file__).resolve().parents[2]


class _Headers:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


def _request(
    auth_header: str | None = None,
    client_ip: str = "203.0.113.10",
    session_cookie: str = "",
):
    request = MagicMock()
    request.url.path = "/api/v1/auth/me"
    request.client.host = client_ip
    request.cookies.get.side_effect = (
        lambda name, default="": session_cookie
        if name == "agenticorg_session"
        else default
    )
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


def test_org_invite_rejects_names_larger_than_user_column() -> None:
    from pydantic import ValidationError

    from api.v1.org import InviteRequest

    with pytest.raises(ValidationError):
        InviteRequest(
            email="large-name@example.com",
            name="x" * 256,
            role="analyst",
        )


def test_local_e2e_clears_persistent_redis_auth_state_before_run() -> None:
    """A failed local e2e run must not poison the next run via Redis volume state."""
    src = (REPO / "scripts" / "local_e2e.sh").read_text(encoding="utf-8")

    assert "clear_e2e_redis_auth_state" in src
    assert "Waiting for redis ready" in src
    for pattern in (
        "auth:blocked:*",
        "auth:failures:*",
        "auth:login_attempts:*",
        "auth:signup:*",
        "token_blacklist:*",
    ):
        assert pattern in src


def test_local_e2e_uses_fresh_ui_port_and_own_process_readiness() -> None:
    """Do not let a stale Vite server satisfy the local e2e readiness check."""
    src = (REPO / "scripts" / "local_e2e.sh").read_text(encoding="utf-8")

    assert "REQUESTED_LOCAL_UI_PORT" in src
    assert "choose_free_local_port" in src
    assert "LOCAL_UI_PORT unset; selected free UI port" in src
    assert 'LOCAL_E2E_WORKERS="${LOCAL_E2E_WORKERS:-1}"' in src
    assert 'LOCAL_UI_MODE="${LOCAL_UI_MODE:-preview}"' in src
    assert "npm run preview" in src
    assert 'VITE_API_URL="${API_URL}" npm run build' not in src
    assert 'PLAYWRIGHT_ARGS+=("--workers=${LOCAL_E2E_WORKERS}")' in src
    assert "UI_LOG=\"/tmp/local_e2e_ui_${LOCAL_UI_PORT}.log\"" in src

    process_check = src.index('if ! kill -0 "$UI_PID"')
    curl_check = src.index('if curl -fsS "${UI_URL}"')
    assert process_check < curl_check


def test_vite_dev_server_ignores_playwright_artifacts() -> None:
    """Playwright trace/report churn must not destabilize the local dev server."""
    src = (REPO / "ui" / "vite.config.ts").read_text(encoding="utf-8")

    assert "watch:" in src
    assert "**/test-results/**" in src
    assert "**/playwright-report/**" in src
    assert "**/coverage/**" in src
    assert "const proxy =" in src
    assert "preview: { proxy }" in src


@pytest.mark.asyncio
async def test_industry_pack_install_insert_supplies_id_without_schema_default() -> None:
    """Raw installer SQL must not depend on a DB-side UUID default existing."""
    from core.agents.packs.installer import _merge_install_assets
    from core.models.industry_pack_install import IndustryPackInstall

    class _NoRows:
        def first(self):
            return None

    class _Result:
        def mappings(self):
            return _NoRows()

    class _Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def execute(self, stmt, params):
            self.calls.append((str(stmt), params))
            return _Result()

    session = _Session()
    await _merge_install_assets(
        session,
        UUID(TENANT_ID),
        "ca-firm",
        [uuid4()],
        [uuid4()],
    )

    insert_sql, params = session.calls[1]
    assert "INSERT INTO industry_pack_installs" in insert_sql
    assert "id," in insert_sql
    assert isinstance(params["id"], UUID)
    assert IndustryPackInstall.__table__.c.id.server_default is not None


def test_auth_logout_e2e_uses_disposable_login_not_shared_token() -> None:
    """Logout blacklists the current JWT, so e2e must not logout the shared suite token."""
    src = (REPO / "ui" / "e2e" / "qa-module-2-auth.spec.ts").read_text(
        encoding="utf-8"
    )
    block = src.split(
        'test("TC-AUTH-010 logout clears session and redirects protected routes"',
        1,
    )[1].split("// ---------------------------------------------------------------------------\n// TC-AUTH-011", 1)[0]

    assert "DEMO.ceo.email" in block
    assert "DEMO.ceo.password" in block
    assert "setSessionToken(page, E2E_TOKEN)" not in block


@pytest.mark.asyncio
async def test_successful_login_clears_ip_login_throttle() -> None:
    from api.v1.auth import _check_rate_limit, _clear_rate_limit, _login_attempts

    client_ip = "203.0.113.101"
    _login_attempts.clear()

    with patch("api.v1.auth._get_throttle_redis", new_callable=AsyncMock, return_value=None):
        assert await _check_rate_limit(client_ip) is False
        assert client_ip in _login_attempts
        await _clear_rate_limit(client_ip)
        assert client_ip not in _login_attempts
        for _ in range(5):
            assert await _check_rate_limit(client_ip) is False
        assert await _check_rate_limit(client_ip) is True


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
async def test_legacy_auth_missing_credentials_ignore_existing_ip_lockout() -> None:
    from auth.middleware import AuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = AuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.110"
    _mem_blocked[client_ip] = 9999999999.0

    with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
        response = await middleware.dispatch(
            _request(auth_header=None, client_ip=client_ip),
            AsyncMock(),
        )

    assert response.status_code == 401
    assert client_ip not in _mem_failures


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
async def test_legacy_auth_explicit_bearer_wins_over_ambient_cookie() -> None:
    from auth.middleware import AuthMiddleware

    middleware = AuthMiddleware(app=MagicMock())
    call_next = AsyncMock(return_value=MagicMock(status_code=200))
    mock_claims = {
        "sub": "api@test.io",
        "agenticorg:tenant_id": TENANT_ID,
        "grantex:scopes": ["agenticorg:admin"],
    }

    with patch(
        "auth.middleware.validate_token",
        new_callable=AsyncMock,
        return_value=mock_claims,
    ) as validate_token:
        response = await middleware.dispatch(
            _request(
                auth_header="Bearer explicit-token",
                session_cookie="stale-cookie-token",
            ),
            call_next,
        )

    assert getattr(response, "status_code", 200) == 200
    validate_token.assert_awaited_once_with("explicit-token")
    call_next.assert_called_once()


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
async def test_grantex_auth_missing_credentials_ignore_existing_ip_lockout() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core.auth_state import _mem_blocked, _mem_failures

    _mem_failures.clear()
    _mem_blocked.clear()
    middleware = GrantexAuthMiddleware(app=MagicMock())
    client_ip = "203.0.113.111"
    _mem_blocked[client_ip] = 9999999999.0

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


@pytest.mark.asyncio
async def test_grantex_auth_explicit_bearer_wins_over_ambient_cookie() -> None:
    from auth.grantex_middleware import GrantexAuthMiddleware

    middleware = GrantexAuthMiddleware(app=MagicMock())
    call_next = AsyncMock(return_value=MagicMock(status_code=200))
    mock_claims = {
        "sub": "api@test.io",
        "agenticorg:tenant_id": TENANT_ID,
        "grantex:scopes": ["agenticorg:admin"],
    }

    with patch("auth.grantex_middleware._is_grantex_token", return_value=False), \
         patch(
             "auth.grantex_middleware.validate_token",
             new_callable=AsyncMock,
             return_value=mock_claims,
         ) as validate_token:
        response = await middleware.dispatch(
            _request(
                auth_header="Bearer explicit-token",
                session_cookie="stale-cookie-token",
            ),
            call_next,
        )

    assert getattr(response, "status_code", 200) == 200
    validate_token.assert_awaited_once_with("explicit-token")
    call_next.assert_called_once()
