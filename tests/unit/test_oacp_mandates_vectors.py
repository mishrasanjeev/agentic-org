# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The local mandate verifier against the shared Grantex mandate vectors (every case, both files).

``tests/fixtures/oacp/oacp-mandates-v1.json`` (31 cases, 5 chains) and
``oacp-mandates-v1-extra.json`` (repeated JSON member names) are the files the
Grantex auth service and its TypeScript, Python and Go SDKs run. Each case must
give the exact refusal code. The status-list path is exercised with a Status
List Token signed by a key generated here, read through the pinned trust
fetcher (``OacpGrantexArtifactVerifier.status_at``).
"""

from __future__ import annotations

import base64
import json
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from core.commerce import oacp_mandates as m
from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    reset_oacp_trust_caches,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oacp"
VECTORS: dict[str, Any] = json.loads((FIXTURES / "oacp-mandates-v1.json").read_text(encoding="utf-8"))
EXTRA: dict[str, Any] = json.loads((FIXTURES / "oacp-mandates-v1-extra.json").read_text(encoding="utf-8"))


def _now(vectors: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(vectors["verify_at"], tz=UTC)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _claims(token: str) -> dict[str, Any]:
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_vector_files_have_the_expected_case_counts() -> None:
    assert len(VECTORS["cases"]) == 31 and len(VECTORS["chains"]) == 5
    assert len(EXTRA["cases"]) == 4


def test_checkout_hash_matches_the_vectors() -> None:
    assert m.checkout_hash(VECTORS["checkout"]) == VECTORS["checkout_hash"]


@pytest.mark.parametrize(
    ("vectors", "case"),
    [(VECTORS, c) for c in VECTORS["cases"]] + [(EXTRA, c) for c in EXTRA["cases"]],
    ids=[f"v1:{c['name']}" for c in VECTORS["cases"]] + [f"extra:{c['name']}" for c in EXTRA["cases"]],
)
async def test_vector_case(vectors: dict[str, Any], case: dict[str, Any]) -> None:
    result = await m.verify_oacp_mandate(
        case["mandate"],
        issuer=vectors["issuer"],
        jwks=vectors["jwks"],
        now=_now(vectors),
        expected_type=case.get("expected_type"),
        expected_audience=case.get("expected_audience", vectors["expected_audience"]),
        status=case.get("status", 0),
    )
    if case["expect"] is None:
        assert result.valid, result
        assert result.status == "valid"
        assert result.mandate_hash == m.mandate_hash(case["mandate"])
        assert result.jti == result.claims["jti"]
    else:
        assert not result.valid
        assert result.refusal_code == case["expect"], (case["name"], result)
        assert result.refusal_code in m.REFUSAL_CODES


@pytest.mark.parametrize("chain", VECTORS["chains"], ids=[c["name"] for c in VECTORS["chains"]])
async def test_vector_chain(chain: dict[str, Any]) -> None:
    claims: dict[str, Any] = {}
    for member in m.MANDATE_TYPES:
        if chain.get(member):
            result = await m.verify_oacp_mandate(
                chain[member], issuer=VECTORS["issuer"], jwks=VECTORS["jwks"], now=_now(VECTORS), status=0
            )
            assert result.valid, (member, result)
            claims[member] = result.claims
    assert m.check_mandate_chain(**claims) == chain["expect"]


def _expectation(chain: dict[str, Any], **overrides: Any) -> m.MandateExpectation:
    payment = _claims(chain["payment"])["payment"]
    values = {
        "tenant_id": _claims(chain["payment"])["tenant_id"],
        "merchant_id": VECTORS["expected_audience"],
        "checkout_hash": payment["transaction_id"],
        "amount_minor_units": payment["amount"],
        "currency": payment["currency"],
    }
    values.update(overrides)
    return m.MandateExpectation(**values)


async def _valid_status(_uri: str, _idx: int, _jwks: Any) -> int:
    return 0


async def test_valid_chain_produces_the_server_verification_ref() -> None:
    chain = next(c for c in VECTORS["chains"] if c["name"] == "valid_chain")
    verified = await m.verify_mandate_chain(
        {k: chain[k] for k in m.MANDATE_TYPES},
        issuer=VECTORS["issuer"],
        jwks=VECTORS["jwks"],
        now=_now(VECTORS),
        expected=_expectation(chain),
        status_reader=_valid_status,
    )
    assert verified.valid, verified
    assert verified.hashes == {k: m.mandate_hash(chain[k]) for k in m.MANDATE_TYPES}
    ref = m.parse_verification_ref(verified.verification_ref)
    assert ref is not None
    assert ref["jti"] == verified.jtis["payment"] and ref["mandate_hash"] == verified.hashes["payment"]
    assert ref["verified_at"].endswith("Z")


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"tenant_id": "cten_OTHER"}, m.MANDATE_BINDING_MISMATCH),
        ({"checkout_hash": "A" * 43}, m.MANDATE_BINDING_MISMATCH),
        ({"amount_minor_units": 1}, m.MANDATE_BINDING_MISMATCH),
        ({"currency": "USD"}, m.MANDATE_BINDING_MISMATCH),
        ({"merchant_id": "mch_OTHER"}, m.MANDATE_AUDIENCE_MISMATCH),
    ],
)
async def test_a_chain_not_bound_to_the_server_expectation_is_refused(override: dict[str, Any], code: str) -> None:
    chain = next(c for c in VECTORS["chains"] if c["name"] == "valid_chain")
    verified = await m.verify_mandate_chain(
        {k: chain[k] for k in m.MANDATE_TYPES},
        issuer=VECTORS["issuer"],
        jwks=VECTORS["jwks"],
        now=_now(VECTORS),
        expected=_expectation(chain, **override),
        status_reader=_valid_status,
    )
    assert not verified.valid and verified.refusal_code == code
    assert verified.verification_ref is None


async def test_a_mismatched_chain_never_yields_a_verification_ref() -> None:
    chain = next(c for c in VECTORS["chains"] if c["name"] == "payment_amount_differs")
    verified = await m.verify_mandate_chain(
        {k: chain[k] for k in m.MANDATE_TYPES},
        issuer=VECTORS["issuer"],
        jwks=VECTORS["jwks"],
        now=_now(VECTORS),
        expected=_expectation(chain),
        status_reader=_valid_status,
    )
    assert verified.refusal_code == m.MANDATE_CHAIN_MISMATCH and verified.verification_ref is None


async def test_no_status_reader_fails_closed() -> None:
    intent = next(c for c in VECTORS["cases"] if c["name"] == "valid_intent")["mandate"]
    result = await m.verify_oacp_mandate(intent, issuer=VECTORS["issuer"], jwks=VECTORS["jwks"], now=_now(VECTORS))
    assert result.refusal_code == m.REVOCATION_STATUS_UNAVAILABLE


@pytest.mark.parametrize("ref", [None, "", "oacp-mandate-v1;jti=x", "other;jti=a;mandate_hash=b;verified_at=c"])
def test_parse_verification_ref_refuses_anything_else(ref: Any) -> None:
    assert m.parse_verification_ref(ref) is None


# --- the pinned status list, through the Phase 1 trust fetcher ---------------------------------


def _status_token(key: ec.EllipticCurvePrivateKey, entry: dict[str, Any], status: int) -> str:
    data = bytearray(16)
    data[entry["idx"] // 4] |= status << ((entry["idx"] % 4) * 2)
    header = _b64u(json.dumps({"alg": "ES256", "kid": "status-test-es", "typ": "statuslist+jwt"}).encode())
    claims = _b64u(
        json.dumps(
            {
                "iss": VECTORS["issuer"],
                "sub": entry["uri"],
                "iat": VECTORS["verify_at"] - 30,
                "exp": VECTORS["verify_at"] + 3600,
                "ttl": 30,
                "status_list": {"bits": 2, "lst": _b64u(zlib.compress(bytes(data)))},
            }
        ).encode()
    )
    r, s = decode_dss_signature(key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


@pytest.mark.parametrize(("status", "expected"), [(0, None), (1, "mandate_revoked"), (2, "mandate_suspended")])
async def test_status_list_is_read_through_the_pinned_trust_fetcher(status: int, expected: str | None) -> None:
    reset_oacp_trust_caches()
    intent = next(c for c in VECTORS["cases"] if c["name"] == "valid_intent")["mandate"]
    entry = _claims(intent)["status"]["status_list"]
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    jwks = {
        "keys": [
            *VECTORS["jwks"]["keys"],
            {
                "kty": "EC",
                "crv": "P-256",
                "kid": "status-test-es",
                "alg": "ES256",
                "use": "sig",
                "x": _b64u(numbers.x.to_bytes(32, "big")),
                "y": _b64u(numbers.y.to_bytes(32, "big")),
            },
        ]
    }
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json=jwks)
        return httpx.Response(200, text=_status_token(key, entry, status))

    verifier = OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=VECTORS["issuer"],
            jwks_uri=f"{VECTORS['issuer']}/.well-known/jwks.json",
            status_list_origins=frozenset({VECTORS["issuer"]}),
        ),
        transport_factory=lambda: httpx.MockTransport(handler),
    )
    jwks_source, reader, local = m.trust_sources(verifier, _now(VECTORS))
    result = await m.verify_oacp_mandate(
        intent,
        issuer=VECTORS["issuer"],
        jwks=jwks_source,
        now=_now(VECTORS),
        status_reader=reader,
        allow_local_endpoints=local,
    )
    assert (None if result.valid else result.refusal_code) == expected
    assert entry["uri"] in fetched
    reset_oacp_trust_caches()


async def test_a_status_list_outside_the_pinned_origins_is_unavailable() -> None:
    reset_oacp_trust_caches()
    intent = next(c for c in VECTORS["cases"] if c["name"] == "valid_intent")["mandate"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json=VECTORS["jwks"])
        raise AssertionError("the status list must not be fetched from an unpinned origin")

    verifier = OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=VECTORS["issuer"],
            jwks_uri=f"{VECTORS['issuer']}/.well-known/jwks.json",
            status_list_origins=frozenset({"https://other.example"}),
        ),
        transport_factory=lambda: httpx.MockTransport(handler),
    )
    jwks_source, reader, local = m.trust_sources(verifier, _now(VECTORS))
    result = await m.verify_oacp_mandate(
        intent, issuer=VECTORS["issuer"], jwks=jwks_source, now=_now(VECTORS), status_reader=reader
    )
    assert result.refusal_code == m.REVOCATION_STATUS_UNAVAILABLE
    reset_oacp_trust_caches()
