# SPDX-License-Identifier: Apache-2.0
"""Inbound RFC 9421 agent request signatures (G47), TAP body objects (G49) and scheme agent ids (G51).

Before G47 nothing verified an agent's request signature on the public commerce
reads or the A2A routes: a TAP or Web Bot Auth signature was ignored, and an
unsigned request was indistinguishable from a signed one. Exit gate: a
TAP-signed request from a test agent is accepted and ledgered; an unsigned one
is refused in ``enforce`` mode and logged in ``log`` mode.

Keys are generated at test time (Ed25519, P-256 and RSA-PSS); the shared
Grantex vectors (``tests/fixtures/agent_httpsig``) are verified as published.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import FastAPI
from fastapi.testclient import TestClient
from grantex_agent_httpsig import (
    HttpRequest,
    build_agentic_consumer,
    build_agentic_payment_container,
    build_grantex_consent_record,
    jwk_thumbprint,
    private_jwk_from_key,
    public_jwk,
    sign_with_profile,
    with_tap_body_objects,
)
from starlette.requests import Request
from structlog.testing import capture_logs

from auth import httpsig, httpsig_keys
from core.config import settings
from core.models.audit import AuditLog
from core.tool_gateway.audit_logger import verify_audit_row

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "agent_httpsig" / "agent-httpsig-profile-vectors.json"
    ).read_text(encoding="utf-8")
)
TENANT = "11111111-1111-4111-8111-111111111111"
KEY_STORE = "https://keys.example/keys/{keyid}"
A2A_PATH = "/api/v1/a2a/message:send"
A2A_BODY = {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "Is the tote available?"}]}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeRedis:
    """``SET NX EX`` semantics, enough for the nonce store."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int | None]] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.values:
            return None
        self.values[key] = (value, ex)
        return True


class KeyServer:
    """An httpx MockTransport answering the TAP key store, directories and the registry lookup."""

    def __init__(self) -> None:
        self.tap_keys: dict[str, dict[str, Any]] = {}
        self.directories: dict[str, httpx.Response] = {}
        self.registry: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://keys.example/keys/"):
            keyid = url.rsplit("/", 1)[1]
            jwk = self.tap_keys.get(keyid)
            return httpx.Response(200, json=jwk) if jwk else httpx.Response(404)
        origin = f"https://{request.url.host}"
        if request.url.path == "/.well-known/http-message-signatures-directory" and origin in self.directories:
            return self.directories[origin]
        if request.url.path == "/v1/registry/agents":
            answer = self.registry.get(request.url.query.decode())
            return httpx.Response(200, json=answer) if answer else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture()
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()

    async def get_redis() -> FakeRedis:
        return fake

    monkeypatch.setattr(httpsig_keys, "get_async_redis", get_redis)
    return fake


@pytest.fixture()
def keys(monkeypatch: pytest.MonkeyPatch) -> KeyServer:
    server = KeyServer()
    httpsig_keys.reset_httpsig_caches()
    monkeypatch.setattr(httpsig_keys, "_transport_factory", lambda: httpx.MockTransport(server.handler))
    monkeypatch.setattr(settings, "httpsig_tap_key_store_urls", KEY_STORE)
    monkeypatch.setattr(settings, "httpsig_signature_agent_origins", "")
    monkeypatch.setattr(settings, "httpsig_authorities", "")
    monkeypatch.setattr(settings, "httpsig_registry_lookup_enabled", False)
    monkeypatch.setattr(settings, "httpsig_verify_mode", "log")
    yield server
    httpsig_keys.reset_httpsig_caches()


def _ed25519() -> dict[str, Any]:
    return private_jwk_from_key(ed25519.Ed25519PrivateKey.generate())


def _p256() -> dict[str, Any]:
    return private_jwk_from_key(ec.generate_private_key(ec.SECP256R1()))


def _rsa() -> dict[str, Any]:
    return private_jwk_from_key(rsa.generate_private_key(public_exponent=65537, key_size=2048))


def _starlette_request(method: str, url: str, headers: list[tuple[str, str]]) -> Request:
    parts = urlsplit(url)
    raw = [(b"host", parts.netloc.encode())] + [(k.lower().encode(), v.encode()) for k, v in headers]
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": parts.path,
            "raw_path": parts.path.encode(),
            "query_string": parts.query.encode(),
            "headers": raw,
            "server": (parts.hostname, 443),
        }
    )


def _tap_sign(
    key: dict[str, Any],
    url: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    tag: str = "agent-browser-auth",
    keyid: str = "tap-key-01",
    nonce: str | None = None,
    headers: list[tuple[str, str]] | None = None,
    components: tuple[str, ...] = ("@authority", "@path"),
) -> dict[str, str]:
    signed = sign_with_profile(
        HttpRequest(method, url, headers or [], body),
        profile="tap",
        key=key,
        keyid=keyid,
        tag=tag,
        nonce=nonce or str(uuid.uuid4()),
        components=components,
    )
    return dict(signed.headers)


# ---------------------------------------------------------------------------
# Shared vectors (grantex spec/examples/agent-httpsig-profile-vectors.json)
# ---------------------------------------------------------------------------


def _vector(name: str) -> dict[str, Any]:
    return next(v for v in VECTORS["sign"] if v["name"] == name)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["tap payer, minimum components", "tap browser, method and content-digest"])
async def test_shared_tap_vectors_verify_with_the_key_store_key(keys: KeyServer, redis: FakeRedis, name: str) -> None:
    vector = _vector(name)
    keys.tap_keys["tap-key-01"] = {**VECTORS["public_jwk"], "kid": "tap-key-01"}
    req = VECTORS["request"]
    request = _starlette_request(
        req["method"], req["url"], [*map(tuple, req["headers"]), *map(tuple, vector["headers"])]
    )
    check = await httpsig.check_request_signature(
        request, body=req["body"].encode(), now=vector["options"]["created"] + 5
    )
    assert check.status == "verified", check
    assert check.identity is not None
    assert (check.identity.profile, check.identity.keyid, check.identity.key_source) == (
        "tap",
        "tap-key-01",
        "tap_key_store",
    )
    # The nonce is held for at least TAP's 8-minute replay window.
    [(_, ttl)] = redis.values.values()
    assert ttl is not None and ttl >= 480
    replay = await httpsig.check_request_signature(
        request, body=req["body"].encode(), now=vector["options"]["created"] + 6
    )
    assert (replay.status, replay.reason) == ("invalid", "nonce_replayed")


@pytest.mark.asyncio
async def test_shared_web_bot_auth_vector_verifies_through_the_signed_directory(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    vector = _vector("web-bot-auth directory")
    directory = VECTORS["directory"]
    keys.directories["https://agents.example"] = httpx.Response(
        200, headers=[tuple(h) for h in directory["headers"]], content=directory["body"].encode()
    )
    monkeypatch.setattr(settings, "httpsig_signature_agent_origins", "https://agents.example")
    req = VECTORS["request"]
    request = _starlette_request(
        req["method"], req["url"], [*map(tuple, req["headers"]), *map(tuple, vector["headers"])]
    )
    check = await httpsig.check_request_signature(request, body=req["body"].encode(), now=directory["created"] + 5)
    assert check.status == "verified", check
    assert check.identity is not None
    assert check.identity.profile == "web-bot-auth"
    assert check.identity.signature_agent == "https://agents.example"
    assert check.identity.key_source == "signature_agent_directory"
    assert check.identity.keyid == VECTORS["thumbprint"]


@pytest.mark.asyncio
async def test_web_bot_auth_directory_outside_the_allow_list_is_never_fetched(
    keys: KeyServer, redis: FakeRedis
) -> None:
    vector = _vector("web-bot-auth directory")
    req = VECTORS["request"]
    request = _starlette_request(
        req["method"], req["url"], [*map(tuple, req["headers"]), *map(tuple, vector["headers"])]
    )
    check = await httpsig.check_request_signature(request, body=req["body"].encode(), now=1790000005)
    assert (check.status, check.reason) == ("invalid", "key_unknown")
    assert keys.requests == []


@pytest.mark.asyncio
async def test_shared_tap_body_object_vectors_verify_after_the_signature(keys: KeyServer, redis: FakeRedis) -> None:
    objects = VECTORS["tap_body_objects"]
    keys.tap_keys["tap-key-01"] = {**VECTORS["public_jwk"], "kid": "tap-key-01"}
    req = VECTORS["request"]
    body = json.dumps(
        {
            "cart_id": "c-1001",
            "agenticConsumer": objects["agenticConsumer"],
            "agenticPaymentContainer": objects["agenticPaymentContainer"],
        }
    ).encode()
    vector = _vector("tap payer, minimum components")  # signs @authority @path with the same nonce
    request = _starlette_request(
        req["method"], req["url"], [*map(tuple, req["headers"]), *map(tuple, vector["headers"])]
    )
    check = await httpsig.check_request_signature(request, body=body, now=vector["options"]["created"] + 5)
    assert check.status == "verified"
    assert check.identity is not None and check.identity.tap_body is not None
    assert check.identity.tap_body["consumer_verified"] is True
    assert check.identity.tap_body["payment_container_verified"] is True
    assert check.identity.tap_body["usable_for_payment"] is True


# ---------------------------------------------------------------------------
# Profiles and algorithms with generated keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make_key", [_ed25519, _p256, _rsa], ids=["ed25519", "p256", "rsa-pss"])
async def test_tap_signatures_verify_for_every_supported_key_type(
    keys: KeyServer, redis: FakeRedis, make_key: Any
) -> None:
    key = make_key()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    url = "https://merchant.example/api/v1/public/commerce/sellers/merchant.example/catalog.json?tenant_id=" + TENANT
    request = _starlette_request("GET", url, list(_tap_sign(key, url).items()))
    check = await httpsig.check_request_signature(request, body=b"")
    assert check.status == "verified", check


@pytest.mark.asyncio
@pytest.mark.parametrize("make_key", [_ed25519, _p256, _rsa], ids=["ed25519", "p256", "rsa-pss"])
async def test_web_bot_auth_signatures_verify_for_every_supported_key_type(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch, make_key: Any
) -> None:
    from grantex_agent_httpsig import sign_directory_response

    key = make_key()
    monkeypatch.setattr(settings, "httpsig_signature_agent_origins", "https://agents.example")
    body = json.dumps({"keys": [{**public_jwk(key), "kid": jwk_thumbprint(key)}]})
    signed = sign_directory_response(body, signing_keys=[key], authority="agents.example")
    keys.directories["https://agents.example"] = httpx.Response(200, headers=signed, content=body.encode())
    url = "https://merchant.example/api/v1/public/commerce/sellers/merchant.example/llms.txt?tenant_id=" + TENANT
    headers = sign_with_profile(
        HttpRequest("GET", url), profile="web-bot-auth", key=key, signature_agent_url="https://agents.example"
    ).headers
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(headers.items())), body=b"")
    assert check.status == "verified", check
    assert check.identity is not None and check.identity.key_source == "signature_agent_directory"


@pytest.mark.asyncio
async def test_a_tampered_or_mis_addressed_request_is_invalid(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    headers = _tap_sign(key, url)
    tampered = _starlette_request("GET", url.replace("/m/", "/other/"), list(headers.items()))
    check = await httpsig.check_request_signature(tampered, body=b"")
    assert (check.status, check.reason) == ("invalid", "signature_mismatch")
    # A configured authority list refuses a request addressed to another host.
    monkeypatch.setattr(settings, "httpsig_authorities", "seller.example")
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(headers.items())), body=b"")
    assert (check.status, check.reason) == ("invalid", "authority_not_accepted")
    assert redis.values == {}


@pytest.mark.asyncio
async def test_an_unknown_keyid_is_negatively_cached(keys: KeyServer, redis: FakeRedis) -> None:
    key = _ed25519()
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    for _ in range(3):
        check = await httpsig.check_request_signature(
            _starlette_request("GET", url, list(_tap_sign(key, url, keyid="unknown-key").items())), body=b""
        )
        assert (check.status, check.reason) == ("invalid", "key_unknown")
    assert len(keys.requests) == 1


@pytest.mark.asyncio
async def test_a_window_longer_than_eight_minutes_is_refused(keys: KeyServer, redis: FakeRedis) -> None:
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    # The library refuses to sign past TAP's window, so the field is made by hand with the web-bot-auth
    # window and a TAP tag: the verifier must refuse it.
    created = int(time.time())
    headers = {
        "Signature-Input": (
            f'sig1=("@authority" "@path");created={created};expires={created + 3600};keyid="tap-key-01";'
            'nonce="n-1";tag="agent-browser-auth"'
        ),
        "Signature": "sig1=:" + base64.b64encode(b"x" * 64).decode() + ":",
    }
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(headers.items())), body=b"")
    assert (check.status, check.reason) == ("invalid", "window_too_long")


@pytest.mark.asyncio
async def test_unsigned_and_foreign_tag_requests_are_unsigned(keys: KeyServer, redis: FakeRedis) -> None:
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    assert (await httpsig.check_request_signature(_starlette_request("GET", url, []), body=b"")).status == "unsigned"
    foreign = {
        "Signature-Input": 'sig1=("@authority");created=1;expires=2;keyid="k";tag="other"',
        "Signature": "sig1=:AA==:",
    }
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(foreign.items())), body=b"")
    assert (check.status, check.reason) == ("unsigned", "signature_not_found")


@pytest.mark.asyncio
async def test_redis_down_makes_a_signed_request_unverifiable(keys: KeyServer, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_redis() -> None:
        return None

    monkeypatch.setattr(httpsig_keys, "get_async_redis", no_redis)
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    check = await httpsig.check_request_signature(
        _starlette_request("GET", url, list(_tap_sign(key, url).items())), body=b""
    )
    assert (check.status, check.reason) == ("unavailable", "nonce_store_unavailable")


# ---------------------------------------------------------------------------
# Grantex registry directory (countersigned with the platform key) and scheme agent ids (G51)
# ---------------------------------------------------------------------------


def _registry_directory(
    platform: ec.EllipticCurvePrivateKey, agent_key: dict[str, Any], *, authority: str
) -> httpx.Response:
    """The directory as the Grantex registry serves it (apps/auth-service signature-directory.ts)."""
    body = json.dumps({"keys": [{**public_jwk(agent_key), "kid": jwk_thumbprint(agent_key), "use": "sig"}]})
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + ":"
    created = int(time.time())
    params = (
        f'("@authority";req "content-digest");created={created};expires={created + 300};'
        'keyid="platform-1";alg="ecdsa-p256-sha256";tag="grantex-registry-directory"'
    )
    base = f'"@authority";req: {authority}\n"content-digest": {digest}\n"@signature-params": {params}'
    r, s = decode_dss_signature(platform.sign(base.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    signature = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()
    return httpx.Response(
        200,
        headers={
            "Content-Type": "application/http-message-signatures-directory+json",
            "Content-Digest": digest,
            "Signature-Input": f"registry={params}",
            "Signature": f"registry=:{signature}:",
        },
        content=body.encode(),
    )


@pytest.fixture()
def registry(keys: KeyServer, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    platform = ec.generate_private_key(ec.SECP256R1())
    platform_jwk = {**public_jwk(private_jwk_from_key(platform)), "kid": "platform-1", "alg": "ES256"}

    async def platform_jwks() -> dict[str, Any]:
        return {"keys": [platform_jwk]}

    monkeypatch.setattr(httpsig_keys, "grantex_base_url_for_env", lambda env=None: "https://registry.example")
    monkeypatch.setattr(httpsig_keys, "_grantex_platform_jwks", platform_jwks)
    monkeypatch.setattr(settings, "httpsig_signature_agent_origins", "https://registry.example")
    return {"platform": platform}


@pytest.mark.asyncio
async def test_registry_directory_keys_are_trusted_through_the_platform_countersignature(
    keys: KeyServer, redis: FakeRedis, registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_key = _p256()
    keys.directories["https://registry.example"] = _registry_directory(
        registry["platform"], agent_key, authority="registry.example"
    )
    monkeypatch.setattr(settings, "httpsig_registry_lookup_enabled", True)
    thumbprint = jwk_thumbprint(agent_key)
    keys.registry[f"key_thumbprint={thumbprint}"] = {
        "agent_did": "did:web:agents.example:shopper-01",
        "attestations": [
            {"type": "agent_identity", "scheme_agent_ids": [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}]}
        ],
    }
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    headers = sign_with_profile(
        HttpRequest("GET", url), profile="web-bot-auth", key=agent_key, signature_agent_url="https://registry.example"
    ).headers
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(headers.items())), body=b"")
    assert check.status == "verified", check
    assert check.identity is not None
    assert check.identity.key_source == "grantex_registry_directory"
    assert check.identity.agent_did == "did:web:agents.example:shopper-01"
    assert [dict(i) for i in check.identity.scheme_agent_ids] == [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}]


@pytest.mark.asyncio
async def test_a_registry_directory_with_a_bad_countersignature_is_not_trusted(
    keys: KeyServer, redis: FakeRedis, registry: dict[str, Any]
) -> None:
    agent_key = _ed25519()
    other_platform = ec.generate_private_key(ec.SECP256R1())
    keys.directories["https://registry.example"] = _registry_directory(
        other_platform, agent_key, authority="registry.example"
    )
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    headers = sign_with_profile(
        HttpRequest("GET", url), profile="web-bot-auth", key=agent_key, signature_agent_url="https://registry.example"
    ).headers
    check = await httpsig.check_request_signature(_starlette_request("GET", url, list(headers.items())), body=b"")
    # Neither the countersignature nor a per-key directory signature verifies: the key is not used.
    assert (check.status, check.reason) == ("invalid", "key_unknown")


@pytest.mark.asyncio
async def test_a_tap_agent_is_looked_up_by_its_scheme_agent_id(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(httpsig_keys, "grantex_base_url_for_env", lambda env=None: "https://registry.example")
    monkeypatch.setattr(settings, "httpsig_registry_lookup_enabled", True)
    key = _ed25519()
    keys.tap_keys["tap-agent-0001"] = {**public_jwk(key), "kid": "tap-agent-0001"}
    keys.registry["scheme=visa_tap&scheme_agent_id=tap-agent-0001"] = {
        "agent_did": "did:web:agents.example:shopper-01",
        "attestations": [{"scheme_agent_ids": [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}]}],
    }
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    request = _starlette_request("GET", url, list(_tap_sign(key, url, keyid="tap-agent-0001").items()))
    check = await httpsig.check_request_signature(request, body=b"")
    assert check.status == "verified"
    assert check.identity is not None and check.identity.agent_did == "did:web:agents.example:shopper-01"
    assert [dict(i) for i in check.identity.scheme_agent_ids] == [{"scheme": "visa_tap", "agent_id": "tap-agent-0001"}]


@pytest.mark.asyncio
async def test_registry_lookup_is_off_by_default(keys: KeyServer, redis: FakeRedis) -> None:
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    url = "https://merchant.example/api/v1/public/commerce/sellers/m/catalog.json"
    check = await httpsig.check_request_signature(
        _starlette_request("GET", url, list(_tap_sign(key, url).items())), body=b""
    )
    assert check.status == "verified"
    assert check.identity is not None and check.identity.scheme_agent_ids == ()
    assert all(r.url.path != "/v1/registry/agents" for r in keys.requests)


def test_the_verify_mode_defaults_to_log_and_reads_its_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import Settings

    assert Settings().httpsig_verify_mode == "log"
    monkeypatch.setenv("AGENTICORG_HTTPSIG_VERIFY_MODE", "enforce")
    assert Settings().httpsig_verify_mode == "enforce"
    for name in ("httpsig_sign_outbound", "httpsig_registry_lookup_enabled"):
        assert getattr(Settings(), name) is False
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforced-typo")
    assert httpsig.verify_mode() == "log"


# ---------------------------------------------------------------------------
# Exit gate: the A2A route, the dependency and the commerce ledger
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def add(self, row: Any) -> None:
        self.rows.append(row)


@pytest.fixture()
def a2a(keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from api.v1 import a2a_interop

    ledger_rows: list[Any] = []
    answered: list[str] = []

    @asynccontextmanager
    async def tenant_session(_tenant: Any):
        yield _Session(ledger_rows)

    async def answer(**kwargs: Any) -> tuple[dict[str, Any], None, None]:
        answered.append(kwargs["question"])
        return (
            {
                "answer": "In stock.",
                "status": "answered",
                "source_label": "s",
                "freshness_label": "f",
                "refusal_reason": None,
            },
            None,
            None,
        )

    monkeypatch.setattr("core.database.get_tenant_session", tenant_session)
    monkeypatch.setattr(a2a_interop, "_answer_buyer_question_for_scope", answer)
    app = FastAPI()

    @app.middleware("http")
    async def buyer_auth(request: Any, call_next: Any) -> Any:
        # What GrantexAuthMiddleware sets for a seller-issued buyer credential.
        state = request.state
        state.tenant_id, state.auth_mode = TENANT, "commerce_buyer"
        state.buyer_merchant_id, state.buyer_seller_agent_id, state.buyer_agent_id = (
            "merchant.example",
            "seller-01",
            "shopper-01",
        )
        return await call_next(request)

    app.include_router(a2a_interop.router, prefix="/api/v1")
    client = TestClient(app, base_url="https://merchant.example")
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    return {"client": client, "key": key, "rows": ledger_rows, "answered": answered}


def _send(a2a: dict[str, Any], *, signed: bool, body: dict[str, Any] | None = None, nonce: str | None = None):
    content = json.dumps(body or A2A_BODY).encode()
    headers = {"Content-Type": "application/json", "A2A-Version": "1.0"}
    if signed:
        headers.update(
            _tap_sign(
                a2a["key"],
                "https://merchant.example" + A2A_PATH,
                method="POST",
                body=content,
                nonce=nonce,
                headers=list(headers.items()),
                components=("@method", "@authority", "@path", "content-digest"),
            )
        )
    return a2a["client"].post(A2A_PATH, content=content, headers=headers)


def _signature_rows(rows: list[Any]) -> list[Any]:
    return [r for r in rows if isinstance(r, AuditLog) and r.event_type == "a2a.agent_signature"]


@pytest.mark.parametrize("mode", ["log", "enforce"])
def test_exit_gate_a_tap_signed_request_is_accepted_and_ledgered(
    a2a: dict[str, Any], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "httpsig_verify_mode", mode)
    response = _send(a2a, signed=True)
    assert response.status_code == 200, response.text
    assert response.json()["message"]["parts"] == [{"text": "In stock."}]
    [row] = _signature_rows(a2a["rows"])
    assert row.outcome == "success"
    assert row.tenant_id == uuid.UUID(TENANT)
    assert row.details["ledger"]["buyer_agent_id"] == "shopper-01"
    signature = row.details["agent_signature"]
    assert (signature["profile"], signature["tag"], signature["keyid"]) == ("tap", "agent-browser-auth", "tap-key-01")
    assert signature["key_source"] == "tap_key_store"
    assert verify_audit_row(row)
    # Nothing secret reaches the ledger row.
    assert '"d"' not in json.dumps(row.details)


def test_exit_gate_an_unsigned_request_is_refused_in_enforce_mode(
    a2a: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    response = _send(a2a, signed=False)
    assert response.status_code == 401
    assert response.json()["detail"] == {"error": "agent_signature_required", "reason": "signature_missing"}
    assert 'tag="agent-browser-auth"' in response.headers["Accept-Signature"]
    assert a2a["answered"] == []
    [row] = _signature_rows(a2a["rows"])
    assert row.outcome == "refused"


def test_exit_gate_an_unsigned_request_is_logged_in_log_mode(a2a: dict[str, Any]) -> None:
    with capture_logs() as logs:
        response = _send(a2a, signed=False)
    assert response.status_code == 200
    assert a2a["answered"] == ["Is the tote available?"]
    entries = [e for e in logs if e["event"] == "agent_request_signature"]
    assert entries and entries[0]["status"] == "unsigned" and entries[0]["mode"] == "log"
    assert _signature_rows(a2a["rows"]) == []


def test_a_replayed_signed_request_is_refused_in_enforce_mode(
    a2a: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    nonce = str(uuid.uuid4())
    assert _send(a2a, signed=True, nonce=nonce).status_code == 200
    response = _send(a2a, signed=True, nonce=nonce)
    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "nonce_replayed"


def test_enforce_mode_refuses_with_503_when_the_nonce_store_is_down(
    a2a: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_redis() -> None:
        return None

    monkeypatch.setattr(httpsig_keys, "get_async_redis", no_redis)
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    response = _send(a2a, signed=True)
    assert response.status_code == 503
    assert a2a["answered"] == []
    monkeypatch.setattr(settings, "httpsig_verify_mode", "log")
    assert _send(a2a, signed=True).status_code == 200


def test_tap_body_objects_on_a2a_are_verified_and_recorded(a2a: dict[str, Any]) -> None:
    nonce = str(uuid.uuid4())
    key = a2a["key"]
    consent = {
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
    body = with_tap_body_objects(
        A2A_BODY,
        consumer=build_agentic_consumer(key=key, kid="tap-key-01", nonce=nonce, id_token="e30.e30.c2ln"),
        payment_container=build_agentic_payment_container(key=key, kid="tap-key-01", nonce=nonce),
        consent_record=build_grantex_consent_record(consent, nonce=nonce),
    )
    assert _send(a2a, signed=True, body=body, nonce=nonce).status_code == 200
    [row] = _signature_rows(a2a["rows"])
    tap_body = row.details["agent_signature"]["tap_body"]
    assert tap_body["usable_for_payment"] is True
    assert tap_body["consumer_verified"] is True
    assert tap_body["consent_record_nonce_matches"] is True
    assert tap_body["consent_id"] == "consent-0001"


def test_a_tampered_payment_container_is_not_usable_for_payment(a2a: dict[str, Any]) -> None:
    nonce = str(uuid.uuid4())
    container = build_agentic_payment_container(key=a2a["key"], kid="tap-key-01", nonce=nonce)
    container["paymentCredentialsHash"] = "changed"
    body = with_tap_body_objects(A2A_BODY, payment_container=container)
    assert _send(a2a, signed=True, body=body, nonce=nonce).status_code == 200
    [row] = _signature_rows(a2a["rows"])
    tap_body = row.details["agent_signature"]["tap_body"]
    assert tap_body["usable_for_payment"] is False
    assert tap_body["payment_container_problems"] == ["signature_invalid"]


def _public_client(keys: KeyServer, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, Any]]:
    from api.v1 import commerce_public

    async def ready(**_: Any) -> dict[str, Any]:
        return {
            "status": "public_catalog_ready",
            "products": [],
            "schema_org_jsonld": {"@context": "https://schema.org"},
            "source_label": "s",
            "freshness_label": "f",
        }

    monkeypatch.setattr(commerce_public, "_load_public_catalog_snapshot", ready)
    monkeypatch.setattr(commerce_public, "build_public_catalog_html", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(commerce_public, "build_public_sitemap_xml", lambda *a, **k: "<urlset/>")
    monkeypatch.setattr(commerce_public, "build_public_llms_txt", lambda *a, **k: "# llms")
    monkeypatch.setattr(commerce_public, "find_public_product", lambda snapshot, slug: {"slug": slug})
    monkeypatch.setattr(commerce_public, "build_schema_org_jsonld", lambda *a, **k: {})
    app = FastAPI()
    app.include_router(commerce_public.public_router, prefix="/api/v1")
    key = _ed25519()
    keys.tap_keys["tap-key-01"] = {**public_jwk(key), "kid": "tap-key-01"}
    return TestClient(app, base_url="https://merchant.example"), key


def test_public_commerce_reads_log_signed_and_unsigned_requests_without_ledgering(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, key = _public_client(keys, monkeypatch)
    path = f"/api/v1/public/commerce/sellers/merchant.example/catalog.json?tenant_id={TENANT}"
    with capture_logs() as logs:
        assert client.get(path, headers=_tap_sign(key, "https://merchant.example" + path)).status_code == 200
        assert client.get(path).status_code == 200
    statuses = [e["status"] for e in logs if e["event"] == "agent_request_signature"]
    assert statuses == ["verified", "unsigned"]
    verified = next(e for e in logs if e["event"] == "agent_request_signature")
    assert verified["agent_signature"]["keyid"] == "tap-key-01"
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    assert client.get(path).status_code == 401
    assert client.get(path, headers=_tap_sign(key, "https://merchant.example" + path)).status_code == 200


AGENT_API_PATHS = ("catalog.json", "products/tote.json")
PUBLIC_PAGE_PATHS = ("", "/products/tote", "/schema-org.jsonld", "/sitemap.xml", "/llms.txt")


@pytest.mark.parametrize("suffix", AGENT_API_PATHS)
def test_enforce_refuses_unsigned_commerce_api_reads(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    client, _ = _public_client(keys, monkeypatch)
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    response = client.get(f"/api/v1/public/commerce/sellers/merchant.example/{suffix}?tenant_id={TENANT}")
    assert response.status_code == 401


@pytest.mark.parametrize("suffix", PUBLIC_PAGE_PATHS)
def test_enforce_never_refuses_public_pages_crawlers_read_unsigned(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    client, _ = _public_client(keys, monkeypatch)
    monkeypatch.setattr(settings, "httpsig_verify_mode", "enforce")
    with capture_logs() as logs:
        response = client.get(f"/api/v1/public/commerce/sellers/merchant.example{suffix}?tenant_id={TENANT}")
    assert response.status_code == 200, response.text
    [entry] = [e for e in logs if e["event"] == "agent_request_signature"]
    assert (entry["status"], entry["mode"], entry["enforceable"]) == ("unsigned", "log", False)


def test_the_product_json_route_is_reached_not_shadowed_by_the_product_page(
    keys: KeyServer, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before: "/products/{product_slug}" (HTML) was registered first and also matched "tote.json",
    # so the JSON product route was unreachable and enforce mode never applied to it.
    client, _ = _public_client(keys, monkeypatch)
    response = client.get(f"/api/v1/public/commerce/sellers/merchant.example/products/tote.json?tenant_id={TENANT}")
    assert response.status_code == 200
    assert response.json()["status"] == "public_product_ready"
    assert response.json()["product"] == {"slug": "tote"}
    page = client.get(f"/api/v1/public/commerce/sellers/merchant.example/products/tote?tenant_id={TENANT}")
    assert page.headers["content-type"].startswith("text/html")


def test_the_dependency_is_on_every_covered_route() -> None:
    from fastapi.routing import APIRoute

    from api.main import app

    enforceable = {
        "/api/v1/a2a/message:send",
        "/api/v1/a2a/extendedAgentCard",
        "/api/v1/public/commerce/sellers/{merchant_id}/catalog.json",
        "/api/v1/public/commerce/sellers/{merchant_id}/products/{product_slug}.json",
    }
    seen = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        if route.path in enforceable:
            assert httpsig.verify_agent_request_signature in calls, route.path
            seen.add(route.path)
        elif route.path.startswith("/api/v1/public/commerce/"):
            assert httpsig.observe_agent_request_signature in calls, route.path
            assert httpsig.verify_agent_request_signature not in calls, route.path
        else:
            assert httpsig.verify_agent_request_signature not in calls, route.path
    assert seen == enforceable
