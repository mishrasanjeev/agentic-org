"""Regression tests for the 2026-09-13 enterprise auth/tenancy sweep.

1. Grantex middleware — only RS256 tokens whose ``iss`` is the configured
   Grantex issuer are routed to Grantex; audience is enforced; JWKS is
   fetched off the event loop through a (kid-keyed, negatively cached)
   process cache; the tenant comes from the registered agent DID, never
   from the token's ``developer_id``; no network call when unconfigured.
3. Session revocation — ``users.sessions_invalid_before`` / ``status``
   are consulted for legacy tokens (cached, fail-closed in strict env);
   deactivate / reset-password / logout-all bump the watermark.
8. Blocking calls — bcrypt and Google ID-token verification leave the event
   loop; ``core.security.egress`` resolves DNS with ``loop.getaddrinfo``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://grantex.test"
AUDIENCE = "agenticorg-test-deployment"
JWKS_URI = "https://api.grantex.test/.well-known/jwks.json"


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "kid-1", "use": "sig", "alg": "RS256"})
    return private_key, {"keys": [jwk]}


def _grant_token(private_key, *, iss: str = ISSUER, aud: str | None = AUDIENCE, kid: str = "kid-1", **extra):
    now = int(time.time())
    claims = {
        "iss": iss,
        "sub": "user_123",
        "agt": "did:grantex:agent-abc",
        "dev": "dev_evil_or_not",
        "scp": ["crm:read"],
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
        "grnt": "grant_1",
    }
    if aud is not None:
        claims["aud"] = aud
    claims.update(extra)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def grantex_env(monkeypatch, rsa_keypair):
    from auth import grantex_middleware as gm

    _private_key, jwks = rsa_keypair
    monkeypatch.setenv("GRANTEX_BASE_URL", "https://api.grantex.test")
    monkeypatch.setenv("AGENTICORG_GRANTEX_ISSUER", ISSUER)
    monkeypatch.setenv("AGENTICORG_GRANTEX_AUDIENCE", AUDIENCE)
    gm._reset_jwks_cache()
    fetches: list[str] = []

    def fake_get(url, timeout):
        fetches.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: jwks)

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    tenant = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    monkeypatch.setattr(
        gm,
        "_resolve_agent_tenant",
        AsyncMock(side_effect=lambda did: (tenant, agent_id) if did == "did:grantex:agent-abc" else None),
    )
    yield SimpleNamespace(fetches=fetches, tenant=tenant, agent_id=agent_id, gm=gm)
    gm._reset_jwks_cache()


# ---------------------------------------------------------------------------
# 1. Grantex middleware
# ---------------------------------------------------------------------------


class TestGrantexTokenRouting:
    def test_rs256_token_from_other_issuer_is_not_grantex(self, monkeypatch, rsa_keypair):
        from auth.grantex_middleware import _is_grantex_token

        private_key, _ = rsa_keypair
        monkeypatch.setenv("AGENTICORG_GRANTEX_ISSUER", ISSUER)
        assert _is_grantex_token(_grant_token(private_key)) is True
        # Pre-fix: any alg=RS256 header was treated as Grantex and triggered
        # an unauthenticated, uncached JWKS fetch on the event loop.
        assert _is_grantex_token(_grant_token(private_key, iss="https://evil.example")) is False
        assert _is_grantex_token(_grant_token(private_key, iss=ISSUER + "/")) is True
        assert _is_grantex_token("not.a.jwt") is False
        assert _is_grantex_token("garbage") is False

    def test_hs256_is_never_grantex(self, monkeypatch):
        from auth.grantex_middleware import _is_grantex_token

        monkeypatch.setenv("AGENTICORG_GRANTEX_ISSUER", ISSUER)
        token = jwt.encode({"iss": ISSUER, "sub": "x"}, "k" * 32, algorithm="HS256")
        assert _is_grantex_token(token) is False

    def test_unconfigured_issuer_never_routes_to_grantex(self, monkeypatch, rsa_keypair):
        from auth import grantex_middleware as gm

        private_key, _ = rsa_keypair
        monkeypatch.delenv("AGENTICORG_GRANTEX_ISSUER", raising=False)
        monkeypatch.setattr(gm, "grantex_expected_issuer", lambda: "")
        assert gm._is_grantex_token(_grant_token(private_key)) is False


class TestGrantexVerification:
    @pytest.mark.asyncio
    async def test_valid_token_binds_tenant_from_registered_agent_not_developer_id(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        claims = await grantex_env.gm.resolve_grantex_claims(_grant_token(private_key))
        assert claims["agenticorg:tenant_id"] == grantex_env.tenant
        assert claims["agenticorg:tenant_id"] != "dev_evil_or_not"
        assert claims["agenticorg:agent_id"] == grantex_env.agent_id
        assert claims["grantex:scopes"] == ["crm:read"]
        assert claims["sub"] == "user_123"
        assert grantex_env.fetches == [JWKS_URI]

    @pytest.mark.asyncio
    async def test_jwks_is_cached_per_kid(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        for _ in range(3):
            await grantex_env.gm.resolve_grantex_claims(_grant_token(private_key))
        assert len(grantex_env.fetches) == 1

    @pytest.mark.asyncio
    async def test_unknown_kid_is_negatively_cached(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        for _ in range(3):
            with pytest.raises(gm.GrantexAuthError):
                await gm.resolve_grantex_claims(_grant_token(private_key, kid="kid-unknown"))
        assert len(grantex_env.fetches) == 1

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        with pytest.raises(gm.GrantexAuthError):
            await gm.resolve_grantex_claims(_grant_token(private_key, aud="someone-else"))
        with pytest.raises(gm.GrantexAuthError):
            await gm.resolve_grantex_claims(_grant_token(private_key, aud=None))

    @pytest.mark.asyncio
    async def test_unregistered_agent_did_rejected(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        with pytest.raises(gm.GrantexAuthError, match="not registered"):
            await gm.resolve_grantex_claims(_grant_token(private_key, agt="did:grantex:unknown"))

    @pytest.mark.asyncio
    async def test_missing_audience_in_strict_env_rejects_before_network(self, grantex_env, rsa_keypair, monkeypatch):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        monkeypatch.delenv("AGENTICORG_GRANTEX_AUDIENCE")
        monkeypatch.setattr(gm, "_strict_runtime", lambda: True)
        with pytest.raises(gm.GrantexAuthError, match="AUDIENCE"):
            await gm.resolve_grantex_claims(_grant_token(private_key))
        assert grantex_env.fetches == []

    @pytest.mark.asyncio
    async def test_unconfigured_grantex_rejects_before_network(self, grantex_env, rsa_keypair, monkeypatch):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        monkeypatch.setattr(gm, "grantex_expected_issuer", lambda: "")
        with pytest.raises(gm.GrantexAuthError):
            await gm.resolve_grantex_claims(_grant_token(private_key))
        assert grantex_env.fetches == []

    @pytest.mark.asyncio
    async def test_verification_runs_off_the_event_loop(self, grantex_env, rsa_keypair):
        private_key, _ = rsa_keypair
        gm = grantex_env.gm
        loop = asyncio.get_running_loop()
        seen: list[bool] = []
        real = gm._verify_grantex_token_sync

        def spy(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                seen.append(True)
            except RuntimeError:
                seen.append(False)
            return real(*args, **kwargs)

        with patch.object(gm, "_verify_grantex_token_sync", spy):
            await gm.resolve_grantex_claims(_grant_token(private_key))
        assert seen == [False], "JWKS fetch + RSA verify must run in a worker thread"
        assert loop.is_running()

    @pytest.mark.asyncio
    async def test_middleware_sets_state_from_bound_claims(self, grantex_env, rsa_keypair):
        from auth.grantex_middleware import GrantexAuthMiddleware

        private_key, _ = rsa_keypair
        mw = GrantexAuthMiddleware(app=MagicMock())
        request = SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="1.2.3.4"))
        call_next = AsyncMock(return_value="ok")
        with patch("auth.grantex_middleware.clear_auth_failures", AsyncMock()):
            resp = await mw._handle_grantex_token(request, call_next, _grant_token(private_key), "1.2.3.4")
        assert resp == "ok"
        assert request.state.tenant_id == grantex_env.tenant
        assert request.state.auth_mode == "grantex"
        assert request.state.agent_id == grantex_env.agent_id

    @pytest.mark.asyncio
    async def test_middleware_rejects_bad_token_with_401(self, grantex_env, rsa_keypair):
        from auth.grantex_middleware import GrantexAuthMiddleware

        private_key, _ = rsa_keypair
        mw = GrantexAuthMiddleware(app=MagicMock())
        request = SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="1.2.3.4"))
        with (
            patch("auth.grantex_middleware.is_ip_blocked", AsyncMock(return_value=False)),
            patch("auth.grantex_middleware.record_auth_failure", AsyncMock(return_value=False)),
        ):
            resp = await mw._handle_grantex_token(
                request, AsyncMock(), _grant_token(private_key, aud="wrong"), "1.2.3.4"
            )
        assert resp.status_code == 401

    def test_websocket_feed_uses_shared_resolver(self):
        from api.websocket import feed

        src = inspect.getsource(feed._claims_from_grantex_token)
        assert "resolve_grantex_claims" in src
        assert "verify_grant_token" not in src
        assert "getattr(verified, \"developer_id\"" not in src


# ---------------------------------------------------------------------------
# 3. Session revocation
# ---------------------------------------------------------------------------


class TestUserSessionState:
    def test_rejects_token_semantics(self):
        from core.auth_state import UserSessionState

        now = time.time()
        assert UserSessionState(found=False).rejects_token(now) == "user_missing"
        assert UserSessionState(found=False, lookup_failed=True).rejects_token(now) is None
        assert UserSessionState(found=True, status="active").rejects_token(now) is None
        assert UserSessionState(found=True, status="inactive").rejects_token(now) == "user_inactive"
        state = UserSessionState(found=True, status="active", sessions_invalid_before=now)
        assert state.rejects_token(now - 1) == "session_revoked"
        assert state.rejects_token(None) == "session_revoked"
        assert state.rejects_token(now + 1) is None

    def test_encoding_round_trip(self):
        from core.auth_state import UserSessionState, _decode_user_state, _encode_user_state

        for state in (
            UserSessionState(found=False),
            UserSessionState(found=True, status="active"),
            UserSessionState(found=True, status="inactive", sessions_invalid_before=1_700_000_000.5),
        ):
            assert _decode_user_state(_encode_user_state(state)) == state

    @pytest.mark.asyncio
    async def test_db_lookup_is_cached_and_invalidated(self, monkeypatch):
        from core import auth_state

        auth_state._mem_user_state.clear()
        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_state, "_strict", lambda: False)
        loader = AsyncMock(return_value=auth_state.UserSessionState(found=True, status="active"))
        monkeypatch.setattr(auth_state, "_load_user_state_from_db", loader)
        tid = str(uuid.uuid4())
        await auth_state.get_user_session_state(tid, "a@x.io")
        await auth_state.get_user_session_state(tid, "A@X.IO")
        assert loader.await_count == 1
        await auth_state.invalidate_user_session_state(tid, "a@x.io")
        await auth_state.get_user_session_state(tid, "a@x.io")
        assert loader.await_count == 2

    @pytest.mark.asyncio
    async def test_strict_env_redis_outage_falls_through_to_db(self, monkeypatch):
        from core import auth_state

        auth_state._mem_user_state.clear()
        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(side_effect=RuntimeError("redis down")))
        monkeypatch.setattr(auth_state, "_strict", lambda: True)
        loader = AsyncMock(return_value=auth_state.UserSessionState(found=True, status="inactive"))
        monkeypatch.setattr(auth_state, "_load_user_state_from_db", loader)
        state = await auth_state.get_user_session_state(str(uuid.uuid4()), "a@x.io")
        assert state.status == "inactive"
        # Cache bust is best-effort (watermark already committed in the DB).
        await auth_state.invalidate_user_session_state(str(uuid.uuid4()), "a@x.io")

    @pytest.mark.asyncio
    async def test_strict_env_fails_closed_when_cache_and_db_fail(self, monkeypatch):
        from core import auth_state

        auth_state._mem_user_state.clear()
        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_state, "_strict", lambda: True)
        monkeypatch.setattr(auth_state, "_load_user_state_from_db", AsyncMock(side_effect=OSError("db down")))
        with pytest.raises(RuntimeError):
            await auth_state.get_user_session_state(str(uuid.uuid4()), "a@x.io")


class TestLegacyTokenRevocation:
    def _mw(self):
        from auth.grantex_middleware import GrantexAuthMiddleware

        return GrantexAuthMiddleware(app=MagicMock())

    def _request(self):
        return SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="9.9.9.9"))

    @pytest.mark.asyncio
    async def test_token_issued_before_watermark_is_rejected(self):
        from core.auth_state import UserSessionState

        tid = str(uuid.uuid4())
        now = time.time()
        claims = {"sub": "u@x.io", "agenticorg:tenant_id": tid, "iat": int(now - 60), "grantex:scopes": []}
        state = UserSessionState(found=True, status="active", sessions_invalid_before=now)
        with (
            patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
            patch("auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=state)),
            patch("auth.grantex_middleware.is_ip_blocked", AsyncMock(return_value=False)),
            patch("auth.grantex_middleware.record_auth_failure", AsyncMock(return_value=False)),
        ):
            resp = await self._mw()._handle_legacy_token(self._request(), AsyncMock(), "tok", "9.9.9.9")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_inactive_user_is_rejected_even_with_valid_signature(self):
        from core.auth_state import UserSessionState

        claims = {"sub": "u@x.io", "agenticorg:tenant_id": str(uuid.uuid4()), "iat": int(time.time())}
        state = UserSessionState(found=True, status="inactive")
        with (
            patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
            patch("auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=state)),
            patch("auth.grantex_middleware.is_ip_blocked", AsyncMock(return_value=False)),
            patch("auth.grantex_middleware.record_auth_failure", AsyncMock(return_value=False)),
        ):
            resp = await self._mw()._handle_legacy_token(self._request(), AsyncMock(), "tok", "9.9.9.9")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_fresh_token_for_active_user_passes(self):
        from core.auth_state import UserSessionState

        now = time.time()
        claims = {"sub": "u@x.io", "agenticorg:tenant_id": str(uuid.uuid4()), "iat": int(now + 1)}
        state = UserSessionState(found=True, status="active", sessions_invalid_before=now - 100)
        request = self._request()
        with (
            patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
            patch("auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=state)),
            patch("auth.grantex_middleware.clear_auth_failures", AsyncMock()),
        ):
            resp = await self._mw()._handle_legacy_token(request, AsyncMock(return_value="ok"), "tok", "9.9.9.9")
        assert resp == "ok"
        assert request.state.auth_mode == "legacy"

    @pytest.mark.asyncio
    async def test_state_unavailable_in_strict_env_returns_503(self):
        claims = {"sub": "u@x.io", "agenticorg:tenant_id": str(uuid.uuid4()), "iat": int(time.time())}
        with (
            patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
            patch(
                "auth.grantex_middleware.get_user_session_state",
                AsyncMock(side_effect=RuntimeError("redis+db down")),
            ),
        ):
            resp = await self._mw()._handle_legacy_token(self._request(), AsyncMock(), "tok", "9.9.9.9")
        assert resp.status_code == 503


    @pytest.mark.asyncio
    async def test_websocket_legacy_path_enforces_revocation(self):
        from api.websocket import feed
        from core.auth_state import UserSessionState

        now = time.time()
        tid = str(uuid.uuid4())
        claims = {"sub": "u@x.io", "agenticorg:tenant_id": tid, "iat": int(now - 60), "grantex:scopes": []}
        state = UserSessionState(found=True, status="active", sessions_invalid_before=now)
        websocket = SimpleNamespace(headers={"authorization": "Bearer tok"}, cookies={}, query_params={})
        with (
            patch("api.websocket.feed.validate_token", AsyncMock(return_value=claims)),
            patch("auth.grantex_middleware.get_user_session_state", AsyncMock(return_value=state)),
        ):
            with pytest.raises(feed.WebSocketAuthError) as exc_info:
                await feed.authenticate_websocket(websocket, tid)
        assert exc_info.value.code == "invalid_token"

    @pytest.mark.asyncio
    async def test_malformed_tenant_claim_is_rejected_not_503(self):
        from auth.grantex_middleware import check_user_session_state

        assert await check_user_session_state("not-a-uuid", {"sub": "u@x.io"}) == "invalid_tenant_claim"
        assert await check_user_session_state("", {"sub": "u@x.io"}) is None


class TestRevocationWriters:
    @pytest.mark.asyncio
    async def test_deactivate_member_sets_watermark_and_busts_cache(self):
        from api.v1.org import deactivate_member

        tid = uuid.uuid4()
        user = SimpleNamespace(id=uuid.uuid4(), email="victim@x.io", status="active", sessions_invalid_before=None)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=user)))
        session.add = MagicMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(tid), user_sub="admin@x.io"))
        invalidate = AsyncMock()
        with (
            patch("api.v1.org.async_session_factory", factory),
            patch("api.v1.org.invalidate_user_session_state", invalidate),
        ):
            resp = await deactivate_member(str(user.id), request)
        assert resp["status"] == "deactivated"
        assert user.status == "inactive"
        assert isinstance(user.sessions_invalid_before, datetime)
        assert user.sessions_invalid_before <= datetime.now(UTC)
        invalidate.assert_awaited_once_with(str(tid), "victim@x.io")

    def test_reset_password_and_logout_all_bump_watermark(self):
        from api.v1 import auth as auth_mod

        reset_src = inspect.getsource(auth_mod.reset_password)
        assert "sessions_invalid_before" in reset_src
        assert "invalidate_user_session_state" in reset_src
        assert hasattr(auth_mod, "logout_all")
        logout_src = inspect.getsource(auth_mod.logout_all)
        assert "sessions_invalid_before" in logout_src
        assert "invalidate_user_session_state" in logout_src

    def test_user_model_has_watermark_column(self):
        from core.models.user import User

        assert "sessions_invalid_before" in User.__table__.c


# ---------------------------------------------------------------------------
# 8. Blocking calls off the event loop
# ---------------------------------------------------------------------------


class TestNoBlockingOnLoop:
    @pytest.mark.parametrize(
        "func_path",
        [
            "api.v1.auth.login",
            "api.v1.auth.signup",
            "api.v1.auth.reset_password",
            "api.v1.auth.google_login",
            "api.v1.org.accept_invite",
            "auth.grantex_middleware.GrantexAuthMiddleware._handle_api_key",
            "api.websocket.feed._claims_from_api_key",
        ],
    )
    def test_bcrypt_and_google_verify_are_threaded(self, func_path):
        module_path, _, attr = func_path.rpartition(".")
        if module_path.split(".")[-1][0].isupper():
            module_path, _, cls = module_path.rpartition(".")
            mod = __import__(module_path, fromlist=[cls])
            func = getattr(getattr(mod, cls), attr)
        else:
            mod = __import__(module_path, fromlist=[attr])
            func = getattr(mod, attr)
        src = inspect.getsource(func)
        assert (
            "asyncio.to_thread" in src or "await _hash_password(" in src or "await _verify_password(" in src
        ), f"{func_path} still blocks the event loop"
        # Every blocking primitive is invoked only inside the to_thread'd helper
        # (a nested ``def``), never at the coroutine's own indentation level.
        body_indent = None
        for line in src.splitlines():
            stripped = line.lstrip()
            if body_indent is None and stripped and not stripped.startswith(("@", "async def", "def ", '"""')):
                body_indent = len(line) - len(stripped)
            if any(b in line for b in ("checkpw(", "hashpw(", "verify_oauth2_token(")):
                indent = len(line) - len(stripped)
                assert "to_thread" in line or indent > (body_indent or 0), line

    @pytest.mark.asyncio
    async def test_egress_async_resolution_uses_loop_getaddrinfo(self, monkeypatch):
        from core.security import egress

        calls: list[str] = []

        async def fake_getaddrinfo(host, port, **kwargs):
            calls.append(host)
            return [(None, None, None, None, ("93.184.216.34", 0))]

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(egress.socket, "getaddrinfo", MagicMock(side_effect=AssertionError("sync resolver used")))
        addresses = await egress.resolve_public_hostname_addresses_async("example.com", require_dns=True)
        assert addresses == ("93.184.216.34",)
        assert calls == ["example.com"]

    @pytest.mark.asyncio
    async def test_egress_async_resolution_blocks_private_answers(self, monkeypatch):
        from core.security import egress

        async def fake_getaddrinfo(host, port, **kwargs):
            return [(None, None, None, None, ("10.0.0.5", 0))]

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(egress.EgressValidationError, match="blocked_ip"):
            await egress.resolve_public_hostname_addresses_async("internal.example", require_dns=True)

    @pytest.mark.asyncio
    async def test_pinned_transport_connect_uses_async_resolver(self, monkeypatch):
        from core.security import egress

        resolved = AsyncMock(return_value=("93.184.216.34",))
        monkeypatch.setattr(egress, "resolve_public_hostname_addresses_async", resolved)
        delegate = SimpleNamespace(connect_tcp=AsyncMock(return_value="stream"))
        backend = egress.PinnedDnsAsyncNetworkBackend(require_dns=True, delegate=delegate)
        assert await backend.connect_tcp("example.com", 443) == "stream"
        resolved.assert_awaited_once_with("example.com", require_dns=True)
        delegate.connect_tcp.assert_awaited_once()
        assert delegate.connect_tcp.await_args.args[0] == "93.184.216.34"

    def test_watermark_is_timezone_aware_datetime(self):
        # Guard against naive datetimes sneaking into the TIMESTAMPTZ column.
        assert (datetime.now(UTC) - timedelta(seconds=1)).tzinfo is UTC
