# SPDX-License-Identifier: Apache-2.0
"""OACP artifact JWS profile v1: detached ES256 JWS over the RFC 8785 envelope.

Pure functions, no I/O. Grantex signs every OACP artifact envelope with a
detached compact JWS (RFC 7515 Appendix F):

- ``envelope.signature`` is ``BASE64URL(header) + ".." + BASE64URL(signature)``;
- the protected header is exactly ``{"alg":"ES256","kid":<issuer_key_id>,
  "typ":"oacp-artifact+jws"}``;
- the JWS Signing Input is ``BASE64URL(header) + "." + BASE64URL(JCS(envelope
  without "signature"))`` (RFC 8785 canonical JSON), so scope, expiry,
  ``payload_hash``, status reference and safety metadata are all signed;
- the signature is ES256 on P-256 as the 64-byte IEEE P1363 ``r || s``
  (RFC 7518 section 3.4);
- ``payload_hash`` is the lowercase hex SHA-256 of the RFC 8785 payload.

``verify_oacp_artifact_jws`` checks steps 1-6 of the cross-repo verification
order and returns one of the shared refusal codes. It has the inputs and
outputs the Grantex Python SDK's ``verify_oacp_artifact`` will have, so the
call site switches to the SDK once that release is published.

The module also verifies the Token Status List token
(draft-ietf-oauth-status-list section 5.1, ``typ`` ``statuslist+jwt``) that
carries an artifact's revocation status, and decodes a status at an index
(section 4.1: DEFLATE/ZLIB, entries packed from the least significant bit).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

try:  # RFC 8785 canonicalisation shipped by the Grantex SDK.
    from grantex.canonical import CanonicalizationError, canonicalize_bytes
except ImportError:  # pragma: no cover - the SDK is a pinned dependency

    class CanonicalizationError(ValueError):  # type: ignore[no-redef]
        """The value has no canonical form."""

    def canonicalize_bytes(value: Any) -> bytes:  # type: ignore[misc]
        # Byte-identical to RFC 8785 for OACP values (strings, integers,
        # booleans, null, arrays, objects with BMP member names; no floats).
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise CanonicalizationError(str(exc)) from exc


OACP_JWS_PROFILE = "oacp-artifact-jws-v1"
OACP_JWS_ALG = "ES256"
OACP_JWS_TYP = "oacp-artifact+jws"
OACP_KID_PREFIX = "oacp-"
STATUS_LIST_JWT_TYP = "statuslist+jwt"

# Shared refusal codes (cross-repo verification order, steps 1-6).
SIGNATURE_MISSING_OR_PLACEHOLDER = "signature_missing_or_placeholder"
SIGNATURE_ALGORITHM_UNSUPPORTED = "signature_algorithm_unsupported"
PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
DETACHED_JWS_VERIFICATION_FAILED = "detached_jws_verification_failed"
ISSUER_KEY_UNTRUSTED = "issuer_key_untrusted"

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")
_P256_COORDINATE_BYTES = 32
_MIN_RSA_BITS = 2048


class OacpJwsError(ValueError):
    """A compact JWS or JWK could not be verified."""


@dataclass(frozen=True)
class OacpJwsVerification:
    """Outcome of steps 1-6 for one artifact."""

    valid: bool
    refusal_code: str | None
    message: str
    issuer_key_id: str | None = None
    payload_hash: str | None = None
    # True when the refusal is "kid not in the JWKS" (a caller may refresh a
    # cached JWKS once before refusing).
    kid_not_found: bool = False


def b64url_decode_canonical(segment: str) -> bytes | None:
    """Decode unpadded base64url, refusing padding, stray characters and non-canonical trailing bits."""
    if not isinstance(segment, str) or not _B64URL_RE.match(segment) or len(segment) % 4 == 1:
        return None
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return None
    if b64url_encode(decoded) != segment:
        return None
    return decoded


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def oacp_payload_hash(payload: Any) -> str:
    """Lowercase hex SHA-256 of the RFC 8785 canonical payload."""
    return hashlib.sha256(canonicalize_bytes(payload)).hexdigest()


def oacp_signing_input(header_b64: str, envelope: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    return f"{header_b64}.{b64url_encode(canonicalize_bytes(unsigned))}".encode("ascii")


def parse_detached_jws(signature: Any) -> tuple[str, str] | None:
    """Return ``(header_b64, signature_b64)`` for a structurally well-formed detached compact JWS.

    Step 1 is structural: three parts, an empty middle part and two non-empty
    unpadded base64url segments. Strict decoding (canonical encoding, header
    JSON, a 64-byte signature) belongs to steps 4 and 6, so a template string
    shaped like a JWS is refused as a failed verification, as the shared
    vectors require.
    """
    if not isinstance(signature, str):
        return None
    parts = signature.split(".")
    if len(parts) != 3 or parts[1] != "" or not parts[0] or not parts[2]:
        return None
    for segment in (parts[0], parts[2]):
        if not _B64URL_RE.match(segment) or len(segment) % 4 == 1:
            return None
    return parts[0], parts[2]


def _matching_jwks_entries(jwks: Mapping[str, Any] | None, kid: str) -> list[Mapping[str, Any]]:
    keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
    if not isinstance(keys, Sequence) or isinstance(keys, str | bytes):
        return []
    return [key for key in keys if isinstance(key, Mapping) and key.get("kid") == kid]


def p256_public_key_from_jwk(jwk: Mapping[str, Any]) -> ec.EllipticCurvePublicKey | None:
    """Return the P-256 key for an ES256 signing JWK, or None when it is unusable."""
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return None
    if jwk.get("use", "sig") != "sig" or jwk.get("alg", OACP_JWS_ALG) != OACP_JWS_ALG:
        return None
    if "d" in jwk:  # a private JWK in a published key set is never trusted
        return None
    x = b64url_decode_canonical(str(jwk.get("x") or ""))
    y = b64url_decode_canonical(str(jwk.get("y") or ""))
    if x is None or y is None or len(x) != _P256_COORDINATE_BYTES or len(y) != _P256_COORDINATE_BYTES:
        return None
    try:
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        ).public_key()
    except ValueError:  # point not on the curve
        return None


def rsa_public_key_from_jwk(jwk: Mapping[str, Any]) -> rsa.RSAPublicKey | None:
    """Return the RSA key for an RS256 signing JWK, or None when it is unusable."""
    if jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
        return None
    if "d" in jwk:
        return None
    n = b64url_decode_canonical(str(jwk.get("n") or ""))
    e = b64url_decode_canonical(str(jwk.get("e") or ""))
    if not n or not e:
        return None
    modulus = int.from_bytes(n, "big")
    if modulus.bit_length() < _MIN_RSA_BITS:
        return None
    try:
        return rsa.RSAPublicNumbers(int.from_bytes(e, "big"), modulus).public_key()
    except ValueError:
        return None


def es256_verify(key: ec.EllipticCurvePublicKey, signing_input: bytes, signature: bytes) -> bool:
    """Verify a 64-byte IEEE P1363 ES256 signature (RFC 7518 section 3.4)."""
    if len(signature) != 2 * _P256_COORDINATE_BYTES:
        return False
    r = int.from_bytes(signature[:_P256_COORDINATE_BYTES], "big")
    s = int.from_bytes(signature[_P256_COORDINATE_BYTES:], "big")
    try:
        key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def _refusal(code: str, message: str, **extra: Any) -> OacpJwsVerification:
    return OacpJwsVerification(valid=False, refusal_code=code, message=message, **extra)


def verify_oacp_artifact_jws(
    envelope: Mapping[str, Any],
    payload: Any,
    jwks: Mapping[str, Any] | None,
    *,
    expected_issuer: str | None = None,
) -> OacpJwsVerification:
    """Steps 1-6 of the OACP verification order for one artifact.

    1. ``signature`` is a well-formed detached JWS, else
       ``signature_missing_or_placeholder``.
    2. ``signature_alg`` is ES256, else ``signature_algorithm_unsupported``.
    3. The recomputed payload hash equals ``payload_hash``, else
       ``payload_hash_mismatch``.
    4. The protected header is exactly alg/kid/typ with alg ES256, typ
       ``oacp-artifact+jws`` and kid equal to ``issuer_key_id``, else
       ``detached_jws_verification_failed``.
    5. Exactly one usable ES256 P-256 JWK carries the kid (and the envelope
       names the pinned issuer when ``expected_issuer`` is given), else
       ``issuer_key_untrusted``.
    6. The ECDSA signature verifies over the signing input, else
       ``detached_jws_verification_failed``.
    """
    if not isinstance(envelope, Mapping):
        return _refusal(SIGNATURE_MISSING_OR_PLACEHOLDER, "Artifact envelope is not an object.")

    parsed = parse_detached_jws(envelope.get("signature"))
    if parsed is None:
        return _refusal(SIGNATURE_MISSING_OR_PLACEHOLDER, "Detached JWS signature is missing or malformed.")
    header_b64, signature_b64 = parsed

    if envelope.get("signature_alg") != OACP_JWS_ALG:
        return _refusal(SIGNATURE_ALGORITHM_UNSUPPORTED, "Signature algorithm is unsupported.")

    try:
        payload_hash = oacp_payload_hash(payload)
    except CanonicalizationError:
        return _refusal(PAYLOAD_HASH_MISMATCH, "Payload has no canonical form.")
    if payload_hash != envelope.get("payload_hash"):
        return _refusal(PAYLOAD_HASH_MISMATCH, "Payload hash does not match the artifact.")

    issuer_key_id = envelope.get("issuer_key_id")
    try:
        header = json.loads(b64url_decode_canonical(header_b64) or b"")
    except (ValueError, UnicodeDecodeError):
        header = None
    if (
        not isinstance(header, dict)
        or set(header) != {"alg", "kid", "typ"}
        or header.get("alg") != OACP_JWS_ALG
        or header.get("typ") != OACP_JWS_TYP
        or not isinstance(issuer_key_id, str)
        or header.get("kid") != issuer_key_id
    ):
        return _refusal(
            DETACHED_JWS_VERIFICATION_FAILED,
            "Protected header is not the OACP ES256 profile for this issuer key.",
            payload_hash=payload_hash,
        )

    if expected_issuer is not None and (
        not expected_issuer or str(envelope.get("issuer") or "").rstrip("/") != expected_issuer.rstrip("/")
    ):
        return _refusal(
            ISSUER_KEY_UNTRUSTED,
            "Artifact names an issuer other than the pinned Grantex issuer.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
        )
    if not issuer_key_id.startswith(OACP_KID_PREFIX):
        return _refusal(
            ISSUER_KEY_UNTRUSTED,
            "Issuer key id is not an OACP issuer key.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
        )
    matches = _matching_jwks_entries(jwks, issuer_key_id)
    if len(matches) != 1:
        return _refusal(
            ISSUER_KEY_UNTRUSTED,
            "Issuer key is not published exactly once in the issuer JWKS.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
            kid_not_found=not matches,
        )
    public_key = p256_public_key_from_jwk(matches[0])
    if public_key is None:
        return _refusal(
            ISSUER_KEY_UNTRUSTED,
            "Published issuer key is not a usable ES256 P-256 signing key.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
        )

    try:
        signing_input = oacp_signing_input(header_b64, envelope)
    except CanonicalizationError:
        return _refusal(
            DETACHED_JWS_VERIFICATION_FAILED,
            "Envelope has no canonical form.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
        )
    signature_bytes = b64url_decode_canonical(signature_b64)
    if signature_bytes is None or not es256_verify(public_key, signing_input, signature_bytes):
        return _refusal(
            DETACHED_JWS_VERIFICATION_FAILED,
            "Detached JWS signature does not verify.",
            issuer_key_id=issuer_key_id,
            payload_hash=payload_hash,
        )
    return OacpJwsVerification(
        valid=True,
        refusal_code=None,
        message="Detached JWS verified.",
        issuer_key_id=issuer_key_id,
        payload_hash=payload_hash,
    )


def verify_compact_jwt(
    token: str,
    jwks: Mapping[str, Any] | None,
    *,
    expected_typ: str,
    allowed_algs: Sequence[str] = ("ES256", "RS256"),
) -> dict[str, Any]:
    """Verify a compact JWS (attached payload) against a JWKS and return its claims.

    Raises ``OacpJwsError`` for any malformed token, unknown or ambiguous kid,
    unusable key, wrong ``typ`` or failed signature.
    """
    if not isinstance(token, str):
        raise OacpJwsError("token is not a string")
    parts = token.strip().split(".")
    if len(parts) != 3 or not all(parts):
        raise OacpJwsError("token is not a compact JWS")
    header_raw = b64url_decode_canonical(parts[0])
    payload_raw = b64url_decode_canonical(parts[1])
    signature = b64url_decode_canonical(parts[2])
    if header_raw is None or payload_raw is None or signature is None:
        raise OacpJwsError("token segment is not canonical base64url")
    try:
        header = json.loads(header_raw)
        claims = json.loads(payload_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OacpJwsError("token segment is not JSON") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise OacpJwsError("token header or claims are not objects")
    if header.get("typ") != expected_typ:
        raise OacpJwsError("unexpected typ")
    if "crit" in header:
        raise OacpJwsError("crit header is not supported")
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in allowed_algs or not isinstance(kid, str) or not kid:
        raise OacpJwsError("unsupported alg or missing kid")
    matches = _matching_jwks_entries(jwks, kid)
    if len(matches) != 1:
        raise OacpJwsError("kid is not published exactly once")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    if alg == "ES256":
        ec_key = p256_public_key_from_jwk(matches[0])
        if ec_key is None or not es256_verify(ec_key, signing_input, signature):
            raise OacpJwsError("signature does not verify")
    else:
        rsa_key = rsa_public_key_from_jwk(matches[0])
        if rsa_key is None:
            raise OacpJwsError("unusable RSA key")
        try:
            rsa_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise OacpJwsError("signature does not verify") from exc
    return claims


_MAX_STATUS_LIST_BYTES = 16 * 1024 * 1024


def token_status_at(status_list: Mapping[str, Any], idx: int) -> int:
    """Return the status at ``idx`` of a Token Status List ``status_list`` claim.

    draft-ietf-oauth-status-list section 4.1: ``lst`` is the base64url of the
    ZLIB/DEFLATE-compressed byte array; ``bits`` is 1, 2, 4 or 8; entries are
    packed from the least significant bit of each byte. Raises ``OacpJwsError``
    for a malformed list or an index out of bounds.
    """
    bits = status_list.get("bits")
    lst = status_list.get("lst")
    if bits not in (1, 2, 4, 8) or not isinstance(lst, str):
        raise OacpJwsError("status_list claim is malformed")
    if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
        raise OacpJwsError("status index is not a non-negative integer")
    compressed = b64url_decode_canonical(lst)
    if compressed is None:
        raise OacpJwsError("status list is not canonical base64url")
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, _MAX_STATUS_LIST_BYTES)
        if decompressor.unconsumed_tail:
            raise OacpJwsError("status list exceeds the size limit")
    except zlib.error as exc:
        raise OacpJwsError("status list is not ZLIB data") from exc
    per_byte = 8 // bits
    byte_index = idx // per_byte
    if byte_index >= len(raw):
        raise OacpJwsError("status index is out of bounds")
    shift = (idx % per_byte) * bits
    return (raw[byte_index] >> shift) & ((1 << bits) - 1)
