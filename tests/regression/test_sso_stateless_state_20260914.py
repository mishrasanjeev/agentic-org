"""Bug sheet 2026-09-14 row 7: SSO login returned 503 whenever Redis was down.

Tester steps replayed here: Redis unavailable -> click SSO login -> IdP
redirect -> callback. Before the fix ``sso_login`` refused with 503 ("SSO
state store unavailable (Redis required)") and the route rate limiter 503'd
both SSO routes in strict env.

The flow state is now a signed ``state`` token plus an AES-GCM encrypted,
HttpOnly, path-scoped flow cookie carrying the PKCE verifier. Redis only
backs a best-effort one-shot replay marker. These tests pin both the
availability fix and every fail-closed rejection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from api.v1 import sso as sso_module
from auth.sso.oidc import OIDCProvider
from auth.sso.state_token import (
    FLOW_COOKIE_NAME,
    FLOW_COOKIE_PATH,
    SSOStateError,
    decrypt_flow_cookie,
    encrypt_flow_cookie,
    issue_state_token,
    verify_state_token,
)

TENANT_ID = uuid.uuid4()
LOGIN = "/api/v1/auth/sso/okta/login"
CALLBACK = "/api/v1/auth/sso/okta/callback"


def _provider() -> OIDCProvider:
    provider = OIDCProvider(
        provider_key="okta",
        config={
            "issuer": "https://idp.example.com",
            "client_id": "client-abc",
            "client_secret_enc": "",
            "redirect_uri": "https://app.example.com/api/v1/auth/sso/okta/callback",
        },
    )
    provider._discovery = {"authorization_endpoint": "https://idp.example.com/authorize"}
    return provider


class _FakeRedis:
    """Minimal async ``SET NX EX`` semantics."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str, bool, int]] = []

    async def set(self, key, value, nx=False, ex=None):
        self.calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


class _Harness:
    def __init__(self, redis) -> None:
        self.provider = _provider()
        self.provider.exchange_code = AsyncMock(
            return_value=SimpleNamespace(claims={"email": "member@example.com"})
        )
        self.user = SimpleNamespace(
            id=uuid.uuid4(), email="member@example.com", name="Member", role="admin", domain="finance"
        )
        self.jit = AsyncMock(return_value=self.user)
        self.redis_factory = AsyncMock(return_value=redis)
        self.logger = MagicMock()
        tenant_result = MagicMock()
        tenant_result.scalar_one.return_value = SimpleNamespace(name="Tenant A")
        session = MagicMock()
        session.execute = AsyncMock(return_value=tenant_result)

        @asynccontextmanager
        async def tenant_session(value):
            assert value == TENANT_ID
            yield session

        self.tenant_session = tenant_session

    def patches(self):
        return [
            patch.object(sso_module, "_redis", new=self.redis_factory),
            patch.object(sso_module, "_load_provider", new=AsyncMock(return_value=(self.provider, MagicMock()))),
            patch.object(sso_module, "jit_provision_user", new=self.jit),
            patch.object(sso_module, "get_tenant_session", self.tenant_session),
            patch.object(sso_module, "logger", self.logger),
        ]


@pytest.fixture
def harness(request):
    h = _Harness(getattr(request, "param", None))
    with ExitStack() as stack:
        for p in h.patches():
            stack.enter_context(p)
        app = FastAPI()
        app.include_router(sso_module.public_router, prefix="/api/v1")
        h.client = TestClient(app, base_url="http://testserver")
        yield h


def _set_cookies(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _flow_cookie_header(response) -> str:
    return next(c for c in _set_cookies(response) if c.startswith(f"{FLOW_COOKIE_NAME}="))


def _login(client: TestClient, return_to: str | None = None):
    params = {"tenant_id": str(TENANT_ID)}
    if return_to is not None:
        params["return_to"] = return_to
    resp = client.get(LOGIN, params=params, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    query = parse_qs(urlparse(resp.headers["location"]).query)
    return resp, query["state"][0], query


def _assert_flow_cookie_cleared(response) -> None:
    header = _flow_cookie_header(response)
    assert header.startswith(f'{FLOW_COOKIE_NAME}="";') or header.startswith(f"{FLOW_COOKIE_NAME}=;")
    assert "Max-Age=0" in header
    assert f"Path={FLOW_COOKIE_PATH}" in header


# ---------------------------------------------------------------------------
# Login no longer needs Redis
# ---------------------------------------------------------------------------


class TestLoginWithoutRedis:
    def test_login_redirects_to_idp_when_redis_is_down(self, harness):
        resp, state, query = _login(harness.client)

        location = resp.headers["location"]
        assert location.startswith("https://idp.example.com/authorize?")
        harness.redis_factory.assert_not_awaited()  # login never touches Redis

        claims = verify_state_token(state, provider_key="okta")
        assert claims.tenant_id == TENANT_ID
        assert claims.nonce == query["nonce"][0]
        assert claims.exp - claims.iat == 600
        body = json.loads(base64.urlsafe_b64decode(state.split(".")[0] + "=="))
        assert set(body) == {"v", "tid", "pk", "n", "rt", "iat", "exp", "jti"}

        cookie = _flow_cookie_header(resp)
        attrs = [a.strip().lower() for a in cookie.split(";")]
        assert "httponly" in attrs
        assert f"path={FLOW_COOKIE_PATH}".lower() in attrs
        assert "samesite=lax" in attrs
        assert "max-age=600" in attrs

        # The PKCE verifier rides only in the encrypted cookie: never in the
        # IdP URL / state, and it matches the S256 challenge sent to the IdP.
        cookie_value = cookie.split(";", 1)[0].split("=", 1)[1]
        verifier = decrypt_flow_cookie(cookie_value, expected_jti=claims.jti)
        assert verifier not in location
        assert verifier not in cookie_value
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert query["code_challenge"] == [challenge]

    def test_open_redirect_return_to_is_normalized_into_state(self, harness):
        _resp, state, _q = _login(harness.client, return_to="//evil.example/phish")
        assert verify_state_token(state, provider_key="okta").return_to == "/dashboard"


# ---------------------------------------------------------------------------
# Callback success paths
# ---------------------------------------------------------------------------


class TestCallbackSuccess:
    def test_callback_succeeds_without_redis_and_logs_degraded_marker(self, harness):
        login_resp, state, query = _login(harness.client, return_to="/agents")
        cookie_value = _flow_cookie_header(login_resp).split(";", 1)[0].split("=", 1)[1]
        claims = verify_state_token(state, provider_key="okta")
        verifier = decrypt_flow_cookie(cookie_value, expected_jti=claims.jti)

        resp = harness.client.get(CALLBACK, params={"code": "idp-code", "state": state}, follow_redirects=False)

        assert resp.status_code == 303, resp.text
        location = resp.headers["location"]
        assert location.endswith("/agents")
        assert verifier not in location and "#token=" not in location
        harness.provider.exchange_code.assert_awaited_once_with("idp-code", verifier, query["nonce"][0])
        harness.jit.assert_awaited_once()
        assert harness.jit.await_args.args[0] == TENANT_ID
        assert any(c.startswith("agenticorg_session=") for c in _set_cookies(resp))
        _assert_flow_cookie_cleared(resp)
        events = [c.args[0] for c in harness.logger.warning.call_args_list]
        assert "sso_replay_marker_unavailable" in events

    @pytest.mark.parametrize("harness", [MagicMock()], indirect=True)
    def test_redis_error_on_marker_still_succeeds_with_warning(self, harness):
        redis = harness.redis_factory.return_value
        redis.set = AsyncMock(side_effect=RedisConnectionError("redis down"))
        _login_resp, state, _q = _login(harness.client)

        resp = harness.client.get(CALLBACK, params={"code": "idp-code", "state": state}, follow_redirects=False)

        assert resp.status_code == 303, resp.text
        warning = next(
            c for c in harness.logger.warning.call_args_list if c.args[0] == "sso_replay_marker_unavailable"
        )
        assert warning.kwargs["reason"] == "redis_error"
        assert "tenant_id" not in warning.kwargs  # no tenant ids / secrets in the event
        assert state not in str(warning)

    @pytest.mark.parametrize("harness", [MagicMock()], indirect=True)
    def test_non_redis_error_on_marker_fails_closed(self, harness):
        redis = harness.redis_factory.return_value
        redis.set = AsyncMock(side_effect=RuntimeError("unexpected"))
        _login_resp, state, _q = _login(harness.client)

        with pytest.raises(RuntimeError):
            harness.client.get(CALLBACK, params={"code": "idp-code", "state": state}, follow_redirects=False)
        harness.provider.exchange_code.assert_not_awaited()

    @pytest.mark.parametrize("harness", [_FakeRedis()], indirect=True)
    def test_replay_is_rejected_when_redis_is_available(self, harness):
        login_resp, state, _q = _login(harness.client)
        cookie_value = _flow_cookie_header(login_resp).split(";", 1)[0].split("=", 1)[1]

        first = harness.client.get(CALLBACK, params={"code": "idp-code", "state": state}, follow_redirects=False)
        assert first.status_code == 303

        # Attacker (or double-submit) replays the same state AND flow cookie.
        replay_client = TestClient(harness.client.app, base_url="http://testserver")
        replay = replay_client.get(
            CALLBACK,
            params={"code": "idp-code", "state": state},
            headers={"cookie": f"{FLOW_COOKIE_NAME}={cookie_value}"},
            follow_redirects=False,
        )
        assert replay.status_code == 400
        assert replay.json()["detail"] == "Invalid or expired state"
        harness.provider.exchange_code.assert_awaited_once()

        redis = harness.redis_factory.return_value
        jti = verify_state_token(state, provider_key="okta").jti
        key, value, nx, ex = redis.calls[0]
        assert (key, value, nx) == (f"sso:used:{jti}", "1", True)
        assert 0 < ex <= 600


# ---------------------------------------------------------------------------
# Callback rejections (all fail closed with 400 and clear the flow cookie)
# ---------------------------------------------------------------------------


def _callback_with(client: TestClient, state: str, cookie_value: str | None):
    headers = {"cookie": f"{FLOW_COOKIE_NAME}={cookie_value}"} if cookie_value is not None else {}
    return client.get(
        CALLBACK, params={"code": "idp-code", "state": state}, headers=headers, follow_redirects=False
    )


class TestCallbackRejections:
    def _flow(self, **overrides):
        kwargs = {"tenant_id": TENANT_ID, "provider_key": "okta", "nonce": "n-1", "return_to": "/dashboard"}
        kwargs.update(overrides)
        state, claims = issue_state_token(**kwargs)
        return state, claims, encrypt_flow_cookie(jti=claims.jti, verifier="v" * 64)

    def _assert_rejected(self, harness, resp):
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "Invalid or expired state"
        harness.provider.exchange_code.assert_not_awaited()
        harness.jit.assert_not_awaited()
        _assert_flow_cookie_cleared(resp)

    def test_valid_flow_is_accepted(self, harness):
        state, _claims, cookie = self._flow()
        assert _callback_with(harness.client, state, cookie).status_code == 303

    def test_tampered_signature(self, harness):
        state, _claims, cookie = self._flow()
        body, sig = state.split(".")
        forged_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
        self._assert_rejected(harness, _callback_with(harness.client, f"{body}.{forged_sig}", cookie))

    def test_tampered_tenant_with_original_signature(self, harness):
        state, _claims, cookie = self._flow()
        body, sig = state.split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        payload["tid"] = str(uuid.uuid4())
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        self._assert_rejected(harness, _callback_with(harness.client, f"{forged}.{sig}", cookie))

    def test_expired_state(self, harness):
        state, _claims, cookie = self._flow(now=int(time.time()) - 601)
        self._assert_rejected(harness, _callback_with(harness.client, state, cookie))

    def test_future_iat_beyond_skew(self, harness):
        state, _claims, cookie = self._flow(now=int(time.time()) + 120)
        self._assert_rejected(harness, _callback_with(harness.client, state, cookie))

    def test_provider_key_mismatch(self, harness):
        state, _claims, cookie = self._flow(provider_key="azure")
        self._assert_rejected(harness, _callback_with(harness.client, state, cookie))

    def test_missing_flow_cookie(self, harness):
        state, _claims, _cookie = self._flow()
        self._assert_rejected(harness, _callback_with(harness.client, state, None))

    def test_cookie_from_another_login_is_rejected(self, harness):
        state_a, _claims_a, _cookie_a = self._flow()
        _state_b, _claims_b, cookie_b = self._flow()
        self._assert_rejected(harness, _callback_with(harness.client, state_a, cookie_b))

    def test_browser_that_started_a_second_login_cannot_finish_the_first(self, harness):
        _r1, first_state, _q1 = _login(harness.client)
        _r2, _second_state, _q2 = _login(harness.client)  # overwrites the flow cookie
        resp = harness.client.get(CALLBACK, params={"code": "c", "state": first_state}, follow_redirects=False)
        self._assert_rejected(harness, resp)

    def test_forged_cookie_ciphertext_is_rejected(self, harness):
        state, _claims, cookie = self._flow()
        raw = bytearray(base64.urlsafe_b64decode(cookie + "=="))
        raw[-1] ^= 0x01
        forged = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
        self._assert_rejected(harness, _callback_with(harness.client, state, forged))

    def test_provisioning_value_error_still_maps_to_403_and_clears_cookie(self, harness):
        harness.jit.side_effect = ValueError("user is inactive")
        state, _claims, cookie = self._flow()
        resp = _callback_with(harness.client, state, cookie)
        assert resp.status_code == 403
        _assert_flow_cookie_cleared(resp)


class TestStateTokenUnit:
    def test_state_signed_under_domain_separated_key_not_raw_secret(self):
        import hmac

        from core.config import settings

        state, _claims = issue_state_token(tenant_id=TENANT_ID, provider_key="okta", nonce="n", return_to="/")
        body, sig = state.split(".")
        raw_secret_sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
        assert base64.urlsafe_b64decode(sig + "=") != raw_secret_sig

    def test_wrong_version_rejected_even_if_signed(self):
        from auth.sso import state_token

        payload = {"v": 2, "tid": str(TENANT_ID), "pk": "okta", "n": "n", "rt": "/", "iat": int(time.time()),
                   "exp": int(time.time()) + 600, "jti": "j"}
        body = state_token._b64e(json.dumps(payload).encode())
        token = f"{body}.{state_token._b64e(state_token._mac(body))}"
        with pytest.raises(SSOStateError) as exc:
            verify_state_token(token, provider_key="okta")
        assert exc.value.reason == "state_bad_version"

    def test_verifier_absent_from_state(self):
        state, claims = issue_state_token(tenant_id=TENANT_ID, provider_key="okta", nonce="n", return_to="/")
        cookie = encrypt_flow_cookie(jti=claims.jti, verifier="super-secret-verifier")
        assert "super-secret-verifier" not in state
        assert b"super-secret-verifier" not in base64.urlsafe_b64decode(cookie + "==")


# ---------------------------------------------------------------------------
# Rate-limit backend outage: SSO classes degrade, credential classes 503
# ---------------------------------------------------------------------------


class TestRateLimitOutage:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("rate_class", ["auth-sso-login-initiation", "auth-sso-callback"])
    async def test_sso_classes_allowed(self, rate_class):
        from api import route_enforcement

        request = SimpleNamespace(state=SimpleNamespace(), client=SimpleNamespace(host="203.0.113.5"), headers={})
        with (
            patch("core.auth_state.check_window_rate", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch.object(route_enforcement, "_client_ip", return_value="203.0.113.5"),
        ):
            await route_enforcement._check_rate_limit(request, {"rate_limit": rate_class})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rate_class", ["auth-login", "auth-signup", "auth-password-reset", "auth-discovery"])
    async def test_credential_classes_still_fail_closed(self, rate_class):
        from api import route_enforcement

        request = SimpleNamespace(state=SimpleNamespace())
        with (
            patch("core.auth_state.check_window_rate", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch.object(route_enforcement, "_client_ip", return_value="203.0.113.5"),
            pytest.raises(HTTPException) as exc,
        ):
            await route_enforcement._check_rate_limit(request, {"rate_limit": rate_class})
        assert exc.value.status_code == 503

    def test_real_app_sso_login_survives_full_redis_outage(self):
        """Tester replay through the real app: limiter backend raising (strict
        env) and the async Redis client unavailable -> SSO login still 303s."""
        from api.main import app

        h = _Harness(None)
        with ExitStack() as stack:
            for p in h.patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("core.auth_state.check_window_rate", AsyncMock(side_effect=RuntimeError("redis down")))
            )
            client = TestClient(app, base_url="http://testserver")
            resp = client.get(LOGIN, params={"tenant_id": str(TENANT_ID)}, follow_redirects=False)
            assert resp.status_code == 303, resp.text
            state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
            cb = client.get(CALLBACK, params={"code": "c", "state": state}, follow_redirects=False)
            assert cb.status_code == 303, cb.text
            assert any(c.startswith("agenticorg_session=") for c in _set_cookies(cb))
