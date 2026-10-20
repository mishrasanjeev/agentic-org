# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""OACP artifact JWS profile v1: shared vectors and the local verifier.

Every case in tests/fixtures/oacp/oacp-artifact-jws-v1.json must produce the
exact refusal code the cross-repo contract assigns (steps 1-6), both through
the detached-JWS verifier alone and through ``verify_oacp_artifact``.
"""

from __future__ import annotations

import zlib

import pytest

from core.commerce.oacp_artifacts import (
    OacpArtifactScope,
    OacpArtifactVerificationInput,
    verify_oacp_artifact,
)
from core.commerce.oacp_jws import (
    OacpJwsError,
    b64url_decode_canonical,
    b64url_encode,
    oacp_payload_hash,
    p256_public_key_from_jwk,
    parse_detached_jws,
    token_status_at,
    verify_compact_jwt,
    verify_oacp_artifact_jws,
)
from tests.oacp_signing import (
    EcSigningKey,
    RsaSigningKey,
    build_envelope,
    load_vectors,
    sign_envelope,
    status_list_token,
)

VECTORS = load_vectors()
VECTOR_ISSUER = "https://issuer.example"


def _vector_scope() -> OacpArtifactScope:
    envelope = VECTORS["cases"][0]["envelope"]
    return OacpArtifactScope(
        tenant_id=envelope["tenant_id"],
        merchant_id=envelope["merchant_id"],
        seller_agent_id=envelope["seller_agent_id"],
        buyer_agent_id=envelope["buyer_agent_id"],
    )


def test_vector_canonicalization_and_payload_hash_match_the_contract() -> None:
    payload = VECTORS["cases"][0]["payload"]
    assert oacp_payload_hash(payload) == VECTORS["payload_hash"]
    # Upper-case and underscore member names sort by UTF-16 code units (RFC 8785).
    assert VECTORS["payload_canonical"].index('"Zeta_upper"') < VECTORS["payload_canonical"].index('"a_b"')
    assert VECTORS["payload_canonical"].index('"a_b"') < VECTORS["payload_canonical"].index('"ab"')


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[case["name"] for case in VECTORS["cases"]])
def test_every_shared_vector_case_returns_the_exact_code(case: dict) -> None:
    result = verify_oacp_artifact_jws(case["envelope"], case["payload"], VECTORS["jwks"], expected_issuer=VECTOR_ISSUER)
    assert ("valid" if result.valid else result.refusal_code) == case["expect"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[case["name"] for case in VECTORS["cases"]])
def test_every_shared_vector_case_through_verify_oacp_artifact(case: dict) -> None:
    result = verify_oacp_artifact(
        OacpArtifactVerificationInput(
            envelope=case["envelope"],
            payload=case["payload"],
            jwks=VECTORS["jwks"],
            expected_issuer=VECTOR_ISSUER,
            now_iso=VECTORS["verify_at"],
            expected_scope=_vector_scope(),
        )
    )
    assert ("valid" if result["valid"] else result["refusal_code"]) == case["expect"]
    if result["valid"]:
        assert result["issuer_key_id"] == "oacp-test-2026-10-a"
        assert result["payload_hash"] == VECTORS["payload_hash"]
        assert result["verified_at"] == VECTORS["verify_at"]


def test_signature_is_checked_before_any_other_field() -> None:
    """A tampered artifact is refused on its signature even when step-7 checks would also fail."""
    case = next(c for c in VECTORS["cases"] if c["name"] == "tampered_envelope_expiry")
    result = verify_oacp_artifact(
        OacpArtifactVerificationInput(
            envelope=case["envelope"],
            payload=case["payload"],
            jwks=VECTORS["jwks"],
            expected_issuer=VECTOR_ISSUER,
            now_iso="2027-01-01T00:00:00.000Z",  # long after expiry
            expected_scope=OacpArtifactScope("other", "other", "other", "other"),
        )
    )
    assert result["refusal_code"] == "detached_jws_verification_failed"


def test_artifact_naming_another_issuer_is_untrusted() -> None:
    case = VECTORS["cases"][0]
    result = verify_oacp_artifact_jws(
        case["envelope"], case["payload"], VECTORS["jwks"], expected_issuer="https://grantex.example"
    )
    assert result.refusal_code == "issuer_key_untrusted"


def test_unconfigured_issuer_refuses_everything() -> None:
    case = VECTORS["cases"][0]
    result = verify_oacp_artifact_jws(case["envelope"], case["payload"], VECTORS["jwks"], expected_issuer="")
    assert result.refusal_code == "issuer_key_untrusted"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda jwk: {**jwk, "use": "enc"},
        lambda jwk: {**jwk, "alg": "ES384"},
        lambda jwk: {**jwk, "crv": "P-384"},
        lambda jwk: {**jwk, "kty": "OKP"},
        lambda jwk: {**jwk, "x": jwk["x"][:-2]},
        lambda jwk: {**jwk, "d": "AAAA"},
    ],
    ids=["use_enc", "alg_es384", "crv_p384", "kty_okp", "short_x", "private_member"],
)
def test_unusable_published_key_is_untrusted(mutate) -> None:
    case = VECTORS["cases"][0]
    keys = [mutate(dict(VECTORS["jwks"]["keys"][0])), VECTORS["jwks"]["keys"][1]]
    result = verify_oacp_artifact_jws(case["envelope"], case["payload"], {"keys": keys}, expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "issuer_key_untrusted"


def test_duplicate_kid_in_jwks_is_untrusted() -> None:
    case = VECTORS["cases"][0]
    first = VECTORS["jwks"]["keys"][0]
    result = verify_oacp_artifact_jws(
        case["envelope"], case["payload"], {"keys": [first, dict(first)]}, expected_issuer=VECTOR_ISSUER
    )
    assert result.refusal_code == "issuer_key_untrusted"
    assert result.kid_not_found is False


def test_key_withdrawn_from_the_jwks_is_untrusted() -> None:
    """A retired-and-withdrawn, expired or revoked key is absent from the JWKS."""
    case = VECTORS["cases"][0]
    remaining = {"keys": [VECTORS["jwks"]["keys"][1]]}
    result = verify_oacp_artifact_jws(case["envelope"], case["payload"], remaining, expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "issuer_key_untrusted"
    assert result.kid_not_found is True


def test_non_oacp_kid_is_untrusted_even_when_published() -> None:
    key = EcSigningKey(kid="kid_c6z_internal_demo")
    payload = {"tenant_id": "t", "merchant_id": "m", "seller_agent_id": "s"}
    envelope = build_envelope(payload, key=key, issued_at="2026-10-19T10:00:00Z", expires_at="2026-10-19T11:00:00Z")
    result = verify_oacp_artifact_jws(envelope, payload, {"keys": [key.public_jwk()]}, expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "issuer_key_untrusted"


def test_extra_protected_header_member_fails_verification() -> None:
    key = EcSigningKey(kid="oacp-test-extra")
    payload = {"tenant_id": "t", "merchant_id": "m", "seller_agent_id": "s"}
    envelope = build_envelope(payload, key=key, issued_at="2026-10-19T10:00:00Z", expires_at="2026-10-19T11:00:00Z")
    envelope = sign_envelope(
        envelope,
        key,
        header={"alg": "ES256", "kid": key.kid, "typ": "oacp-artifact+jws", "b64": False},
    )
    result = verify_oacp_artifact_jws(envelope, payload, {"keys": [key.public_jwk()]}, expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "detached_jws_verification_failed"


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        (None, "signature_missing_or_placeholder"),
        ("", "signature_missing_or_placeholder"),
        ("detached_jws_required_before_publication", "signature_missing_or_placeholder"),
        ("a..b..c", "signature_missing_or_placeholder"),
        ("eyJ9..abc=", "signature_missing_or_placeholder"),
        ("eyJ9..ab+c", "signature_missing_or_placeholder"),
    ],
)
def test_malformed_signatures_are_missing_or_placeholder(signature, expected) -> None:
    case = VECTORS["cases"][0]
    envelope = {**case["envelope"], "signature": signature}
    result = verify_oacp_artifact_jws(envelope, case["payload"], VECTORS["jwks"], expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == expected


def test_wrong_signature_alg_is_unsupported() -> None:
    case = VECTORS["cases"][0]
    envelope = {**case["envelope"], "signature_alg": "HS256"}
    result = verify_oacp_artifact_jws(envelope, case["payload"], VECTORS["jwks"], expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "signature_algorithm_unsupported"


def test_non_canonical_signature_encoding_fails_verification() -> None:
    case = VECTORS["cases"][0]
    header_b64, signature_b64 = case["envelope"]["signature"].split("..")
    # 64 bytes encode to 86 characters; the last character carries 4 unused bits.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    tweaked = signature_b64[:-1] + alphabet[alphabet.index(signature_b64[-1]) + 1]
    assert b64url_decode_canonical(tweaked) is None
    envelope = {**case["envelope"], "signature": f"{header_b64}..{tweaked}"}
    result = verify_oacp_artifact_jws(envelope, case["payload"], VECTORS["jwks"], expected_issuer=VECTOR_ISSUER)
    assert result.refusal_code == "detached_jws_verification_failed"


def test_step_seven_checks_run_after_a_valid_signature() -> None:
    key = EcSigningKey(kid="oacp-test-step7")
    jwks = {"keys": [key.public_jwk()]}
    payload = {"tenant_id": "cten_A", "merchant_id": "mch_A", "seller_agent_id": "seller_A"}
    scope = OacpArtifactScope("cten_A", "mch_A", "seller_A", "")

    def verify(envelope: dict, now_iso: str = "2026-10-19T10:30:00.000Z", artifact_payload: dict = payload) -> str:
        result = verify_oacp_artifact(
            OacpArtifactVerificationInput(
                envelope=envelope,
                payload=artifact_payload,
                jwks=jwks,
                expected_issuer=VECTOR_ISSUER,
                now_iso=now_iso,
                expected_scope=scope,
            )
        )
        return "valid" if result["valid"] else result["refusal_code"]

    good = build_envelope(payload, key=key, issued_at="2026-10-19T10:00:00.000Z", expires_at="2026-10-19T11:00:00.000Z")
    assert verify(good) == "valid"
    assert verify(good, now_iso="2026-10-19T11:00:01.000Z") == "artifact_expired_or_stale"
    too_long = build_envelope(
        payload, key=key, issued_at="2026-10-19T10:00:00.000Z", expires_at="2026-10-20T10:00:00.000Z"
    )
    assert verify(too_long) == "artifact_ttl_exceeds_default"
    other_scope = build_envelope(
        payload,
        key=key,
        issued_at="2026-10-19T10:00:00.000Z",
        expires_at="2026-10-19T11:00:00.000Z",
        merchant_id="mch_OTHER",
    )
    assert verify(other_scope) == "artifact_scope_mismatch"
    unsafe = build_envelope(
        payload,
        key=key,
        issued_at="2026-10-19T10:00:00.000Z",
        expires_at="2026-10-19T11:00:00.000Z",
        safety={"public_safe": False},
    )
    assert verify(unsafe) == "artifact_missing_or_invalid"
    private_payload = {**payload, "raw_provider_payload": {"x": 1}}
    leaked = build_envelope(
        private_payload, key=key, issued_at="2026-10-19T10:00:00.000Z", expires_at="2026-10-19T11:00:00.000Z"
    )
    assert verify(leaked, artifact_payload=private_payload) == "artifact_missing_or_invalid"
    not_yet = build_envelope(
        payload,
        key=key,
        issued_at="2026-10-19T10:00:00.000Z",
        expires_at="2026-10-19T11:00:00.000Z",
        not_before="2026-10-19T10:45:00.000Z",
    )
    assert verify(not_yet) == "artifact_not_yet_valid"


def test_parse_detached_jws_and_base64url_rules() -> None:
    assert parse_detached_jws("aGVhZA..c2ln") == ("aGVhZA", "c2ln")
    assert parse_detached_jws("aGVhZA.cGF5.c2ln") is None
    assert b64url_decode_canonical("aGVhZA=") is None
    assert b64url_decode_canonical("a") is None
    assert b64url_decode_canonical(b64url_encode(b"\x00\xff")) == b"\x00\xff"
    assert p256_public_key_from_jwk(VECTORS["jwks"]["keys"][0]) is not None


def test_token_status_list_decoding() -> None:
    raw = bytes([0b11_10_01_00, 0b00_00_00_10])
    claim = {"bits": 2, "lst": b64url_encode(zlib.compress(raw))}
    assert [token_status_at(claim, idx) for idx in range(5)] == [0, 1, 2, 3, 2]
    with pytest.raises(OacpJwsError):
        token_status_at(claim, 8)
    with pytest.raises(OacpJwsError):
        token_status_at({"bits": 3, "lst": claim["lst"]}, 0)
    with pytest.raises(OacpJwsError):
        token_status_at({"bits": 2, "lst": b64url_encode(b"not zlib")}, 0)


@pytest.mark.parametrize("key", [EcSigningKey(kid="status-es"), RsaSigningKey(kid="status-rs")], ids=["es256", "rs256"])
def test_status_list_token_verification(key) -> None:
    uri = "https://issuer.example/status/oacp/lst_A"
    token = status_list_token(key, uri=uri, statuses={3: 1}, iat=1_000, exp=2_000)
    jwks = {"keys": [key.public_jwk()]}
    claims = verify_compact_jwt(token, jwks, expected_typ="statuslist+jwt")
    assert claims["sub"] == uri
    assert token_status_at(claims["status_list"], 3) == 1
    with pytest.raises(OacpJwsError):
        verify_compact_jwt(token, jwks, expected_typ="oacp-artifact+jws")
    with pytest.raises(OacpJwsError):
        verify_compact_jwt(token, {"keys": []}, expected_typ="statuslist+jwt")
    head, body, sig = token.split(".")
    other = status_list_token(key, uri=uri, statuses={}, iat=1_000, exp=2_000).split(".")[1]
    with pytest.raises(OacpJwsError):
        verify_compact_jwt(f"{head}.{other}.{sig}", jwks, expected_typ="statuslist+jwt")


def test_clock_skew_and_revocation_family_ttl_match_grantex() -> None:
    key = EcSigningKey(kid="oacp-20261019-0123456789abcdef")
    jwks = {"keys": [key.public_jwk()]}
    payload = {"tenant_id": "cten_A", "merchant_id": "mch_A", "seller_agent_id": "seller_A"}
    scope = OacpArtifactScope("cten_A", "mch_A", "seller_A", "")

    def verify(envelope: dict, now_iso: str) -> str:
        result = verify_oacp_artifact(
            OacpArtifactVerificationInput(
                envelope=envelope,
                payload=payload,
                jwks=jwks,
                expected_issuer=VECTOR_ISSUER,
                now_iso=now_iso,
                expected_scope=scope,
            )
        )
        return "valid" if result["valid"] else result["refusal_code"]

    envelope = build_envelope(
        payload, key=key, issued_at="2026-10-19T10:01:00.000Z", expires_at="2026-10-19T11:00:00.000Z"
    )
    assert verify(envelope, "2026-10-19T10:00:00.000Z") == "valid"  # issuer clock 60 s ahead
    assert verify(envelope, "2026-10-19T09:59:59.000Z") == "artifact_not_yet_valid"
    assert verify(envelope, "2026-10-19T11:00:01.000Z") == "artifact_expired_or_stale"  # expiry is not extended

    revocation = build_envelope(
        payload,
        key=key,
        artifact_type="revocation",
        issued_at="2026-10-19T10:00:00.000Z",
        expires_at="2026-10-20T10:00:00.000Z",
    )
    assert verify(revocation, "2026-10-19T12:00:00.000Z") == "valid"
    too_long = build_envelope(
        payload,
        key=key,
        artifact_type="revocation",
        issued_at="2026-10-19T10:00:00.000Z",
        expires_at="2026-10-20T10:00:01.000Z",
    )
    assert verify(too_long, "2026-10-19T12:00:00.000Z") == "artifact_ttl_exceeds_default"
