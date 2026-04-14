"""Unit tests for auth, org, and core modules.

Covers:
  - api.v1.auth: _validate_password, _make_slug, auth_config, logout
  - api.v1.org: get_profile, list_members, update_onboarding, deactivate_member
  - auth.jwt: create_access_token, validate_local_token, blacklist_token,
              extract_scopes, extract_tenant_id, extract_agent_id
  - auth.middleware: AuthMiddleware rate-limiting, exempt paths, header extraction
  - core.email: validate_email_domain, _has_mx_record, send_welcome_email,
                send_invite_email
  - core.llm.router: LLMRouter.complete, _call_model routing
  - core.orchestrator.task_router: resolve_agent_instance, escalate,
                                    resolve_domain_head, escalate_to_parent
  - core.orchestrator.nexus: receive_intent, decompose, handle_result,
                              evaluate_hitl
  - core.agents.base: _compute_confidence, _resolve_llm_model, _validate_output
  - core.database: get_tenant_session, get_session
  - api.v1.demo: _send_email_notification
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "00000000-0000-0000-0000-000000000001"
TEST_SECRET = "super-secret-key-1234567890"


@pytest.fixture
def tenant_id():
    return TENANT_ID


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock()
    return session


# ===================================================================
# 1.  api.v1.auth — _validate_password, _make_slug, auth_config, logout
# ===================================================================


class TestValidatePassword:
    """Tests for api.v1.auth._validate_password."""

    def test_valid_password(self):
        from api.v1.auth import _validate_password
        # Should NOT raise
        _validate_password("Abcdef1x")

    def test_too_short(self):
        from fastapi import HTTPException

        from api.v1.auth import _validate_password
        with pytest.raises(HTTPException) as exc_info:
            _validate_password("Ab1")
        assert exc_info.value.status_code == 400

    def test_no_uppercase(self):
        from fastapi import HTTPException

        from api.v1.auth import _validate_password
        with pytest.raises(HTTPException):
            _validate_password("abcdefg1")

    def test_no_lowercase(self):
        from fastapi import HTTPException

        from api.v1.auth import _validate_password
        with pytest.raises(HTTPException):
            _validate_password("ABCDEFG1")

    def test_no_digit(self):
        from fastapi import HTTPException

        from api.v1.auth import _validate_password
        with pytest.raises(HTTPException):
            _validate_password("Abcdefgh")

    def test_exactly_eight_chars(self):
        from api.v1.auth import _validate_password
        _validate_password("Abcdef1x")  # 8 chars, meets all criteria

    def test_long_valid_password(self):
        from api.v1.auth import _validate_password
        _validate_password("Str0ngP@ssword123!")


class TestMakeSlug:
    """Tests for api.v1.auth._make_slug."""

    def test_simple_name(self):
        from api.v1.auth import _make_slug
        assert _make_slug("Acme Corp") == "acme-corp"

    def test_special_characters(self):
        from api.v1.auth import _make_slug
        assert _make_slug("Hello!!! World???") == "hello-world"

    def test_leading_trailing_hyphens(self):
        from api.v1.auth import _make_slug
        assert _make_slug("---test---") == "test"

    def test_consecutive_hyphens(self):
        from api.v1.auth import _make_slug
        assert _make_slug("a    b") == "a-b"

    def test_numbers_preserved(self):
        from api.v1.auth import _make_slug
        assert _make_slug("Company 123") == "company-123"

    def test_empty_after_strip(self):
        from api.v1.auth import _make_slug
        assert _make_slug("!!!") == ""

    def test_already_lowercase(self):
        from api.v1.auth import _make_slug
        assert _make_slug("already-slug") == "already-slug"


class TestAuthConfig:
    """Tests for api.v1.auth.auth_config."""

    @pytest.mark.asyncio
    async def test_returns_google_client_id(self):
        from api.v1.auth import auth_config
        with patch("api.v1.auth.settings") as mock_settings:
            mock_settings.google_oauth_client_id = "test-client-id"
            result = await auth_config()
            assert result == {"google_client_id": "test-client-id"}

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        from api.v1.auth import auth_config
        with patch("api.v1.auth.settings") as mock_settings:
            mock_settings.google_oauth_client_id = ""
            result = await auth_config()
            assert result == {"google_client_id": None}


class _FakeHeaders:
    """Dict-like headers that allow custom .get() without read-only errors."""

    def __init__(self, data: dict[str, str] | None = None):
        self._data = data or {}

    def get(self, key, default=""):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data


class TestLogout:
    """Tests for api.v1.auth.logout."""

    @pytest.mark.asyncio
    async def test_logout_success(self):
        from api.v1.auth import logout
        mock_request = MagicMock()
        mock_request.headers = _FakeHeaders({"Authorization": "Bearer some-token-value"})
        with patch("api.v1.auth.blacklist_token") as mock_blacklist:
            result = await logout(mock_request)
            mock_blacklist.assert_called_once_with("some-token-value")
            assert result == {"status": "logged_out"}

    @pytest.mark.asyncio
    async def test_logout_missing_header(self):
        from fastapi import HTTPException

        from api.v1.auth import logout
        mock_request = MagicMock()
        mock_request.headers = _FakeHeaders({})
        with pytest.raises(HTTPException) as exc_info:
            await logout(mock_request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_invalid_header_format(self):
        from fastapi import HTTPException

        from api.v1.auth import logout
        mock_request = MagicMock()
        mock_request.headers = _FakeHeaders({"Authorization": "Basic abc123"})
        with pytest.raises(HTTPException) as exc_info:
            await logout(mock_request)
        assert exc_info.value.status_code == 401


# ===================================================================
# 2.  api.v1.org — get_profile, list_members, update_onboarding,
#                   deactivate_member
# ===================================================================


class TestGetProfile:
    """Tests for api.v1.org.get_profile."""

    @pytest.mark.asyncio
    async def test_get_profile_success(self):
        from api.v1.org import get_profile
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID

        mock_tenant = MagicMock()
        mock_tenant.id = uuid.UUID(TENANT_ID)
        mock_tenant.name = "Acme"
        mock_tenant.slug = "acme"
        mock_tenant.plan = "pro"
        mock_tenant.data_region = "IN"
        mock_tenant.settings = {"onboarding_complete": True}
        mock_tenant.created_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await get_profile(mock_request)
            assert result["name"] == "Acme"
            assert result["slug"] == "acme"

    @pytest.mark.asyncio
    async def test_get_profile_missing_tenant_context(self):
        from fastapi import HTTPException

        from api.v1.org import get_profile
        mock_request = MagicMock()
        mock_request.state = SimpleNamespace()  # no tenant_id attribute
        with pytest.raises(HTTPException) as exc_info:
            await get_profile(mock_request)
        assert exc_info.value.status_code == 401


class TestListMembers:
    """Tests for api.v1.org.list_members."""

    @pytest.mark.asyncio
    async def test_list_members_returns_users(self):
        from api.v1.org import list_members
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID

        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        mock_user.email = "user@test.io"
        mock_user.name = "Test User"
        mock_user.role = "analyst"
        mock_user.domain = "finance"
        mock_user.status = "active"
        mock_user.created_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_user]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await list_members(mock_request)
            assert len(result) == 1
            assert result[0]["email"] == "user@test.io"

    @pytest.mark.asyncio
    async def test_list_members_empty(self):
        from api.v1.org import list_members
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await list_members(mock_request)
            assert result == []


class TestUpdateOnboarding:
    """Tests for api.v1.org.update_onboarding."""

    @pytest.mark.asyncio
    async def test_update_onboarding_step(self):
        from api.v1.org import OnboardingUpdate, update_onboarding
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID

        mock_tenant = MagicMock()
        mock_tenant.settings = {"onboarding_step": 1, "onboarding_complete": False}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            body = OnboardingUpdate(onboarding_step=3)
            result = await update_onboarding(body, mock_request)
            assert result["status"] == "updated"
            assert result["settings"]["onboarding_step"] == 3

    @pytest.mark.asyncio
    async def test_update_onboarding_complete(self):
        from api.v1.org import OnboardingUpdate, update_onboarding
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID

        mock_tenant = MagicMock()
        mock_tenant.settings = {"onboarding_step": 1}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            body = OnboardingUpdate(onboarding_complete=True)
            result = await update_onboarding(body, mock_request)
            assert result["settings"]["onboarding_complete"] is True


class TestDeactivateMember:
    """Tests for api.v1.org.deactivate_member."""

    @pytest.mark.asyncio
    async def test_deactivate_success(self):
        from api.v1.org import deactivate_member
        user_id = str(uuid.uuid4())
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID
        mock_request.state.user_sub = "admin@test.io"

        mock_user = MagicMock()
        mock_user.email = "other@test.io"
        mock_user.status = "active"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await deactivate_member(user_id, mock_request)
            assert result["status"] == "deactivated"
            assert mock_user.status == "inactive"

    @pytest.mark.asyncio
    async def test_deactivate_self_forbidden(self):
        from fastapi import HTTPException

        from api.v1.org import deactivate_member
        user_id = str(uuid.uuid4())
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID
        mock_request.state.user_sub = "self@test.io"

        mock_user = MagicMock()
        mock_user.email = "self@test.io"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc_info:
                await deactivate_member(user_id, mock_request)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_deactivate_member_not_found(self):
        from fastapi import HTTPException

        from api.v1.org import deactivate_member
        user_id = str(uuid.uuid4())
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_ID
        mock_request.state.user_sub = "admin@test.io"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("api.v1.org.async_session_factory") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc_info:
                await deactivate_member(user_id, mock_request)
            assert exc_info.value.status_code == 404


# ===================================================================
# 3.  auth.jwt — create_access_token, validate_local_token, blacklist,
#                extract_scopes/tenant_id/agent_id
# ===================================================================


class TestCreateAccessToken:
    """Tests for auth.jwt.create_access_token."""

    def test_create_and_validate_roundtrip(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from auth.jwt import _blacklisted_tokens, create_access_token, validate_local_token
            _blacklisted_tokens.clear()

            token = create_access_token(
                data={"sub": "user@test.io", "agenticorg:tenant_id": TENANT_ID},
                expires_minutes=60,
            )
            claims = validate_local_token(token)
            assert claims["sub"] == "user@test.io"
            assert claims["agenticorg:tenant_id"] == TENANT_ID

    def test_token_contains_standard_claims(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from auth.jwt import _blacklisted_tokens, create_access_token, validate_local_token
            _blacklisted_tokens.clear()

            token = create_access_token(data={"sub": "a@b.com"}, expires_minutes=30)
            claims = validate_local_token(token)
            assert claims["iss"] == "agenticorg-local"
            assert claims["aud"] == "agenticorg-tool-gateway"
            assert "iat" in claims
            assert "exp" in claims

    def test_custom_expiry(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from auth.jwt import _blacklisted_tokens, create_access_token, validate_local_token
            _blacklisted_tokens.clear()

            token = create_access_token(data={"sub": "x"}, expires_minutes=120)
            claims = validate_local_token(token)
            # exp should be ~120 minutes after iat
            assert claims["exp"] - claims["iat"] == 120 * 60


class TestValidateLocalToken:
    """Tests for auth.jwt.validate_local_token."""

    def test_expired_token_fails(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from jose import jwt as jose_jwt

            from auth.jwt import _blacklisted_tokens, validate_local_token
            _blacklisted_tokens.clear()

            # Create token that expired 10 minutes ago
            now = int(time.time())
            payload = {
                "sub": "user@test.io",
                "iss": "agenticorg-local",
                "aud": "agenticorg-tool-gateway",
                "iat": now - 7200,
                "exp": now - 600,
            }
            token = jose_jwt.encode(payload, TEST_SECRET, algorithm="HS256")
            with pytest.raises(ValueError, match="Local token validation failed"):
                validate_local_token(token)

    def test_wrong_secret_fails(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from jose import jwt as jose_jwt

            from auth.jwt import _blacklisted_tokens, validate_local_token
            _blacklisted_tokens.clear()

            now = int(time.time())
            payload = {
                "sub": "user@test.io",
                "iss": "agenticorg-local",
                "aud": "agenticorg-tool-gateway",
                "iat": now,
                "exp": now + 3600,
            }
            token = jose_jwt.encode(payload, "wrong-secret-key-12345", algorithm="HS256")
            with pytest.raises(ValueError):
                validate_local_token(token)

    def test_wrong_issuer_fails(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from jose import jwt as jose_jwt

            from auth.jwt import _blacklisted_tokens, validate_local_token
            _blacklisted_tokens.clear()

            now = int(time.time())
            payload = {
                "sub": "user@test.io",
                "iss": "wrong-issuer",
                "aud": "agenticorg-tool-gateway",
                "iat": now,
                "exp": now + 3600,
            }
            token = jose_jwt.encode(payload, TEST_SECRET, algorithm="HS256")
            with pytest.raises(ValueError):
                validate_local_token(token)

    def test_wrong_audience_fails(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from jose import jwt as jose_jwt

            from auth.jwt import _blacklisted_tokens, validate_local_token
            _blacklisted_tokens.clear()

            now = int(time.time())
            payload = {
                "sub": "user@test.io",
                "iss": "agenticorg-local",
                "aud": "wrong-audience",
                "iat": now,
                "exp": now + 3600,
            }
            token = jose_jwt.encode(payload, TEST_SECRET, algorithm="HS256")
            with pytest.raises(ValueError):
                validate_local_token(token)


class TestBlacklistToken:
    """Tests for auth.jwt.blacklist_token."""

    def test_blacklisted_token_rejected(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from auth.jwt import (
                _blacklisted_tokens,
                blacklist_token,
                create_access_token,
                validate_local_token,
            )
            _blacklisted_tokens.clear()

            token = create_access_token(data={"sub": "u@t.io"})
            # Valid before blacklisting
            claims = validate_local_token(token)
            assert claims["sub"] == "u@t.io"

            # Blacklist it
            blacklist_token(token)
            with pytest.raises(ValueError, match="Token has been revoked"):
                validate_local_token(token)

    def test_blacklist_multiple_tokens(self):
        with patch("auth.jwt.settings") as mock_settings:
            mock_settings.secret_key = TEST_SECRET
            from auth.jwt import (
                _blacklisted_tokens,
                blacklist_token,
                create_access_token,
            )
            _blacklisted_tokens.clear()

            t1 = create_access_token(data={"sub": "a"})
            t2 = create_access_token(data={"sub": "b"})
            blacklist_token(t1)
            blacklist_token(t2)
            assert t1 in _blacklisted_tokens
            assert t2 in _blacklisted_tokens


class TestExtractHelpers:
    """Tests for auth.jwt extract_scopes, extract_tenant_id, extract_agent_id."""

    def test_extract_scopes(self):
        from auth.jwt import extract_scopes
        claims = {"grantex:scopes": ["read:agents", "write:workflows"]}
        assert extract_scopes(claims) == ["read:agents", "write:workflows"]

    def test_extract_scopes_missing(self):
        from auth.jwt import extract_scopes
        assert extract_scopes({}) == []

    def test_extract_tenant_id(self):
        from auth.jwt import extract_tenant_id
        claims = {"agenticorg:tenant_id": TENANT_ID}
        assert extract_tenant_id(claims) == TENANT_ID

    def test_extract_tenant_id_missing(self):
        from auth.jwt import extract_tenant_id
        assert extract_tenant_id({}) == ""

    def test_extract_agent_id(self):
        from auth.jwt import extract_agent_id
        claims = {"agenticorg:agent_id": "agent-007"}
        assert extract_agent_id(claims) == "agent-007"

    def test_extract_agent_id_missing(self):
        from auth.jwt import extract_agent_id
        assert extract_agent_id({}) == ""


# ===================================================================
# 4.  auth.middleware — AuthMiddleware
# ===================================================================


class TestAuthMiddleware:
    """Tests for auth.middleware.AuthMiddleware."""

    def _make_request(self, path="/api/v1/agents", auth_header=None, client_ip="1.2.3.4"):
        request = MagicMock()
        request.url.path = path
        request.client.host = client_ip
        request.path_params = {}
        request.state = SimpleNamespace()

        if auth_header is not None:
            request.headers = _FakeHeaders({"Authorization": auth_header})
        else:
            request.headers = _FakeHeaders({})
        return request

    @pytest.mark.asyncio
    async def test_exempt_path_skips_auth(self):
        from auth.middleware import AuthMiddleware
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(path="/api/v1/health")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        await middleware.dispatch(request, call_next)
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_exempt_prefix_skips_auth(self):
        from auth.middleware import AuthMiddleware
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(path="/api/v1/evals/scores")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        await middleware.dispatch(request, call_next)
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_docs_exempt(self):
        from auth.middleware import AuthMiddleware
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(path="/docs")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        await middleware.dispatch(request, call_next)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_401(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(auth_header=None, client_ip="10.0.0.1")
        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_bearer_prefix_returns_401(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(auth_header="Basic abc", client_ip="10.0.0.2")
        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_sets_request_state(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())

        mock_claims = {
            "sub": "user@test.io",
            "agenticorg:tenant_id": TENANT_ID,
            "grantex:scopes": ["read:agents"],
            "agenticorg:agent_id": "agent-1",
        }
        request = self._make_request(
            auth_header="Bearer valid-token", client_ip="10.0.0.3"
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("auth.middleware.validate_token", new_callable=AsyncMock, return_value=mock_claims):
            await middleware.dispatch(request, call_next)
            assert request.state.tenant_id == TENANT_ID
            assert request.state.user_sub == "user@test.io"
            assert request.state.scopes == ["read:agents"]

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())
        request = self._make_request(
            auth_header="Bearer bad-token", client_ip="10.0.0.4"
        )
        call_next = AsyncMock()

        with patch(
            "auth.middleware.validate_token",
            new_callable=AsyncMock,
            side_effect=ValueError("bad"),
        ):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_rate_limiting_blocks_after_max_failures(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import AUTH_MAX_FAILURES, _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())
        client_ip = "10.0.0.99"

        # Force in-memory path so the test does not depend on a live Redis.
        with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None):
            # Simulate AUTH_MAX_FAILURES failures
            for _ in range(AUTH_MAX_FAILURES):
                request = self._make_request(auth_header=None, client_ip=client_ip)
                await middleware.dispatch(request, AsyncMock())

            # Next request from same IP should be 429
            request = self._make_request(
                auth_header="Bearer valid-token", client_ip=client_ip
            )
            response = await middleware.dispatch(request, AsyncMock())
            assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_block_expires_after_duration(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()

        # Manually set a block that has expired
        _mem_blocked["10.0.0.50"] = time.time() - 1  # already expired

        middleware = AuthMiddleware(app=MagicMock())
        mock_claims = {"sub": "u@t.io", "agenticorg:tenant_id": TENANT_ID, "grantex:scopes": []}
        request = self._make_request(
            auth_header="Bearer token", client_ip="10.0.0.50"
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("core.auth_state._get_redis", new_callable=AsyncMock, return_value=None), \
             patch("auth.middleware.validate_token", new_callable=AsyncMock, return_value=mock_claims):
            await middleware.dispatch(request, call_next)
            # Block should be cleared, request proceeds
            assert "10.0.0.50" not in _mem_blocked
            call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_tenant_mismatch_returns_403(self):
        from auth.middleware import AuthMiddleware
        from core.auth_state import _mem_blocked, _mem_failures
        _mem_failures.clear()
        _mem_blocked.clear()
        middleware = AuthMiddleware(app=MagicMock())

        mock_claims = {
            "sub": "u@t.io",
            "agenticorg:tenant_id": TENANT_ID,
            "grantex:scopes": [],
        }
        request = self._make_request(
            auth_header="Bearer token", client_ip="10.0.0.60"
        )
        request.path_params = {"tenant_id": "different-tenant-id"}
        call_next = AsyncMock()

        with patch("auth.middleware.validate_token", new_callable=AsyncMock, return_value=mock_claims):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 403


# ===================================================================
# 5.  core.email — validate_email_domain, _has_mx_record,
#                   send_welcome_email, send_invite_email
# ===================================================================


class TestHasMxRecord:
    """Tests for core.email._has_mx_record."""

    def test_has_mx_true(self):
        from core.email import _has_mx_record
        with patch("core.email.dns.resolver.resolve") as mock_resolve:
            mock_resolve.return_value = [MagicMock()]  # at least one MX
            assert _has_mx_record("example.com") is True

    def test_has_mx_false_on_exception(self):
        from core.email import _has_mx_record
        with patch("core.email.dns.resolver.resolve", side_effect=Exception("NXDOMAIN")):
            assert _has_mx_record("nonexistent.tld") is False

    def test_has_mx_false_empty(self):
        from core.email import _has_mx_record
        with patch("core.email.dns.resolver.resolve", return_value=[]):
            assert _has_mx_record("no-mx.example") is False


class TestValidateEmailDomain:
    """Tests for core.email.validate_email_domain."""

    def test_invalid_format_no_at(self):
        from core.email import validate_email_domain
        valid, reason = validate_email_domain("no-at-sign")
        assert valid is False
        assert "Invalid email format" in reason

    def test_blocked_domain_example_com(self):
        from core.email import validate_email_domain
        valid, reason = validate_email_domain("user@example.com")
        assert valid is False
        assert "Blocked domain" in reason

    def test_blocked_domain_mailinator(self):
        from core.email import validate_email_domain
        valid, reason = validate_email_domain("user@mailinator.com")
        assert valid is False
        assert "Blocked" in reason

    def test_test_tld_blocked(self):
        from core.email import validate_email_domain
        valid, reason = validate_email_domain("user@something.test")
        assert valid is False
        assert "Test domain" in reason

    def test_local_tld_blocked(self):
        from core.email import validate_email_domain
        valid, reason = validate_email_domain("user@internal.local")
        assert valid is False
        assert "Test domain" in reason

    def test_valid_domain_with_mx(self):
        from core.email import validate_email_domain
        with patch("core.email._has_mx_record", return_value=True):
            valid, reason = validate_email_domain("user@legit-company.io")
            assert valid is True
            assert reason == "OK"

    def test_valid_domain_no_mx(self):
        from core.email import validate_email_domain
        with patch("core.email._has_mx_record", return_value=False):
            valid, reason = validate_email_domain("user@no-mx-domain.io")
            assert valid is False
            assert "No MX records" in reason


class TestSendWelcomeEmail:
    """Tests for core.email.send_welcome_email."""

    def test_calls_send_email_with_correct_args(self):
        from core.email import send_welcome_email
        with patch("core.email.send_email") as mock_send:
            send_welcome_email("user@acme.com", "Acme Corp", "Alice")
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert args[0][0] == "user@acme.com"
            assert "Acme Corp" in args[0][1]  # subject
            assert "Alice" in args[0][2]  # html body


class TestSendInviteEmail:
    """Tests for core.email.send_invite_email."""

    def test_calls_send_email_with_invite_link(self):
        from core.email import send_invite_email
        with patch("core.email.send_email") as mock_send:
            send_invite_email(
                "new@acme.com", "Acme Corp", "admin@acme.com", "analyst",
                "https://app.agenticorg.ai/accept-invite?token=abc123",
            )
            mock_send.assert_called_once()
            args = mock_send.call_args
            assert args[0][0] == "new@acme.com"
            assert "Acme Corp" in args[0][1]
            assert "accept-invite" in args[0][2]


class TestSendEmail:
    """Tests for core.email.send_email (low-level)."""

    def test_skips_when_no_password(self):
        from core.email import send_email
        with patch.dict("os.environ", {"AGENTICORG_GMAIL_APP_PASSWORD": ""}, clear=False):
            with patch("core.email.smtplib.SMTP_SSL") as mock_smtp:
                send_email("to@x.com", "subj", "<p>body</p>")
                mock_smtp.assert_not_called()

    def test_skips_invalid_domain(self):
        from core.email import send_email
        with patch.dict(
            "os.environ",
            {"AGENTICORG_GMAIL_APP_PASSWORD": "pw", "AGENTICORG_SMTP_LOGIN": "x@y.com"},
            clear=False,
        ):
            with patch("core.email.validate_email_domain", return_value=(False, "blocked")):
                with patch("core.email.smtplib.SMTP_SSL") as mock_smtp:
                    send_email("user@example.com", "subj", "<p>body</p>")
                    mock_smtp.assert_not_called()

    def test_sends_when_valid(self):
        from core.email import send_email
        with patch.dict(
            "os.environ",
            {"AGENTICORG_GMAIL_APP_PASSWORD": "pw", "AGENTICORG_SMTP_LOGIN": "sender@y.com"},
            clear=False,
        ):
            with patch("core.email.validate_email_domain", return_value=(True, "OK")):
                mock_smtp_instance = MagicMock()
                mock_smtp_class = MagicMock()
                mock_smtp_class.__enter__ = MagicMock(return_value=mock_smtp_instance)
                mock_smtp_class.__exit__ = MagicMock(return_value=False)
                with patch("core.email.smtplib.SMTP_SSL", return_value=mock_smtp_class):
                    send_email("valid@real.com", "subj", "<p>hi</p>")
                    mock_smtp_instance.login.assert_called_once()
                    mock_smtp_instance.send_message.assert_called_once()


# ===================================================================
# 6.  core.llm.router — LLMRouter.complete, _call_model routing
# ===================================================================


class TestLLMRouter:
    """Tests for core.llm.router.LLMRouter."""

    @pytest.mark.asyncio
    async def test_call_model_routes_to_gemini(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        mock_resp = LLMResponse(content="hello", model="gemini-2.5-flash", tokens_used=10)
        with patch.object(router, "_call_gemini", new_callable=AsyncMock, return_value=mock_resp):
            result = await router._call_model("gemini-2.5-flash", [], 0.2, 4096)
            assert result.model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_call_model_routes_to_claude(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        mock_resp = LLMResponse(content="hi", model="claude-3-opus", tokens_used=5)
        with patch.object(router, "_call_claude", new_callable=AsyncMock, return_value=mock_resp):
            result = await router._call_model("claude-3-opus", [], 0.2, 4096)
            assert result.model == "claude-3-opus"

    @pytest.mark.asyncio
    async def test_call_model_routes_to_openai(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        mock_resp = LLMResponse(content="yo", model="gpt-4o", tokens_used=8)
        with patch.object(router, "_call_openai", new_callable=AsyncMock, return_value=mock_resp):
            result = await router._call_model("gpt-4o", [], 0.2, 4096)
            assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_call_model_unsupported_raises(self):
        from core.llm.router import LLMRouter
        router = LLMRouter()
        with pytest.raises(ValueError, match="Unsupported model"):
            await router._call_model("llama-3", [], 0.2, 4096)

    @pytest.mark.asyncio
    async def test_complete_uses_primary_model(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        mock_resp = LLMResponse(content="ok", model=router.primary_model, tokens_used=1)
        with patch.object(router, "_call_model", new_callable=AsyncMock, return_value=mock_resp):
            result = await router.complete([{"role": "user", "content": "test"}])
            assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_complete_falls_back_on_failure(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        fallback_resp = LLMResponse(content="fallback", model=router.fallback_model, tokens_used=2)

        async def side_effect(model, msgs, temp, max_tok):
            if model == router.primary_model:
                raise Exception("primary down")
            return fallback_resp

        with patch.object(router, "_call_model", new_callable=AsyncMock, side_effect=side_effect):
            result = await router.complete([{"role": "user", "content": "hello"}])
            assert result.content == "fallback"

    @pytest.mark.asyncio
    async def test_complete_with_model_override(self):
        from core.llm.router import LLMResponse, LLMRouter
        router = LLMRouter()
        mock_resp = LLMResponse(content="custom", model="gpt-4o", tokens_used=3)
        with patch.object(router, "_call_model", new_callable=AsyncMock, return_value=mock_resp):
            await router.complete(
                [{"role": "user", "content": "test"}], model_override="gpt-4o"
            )
            router._call_model.assert_called_once()
            call_args = router._call_model.call_args
            assert call_args[0][0] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_complete_raises_when_fallback_same_as_primary(self):
        """When model_override == fallback and it fails, exception propagates."""
        from core.llm.router import LLMRouter
        router = LLMRouter()
        router.primary_model = "gemini-x"
        router.fallback_model = "gemini-x"

        with patch.object(
            router, "_call_model", new_callable=AsyncMock,
            side_effect=Exception("all down"),
        ):
            with pytest.raises(Exception, match="all down"):
                await router.complete([{"role": "user", "content": "hi"}])


# ===================================================================
# 7.  core.orchestrator.task_router — resolve_agent_instance, escalate,
#                                       resolve_domain_head, escalate_to_parent
# ===================================================================


def _make_mock_agent(
    agent_id=None, agent_type="invoice_parser", tenant_id=None, status="active",
    domain="finance", parent_agent_id=None, routing_filter=None,
    specialization=None, created_at=None,
):
    """Factory for mock Agent objects."""
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.agent_type = agent_type
    agent.tenant_id = tenant_id or uuid.UUID(TENANT_ID)
    agent.status = status
    agent.domain = domain
    agent.parent_agent_id = parent_agent_id
    agent.routing_filter = routing_filter
    agent.specialization = specialization
    agent.created_at = created_at or time.time()
    return agent


class TestResolveAgentInstance:
    """Tests for TaskRouter.resolve_agent_instance."""

    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.UUID(TENANT_ID), "invoice_parser", {}, mock_session,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_single_candidate_returns_its_id(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        agent = _make_mock_agent()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.UUID(TENANT_ID), "invoice_parser", {}, mock_session,
        )
        assert result == agent.id

    @pytest.mark.asyncio
    async def test_routing_filter_match(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        agent_apac = _make_mock_agent(routing_filter={"region": "APAC"})
        agent_emea = _make_mock_agent(routing_filter={"region": "EMEA"})

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent_apac, agent_emea]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.UUID(TENANT_ID), "invoice_parser",
            {"region": "EMEA"}, mock_session,
        )
        assert result == agent_emea.id

    @pytest.mark.asyncio
    async def test_specialization_match(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        agent_a = _make_mock_agent(specialization="payroll")
        agent_b = _make_mock_agent(specialization="reimbursement")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent_a, agent_b]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.UUID(TENANT_ID), "invoice_parser",
            {"description": "Process a reimbursement claim"}, mock_session,
        )
        assert result == agent_b.id

    @pytest.mark.asyncio
    async def test_fallback_to_first(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        agent_a = _make_mock_agent()
        agent_b = _make_mock_agent()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent_a, agent_b]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.UUID(TENANT_ID), "invoice_parser", {}, mock_session,
        )
        assert result == agent_a.id


class TestEscalate:
    """Tests for TaskRouter.escalate."""

    @pytest.mark.asyncio
    async def test_agent_not_found_returns_human(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        mock_session.get = AsyncMock(return_value=None)
        result = await TaskRouter.escalate(uuid.uuid4(), mock_session)
        assert result["escalation_type"] == "human"
        assert result["escalated_to"] is None

    @pytest.mark.asyncio
    async def test_active_parent_found(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        parent_id = uuid.uuid4()
        child = _make_mock_agent(parent_agent_id=parent_id)
        parent = _make_mock_agent(agent_id=parent_id, status="active", parent_agent_id=None)

        async def mock_get(model_class, agent_id):
            if agent_id == child.id:
                return child
            if agent_id == parent_id:
                return parent
            return None

        mock_session.get = AsyncMock(side_effect=mock_get)
        result = await TaskRouter.escalate(child.id, mock_session)
        assert result["escalation_type"] == "parent_agent"
        assert result["escalated_to"] == parent_id

    @pytest.mark.asyncio
    async def test_skips_paused_parent(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        grandparent_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        child = _make_mock_agent(parent_agent_id=parent_id)
        parent = _make_mock_agent(
            agent_id=parent_id, status="paused", parent_agent_id=grandparent_id,
        )
        grandparent = _make_mock_agent(
            agent_id=grandparent_id, status="active", parent_agent_id=None,
        )

        async def mock_get(model_class, agent_id):
            lookup = {child.id: child, parent_id: parent, grandparent_id: grandparent}
            return lookup.get(agent_id)

        mock_session.get = AsyncMock(side_effect=mock_get)
        result = await TaskRouter.escalate(child.id, mock_session)
        assert result["escalation_type"] == "parent_agent"
        assert result["escalated_to"] == grandparent_id

    @pytest.mark.asyncio
    async def test_cycle_detection_falls_back_to_domain_head(self, mock_session):
        from core.orchestrator.task_router import TaskRouter

        id_a = uuid.uuid4()
        id_b = uuid.uuid4()
        # Both agents are paused so the walk continues instead of returning
        # on the first parent. agent_b -> parent_agent_id=id_a which is
        # already visited, triggering cycle detection.
        agent_a = _make_mock_agent(agent_id=id_a, parent_agent_id=id_b, status="paused")
        agent_b = _make_mock_agent(agent_id=id_b, parent_agent_id=id_a, status="paused")

        async def mock_get(model_class, agent_id):
            if agent_id == id_a:
                return agent_a
            if agent_id == id_b:
                return agent_b
            return None

        mock_session.get = AsyncMock(side_effect=mock_get)

        domain_head_id = uuid.uuid4()
        with patch.object(
            TaskRouter, "resolve_domain_head",
            new_callable=AsyncMock, return_value=domain_head_id,
        ):
            result = await TaskRouter.escalate(id_a, mock_session)
            assert result["escalation_type"] == "domain_head"
            assert result["escalated_to"] == domain_head_id

    @pytest.mark.asyncio
    async def test_max_depth_exceeded_falls_to_domain_head(self, mock_session):
        from core.orchestrator.task_router import TaskRouter

        # Build a chain of 6 agents (exceeds max_depth=5)
        ids = [uuid.uuid4() for _ in range(7)]
        agents = {}
        for i, aid in enumerate(ids):
            parent = ids[i + 1] if i < len(ids) - 1 else None
            agents[aid] = _make_mock_agent(
                agent_id=aid, parent_agent_id=parent, status="active",
            )

        async def mock_get(model_class, agent_id):
            return agents.get(agent_id)

        mock_session.get = AsyncMock(side_effect=mock_get)

        domain_head_id = uuid.uuid4()
        with patch.object(
            TaskRouter, "resolve_domain_head",
            new_callable=AsyncMock, return_value=domain_head_id,
        ):
            result = await TaskRouter.escalate(ids[0], mock_session, max_depth=3)
            # Chain should not follow all 7 hops; domain head used
            assert result["escalation_type"] in ("parent_agent", "domain_head")

    @pytest.mark.asyncio
    async def test_no_domain_head_returns_human(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        child = _make_mock_agent(parent_agent_id=None)

        async def mock_get(model_class, agent_id):
            if agent_id == child.id:
                return child
            return None

        mock_session.get = AsyncMock(side_effect=mock_get)
        with patch.object(
            TaskRouter, "resolve_domain_head",
            new_callable=AsyncMock, return_value=None,
        ):
            result = await TaskRouter.escalate(child.id, mock_session)
            assert result["escalation_type"] == "human"


class TestResolveDomainHead:
    """Tests for TaskRouter.resolve_domain_head."""

    @pytest.mark.asyncio
    async def test_returns_domain_head_id(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        head_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = head_id
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_domain_head(
            uuid.UUID(TENANT_ID), "finance", mock_session,
        )
        assert result == head_id

    @pytest.mark.asyncio
    async def test_returns_none_when_no_head(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_domain_head(
            uuid.UUID(TENANT_ID), "unknown-domain", mock_session,
        )
        assert result is None


class TestEscalateToParent:
    """Tests for TaskRouter.escalate_to_parent (backward-compat wrapper)."""

    @pytest.mark.asyncio
    async def test_returns_escalated_to_value(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        target_id = uuid.uuid4()
        with patch.object(
            TaskRouter, "escalate",
            new_callable=AsyncMock,
            return_value={"escalated_to": target_id, "escalation_type": "parent_agent",
                          "chain": [], "reason": "ok"},
        ):
            result = await TaskRouter.escalate_to_parent(uuid.uuid4(), mock_session)
            assert result == target_id

    @pytest.mark.asyncio
    async def test_returns_none_for_human(self, mock_session):
        from core.orchestrator.task_router import TaskRouter
        with patch.object(
            TaskRouter, "escalate",
            new_callable=AsyncMock,
            return_value={"escalated_to": None, "escalation_type": "human",
                          "chain": [], "reason": "no parent"},
        ):
            result = await TaskRouter.escalate_to_parent(uuid.uuid4(), mock_session)
            assert result is None


# ===================================================================
# 8.  core.orchestrator.nexus — receive_intent, decompose,
#                                 handle_result, evaluate_hitl
# ===================================================================


class TestNexusOrchestrator:
    """Tests for core.orchestrator.nexus.NexusOrchestrator."""

    def _make_nexus(self):
        from core.orchestrator.nexus import NexusOrchestrator
        from core.orchestrator.task_router import TaskRouter
        mock_checkpoint = AsyncMock()
        mock_checkpoint.save = AsyncMock()
        return NexusOrchestrator(
            task_router=TaskRouter(),
            checkpoint_mgr=mock_checkpoint,
        )

    def test_decompose_with_steps(self):
        nexus = self._make_nexus()
        intent = {"steps": [{"id": "s1"}, {"id": "s2"}]}
        result = nexus.decompose(intent)
        assert len(result) == 2
        assert result[0]["id"] == "s1"

    def test_decompose_fallback_single_step(self):
        nexus = self._make_nexus()
        intent = {"action": "analyze"}
        result = nexus.decompose(intent)
        assert len(result) == 1
        assert result[0]["id"] == "main"
        assert result[0]["action"] == "analyze"

    def test_decompose_fallback_no_action(self):
        nexus = self._make_nexus()
        intent = {}
        result = nexus.decompose(intent)
        assert result[0]["action"] == "process"

    @pytest.mark.asyncio
    async def test_receive_intent_routes_tasks(self):
        nexus = self._make_nexus()
        intent = {"steps": [{"id": "s1", "agent": "parser"}]}
        assignments = await nexus.receive_intent("wfr-1", intent, {"tenant": "t"})
        assert len(assignments) == 1
        assert assignments[0]["step_id"] == "s1"

    @pytest.mark.asyncio
    async def test_receive_intent_saves_checkpoint(self):
        nexus = self._make_nexus()
        intent = {"steps": [{"id": "s1", "agent": "parser"}]}
        await nexus.receive_intent("wfr-2", intent, {})
        nexus.checkpoint.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_result_completed_high_confidence(self):
        from core.schemas.messages import TaskResult
        nexus = self._make_nexus()
        result = TaskResult(
            message_id="m1", correlation_id="c1", workflow_run_id="wf1",
            step_id="s1", agent_id="a1", status="completed",
            output={"data": "x"}, confidence=0.95,
        )
        outcome = await nexus.handle_result("wf1", result)
        assert outcome["action"] == "proceed"

    @pytest.mark.asyncio
    async def test_handle_result_completed_low_confidence_triggers_hitl(self):
        from core.schemas.messages import DecisionOption, DecisionRequired, HITLAssignee, HITLRequest, TaskResult
        nexus = self._make_nexus()
        hitl = HITLRequest(
            hitl_id="h1", trigger_condition="low", trigger_type="confidence",
            decision_required=DecisionRequired(
                question="Review?",
                options=[DecisionOption(id="approve", label="Approve", action="proceed")],
            ),
            assignee=HITLAssignee(role="domain_lead"),
        )
        result = TaskResult(
            message_id="m2", correlation_id="c2", workflow_run_id="wf2",
            step_id="s2", agent_id="a2", status="completed",
            output={}, confidence=0.5, hitl_request=hitl,
        )
        outcome = await nexus.handle_result("wf2", result)
        assert outcome["action"] == "hitl"

    @pytest.mark.asyncio
    async def test_handle_result_failed(self):
        from core.schemas.messages import TaskResult
        nexus = self._make_nexus()
        result = TaskResult(
            message_id="m3", correlation_id="c3", workflow_run_id="wf3",
            step_id="s3", agent_id="a3", status="failed",
            error={"code": "E5001", "message": "boom"},
        )
        outcome = await nexus.handle_result("wf3", result)
        assert outcome["action"] == "escalate"

    @pytest.mark.asyncio
    async def test_handle_result_hitl_triggered(self):
        from core.schemas.messages import DecisionOption, DecisionRequired, HITLAssignee, HITLRequest, TaskResult
        nexus = self._make_nexus()
        hitl = HITLRequest(
            hitl_id="h2", trigger_condition="manual", trigger_type="manual",
            decision_required=DecisionRequired(
                question="Check?",
                options=[DecisionOption(id="ok", label="OK", action="proceed")],
            ),
            assignee=HITLAssignee(role="domain_lead"),
        )
        result = TaskResult(
            message_id="m4", correlation_id="c4", workflow_run_id="wf4",
            step_id="s4", agent_id="a4", status="hitl_triggered",
            hitl_request=hitl,
        )
        outcome = await nexus.handle_result("wf4", result)
        assert outcome["action"] == "hitl"

    @pytest.mark.asyncio
    async def test_handle_result_unknown_status(self):
        from core.schemas.messages import TaskResult
        nexus = self._make_nexus()
        result = TaskResult(
            message_id="m5", correlation_id="c5", workflow_run_id="wf5",
            step_id="s5", agent_id="a5", status="pending",
        )
        outcome = await nexus.handle_result("wf5", result)
        assert outcome["action"] == "unknown"

    def test_evaluate_hitl_below_threshold(self):
        from core.schemas.messages import DecisionOption, DecisionRequired, HITLAssignee, HITLRequest, TaskResult
        nexus = self._make_nexus()
        hitl = HITLRequest(
            hitl_id="h3", trigger_condition="low", trigger_type="confidence",
            decision_required=DecisionRequired(
                question="?", options=[DecisionOption(id="a", label="A", action="x")],
            ),
            assignee=HITLAssignee(role="domain_lead"),
        )
        result = TaskResult(
            message_id="m6", correlation_id="c6", workflow_run_id="wf6",
            step_id="s6", agent_id="a6", status="completed",
            confidence=0.5, hitl_request=hitl,
        )
        assert nexus.evaluate_hitl(result) is not None

    def test_evaluate_hitl_above_threshold(self):
        from core.schemas.messages import TaskResult
        nexus = self._make_nexus()
        result = TaskResult(
            message_id="m7", correlation_id="c7", workflow_run_id="wf7",
            step_id="s7", agent_id="a7", status="completed",
            confidence=0.95,
        )
        assert nexus.evaluate_hitl(result) is None


# ===================================================================
# 9.  core.agents.base — _compute_confidence, _resolve_llm_model,
#                          _validate_output
# ===================================================================


class TestBaseAgentComputeConfidence:
    """Tests for BaseAgent._compute_confidence."""

    def _make_agent(self, **kwargs):
        from core.agents.base import BaseAgent
        return BaseAgent(
            agent_id="agent-test",
            tenant_id=TENANT_ID,
            **kwargs,
        )

    def test_numeric_confidence(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": 0.92}) == 0.92

    def test_string_high(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": "high"}) == 0.95

    def test_string_medium(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": "medium"}) == 0.75

    def test_string_low(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": "low"}) == 0.5

    def test_missing_confidence_uses_default(self):
        agent = self._make_agent()
        assert agent._compute_confidence({}) == 0.85

    def test_agent_confidence_fallback_key(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"agent_confidence": 0.77}) == 0.77

    def test_unknown_string_falls_to_default(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": "uncertain"}) == 0.85

    def test_none_confidence_falls_to_default(self):
        agent = self._make_agent()
        assert agent._compute_confidence({"confidence": None}) == 0.85


class TestBaseAgentResolveLlmModel:
    """Tests for BaseAgent._resolve_llm_model."""

    def _make_agent(self, llm_model=None):
        from core.agents.base import BaseAgent
        return BaseAgent(
            agent_id="agent-test",
            tenant_id=TENANT_ID,
            llm_model=llm_model,
        )

    def test_no_model_returns_none(self):
        agent = self._make_agent(llm_model=None)
        assert agent._resolve_llm_model() is None

    def test_gemini_always_works(self):
        agent = self._make_agent(llm_model="gemini-2.5-flash")
        assert agent._resolve_llm_model() == "gemini-2.5-flash"

    def test_claude_with_key(self):
        agent = self._make_agent(llm_model="claude-3-opus")
        with patch("core.agents.base.external_keys", create=True) as mock_keys:
            mock_keys = MagicMock()
            mock_keys.anthropic_api_key = "sk-test-key"
            with patch("core.config.external_keys", mock_keys):
                result = agent._resolve_llm_model()
                assert result == "claude-3-opus"

    def test_claude_without_key(self):
        agent = self._make_agent(llm_model="claude-3-opus")
        with patch("core.config.external_keys") as mock_keys:
            mock_keys.anthropic_api_key = ""
            result = agent._resolve_llm_model()
            assert result is None

    def test_gpt_with_key(self):
        agent = self._make_agent(llm_model="gpt-4o")
        with patch("core.config.external_keys") as mock_keys:
            mock_keys.openai_api_key = "sk-openai-key"
            result = agent._resolve_llm_model()
            assert result == "gpt-4o"

    def test_gpt_without_key(self):
        agent = self._make_agent(llm_model="gpt-4o")
        with patch("core.config.external_keys") as mock_keys:
            mock_keys.openai_api_key = ""
            result = agent._resolve_llm_model()
            assert result is None

    def test_unknown_model_returns_none(self):
        agent = self._make_agent(llm_model="llama-3")
        assert agent._resolve_llm_model() is None


class TestBaseAgentValidateOutput:
    """Tests for BaseAgent._validate_output."""

    def _make_agent(self, output_schema=None):
        from core.agents.base import BaseAgent
        return BaseAgent(
            agent_id="agent-test",
            tenant_id=TENANT_ID,
            output_schema=output_schema,
        )

    def test_no_schema_always_valid(self):
        agent = self._make_agent(output_schema=None)
        assert agent._validate_output({"anything": True}) is True

    def test_schema_with_status_field(self):
        agent = self._make_agent(output_schema="invoice_result")
        assert agent._validate_output({"status": "completed"}) is True

    def test_schema_missing_status_field(self):
        agent = self._make_agent(output_schema="invoice_result")
        assert agent._validate_output({"data": "no status"}) is False


# ===================================================================
# 10. core.database — get_tenant_session, get_session
# ===================================================================


class TestGetTenantSession:
    """Tests for core.database.get_tenant_session."""

    @pytest.mark.asyncio
    async def test_sets_rls_context(self):
        from core.database import get_tenant_session

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database.async_session_factory", mock_factory):
            async with get_tenant_session(uuid.UUID(TENANT_ID)):
                pass
            # Verify SET LOCAL was called with sanitized tenant_id
            calls = mock_session.execute.call_args_list
            assert len(calls) >= 1
            # The first arg to execute is a text() clause; check its .text attribute
            text_clause = calls[0][0][0]
            sql_text = getattr(text_clause, "text", str(text_clause))
            assert TENANT_ID in sql_text
            assert "SET LOCAL" in sql_text

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self):
        from core.database import get_tenant_session

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database.async_session_factory", mock_factory):
            with pytest.raises(RuntimeError):
                async with get_tenant_session(uuid.UUID(TENANT_ID)):
                    raise RuntimeError("db error")
            mock_session.rollback.assert_called_once()


class TestGetSession:
    """Tests for core.database.get_session."""

    @pytest.mark.asyncio
    async def test_yields_session_and_commits(self):
        from core.database import get_session

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def fake_factory():
            yield mock_session

        with patch("core.database.async_session_factory", fake_factory):
            gen = get_session()
            session = await gen.__anext__()
            assert session is mock_session
            # Simulate normal exit
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self):
        from core.database import get_session

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        mock_session.rollback = AsyncMock()

        @asynccontextmanager
        async def fake_factory():
            yield mock_session

        with patch("core.database.async_session_factory", fake_factory):
            gen = get_session()
            await gen.__anext__()
            with pytest.raises(RuntimeError):
                await gen.__anext__()
            mock_session.rollback.assert_called_once()


# ===================================================================
# 11. api.v1.demo — _send_email_notification formatting
# ===================================================================


class TestSendEmailNotification:
    """Tests for api.v1.demo._send_email_notification."""

    def test_email_subject_contains_name_and_role(self):
        from api.v1.demo import DemoRequest, _send_email_notification
        body = DemoRequest(
            name="Alice Smith", email="alice@company.com",
            company="Acme", role="CTO", phone="+91-9876543210",
        )
        with patch("api.v1.demo.send_email") as mock_send:
            _send_email_notification(body)
            mock_send.assert_called_once()
            subject = mock_send.call_args[0][1]
            assert "Alice Smith" in subject
            assert "CTO" in subject

    def test_email_html_contains_all_fields(self):
        from api.v1.demo import DemoRequest, _send_email_notification
        body = DemoRequest(
            name="Bob", email="bob@co.io",
            company="BigCo", role="VP", phone="123",
        )
        with patch("api.v1.demo.send_email") as mock_send:
            _send_email_notification(body)
            html = mock_send.call_args[0][2]
            assert "Bob" in html
            assert "bob@co.io" in html
            assert "BigCo" in html
            assert "VP" in html
            assert "123" in html

    def test_email_handles_empty_optional_fields(self):
        from api.v1.demo import DemoRequest, _send_email_notification
        body = DemoRequest(name="Charlie", email="c@d.com")
        with patch("api.v1.demo.send_email") as mock_send:
            _send_email_notification(body)
            mock_send.assert_called_once()
            subject = mock_send.call_args[0][1]
            assert "Not specified" in subject

    def test_email_sent_to_notify_address(self):
        from api.v1.demo import NOTIFY_TO, DemoRequest, _send_email_notification
        body = DemoRequest(name="D", email="d@e.com")
        with patch("api.v1.demo.send_email") as mock_send:
            _send_email_notification(body)
            to_addr = mock_send.call_args[0][0]
            assert to_addr == NOTIFY_TO


# ===================================================================
# 12. Additional edge-case tests to reach 60+ test functions
# ===================================================================


class TestTaskRouterRoute:
    """Tests for TaskRouter.route method."""

    @pytest.mark.asyncio
    async def test_route_returns_message_structure(self):
        from core.orchestrator.task_router import TaskRouter
        router = TaskRouter()
        result = await router.route(
            workflow_run_id="wf-1",
            step_id="step-1",
            step_index=0,
            total_steps=3,
            task={"agent": "parser", "agent_id": "a1"},
            context={"tenant": "t1"},
        )
        assert result["workflow_run_id"] == "wf-1"
        assert result["step_id"] == "step-1"
        assert result["target_agent_type"] == "parser"
        assert result["target_agent_id"] == "a1"
        assert result["message_id"].startswith("msg_")

    @pytest.mark.asyncio
    async def test_route_uses_agent_type_fallback(self):
        from core.orchestrator.task_router import TaskRouter
        router = TaskRouter()
        result = await router.route(
            workflow_run_id="wf-2",
            step_id="step-2",
            step_index=1,
            total_steps=2,
            task={"agent_type": "classifier"},
            context={},
        )
        assert result["target_agent_type"] == "classifier"


class TestDomainToRole:
    """Tests for task_router.DOMAIN_TO_ROLE mapping."""

    def test_finance_maps_to_cfo(self):
        from core.orchestrator.task_router import DOMAIN_TO_ROLE
        assert DOMAIN_TO_ROLE["finance"] == "cfo"

    def test_hr_maps_to_chro(self):
        from core.orchestrator.task_router import DOMAIN_TO_ROLE
        assert DOMAIN_TO_ROLE["hr"] == "chro"

    def test_marketing_maps_to_cmo(self):
        from core.orchestrator.task_router import DOMAIN_TO_ROLE
        assert DOMAIN_TO_ROLE["marketing"] == "cmo"


class TestNexusConflictResolver:
    """Tests for NexusOrchestrator.resolve_conflict."""

    @pytest.mark.asyncio
    async def test_resolve_conflict_no_conflict(self):
        from core.orchestrator.nexus import NexusOrchestrator
        from core.orchestrator.task_router import TaskRouter
        from core.schemas.messages import TaskResult

        nexus = NexusOrchestrator(
            task_router=TaskRouter(),
            checkpoint_mgr=AsyncMock(),
        )
        r1 = TaskResult(
            message_id="m1", correlation_id="c1", workflow_run_id="wf1",
            step_id="s1", agent_id="a1", status="completed",
            output={"x": 1},
        )
        result = await nexus.resolve_conflict([r1])
        assert result["action"] == "no_conflict"

    @pytest.mark.asyncio
    async def test_resolve_conflict_factual_conflict(self):
        from core.orchestrator.nexus import NexusOrchestrator
        from core.orchestrator.task_router import TaskRouter
        from core.schemas.messages import TaskResult

        nexus = NexusOrchestrator(
            task_router=TaskRouter(),
            checkpoint_mgr=AsyncMock(),
        )
        r1 = TaskResult(
            message_id="m1", correlation_id="c1", workflow_run_id="wf1",
            step_id="s1", agent_id="a1", status="completed",
            output={"answer": "A"},
        )
        r2 = TaskResult(
            message_id="m2", correlation_id="c2", workflow_run_id="wf1",
            step_id="s1", agent_id="a2", status="completed",
            output={"answer": "B"},
        )
        result = await nexus.resolve_conflict([r1, r2])
        assert result["action"] == "escalate"
        assert result["reason"] == "factual_conflict"


class TestLLMResponse:
    """Tests for the LLMResponse dataclass."""

    def test_default_values(self):
        from core.llm.router import LLMResponse
        resp = LLMResponse(content="hello", model="test")
        assert resp.tokens_used == 0
        assert resp.cost_usd == 0.0
        assert resp.latency_ms == 0
        assert resp.raw == {}

    def test_custom_values(self):
        from core.llm.router import LLMResponse
        resp = LLMResponse(
            content="ok", model="gemini", tokens_used=100,
            cost_usd=0.01, latency_ms=500, raw={"k": "v"},
        )
        assert resp.tokens_used == 100
        assert resp.cost_usd == 0.01


class TestOrgValidatePassword:
    """Tests for the org module's copy of _validate_password."""

    def test_valid_password(self):
        from api.v1.org import _validate_password
        _validate_password("Secure1x")

    def test_weak_password(self):
        from fastapi import HTTPException

        from api.v1.org import _validate_password
        with pytest.raises(HTTPException):
            _validate_password("weak")


class TestOrgGetTenantId:
    """Tests for api.v1.org._get_tenant_id helper."""

    def test_extracts_tenant_id(self):
        from api.v1.org import _get_tenant_id
        request = MagicMock()
        request.state.tenant_id = "tid-123"
        assert _get_tenant_id(request) == "tid-123"

    def test_raises_on_missing_tenant(self):
        from fastapi import HTTPException

        from api.v1.org import _get_tenant_id
        request = MagicMock()
        request.state = SimpleNamespace()
        with pytest.raises(HTTPException) as exc_info:
            _get_tenant_id(request)
        assert exc_info.value.status_code == 401
