"""OIDC provider unit tests.

These exercise the synchronous helper functions in
``auth.sso.oidc`` (PKCE, state, nonce) plus a fully mocked OIDC
discovery + token exchange flow. A real Okta dev tenant test is
deferred to the integration suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from auth.sso.oidc import (
    OIDCProvider,
    new_nonce,
    new_pkce_pair,
    new_state,
)

# ── PKCE / state / nonce helpers ────────────────────────────────────


class TestPKCEHelpers:
    def test_pkce_pair_lengths(self):
        verifier, challenge = new_pkce_pair()
        # RFC 7636: verifier 43..128 chars, challenge always 43 (S256)
        assert 43 <= len(verifier) <= 128
        assert len(challenge) == 43

    def test_pkce_pair_is_different_each_call(self):
        v1, c1 = new_pkce_pair()
        v2, c2 = new_pkce_pair()
        assert v1 != v2
        assert c1 != c2

    def test_state_is_url_safe(self):
        s = new_state()
        # token_urlsafe characters only
        assert all(c.isalnum() or c in "-_" for c in s)
        assert len(s) >= 32

    def test_nonce_is_url_safe(self):
        n = new_nonce()
        assert all(c.isalnum() or c in "-_" for c in n)
        assert len(n) >= 32


# ── OIDCProvider — discovery + authorize URL ────────────────────────


def _make_provider() -> OIDCProvider:
    return OIDCProvider(
        provider_key="okta_test",
        config={
            "issuer": "https://example.okta.com",
            "client_id": "client-abc",
            "client_secret": "shhh",
            "redirect_uri": "https://app.example.com/api/v1/auth/sso/okta_test/callback",
            "scopes": ["openid", "profile", "email"],
        },
    )


class TestOIDCProviderAuthorize:
    @pytest.mark.asyncio
    async def test_prepare_loads_discovery_and_jwks(self):
        provider = _make_provider()

        discovery_doc = {
            "issuer": "https://example.okta.com",
            "authorization_endpoint": "https://example.okta.com/oauth2/v1/authorize",
            "token_endpoint": "https://example.okta.com/oauth2/v1/token",
            "jwks_uri": "https://example.okta.com/oauth2/v1/keys",
        }
        jwks_doc = {"keys": []}

        with patch("httpx.AsyncClient") as mock_client_cls:
            client_ctx = mock_client_cls.return_value.__aenter__.return_value
            # First call: discovery; second call: JWKS
            client_ctx.get = AsyncMock(
                side_effect=[
                    _resp(200, discovery_doc),
                    _resp(200, jwks_doc),
                ]
            )
            await provider.prepare()

        assert provider._discovery == discovery_doc
        assert provider._jwks == jwks_doc

    @pytest.mark.asyncio
    async def test_build_authorize_url_includes_pkce_and_state(self):
        provider = _make_provider()
        provider._discovery = {
            "authorization_endpoint": "https://example.okta.com/oauth2/v1/authorize",
        }
        verifier, challenge = new_pkce_pair()
        state = new_state()
        nonce = new_nonce()

        url = provider.build_authorize_url(state, nonce, challenge)

        assert url.startswith("https://example.okta.com/oauth2/v1/authorize?")
        assert "client_id=client-abc" in url
        assert "response_type=code" in url
        assert f"state={state}" in url
        assert f"nonce={nonce}" in url
        assert f"code_challenge={challenge}" in url
        assert "code_challenge_method=S256" in url
        assert "openid" in url
        # The redirect URI is URL-encoded inside the query string —
        # decode the redirect_uri param and verify the host explicitly
        # to avoid the "incomplete URL substring sanitization" CodeQL
        # warning that fires on a bare ``"host" in url`` check.
        from urllib.parse import parse_qs, unquote, urlparse

        redirect_uri = parse_qs(urlparse(url).query).get("redirect_uri", [""])[0]
        assert urlparse(unquote(redirect_uri)).hostname == "app.example.com"


# ── OIDCProvider — code exchange (mocked token endpoint) ────────────


class TestOIDCCodeExchange:
    @pytest.mark.asyncio
    async def test_exchange_code_calls_token_endpoint_and_verifies_id_token(self):
        provider = _make_provider()
        provider._discovery = {
            "token_endpoint": "https://example.okta.com/oauth2/v1/token",
            "jwks_uri": "https://example.okta.com/oauth2/v1/keys",
            "issuer": "https://example.okta.com",
        }

        # We replace _verify_id_token entirely so we don't need real
        # JWS material. The point of this test is the HTTP call shape.
        async def fake_verify(id_token, expected_nonce):
            assert expected_nonce == "n-1234"
            return {"sub": "user-1", "email": "alice@example.com", "name": "Alice"}

        with patch.object(provider, "_verify_id_token", side_effect=fake_verify):
            with patch("httpx.AsyncClient") as mock_client_cls:
                client_ctx = mock_client_cls.return_value.__aenter__.return_value
                client_ctx.post = AsyncMock(
                    return_value=_resp(
                        200,
                        {
                            "access_token": "at-1",
                            "id_token": "header.payload.sig",
                            "refresh_token": "rt-1",
                        },
                    )
                )
                tokens = await provider.exchange_code(
                    code="auth-code-xyz",
                    code_verifier="verifier-1",
                    expected_nonce="n-1234",
                )

        assert tokens.access_token == "at-1"
        assert tokens.id_token == "header.payload.sig"
        assert tokens.refresh_token == "rt-1"
        assert tokens.claims["email"] == "alice@example.com"

        # Verify the POST body had grant_type=authorization_code
        call = client_ctx.post.await_args
        assert call.args[0] == "https://example.okta.com/oauth2/v1/token"
        body = call.kwargs["data"]
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "auth-code-xyz"
        assert body["code_verifier"] == "verifier-1"

    @pytest.mark.asyncio
    async def test_exchange_code_rejects_missing_id_token(self):
        provider = _make_provider()
        provider._discovery = {
            "token_endpoint": "https://example.okta.com/oauth2/v1/token",
        }
        with patch("httpx.AsyncClient") as mock_client_cls:
            client_ctx = mock_client_cls.return_value.__aenter__.return_value
            client_ctx.post = AsyncMock(
                return_value=_resp(200, {"access_token": "at-1"})
            )
            with pytest.raises(ValueError, match="id_token"):
                await provider.exchange_code(
                    code="x", code_verifier="v", expected_nonce="n"
                )


# ── Helpers ─────────────────────────────────────────────────────────


def _resp(status: int, payload: dict):
    """Build a mock httpx Response that returns ``payload`` from .json()."""
    from unittest.mock import MagicMock

    response = MagicMock()
    response.status_code = status
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response
