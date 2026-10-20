# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Test-only OACP artifact signer and Token Status List issuer.

Keys are generated at test time and never leave the process. The shared
vector file (tests/fixtures/oacp/oacp-artifact-jws-v1.json) carries its own
synthetic key; this helper exists for cases the vectors do not cover
(rotation, expiry, status lists, the cache endpoint).
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from core.commerce.oacp_jws import b64url_encode, canonicalize_bytes, oacp_payload_hash

VECTOR_PATH = Path(__file__).parent / "fixtures" / "oacp" / "oacp-artifact-jws-v1.json"
ISSUER = "https://issuer.example"
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
STATUS_LIST_URI = f"{ISSUER}/status/oacp/lst_TEST01"


def load_vectors() -> dict[str, Any]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


@dataclass
class EcSigningKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))

    def public_jwk(self) -> dict[str, Any]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": b64url_encode(numbers.x.to_bytes(32, "big")),
            "y": b64url_encode(numbers.y.to_bytes(32, "big")),
            "kid": self.kid,
            "alg": "ES256",
            "use": "sig",
        }

    def sign(self, data: bytes) -> bytes:
        r, s = decode_dss_signature(self.private_key.sign(data, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


@dataclass
class RsaSigningKey:
    kid: str
    private_key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )

    def public_jwk(self) -> dict[str, Any]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "n": b64url_encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": b64url_encode(numbers.e.to_bytes(3, "big")),
            "kid": self.kid,
            "alg": "RS256",
            "use": "sig",
        }

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def sign_envelope(envelope: Mapping[str, Any], key: EcSigningKey, *, header: Mapping[str, Any] | None = None) -> dict:
    """Sign an envelope with the OACP detached-JWS profile."""
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    protected = dict(header) if header is not None else {"alg": "ES256", "kid": key.kid, "typ": "oacp-artifact+jws"}
    header_b64 = b64url_encode(json.dumps(protected, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{b64url_encode(canonicalize_bytes(unsigned))}".encode("ascii")
    return {**unsigned, "signature": f"{header_b64}..{b64url_encode(key.sign(signing_input))}"}


def build_envelope(
    payload: Mapping[str, Any],
    *,
    key: EcSigningKey,
    issued_at: str,
    expires_at: str,
    artifact_id: str = "oacp_art_TESTCATALOG01",
    artifact_type: str = "catalog_snapshot",
    status_idx: int = 7,
    status_uri: str = STATUS_LIST_URI,
    issuer: str = ISSUER,
    **overrides: Any,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "schema_version": "oacp.artifact.v1",
        "issuer": issuer,
        "issuer_key_id": key.kid,
        "subject_type": payload.get("artifact_family", artifact_type),
        "subject_id": f"{payload['merchant_id']}:{payload['seller_agent_id']}",
        "tenant_id": payload["tenant_id"],
        "merchant_id": payload["merchant_id"],
        "seller_agent_id": payload["seller_agent_id"],
        "buyer_agent_id": "",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "source_observed_at": issued_at,
        "freshness_class": "fresh",
        "revocation_status_url": status_uri,
        "status": {"status_list": {"idx": status_idx, "uri": status_uri}},
        "policy_version": "oacp-policy-test",
        "evidence_refs": [payload.get("source_evidence_ref", "evidence:redacted")],
        "payload_hash": oacp_payload_hash(payload),
        "signature_alg": "ES256",
        "safety": {
            "public_safe": True,
            "contains_private_data": False,
            "commitment_allowed": False,
            "stale_behavior": "refuse_final_commitment",
        },
    }
    envelope.update(overrides)
    return sign_envelope(envelope, key)


def status_list_token(
    key: EcSigningKey | RsaSigningKey,
    *,
    uri: str,
    statuses: Mapping[int, int],
    iat: int,
    exp: int,
    ttl: int = 600,
    size: int = 64,
    sub: str | None = None,
) -> str:
    """A draft-ietf-oauth-status-list Token Status List token with 2-bit entries."""
    raw = bytearray((size + 3) // 4)
    for idx, status in statuses.items():
        raw[idx // 4] |= (status & 0b11) << ((idx % 4) * 2)
    claims = {
        "sub": sub if sub is not None else uri,
        "iss": ISSUER,
        "iat": iat,
        "exp": exp,
        "ttl": ttl,
        "status_list": {"bits": 2, "lst": b64url_encode(zlib.compress(bytes(raw), 9))},
    }
    alg = "ES256" if isinstance(key, EcSigningKey) else "RS256"
    header_b64 = b64url_encode(json.dumps({"alg": alg, "kid": key.kid, "typ": "statuslist+jwt"}).encode())
    claims_b64 = b64url_encode(json.dumps(claims).encode())
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    return f"{header_b64}.{claims_b64}.{b64url_encode(key.sign(signing_input))}"


@dataclass
class FakeGrantex:
    """An httpx MockTransport serving a JWKS and status lists, counting requests."""

    jwks: dict[str, Any]
    status_lists: dict[str, str] = field(default_factory=dict)
    jwks_status: int = 200
    status_list_status: int = 200
    requests: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == JWKS_URI:
            if self.jwks_status != 200:
                return httpx.Response(self.jwks_status)
            return httpx.Response(200, json=self.jwks)
        if url in self.status_lists:
            if self.status_list_status != 200:
                return httpx.Response(self.status_list_status)
            return httpx.Response(
                200,
                text=self.status_lists[url],
                headers={"Content-Type": "application/statuslist+jwt"},
            )
        return httpx.Response(404)

    def transport_factory(self) -> Callable[[], httpx.MockTransport]:
        return lambda: httpx.MockTransport(self.handler)


def sign_compact_jwt(key: EcSigningKey | RsaSigningKey, claims: Mapping[str, Any], *, typ: str) -> str:
    """A compact JWS with an attached payload (decision grants, witness countersignatures)."""
    alg = "ES256" if isinstance(key, EcSigningKey) else "RS256"
    header_b64 = b64url_encode(json.dumps({"alg": alg, "kid": key.kid, "typ": typ}).encode())
    claims_b64 = b64url_encode(json.dumps(dict(claims)).encode())
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    return f"{header_b64}.{claims_b64}.{b64url_encode(key.sign(signing_input))}"
