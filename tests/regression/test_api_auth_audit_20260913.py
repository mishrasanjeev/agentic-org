"""Regression tests for the 2026-09-13 API/auth audit findings 1-13.

Each class replays the failure scenario from the audit at unit level (mocked
DB/Redis) so the fix is pinned without a live Postgres:

 1. per-IP throttles behind a reverse proxy (``api.client_ip.client_ip``)
 2. legacy API-key scope aliases vs. the scope-family enforcement
 3. invite acceptance must not revive a deactivated member
 4. DSAR access/export/status are admin-gated like erase
 5. logout blacklist TTL follows the token's remaining lifetime
 6. cron triggers are reachable and the key is compared constant-time
 7. Google login honours ``email_verified`` and user status
 8. e-mail case normalisation at every credential entry point
 9. session-state check fails closed when the users row is missing
10. anonymous SSO discovery no longer enumerates other tenants
11. ``create_api_key`` refuses non-user principals with 403 (not a FK 500)
12. WebSocket feed ignores query-string session tokens
13. second-granularity ``iat`` vs microsecond revocation watermark
"""

from __future__ import annotations

import inspect
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _session_factory(session: AsyncMock) -> MagicMock:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _request(ip: str = "10.0.0.9", xff: str | None = None) -> SimpleNamespace:
    headers = {} if xff is None else {"x-forwarded-for": xff}
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers=headers, state=SimpleNamespace())


# ---------------------------------------------------------------------------
# 1. Client IP behind a reverse proxy
# ---------------------------------------------------------------------------


class TestClientIp:
    def test_xff_ignored_when_proxy_headers_untrusted(self):
        from api import client_ip as mod

        with patch.object(mod, "settings", SimpleNamespace(trust_proxy_headers=False)):
            assert mod.client_ip(_request("10.0.0.9", "203.0.113.5, 10.0.0.1")) == "10.0.0.9"

    def test_xff_first_hop_used_when_trusted(self):
        from api import client_ip as mod

        with patch.object(mod, "settings", SimpleNamespace(trust_proxy_headers=True)):
            # Cloud Run appends the peer it saw; the rightmost hop is the only
            # trustworthy one (a client-supplied first hop must not win).
            assert mod.client_ip(_request("10.0.0.9", " 203.0.113.5 , 10.0.0.1")) == "10.0.0.1"
            assert mod.client_ip(_request("10.0.0.9", "10.0.0.1")) == "10.0.0.1"
            # No header -> peer address, never "unknown".
            assert mod.client_ip(_request("10.0.0.9")) == "10.0.0.9"
            assert mod.client_ip(_request("10.0.0.9", "   ")) == "10.0.0.9"

    def test_setting_defaults_off(self):
        from core.config import Settings

        assert Settings.model_fields["trust_proxy_headers"].default is False

    def test_every_throttle_site_uses_the_shared_helper(self):
        from api import route_enforcement
        from api.v1 import auth as auth_mod
        from api.v1 import demo
        from auth.grantex_middleware import GrantexAuthMiddleware

        sources = {
            "route_enforcement": inspect.getsource(route_enforcement),
            "grantex_middleware.dispatch": inspect.getsource(GrantexAuthMiddleware.dispatch),
            "auth.signup": inspect.getsource(auth_mod.signup),
            "auth.login": inspect.getsource(auth_mod.login),
            "auth.forgot_password": inspect.getsource(auth_mod.forgot_password),
            "demo.submit_demo_request": inspect.getsource(demo.submit_demo_request),
        }
        for name, src in sources.items():
            assert "request.client.host" not in src, name
            assert "resolve_client_ip(" in src, name

    @pytest.mark.asyncio
    async def test_login_throttle_keys_on_forwarded_ip_when_trusted(self):
        from api import client_ip as mod
        from api.v1.auth import LoginRequest, login

        seen: list[str] = []

        async def fake_check(ip: str) -> bool:
            seen.append(ip)
            return True

        req = _request("10.0.0.9", "203.0.113.5")
        with (
            patch.object(mod, "settings", SimpleNamespace(trust_proxy_headers=True)),
            patch("api.v1.auth._check_rate_limit", side_effect=fake_check),
        ):
            with pytest.raises(HTTPException) as exc:
                await login(LoginRequest(email="a@x.io", password="p"), req, MagicMock())
        assert exc.value.status_code == 429
        assert seen == ["203.0.113.5"]

    @pytest.mark.asyncio
    async def test_middleware_auth_failure_block_keys_on_forwarded_ip(self):
        from api import client_ip as mod
        from auth.grantex_middleware import GrantexAuthMiddleware

        blocked_for: list[str] = []

        async def fake_blocked(ip: str) -> bool:
            blocked_for.append(ip)
            return True

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/agents"
        request.headers = {"Authorization": "Basic nope", "x-forwarded-for": "203.0.113.5"}
        request.client.host = "10.0.0.9"
        with (
            patch.object(mod, "settings", SimpleNamespace(trust_proxy_headers=True)),
            patch("auth.grantex_middleware.is_ip_blocked", side_effect=fake_blocked),
        ):
            resp = await GrantexAuthMiddleware(app=MagicMock()).dispatch(request, AsyncMock())
        assert resp.status_code == 429
        assert blocked_for == ["203.0.113.5"]


# ---------------------------------------------------------------------------
# 2. Legacy API-key scopes vs. scope families
# ---------------------------------------------------------------------------


class TestScopeAliases:
    OLD_DEFAULTS = ["agents:read", "agents:run", "connectors:read", "mcp:read", "mcp:call", "a2a:read"]

    def _req(self, method: str, scopes: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            method=method, state=SimpleNamespace(auth_mode="api_key", scopes=scopes), url=SimpleNamespace(path="/x")
        )

    def test_old_default_key_can_run_agents_and_list_connectors(self):
        from api.route_enforcement import _check_scope

        _check_scope(self._req("POST", self.OLD_DEFAULTS), {"auth_required": True, "scope": "agents.execute"})
        _check_scope(self._req("GET", self.OLD_DEFAULTS), {"auth_required": True, "scope": "connectors.list"})

    def test_separator_variants_are_equivalent(self):
        from api.route_enforcement import _check_scope

        _check_scope(self._req("GET", ["agents.read"]), {"auth_required": True, "scope": "agents.list"})
        _check_scope(
            self._req("GET", ["report_schedules:read"]), {"auth_required": True, "scope": "report_schedules.list"}
        )

    def test_read_only_key_still_cannot_write(self):
        from api.route_enforcement import _check_scope

        with pytest.raises(HTTPException) as exc:
            _check_scope(
                self._req("POST", ["agents:read", "connectors:read"]),
                {"auth_required": True, "scope": "agents.execute"},
            )
        assert exc.value.status_code == 403

    def test_new_default_scopes_are_canonical(self):
        from api.route_enforcement import SCOPE_FAMILIES
        from api.v1 import api_keys

        src = inspect.getsource(api_keys.create_api_key)
        assert '"agents:write"' in src and '"connectors.read"' in src
        assert '"agents:run"' not in src and '"connectors:read"' not in src
        assert SCOPE_FAMILIES["agents"][1] == "agents:write"
        assert SCOPE_FAMILIES["connectors"][0] == "connectors.read"


# ---------------------------------------------------------------------------
# 3. Invite acceptance must not revive a deactivated member
# ---------------------------------------------------------------------------


class TestInviteStatusGate:
    def _session(self, user):
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        result.first.return_value = (user, SimpleNamespace(name="Acme"))
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        return session

    @pytest.mark.asyncio
    async def test_accept_invite_rejects_inactive_user(self):
        from api.v1.org import AcceptInviteRequest, accept_invite

        uid = uuid.uuid4()
        user = SimpleNamespace(id=uid, email="m@acme.io", status="inactive", tenant_id=uuid.uuid4())
        session = self._session(user)
        with (
            patch(
                "api.v1.org._decode_invite_claims", return_value={"agenticorg:user_id": str(uid), "sub": "m@acme.io"}
            ),
            patch("api.v1.org.async_session_factory", _session_factory(session)),
        ):
            with pytest.raises(HTTPException) as exc:
                await accept_invite(AcceptInviteRequest(token="tok", password="Passw0rd!"))
        assert exc.value.status_code == 409
        assert user.status == "inactive"
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accept_invite_still_activates_pending_user(self):
        from api.v1.org import AcceptInviteRequest, accept_invite

        uid = uuid.uuid4()
        user = SimpleNamespace(
            id=uid,
            email="M@Acme.io",
            name="M",
            role="analyst",
            domain="finance",
            status="pending",
            tenant_id=uuid.uuid4(),
        )
        session = self._session(user)
        with (
            patch(
                "api.v1.org._decode_invite_claims", return_value={"agenticorg:user_id": str(uid), "sub": "m@acme.io"}
            ),
            patch("api.v1.org.async_session_factory", _session_factory(session)),
        ):
            out = await accept_invite(AcceptInviteRequest(token="tok", password="Passw0rd!"))
        assert user.status == "active"
        assert out["access_token"]

    @pytest.mark.asyncio
    async def test_invite_info_rejects_non_pending_user(self):
        from api.v1.org import get_invite_info

        uid = uuid.uuid4()
        user = SimpleNamespace(id=uid, email="m@acme.io", name="M", role="analyst", status="inactive")
        with (
            patch("api.v1.org._decode_invite_claims", return_value={"agenticorg:user_id": str(uid)}),
            patch("api.v1.org.async_session_factory", _session_factory(self._session(user))),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_invite_info(token="tok")
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# 4. DSAR routes are admin-gated
# ---------------------------------------------------------------------------


class TestDsarAdminGate:
    @pytest.mark.parametrize("path", ["/dsar/access", "/dsar/erase", "/dsar/export", "/dsar/{request_id}"])
    def test_route_requires_tenant_admin(self, path):
        from api.deps import require_tenant_admin
        from api.v1.compliance import router

        routes = [r for r in router.routes if getattr(r, "path", "") == path]
        assert len(routes) == 1, path
        assert require_tenant_admin in routes[0].dependencies, path


# ---------------------------------------------------------------------------
# 5. Blacklist TTL follows the token lifetime
# ---------------------------------------------------------------------------


class TestBlacklistTtl:
    @pytest.mark.asyncio
    async def test_auth_jwt_blacklist_covers_long_lived_token(self):
        from auth import jwt as jwt_mod

        redis = MagicMock()
        redis.setex = AsyncMock()
        jwt_mod._blacklisted_tokens.clear()
        with (
            patch.object(jwt_mod, "settings", SimpleNamespace(secret_key="x" * 40, env="development")),
            patch.object(jwt_mod, "_get_redis", lambda: redis),
            patch.object(jwt_mod, "_auth_state_strict", lambda: False),
        ):
            token = jwt_mod.create_access_token({"sub": "u@x.io"}, expires_minutes=24 * 60)
            await jwt_mod.blacklist_token(token)
            short = jwt_mod.create_access_token({"sub": "u@x.io"}, expires_minutes=5)
            await jwt_mod.blacklist_token(short)
            await jwt_mod.blacklist_token("not-a-jwt")

        ttls = [call.args[1] for call in redis.setex.await_args_list]
        assert ttls[0] >= 24 * 3600, ttls
        assert ttls[1] == jwt_mod._BLACKLIST_TTL
        assert ttls[2] == jwt_mod._BLACKLIST_TTL
        assert jwt_mod._blacklisted_tokens[token] >= time.time() + 24 * 3600 - 5
        jwt_mod._blacklisted_tokens.clear()

    @pytest.mark.asyncio
    async def test_core_auth_state_blacklist_covers_long_lived_token(self, monkeypatch):
        from auth.jwt import create_access_token
        from core import auth_state

        redis = MagicMock()
        redis.setex = AsyncMock()
        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=redis))
        monkeypatch.setattr(auth_state, "_strict", lambda: False)
        monkeypatch.setenv("AGENTICORG_SECRET_KEY", "k" * 40)
        with patch("auth.jwt.settings", SimpleNamespace(secret_key="x" * 40, env="development")):
            token = create_access_token({"sub": "u@x.io"}, expires_minutes=7 * 24 * 60)
        await auth_state.blacklist_token(token)
        await auth_state.blacklist_token("opaque")
        ttls = [call.args[1] for call in redis.setex.await_args_list]
        assert ttls[0] >= 7 * 24 * 3600
        assert ttls[1] == auth_state.TOKEN_BLACKLIST_TTL


# ---------------------------------------------------------------------------
# 6. Cron triggers reachable; constant-time key compare; no exception echo
# ---------------------------------------------------------------------------


class TestCronTriggers:
    @pytest.mark.asyncio
    async def test_middleware_exempts_cron_prefix(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        assert "/api/v1/cron/" in GrantexAuthMiddleware.EXEMPT_PREFIXES
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/cron/compliance-alerts"
        request.headers = {}
        request.cookies = {}
        call_next = AsyncMock(return_value="handled")
        assert await GrantexAuthMiddleware(app=MagicMock()).dispatch(request, call_next) == "handled"

    def test_key_compare_is_constant_time_and_still_403s(self):
        from api.v1 import cron

        assert "hmac.compare_digest" in inspect.getsource(cron._verify_cron_key)
        with patch.object(cron, "_get_cron_api_key", return_value="k1"):
            cron._verify_cron_key("k1")
            with pytest.raises(HTTPException) as exc:
                cron._verify_cron_key("k2")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_job_failure_does_not_echo_exception_text(self):
        from api.v1 import cron

        with (
            patch.object(cron, "_get_cron_api_key", return_value="k1"),
            patch(
                "core.cron.compliance_alerts.run_compliance_alert_cron",
                AsyncMock(side_effect=RuntimeError("dsn=postgres://u:p@h")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await cron.trigger_compliance_alerts(x_cron_key="k1")
        assert exc.value.status_code == 500
        assert "postgres" not in exc.value.detail


# ---------------------------------------------------------------------------
# 7. Google login: email_verified + user status
# ---------------------------------------------------------------------------


class TestGoogleLogin:
    def _session(self, users):
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = users
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        return session

    @pytest.mark.asyncio
    async def test_unverified_email_is_rejected_before_any_lookup(self):
        from api.v1 import auth as auth_mod

        session = self._session([])
        with (
            patch.object(auth_mod.settings, "google_oauth_client_id", "cid"),
            patch(
                "api.v1.auth.google_id_token.verify_oauth2_token",
                return_value={"email": "U@X.io", "email_verified": False},
            ),
            patch("api.v1.auth.async_session_factory", _session_factory(session)),
        ):
            with pytest.raises(HTTPException) as exc:
                await auth_mod.google_login(auth_mod.GoogleLoginRequest(credential="c"), MagicMock())
        assert exc.value.status_code == 401
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "inactive"])
    async def test_non_active_user_gets_401_and_no_new_tenant(self, status):
        from api.v1 import auth as auth_mod

        user = SimpleNamespace(
            email="u@x.io", status=status, tenant_id=uuid.uuid4(), role="admin", name="U", domain="all"
        )
        session = self._session([user])
        with (
            patch.object(auth_mod.settings, "google_oauth_client_id", "cid"),
            patch(
                "api.v1.auth.google_id_token.verify_oauth2_token",
                return_value={"email": "U@X.io", "email_verified": True},
            ),
            patch("api.v1.auth.async_session_factory", _session_factory(session)),
        ):
            with pytest.raises(HTTPException) as exc:
                await auth_mod.google_login(auth_mod.GoogleLoginRequest(credential="c"), MagicMock())
        assert exc.value.status_code == 401
        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 8. E-mail case normalisation
# ---------------------------------------------------------------------------


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()


class TestEmailNormalisation:
    @pytest.mark.asyncio
    async def test_signup_stores_lower_cased_email(self):
        from api.v1 import auth as auth_mod

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        added: list = []
        session.add = MagicMock(side_effect=added.append)
        body = auth_mod.SignupRequest(
            org_name="Acme", admin_name="A", admin_email="  Admin@Acme.IO ", password="Passw0rd!"
        )
        with (
            patch("core.auth_state.check_signup_rate", AsyncMock(return_value=False)),
            patch("api.v1.auth._hash_password", AsyncMock(return_value="h")),
            patch("api.v1.auth.seed_tenant_defaults", AsyncMock()),
            patch("api.v1.auth.send_welcome_email", MagicMock()),
            patch("api.v1.auth.async_session_factory", _session_factory(session)),
        ):
            await auth_mod.signup(body, _request(), MagicMock())
        from core.models.user import User

        users = [o for o in added if isinstance(o, User)]
        assert len(users) == 1
        assert users[0].email == "admin@acme.io"
        assert "lower(users.email) = 'admin@acme.io'" in _compiled(session.execute.await_args_list[0].args[0])

    @pytest.mark.asyncio
    async def test_login_and_forgot_password_lookup_case_insensitively(self):
        from api.v1 import auth as auth_mod

        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        with (
            patch("api.v1.auth._check_rate_limit", AsyncMock(return_value=False)),
            patch("api.v1.auth.async_session_factory", _session_factory(session)),
        ):
            with pytest.raises(HTTPException):
                await auth_mod.login(
                    auth_mod.LoginRequest(email="Admin@Acme.IO", password="p"), _request(), MagicMock()
                )
        assert "lower(users.email) = 'admin@acme.io'" in _compiled(session.execute.await_args.args[0])

        session.execute.reset_mock()
        with (
            patch("api.v1.auth.auth_state.check_window_rate", AsyncMock(return_value=False)),
            patch("api.v1.auth.async_session_factory", _session_factory(session)),
        ):
            await auth_mod.forgot_password(auth_mod.ForgotPasswordRequest(email="ADMIN@acme.io"), _request())
        assert "lower(users.email) = 'admin@acme.io'" in _compiled(session.execute.await_args.args[0])

    @pytest.mark.asyncio
    async def test_invite_normalises_and_dedupes_case_insensitively(self):
        from api.v1 import org as org_mod

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [None, SimpleNamespace(name="Acme")]
        session.execute = AsyncMock(return_value=result)
        added: list = []
        session.add = MagicMock(side_effect=added.append)
        request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(uuid.uuid4()), user_sub="admin@acme.io"))
        body = org_mod.InviteRequest(email=" New.Member@Acme.IO ", role="analyst", domain="finance")
        with (
            patch("api.v1.org.async_session_factory", _session_factory(session)),
            patch("api.v1.org.issue_code", AsyncMock(return_value="code")),
            patch("api.v1.org.send_invite_email", MagicMock()),
        ):
            out = await org_mod.invite_member(body, request)
        assert out["email"] == "new.member@acme.io"
        assert added[0].email == "new.member@acme.io"
        assert "lower(users.email) = 'new.member@acme.io'" in _compiled(session.execute.await_args_list[0].args[0])

    def test_google_login_uses_case_insensitive_lookup(self):
        from api.v1 import auth as auth_mod

        assert "func.lower(User.email) == email" in inspect.getsource(auth_mod.google_login)


# ---------------------------------------------------------------------------
# 9 + 13. Session-state check: missing row fails closed; watermark floor
# ---------------------------------------------------------------------------


class TestSessionStateFailClosed:
    def test_missing_row_rejects_but_degraded_lookup_does_not(self):
        from core.auth_state import UserSessionState

        assert UserSessionState(found=False).rejects_token(time.time()) == "user_missing"
        assert UserSessionState(found=False, lookup_failed=True).rejects_token(time.time()) is None

    @pytest.mark.asyncio
    async def test_relaxed_db_failure_is_marked_as_degraded(self, monkeypatch):
        from core import auth_state

        auth_state._mem_user_state.clear()
        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_state, "_strict", lambda: False)
        monkeypatch.setattr(auth_state, "_load_user_state_from_db", AsyncMock(side_effect=OSError("db down")))
        state = await auth_state.get_user_session_state(str(uuid.uuid4()), "a@x.io")
        assert state.found is False and state.lookup_failed is True

    @pytest.mark.asyncio
    async def test_middleware_rejects_legacy_token_for_missing_user(self):
        from auth.grantex_middleware import GrantexAuthMiddleware
        from core.auth_state import UserSessionState

        claims = {"sub": "gone@x.io", "agenticorg:tenant_id": str(uuid.uuid4()), "iat": int(time.time())}
        request = SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="9.9.9.9"))
        with (
            patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
            patch(
                "auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=UserSessionState(found=False))
            ),
            patch("auth.grantex_middleware.is_ip_blocked", AsyncMock(return_value=False)),
            patch("auth.grantex_middleware.record_auth_failure", AsyncMock(return_value=False)),
        ):
            resp = await GrantexAuthMiddleware(app=MagicMock())._handle_legacy_token(
                request, AsyncMock(), "tok", "9.9.9.9"
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_websocket_rejects_legacy_token_for_missing_user(self):
        from api.websocket import feed
        from core.auth_state import UserSessionState

        tid = str(uuid.uuid4())
        claims = {"sub": "gone@x.io", "agenticorg:tenant_id": tid, "iat": int(time.time()), "grantex:scopes": []}
        websocket = SimpleNamespace(headers={"authorization": "Bearer tok"}, cookies={}, query_params={})
        with (
            patch("api.websocket.feed.validate_token", AsyncMock(return_value=claims)),
            patch(
                "auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=UserSessionState(found=False))
            ),
        ):
            with pytest.raises(feed.WebSocketAuthError) as exc:
                await feed.authenticate_websocket(websocket, tid)
        assert exc.value.code == "invalid_token"

    def test_only_legacy_paths_consult_session_state(self):
        from api.websocket import feed
        from auth.grantex_middleware import GrantexAuthMiddleware

        assert "check_user_session_state" in inspect.getsource(GrantexAuthMiddleware._handle_legacy_token)
        assert "check_user_session_state" not in inspect.getsource(GrantexAuthMiddleware._handle_api_key)
        assert "check_user_session_state" not in inspect.getsource(GrantexAuthMiddleware._handle_grantex_token)
        assert "check_user_session_state" not in inspect.getsource(feed._claims_from_api_key)
        assert "check_user_session_state" not in inspect.getsource(feed._claims_from_grantex_token)

    def test_token_issued_in_same_second_as_watermark_is_honoured(self):
        from core.auth_state import UserSessionState

        state = UserSessionState(found=True, status="active", sessions_invalid_before=1_700_000_000.734512)
        assert state.rejects_token(1_700_000_000) is None  # same second, second-granularity iat
        assert state.rejects_token(1_700_000_001) is None
        assert state.rejects_token(1_699_999_999) == "session_revoked"
        assert state.rejects_token(None) == "session_revoked"


# ---------------------------------------------------------------------------
# 10. SSO discovery
# ---------------------------------------------------------------------------


class TestSsoDiscovery:
    def _configs(self):
        return [
            SimpleNamespace(
                provider_key="okta-a",
                display_name="A",
                provider_type="oidc",
                tenant_id=uuid.uuid4(),
                allowed_domains=[],
            ),
            SimpleNamespace(
                provider_key="okta-b",
                display_name="B",
                provider_type="oidc",
                tenant_id=uuid.uuid4(),
                allowed_domains=["Acme.com"],
            ),
        ]

    @pytest.mark.asyncio
    async def test_only_domain_matched_configs_are_returned(self):
        from api.v1.sso import list_providers

        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = self._configs()
        session.execute = AsyncMock(return_value=result)
        with patch("api.v1.sso.async_session_factory", _session_factory(session)):
            out = await list_providers(email="u@ACME.com")
            other = await list_providers(email="u@other.org")
        assert [p["provider_key"] for p in out["providers"]] == ["okta-b"]
        assert other["providers"] == []


# ---------------------------------------------------------------------------
# 11. create_api_key for non-user principals
# ---------------------------------------------------------------------------


class TestCreateApiKeyPrincipal:
    @pytest.mark.asyncio
    async def test_non_user_admin_gets_403_not_fk_violation(self):
        from api.v1.api_keys import CreateKeyRequest, create_api_key

        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(uuid.uuid4()), user_sub="apikey:ao_sk_abc"))
        with patch("api.v1.api_keys.async_session_factory", _session_factory(session)):
            with pytest.raises(HTTPException) as exc:
                await create_api_key(CreateKeyRequest(name="ci"), request)
        assert exc.value.status_code == 403
        assert "A human administrator is required" in exc.value.detail
        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 12. WebSocket feed ignores query-string credentials
# ---------------------------------------------------------------------------


class TestWebSocketTokenExtraction:
    def test_query_string_tokens_are_ignored(self):
        from api.websocket.feed import _extract_token

        for key in ("token", "access_token", "ws_ticket"):
            ws = SimpleNamespace(cookies={}, headers={}, query_params={key: "jwt"})
            assert _extract_token(ws) == ""

    def test_cookie_and_bearer_still_work(self):
        from api.websocket.feed import _extract_token

        assert _extract_token(SimpleNamespace(cookies={"agenticorg_session": "c"}, headers={}, query_params={})) == "c"
        assert (
            _extract_token(SimpleNamespace(cookies={}, headers={"authorization": "Bearer b"}, query_params={})) == "b"
        )
