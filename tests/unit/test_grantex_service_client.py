# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The Grantex service client refuses unusable answers at once and never trusts an unverified token."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from core.commerce import grantex_service
from core.commerce.grantex_service import (
    GrantexServiceClient,
    GrantexServiceUnavailableError,
    GrantexTokenError,
    grantex_service_client_from_env,
)
from core.commerce.oacp_grantex_trust import OacpTrustUnavailableError
from tests.oacp_signing import ISSUER, EcSigningKey, sign_compact_jwt


class _Verifier:
    """Stands in for the pinned-JWKS verifier: returns ``jwks_sequence`` in order, or raises."""

    def __init__(self, *jwks_sequence: Any) -> None:
        self.config = SimpleNamespace(issuer=ISSUER)
        self._sequence = list(jwks_sequence)
        self.calls: list[bool] = []

    async def jwks(self, *, force_refresh: bool = False) -> Any:
        self.calls.append(force_refresh)
        value = self._sequence.pop(0) if len(self._sequence) > 1 else self._sequence[0]
        if isinstance(value, Exception):
            raise value
        return value


def _client(handler: Any, verifier: Any = None, **kwargs: Any) -> GrantexServiceClient:
    return GrantexServiceClient(
        base_url="https://grantex.example/",
        token="service-token-placeholder",
        verifier=verifier or _Verifier({"keys": []}),
        transport_factory=lambda: httpx.MockTransport(handler),
        **kwargs,
    )


def _post(client: GrantexServiceClient) -> Any:
    return asyncio.run(client.post_json("/v1/audit/witness-checkpoints", {"a": 1}))


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(url: str, **_: Any) -> None:
        return None

    monkeypatch.setattr(grantex_service, "validate_public_url_async", _ok)


def test_a_json_object_answer_is_returned_with_its_status() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"status": "witnessed"})

    client = _client(handler)
    response = _post(client)
    assert response.status_code == 201 and response.body == {"status": "witnessed"}
    assert str(seen[0].url) == "https://grantex.example/v1/audit/witness-checkpoints"
    assert seen[0].headers["Authorization"] == "Bearer service-token-placeholder"
    assert json.loads(seen[0].content) == {"a": 1}
    assert client.issuer == ISSUER


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"x" * (256 * 1024 + 1)), "size limit"),
        (httpx.Response(502, content=b"<html>bad gateway</html>"), "without JSON"),
        (httpx.Response(200, json=["not", "an", "object"]), "non-object"),
    ],
)
def test_unusable_answers_are_unavailable(response: httpx.Response, message: str) -> None:
    with pytest.raises(GrantexServiceUnavailableError, match=message):
        _post(_client(lambda _request: response))


def test_a_transport_failure_is_unavailable_without_retry() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(GrantexServiceUnavailableError, match="unreachable"):
        _post(_client(handler))
    assert calls == [1]


def test_an_unsafe_endpoint_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.security.egress import EgressValidationError

    async def _refuse(url: str, **_: Any) -> None:
        raise EgressValidationError("private address")

    monkeypatch.setattr(grantex_service, "validate_public_url_async", _refuse)
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={})

    with pytest.raises(GrantexServiceUnavailableError, match="unsafe"):
        _post(_client(handler))
    assert calls == []


def test_tokens_verify_against_the_pinned_jwks_with_one_refresh_for_an_unknown_kid() -> None:
    old, new = EcSigningKey(kid="platform-old"), EcSigningKey(kid="platform-new")
    token = sign_compact_jwt(new, {"iss": ISSUER, "sub": "x"}, typ="decision+jwt")
    verifier = _Verifier({"keys": [old.public_jwk()]}, {"keys": [new.public_jwk()]})
    claims = asyncio.run(_client(lambda r: httpx.Response(200), verifier).verify_token(token, typ="decision+jwt"))
    assert claims["sub"] == "x"
    assert verifier.calls == [False, True]


def test_bad_tokens_and_an_unreachable_jwks_are_refused() -> None:
    key = EcSigningKey(kid="platform-1")
    jwks = {"keys": [key.public_jwk()]}
    client = _client(lambda r: httpx.Response(200), _Verifier(jwks))
    for bad in (None, "", "x" * 16_385):
        with pytest.raises(GrantexTokenError):
            asyncio.run(client.verify_token(bad, typ="decision+jwt"))
    wrong_typ = sign_compact_jwt(key, {"iss": ISSUER}, typ="audit-witness+jwt")
    with pytest.raises(GrantexTokenError):
        asyncio.run(client.verify_token(wrong_typ, typ="decision+jwt"))
    unknown = sign_compact_jwt(EcSigningKey(kid="platform-2"), {"iss": ISSUER}, typ="decision+jwt")
    with pytest.raises(GrantexTokenError):
        asyncio.run(client.verify_token(unknown, typ="decision+jwt"))
    down = _client(lambda r: httpx.Response(200), _Verifier(OacpTrustUnavailableError("down")))
    with pytest.raises(GrantexServiceUnavailableError, match="JWKS"):
        asyncio.run(down.verify_token(wrong_typ, typ="decision+jwt"))
    assert asyncio.run(down.jwks()) is None
    assert asyncio.run(client.jwks()) == jwks


def test_the_client_is_configured_only_with_both_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    assert grantex_service_client_from_env({}) is None
    assert grantex_service_client_from_env({"GRANTEX_COMMERCE_BASE_URL": "https://grantex.example"}) is None
    monkeypatch.setattr(grantex_service, "default_oacp_grantex_verifier", lambda: _Verifier({"keys": []}))
    client = grantex_service_client_from_env(
        {"GRANTEX_COMMERCE_BASE_URL": "https://grantex.example", "GRANTEX_COMMERCE_INTERNAL_TOKEN": "placeholder"}
    )
    assert isinstance(client, GrantexServiceClient)
