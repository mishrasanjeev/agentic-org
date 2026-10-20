# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Outbound buyer-agent request signatures (G48) and TAP body objects on purchase intents (G49).

Before G48 the Grantex Commerce buyer connector sent every request unsigned:
a merchant edge that verifies the Visa Trusted Agent Protocol could not tell
AgenticOrg's buyer agent from anyone else. With
``AGENTICORG_HTTPSIG_SIGN_OUTBOUND`` on, each request carries a TAP signature
made with the tenant agent key, which the inbound verifier (and any TAP
verifier) accepts; off (the default), requests are exactly as before.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from grantex_agent_httpsig import (
    HttpRequest,
    InMemoryNonceStore,
    private_jwk_from_key,
    public_jwk,
    verify_tap_body_objects,
    verify_with_profile,
)

from connectors.commerce.grantex_commerce import GrantexCommerceConnector
from core.commerce import agent_request_signing as signing
from core.config import settings

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
# A synthetic compact JWS shape; the guardrail only checks a Commerce Passport is present.
PASSPORT = "e30.e30.c2lnbmF0dXJl"
CONSENT = {
    "id": "consent-0001",
    "merchantId": "merchant.example",
    "agentId": "shopper-01",
    "passportType": "commerce",
    "status": "granted",
    "approvedScopes": ["commerce:purchase"],
    "maxAmount": 250000,
    "currency": "INR",
    "expiresAt": "2099-01-01T00:00:00Z",
}


@pytest.fixture(autouse=True)
def _no_tenant_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    # No BYOK and no platform KEK: the tenant helpers seal with the platform Fernet key.
    async def no_kek(_tenant: uuid.UUID) -> None:
        return None

    monkeypatch.setattr("core.crypto.tenant_secrets._resolve_kek", no_kek)


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = {"content": [{"type": "text", "text": json.dumps({"status": "ok"})}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": result})


async def _connector(recorder: Recorder, config: dict[str, Any]) -> GrantexCommerceConnector:
    connector = GrantexCommerceConnector({"base_url": "https://grantex.example", "bearer_token": "gx_test", **config})
    connector._client = httpx.AsyncClient(
        base_url=connector.base_url,
        headers={"Authorization": "Bearer gx_test"},
        transport=httpx.MockTransport(recorder.handler),
    )
    return connector


def _verify(request: httpx.Request, jwk: dict[str, Any], keyid: str) -> Any:
    return verify_with_profile(
        HttpRequest(request.method, str(request.url), list(request.headers.multi_items()), request.content),
        profile="tap",
        resolve_key=lambda ref: public_jwk(jwk) if ref.keyid == keyid else None,
        expected_authority="grantex.example",
        nonce_store=InMemoryNonceStore(),
        required_components=["@method", "content-digest"],
    )


@pytest.mark.asyncio
async def test_signing_is_off_by_default_and_requests_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings.httpsig_sign_outbound is False
    recorder = Recorder()
    connector = await _connector(recorder, {})
    await connector.catalog_search(query="tote")
    [request] = recorder.requests
    assert "signature" not in request.headers and "signature-input" not in request.headers
    assert json.loads(request.content)["params"]["arguments"] == {"query": "tote"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_key",
    [
        lambda: ed25519.Ed25519PrivateKey.generate(),
        lambda: ec.generate_private_key(ec.SECP256R1()),
        lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
    ],
    ids=["ed25519", "p256", "rsa-pss"],
)
async def test_buyer_requests_carry_a_tap_signature_from_the_tenant_agent_key(
    monkeypatch: pytest.MonkeyPatch, make_key: Any
) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    private = make_key()
    jwk = private_jwk_from_key(private)
    sealed = await signing.seal_agent_signing_key(private, TENANT)
    assert jwk["d"] not in sealed
    recorder = Recorder()
    connector = await _connector(
        recorder, {"agent_signing_key_encrypted": sealed, "agent_signing_keyid": "tap-agent-0001"}
    )
    await connector.catalog_search(query="tote")
    await connector.payment_create_intent(
        idempotency_key="idem-1", merchant_id="merchant.example", passport_jwt=PASSPORT
    )
    browse, pay = recorder.requests
    for request, tag in ((browse, "agent-browser-auth"), (pay, "agent-payer-auth")):
        result = _verify(request, jwk, "tap-agent-0001")
        assert result.ok, result
        assert result.tag == tag
        assert result.components == ("@method", "@authority", "@path", "content-digest")
    # A fresh nonce per request.
    assert _verify(browse, jwk, "tap-agent-0001").nonce != _verify(pay, jwk, "tap-agent-0001").nonce
    # The bearer credential is unchanged and the key never leaves the process.
    assert browse.headers["authorization"] == "Bearer gx_test"
    assert jwk["d"] not in json.dumps(dict(browse.headers)) + browse.content.decode()
    assert jwk["d"] not in repr(connector._agent_signing_key)


@pytest.mark.asyncio
async def test_a_configured_signature_agent_adds_a_web_bot_auth_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    private = ed25519.Ed25519PrivateKey.generate()
    jwk = private_jwk_from_key(private)
    recorder = Recorder()
    connector = await _connector(
        recorder,
        {
            "agent_signing_key_encrypted": await signing.seal_agent_signing_key(private, TENANT),
            "agent_signing_keyid": "tap-agent-0001",
            "agent_signature_agent_url": "https://agents.example",
        },
    )
    await connector.inventory_check(sku="tote-1")
    [request] = recorder.requests
    assert request.headers["signature-agent"] == 'sig2="https://agents.example"'
    assert _verify(request, jwk, "tap-agent-0001").ok
    wba = verify_with_profile(
        HttpRequest(request.method, str(request.url), list(request.headers.multi_items()), request.content),
        profile="web-bot-auth",
        resolve_key=lambda ref: public_jwk(jwk) if ref.signature_agent.url == "https://agents.example" else None,
        expected_authority="grantex.example",
        nonce_store=InMemoryNonceStore(),
    )
    assert wba.ok, wba


@pytest.mark.asyncio
async def test_signing_on_without_a_key_refuses_instead_of_sending_unsigned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    recorder = Recorder()
    connector = await _connector(recorder, {})
    result = await connector.catalog_search(query="tote")
    assert result["error"] == "agent_request_signing_failed" and result["refusal"] is True
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_an_undecryptable_key_refuses_without_echoing_the_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    recorder = Recorder()
    connector = await _connector(recorder, {"agent_signing_key_encrypted": "not-a-ciphertext"})
    result = await connector.consent_request(merchant_id="merchant.example")
    assert result["error"] == "agent_request_signing_failed"
    assert "not-a-ciphertext" not in json.dumps(result)
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_purchase_intent_carries_tap_body_objects_and_the_consent_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    private = ed25519.Ed25519PrivateKey.generate()
    jwk = private_jwk_from_key(private)
    recorder = Recorder()
    connector = await _connector(
        recorder,
        {
            "agent_signing_key_encrypted": await signing.seal_agent_signing_key(private, TENANT),
            "agent_signing_keyid": "tap-agent-0001",
        },
    )
    await connector.payment_create_intent(
        idempotency_key="idem-2",
        passport_jwt=PASSPORT,
        merchant_id="merchant.example",
        grantex_consent=CONSENT,
        tap_consumer_id_token="e30.e30.c2ln",
    )
    [request] = recorder.requests
    result = _verify(request, jwk, "tap-agent-0001")
    assert result.ok and result.tag == "agent-payer-auth"
    body = json.loads(request.content)
    # Never forwarded as tool arguments (Grantex tool schemas refuse unknown members).
    assert body["params"]["arguments"] == {
        "idempotency_key": "idem-2",
        "passport_jwt": PASSPORT,
        "merchant_id": "merchant.example",
    }
    assert body["grantexConsentRecord"]["consent_id"] == "consent-0001"
    assert body["grantexConsentRecord"]["nonce"] == result.nonce
    checked = verify_tap_body_objects(
        body, nonce=result.nonce, keyid="tap-agent-0001", resolve_key=lambda kid: public_jwk(jwk)
    )
    assert checked.usable_for_payment and checked.consumer.verified
    assert checked.consent_record_nonce_matches and checked.consent_id == "consent-0001"


@pytest.mark.asyncio
async def test_an_expired_consent_refuses_the_purchase_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "httpsig_sign_outbound", True)
    private = ed25519.Ed25519PrivateKey.generate()
    recorder = Recorder()
    connector = await _connector(
        recorder, {"agent_signing_key_encrypted": await signing.seal_agent_signing_key(private, TENANT)}
    )
    result = await connector.checkout_create(
        idempotency_key="idem-3",
        passport_jwt=PASSPORT,
        grantex_consent={**CONSENT, "expiresAt": "2020-01-01T00:00:00Z"},
    )
    assert result["error"] == "agent_request_signing_failed"
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_without_signing_the_consent_input_is_dropped_not_forwarded() -> None:
    recorder = Recorder()
    connector = await _connector(recorder, {})
    await connector.payment_create_intent(idempotency_key="idem-4", passport_jwt=PASSPORT, grantex_consent=CONSENT)
    [request] = recorder.requests
    body = json.loads(request.content)
    assert "grantexConsentRecord" not in body
    assert body["params"]["arguments"] == {"idempotency_key": "idem-4", "passport_jwt": PASSPORT}


def test_the_signing_key_repr_hides_the_private_key() -> None:
    jwk = private_jwk_from_key(ed25519.Ed25519PrivateKey.generate())
    key = signing.AgentSigningKey(private_jwk=jwk, keyid="tap-agent-0001")
    assert jwk["d"] not in repr(key)
