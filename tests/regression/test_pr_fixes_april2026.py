"""Regression tests for April 2026 PR merges.

Covers:
  PR #6  — Secret key hardening: dev default in dev, rejected in production
  PR #7  — API key endpoints restricted to admin scope
  Local  — Auth failure counters cleared on success in grantex_middleware
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

# ═══════════════════════════════════════════════════════════════════════════
# PR #6: Secret key hardening
# ═══════════════════════════════════════════════════════════════════════════


class TestSecretKeyHardening:
    """Settings must have a safe default for dev but reject it in production."""

    def test_dev_default_loads_without_env_var(self):
        """Development config should load without AGENTICORG_SECRET_KEY set."""
        from core.config import Settings

        with patch.dict("os.environ", {}, clear=False):
            # Remove the env var if set, so Settings falls back to default
            import os
            env_backup = os.environ.pop("AGENTICORG_SECRET_KEY", None)
            try:
                s = Settings(env="development", _env_file=None)
                assert s.secret_key == "dev-only-secret-key"
                assert len(s.secret_key) >= 16
            finally:
                if env_backup is not None:
                    os.environ["AGENTICORG_SECRET_KEY"] = env_backup

    def test_production_rejects_default_key(self):
        """Production must not run with the dev fallback secret."""
        from core.config import Settings

        with pytest.raises(ValidationError, match="AGENTICORG_SECRET_KEY"):
            Settings(env="production", secret_key="dev-only-secret-key", _env_file=None)

    def test_staging_with_explicit_key_works(self):
        """Staging with a real secret key should work."""
        from core.config import Settings

        s = Settings(env="staging", secret_key="a-real-production-secret-key-here")
        assert s.secret_key == "a-real-production-secret-key-here"

    def test_production_with_explicit_key_works(self):
        """Production with a real secret key should work."""
        from core.config import Settings

        s = Settings(env="production", secret_key="prod-secret-minimum-16-chars")
        assert s.env == "production"

    def test_short_key_rejected_everywhere(self):
        """Keys shorter than 16 chars are rejected in any environment."""
        from core.config import Settings

        with pytest.raises(ValidationError):
            Settings(secret_key="short")


# ═══════════════════════════════════════════════════════════════════════════
# PR #7: API key endpoints admin-only
# ═══════════════════════════════════════════════════════════════════════════


class TestApiKeyAdminScope:
    """API key endpoints must require agenticorg:admin scope."""

    def test_require_scope_rejects_non_admin(self):
        """Non-admin users get 403 on API key endpoints."""
        from api.deps import require_scope

        dep = require_scope("agenticorg:admin")
        # Extract the actual checker function from Depends
        checker = dep.dependency

        request = MagicMock()
        request.state.scopes = ["agents:read", "agents:run"]

        with pytest.raises(HTTPException) as exc:
            checker(request)
        assert exc.value.status_code == 403
        assert "Missing scope" in exc.value.detail

    def test_require_scope_allows_admin(self):
        """Admin users pass the scope check."""
        from api.deps import require_scope

        dep = require_scope("agenticorg:admin")
        checker = dep.dependency

        request = MagicMock()
        request.state.scopes = ["agenticorg:admin"]

        # Should not raise
        checker(request)

    def test_require_scope_allows_admin_wildcard(self):
        """Users with agenticorg:admin* prefix pass."""
        from api.deps import require_scope

        dep = require_scope("agenticorg:admin")
        checker = dep.dependency

        request = MagicMock()
        request.state.scopes = ["agenticorg:admin:full"]

        checker(request)

    def test_api_keys_router_has_admin_dependency(self):
        """The api_keys router must have the admin scope dependency."""
        from api.v1.api_keys import router

        # Check that router dependencies include the scope check
        assert len(router.dependencies) > 0
        dep_fns = [str(d.dependency) for d in router.dependencies]
        assert any("checker" in fn or "require_scope" in fn for fn in dep_fns), (
            f"Expected admin scope dependency, got: {dep_fns}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Auth failure counter clearing (grantex_middleware.py)
# ═══════════════════════════════════════════════════════════════════════════

TENANT_ID = "00000000-0000-0000-0000-000000000001"


class TestGrantexMiddlewareFailureClearing:
    """Successful auth must clear prior failure counters for the IP."""

    def _make_request(self, auth_header="", client_ip="10.0.0.1"):
        request = MagicMock()
        request.url.path = "/api/v1/agents"
        request.headers.get.return_value = auth_header
        request.client.host = client_ip
        request.path_params = {}
        request.state = MagicMock()
        return request

    @pytest.mark.asyncio
    async def test_legacy_token_success_clears_failures(self):
        """Successful legacy JWT auth clears prior failure history."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _blocked_ips,
            _failed_attempts,
        )

        _failed_attempts.clear()
        _blocked_ips.clear()

        client_ip = "10.0.0.100"
        _failed_attempts[client_ip] = [time.time() - 10, time.time() - 5]

        middleware = GrantexAuthMiddleware(app=MagicMock())
        mock_claims = {
            "sub": "user@test.io",
            "agenticorg:tenant_id": TENANT_ID,
            "grantex:scopes": [],
        }
        request = self._make_request(
            auth_header="Bearer valid-jwt-token",
            client_ip=client_ip,
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch(
            "auth.grantex_middleware.validate_token",
            new_callable=AsyncMock,
            return_value=mock_claims,
        ), patch(
            "auth.grantex_middleware._is_grantex_token",
            return_value=False,
        ):
            await middleware.dispatch(request, call_next)

        assert client_ip not in _failed_attempts
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_auth_records_failure(self):
        """Failed legacy JWT auth records a failure."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _blocked_ips,
            _failed_attempts,
        )

        _failed_attempts.clear()
        _blocked_ips.clear()

        client_ip = "10.0.0.101"
        middleware = GrantexAuthMiddleware(app=MagicMock())
        request = self._make_request(
            auth_header="Bearer bad-token",
            client_ip=client_ip,
        )
        call_next = AsyncMock()

        with patch(
            "auth.grantex_middleware.validate_token",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid token"),
        ), patch(
            "auth.grantex_middleware._is_grantex_token",
            return_value=False,
        ):
            resp = await middleware.dispatch(request, call_next)

        assert resp.status_code == 401
        assert client_ip in _failed_attempts
        assert len(_failed_attempts[client_ip]) == 1

    @pytest.mark.asyncio
    async def test_block_expiry_clears_stale_failures(self):
        """When an IP block expires, stale failure counters are also cleared."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _blocked_ips,
            _failed_attempts,
        )

        _failed_attempts.clear()
        _blocked_ips.clear()

        client_ip = "10.0.0.102"
        _blocked_ips[client_ip] = time.time() - 1  # Expired block
        _failed_attempts[client_ip] = [time.time() - 100] * 10

        middleware = GrantexAuthMiddleware(app=MagicMock())
        mock_claims = {
            "sub": "user@test.io",
            "agenticorg:tenant_id": TENANT_ID,
            "grantex:scopes": [],
        }
        request = self._make_request(
            auth_header="Bearer valid-token",
            client_ip=client_ip,
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch(
            "auth.grantex_middleware.validate_token",
            new_callable=AsyncMock,
            return_value=mock_claims,
        ), patch(
            "auth.grantex_middleware._is_grantex_token",
            return_value=False,
        ):
            await middleware.dispatch(request, call_next)

        assert client_ip not in _blocked_ips
        assert client_ip not in _failed_attempts

    @pytest.mark.asyncio
    async def test_missing_auth_header_records_failure(self):
        """Missing Authorization header records a failure."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _blocked_ips,
            _failed_attempts,
        )

        _failed_attempts.clear()
        _blocked_ips.clear()

        client_ip = "10.0.0.103"
        middleware = GrantexAuthMiddleware(app=MagicMock())
        request = self._make_request(auth_header="", client_ip=client_ip)
        call_next = AsyncMock()

        resp = await middleware.dispatch(request, call_next)

        assert resp.status_code == 401
        assert client_ip in _failed_attempts

    @pytest.mark.asyncio
    async def test_clear_failures_method(self):
        """_clear_failures removes IP from tracking dict."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _failed_attempts,
        )

        _failed_attempts.clear()
        client_ip = "10.0.0.104"
        _failed_attempts[client_ip] = [time.time()]

        middleware = GrantexAuthMiddleware(app=MagicMock())
        middleware._clear_failures(client_ip)

        assert client_ip not in _failed_attempts

    @pytest.mark.asyncio
    async def test_clear_failures_noop_for_unknown_ip(self):
        """_clear_failures does not raise for unknown IPs."""
        from auth.grantex_middleware import (
            GrantexAuthMiddleware,
            _failed_attempts,
        )

        _failed_attempts.clear()
        middleware = GrantexAuthMiddleware(app=MagicMock())
        middleware._clear_failures("192.168.1.1")  # Should not raise
