# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Local verification of Grantex OACP mandates (profile ``oacp.mandate.v1``, cross-repo contract section 8).

Grantex issues Intent, Cart and Payment mandates as compact ES256 JWTs (RFC 7519;
ES256 on P-256 as the 64-byte IEEE P1363 ``r || s``, RFC 7518 section 3.4)
signed by its OACP issuer key (an ``oacp-`` kid in the Grantex JWK Set), with
``typ`` ``oacp-intent-mandate+jwt``, ``oacp-cart-mandate+jwt`` or
``oacp-payment-mandate+jwt``. Every mandate names its entry on the OACP Token
Status List (``status.status_list``, draft-ietf-oauth-status-list section 6.1).

``verify_oacp_mandate`` stops at the first failure with the shared refusal
code, in the order the Grantex auth service and SDKs use:

1. ``mandate_malformed``: three base64url parts (canonical, unpadded).
2. Header: a JSON object without repeated member names (else
   ``mandate_malformed``); ``alg`` ES256 (``mandate_algorithm_unsupported``);
   exactly ``alg``, ``kid`` and ``typ`` (``mandate_malformed``); ``typ`` one of
   the three, and the expected one (``mandate_type_mismatch``).
3. ``issuer_key_untrusted``: an ``oacp-`` kid published exactly once in the
   pinned JWKS as a usable P-256 signing key (one JWKS refresh for an unknown kid).
4. ``mandate_signature_invalid``.
5. Claims: a JSON object without repeated member names (``mandate_malformed``).
6. ``mandate_issuer_mismatch``: ``iss`` is the pinned issuer.
7. ``mandate_claims_invalid``: the common and per-type claims.
8. ``mandate_not_yet_valid`` (``iat`` beyond 60 s skew), ``mandate_expired``,
   ``mandate_lifetime_exceeded`` (more than 600 s).
9. ``mandate_audience_mismatch``: ``aud`` is the expected merchant.
10. Status list (pinned origin, https): ``revocation_status_unavailable``
    (fail closed), ``mandate_revoked``, ``mandate_suspended``.

``check_mandate_chain`` then checks verified mandates against each other
(``mandate_chain_mismatch``): the parent jti links, ``transaction_id ==
checkout_hash``, payment amount and currency equal the cart total, the cart
total within the intent cap, and the same tenant, merchant, subject, buyer
agent, consent and issuer.

``verify_mandate_chain`` runs both for the three mandates of one checkout and
binds them to what the server expects (Grantex commerce tenant, merchant,
the checkout hash the server computed, the session total). Its success is the
only source of the arbiter's server-side verification ref
(``verification_ref``: the payment mandate jti, its ``mandate_hash`` and the
verification time). Pure functions except the JWKS and status-list reads,
which go through the Phase 1 pinned trust fetcher
(``core.commerce.oacp_grantex_trust``).

Shared vectors: ``tests/fixtures/oacp/oacp-mandates-v1.json`` and
``oacp-mandates-v1-extra.json``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.commerce.oacp_jws import (
    _matching_jwks_entries,
    b64url_decode_canonical,
    canonicalize_bytes,
    es256_verify,
    p256_public_key_from_jwk,
)

MANDATE_TYPES: tuple[str, ...] = ("intent", "cart", "payment")
MANDATE_TYP: dict[str, str] = {
    "intent": "oacp-intent-mandate+jwt",
    "cart": "oacp-cart-mandate+jwt",
    "payment": "oacp-payment-mandate+jwt",
}
MANDATE_VERSION = "oacp.mandate.v1"
MAX_LIFETIME_SECONDS = 600
CLOCK_SKEW_SECONDS = 60
PAYMENT_RAILS: tuple[str, ...] = ("plural", "upi_autopay", "card_network_token")
PRESENCE_MODES: tuple[str, ...] = ("human_present", "human_not_present")
VERIFICATION_REF_PROFILE = "oacp-mandate-v1"

MANDATE_MALFORMED = "mandate_malformed"
MANDATE_ALGORITHM_UNSUPPORTED = "mandate_algorithm_unsupported"
MANDATE_TYPE_MISMATCH = "mandate_type_mismatch"
ISSUER_KEY_UNTRUSTED = "issuer_key_untrusted"
MANDATE_SIGNATURE_INVALID = "mandate_signature_invalid"
MANDATE_ISSUER_MISMATCH = "mandate_issuer_mismatch"
MANDATE_CLAIMS_INVALID = "mandate_claims_invalid"
MANDATE_NOT_YET_VALID = "mandate_not_yet_valid"
MANDATE_EXPIRED = "mandate_expired"
MANDATE_LIFETIME_EXCEEDED = "mandate_lifetime_exceeded"
MANDATE_AUDIENCE_MISMATCH = "mandate_audience_mismatch"
REVOCATION_STATUS_UNAVAILABLE = "revocation_status_unavailable"
MANDATE_REVOKED = "mandate_revoked"
MANDATE_SUSPENDED = "mandate_suspended"
MANDATE_CHAIN_MISMATCH = "mandate_chain_mismatch"
# Local binding of a verified chain to what the server expects (not a Grantex code).
MANDATE_BINDING_MISMATCH = "mandate_binding_mismatch"

REFUSAL_CODES: tuple[str, ...] = (
    MANDATE_MALFORMED,
    MANDATE_ALGORITHM_UNSUPPORTED,
    MANDATE_TYPE_MISMATCH,
    ISSUER_KEY_UNTRUSTED,
    MANDATE_SIGNATURE_INVALID,
    MANDATE_ISSUER_MISMATCH,
    MANDATE_CLAIMS_INVALID,
    MANDATE_NOT_YET_VALID,
    MANDATE_EXPIRED,
    MANDATE_LIFETIME_EXCEEDED,
    MANDATE_AUDIENCE_MISMATCH,
    REVOCATION_STATUS_UNAVAILABLE,
    MANDATE_REVOKED,
    MANDATE_SUSPENDED,
)

# Token Status List values (draft-ietf-oauth-status-list section 7.1).
STATUS_VALID = 0
STATUS_INVALID = 1
STATUS_SUSPENDED = 2

_MAX_MANDATE_CHARS = 32768
_MAX_SAFE_INTEGER = 2**53 - 1
_B64URL = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_HASH_B64URL = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}", re.ASCII)
_AMR = re.compile(r"[a-z][a-z0-9_]{0,31}", re.ASCII)
_TOKEN_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_HEX64 = re.compile(r"[a-f0-9]{64}", re.ASCII)

JwksSource = Callable[[bool], Awaitable[Mapping[str, Any]]]
StatusReader = Callable[[str, int, Mapping[str, Any]], Awaitable[int]]


@dataclass(frozen=True)
class MandateVerification:
    """One mandate's outcome: ``valid`` with its claims and ``mandate_hash``, or a refusal code."""

    valid: bool
    refusal_code: str | None = None
    message: str = ""
    mandate_type: str | None = None
    jti: str | None = None
    kid: str | None = None
    mandate_hash: str | None = None
    claims: dict[str, Any] | None = None
    status: str | None = None  # "valid" or "not_checked"
    kid_not_found: bool = False


def mandate_hash(mandate: str) -> str:
    """Lowercase hex SHA-256 of the compact mandate JWT (the ledger and arbiter ``mandate_hash``)."""
    return hashlib.sha256(mandate.encode("ascii")).hexdigest()


def checkout_hash(checkout: Any) -> str:
    """AP2 ``checkout_hash``: unpadded base64url SHA-256 of the RFC 8785 checkout object."""
    digest = hashlib.sha256(canonicalize_bytes(checkout)).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _DuplicateMemberError(ValueError):
    pass


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateMemberError(key)
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _json_segment(segment: str) -> dict[str, Any] | None:
    """A JOSE header or claims set: canonical base64url of a JSON object with no repeated member name."""
    raw = b64url_decode_canonical(segment)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _full(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _safe_int(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= _MAX_SAFE_INTEGER
    if isinstance(value, float):
        return value.is_integer() and abs(value) <= _MAX_SAFE_INTEGER
    return False


def _non_negative(value: Any) -> bool:
    return _safe_int(value) and value >= 0


def _amount(value: Any) -> bool:
    return isinstance(value, dict) and _non_negative(value.get("value")) and _full(_CURRENCY, value.get("currency"))


def _iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _common_valid(c: Mapping[str, Any]) -> bool:
    status = c.get("status")
    entry = status.get("status_list") if isinstance(status, dict) else None
    amr = c.get("amr")
    return (
        isinstance(c.get("iss"), str)
        and _full(_REF, c.get("sub"))
        and _full(_REF, c.get("aud"))
        and _non_negative(c.get("iat"))
        and _non_negative(c.get("exp"))
        and _full(_REF, c.get("jti"))
        and c.get("mandate_version") == MANDATE_VERSION
        and _full(_REF, c.get("tenant_id"))
        and _full(_REF, c.get("merchant_id"))
        and c.get("aud") == c.get("merchant_id")
        and _full(_REF, c.get("buyer_agent_id"))
        and c.get("presence") in PRESENCE_MODES
        and isinstance(amr, list)
        and all(_full(_AMR, m) for m in amr)
        and _full(_REF, c.get("passport_ref"))
        and _full(_REF, c.get("consent_ref"))
        and isinstance(entry, dict)
        and len(entry) == 2
        and isinstance(entry.get("uri"), str)
        and _non_negative(entry.get("idx"))
    )


def _intent_valid(c: Mapping[str, Any]) -> bool:
    intent = c.get("intent")
    if not isinstance(intent, dict):
        return False
    merchants = intent.get("merchants")
    constraints = intent.get("constraints")
    cnf = c.get("cnf")
    return (
        _full(_HASH_B64URL, intent.get("description_hash"))
        and _amount(intent.get("max_amount"))
        and isinstance(merchants, list)
        and all(isinstance(m, str) and m for m in merchants)
        and c.get("merchant_id") in merchants
        and _iso(intent.get("expires_at"))
        and isinstance(constraints, list)
        and all(isinstance(x, dict) and isinstance(x.get("type"), str) for x in constraints)
        and (("cnf" not in c) or (isinstance(cnf, dict) and isinstance(cnf.get("jwk"), dict)))
    )


def _cart_valid(c: Mapping[str, Any]) -> bool:
    cart = c.get("cart")
    if not isinstance(cart, dict):
        return False
    items = cart.get("line_items")
    ok = (
        _full(_HASH_B64URL, cart.get("checkout_hash"))
        and _full(_REF, cart.get("checkout_id"))
        and isinstance(items, list)
        and len(items) > 0
        and all(
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"] != ""
            and _safe_int(item.get("quantity"))
            and item["quantity"] >= 1
            and _full(_HASH_B64URL, item.get("digest"))
            for item in items
        )
        and _amount(cart.get("total"))
        and _full(_REF, c.get("intent_mandate_jti"))
    )
    if not ok:
        return False
    if c.get("presence") == "human_present":
        webauthn = c.get("webauthn")
        return (
            isinstance(webauthn, dict)
            and _full(_HEX64, webauthn.get("digest"))
            and webauthn.get("challenge") == cart.get("checkout_hash")
            and isinstance(webauthn.get("rp_id"), str)
            and isinstance(webauthn.get("origin"), str)
            and webauthn.get("user_verified") is True
            and isinstance(webauthn.get("asserted_at"), str)
            and _full(_HEX64, webauthn.get("credential_ref"))
            and "passkey" in c.get("amr", [])
        )
    return "webauthn" not in c


def _payment_valid(c: Mapping[str, Any]) -> bool:
    payment = c.get("payment")
    if not isinstance(payment, dict):
        return False
    binding = payment.get("token_binding")
    payee = payment.get("payee")
    return (
        _full(_HASH_B64URL, payment.get("transaction_id"))
        and _non_negative(payment.get("amount"))
        and _full(_CURRENCY, payment.get("currency"))
        and isinstance(payee, dict)
        and payee.get("id") == c.get("merchant_id")
        and payment.get("rail") in PAYMENT_RAILS
        and isinstance(binding, dict)
        and _full(_TOKEN_TYPE, binding.get("type"))
        and _full(_REF, binding.get("id"))
        and _full(_REF, payment.get("cart_mandate_jti"))
    )


_TYPE_VALID: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "intent": _intent_valid,
    "cart": _cart_valid,
    "payment": _payment_valid,
}


def verify_mandate_signed(
    mandate: Any,
    *,
    issuer: str,
    jwks: Mapping[str, Any] | None,
    now_epoch: int,
    expected_type: str | None = None,
    expected_audience: str | None = None,
) -> MandateVerification:
    """Steps 1-9 (no I/O). A valid result has ``status`` ``not_checked``: the status list is step 10."""
    mandate_type: str | None = None

    def refuse(code: str, message: str, **extra: Any) -> MandateVerification:
        return MandateVerification(valid=False, refusal_code=code, message=message, mandate_type=mandate_type, **extra)

    # 1.
    parts = mandate.split(".") if isinstance(mandate, str) and len(mandate) <= _MAX_MANDATE_CHARS else []
    if len(parts) != 3 or not all(_full(_B64URL, part) for part in parts):
        return refuse(MANDATE_MALFORMED, "The mandate is not a compact JWS.")
    header_segment, payload_segment, signature_segment = parts

    # 2.
    header = _json_segment(header_segment)
    if header is None:
        return refuse(MANDATE_MALFORMED, "The header is not a JSON object.")
    if header.get("alg") != "ES256":
        return refuse(MANDATE_ALGORITHM_UNSUPPORTED, "The mandate algorithm is not ES256.")
    if len(header) != 3 or not isinstance(header.get("kid"), str) or "typ" not in header:
        return refuse(MANDATE_MALFORMED, "The header is not exactly alg, kid and typ.")
    mandate_type = next((t for t in MANDATE_TYPES if MANDATE_TYP[t] == header.get("typ")), None)
    if mandate_type is None or (expected_type is not None and mandate_type != expected_type):
        return refuse(MANDATE_TYPE_MISMATCH, "The mandate typ is not the expected OACP mandate type.")

    # 3.
    kid = str(header["kid"])
    if not kid.startswith("oacp-"):
        return refuse(ISSUER_KEY_UNTRUSTED, "The kid is not an OACP issuer key.")
    matches = _matching_jwks_entries(jwks, kid)
    if not matches:
        return refuse(ISSUER_KEY_UNTRUSTED, "The kid is not in the issuer JWK Set.", kid=kid, kid_not_found=True)
    key = p256_public_key_from_jwk(matches[0]) if len(matches) == 1 else None
    if key is None:
        return refuse(ISSUER_KEY_UNTRUSTED, "The issuer key is not a single usable ES256 signing key.", kid=kid)

    # 4.
    signature = b64url_decode_canonical(signature_segment)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    if signature is None or not es256_verify(key, signing_input, signature):
        return refuse(MANDATE_SIGNATURE_INVALID, "The mandate signature does not verify.", kid=kid)

    # 5.
    claims = _json_segment(payload_segment)
    if claims is None:
        return refuse(MANDATE_MALFORMED, "The claims are not a JSON object.", kid=kid)

    # 6.
    if claims.get("iss") != issuer:
        return refuse(MANDATE_ISSUER_MISMATCH, "The mandate names another issuer.", kid=kid)

    # 7.
    if not _common_valid(claims) or not _TYPE_VALID[mandate_type](claims):
        return refuse(MANDATE_CLAIMS_INVALID, f"The {mandate_type} mandate claims are missing or malformed.", kid=kid)

    # 8.
    iat, exp = int(claims["iat"]), int(claims["exp"])
    if iat > now_epoch + CLOCK_SKEW_SECONDS:
        return refuse(MANDATE_NOT_YET_VALID, "The mandate is not yet valid.", kid=kid)
    if now_epoch >= exp:
        return refuse(MANDATE_EXPIRED, "The mandate has expired.", kid=kid)
    if exp <= iat or exp - iat > MAX_LIFETIME_SECONDS:
        return refuse(MANDATE_LIFETIME_EXCEEDED, "The mandate lifetime exceeds the profile maximum.", kid=kid)

    # 9.
    if expected_audience is not None and claims.get("aud") != expected_audience:
        return refuse(MANDATE_AUDIENCE_MISMATCH, "The mandate is for another merchant.", kid=kid)

    return MandateVerification(
        valid=True,
        mandate_type=mandate_type,
        jti=str(claims["jti"]),
        kid=kid,
        mandate_hash=mandate_hash(mandate),
        claims=dict(claims),
        status="not_checked",
    )


def status_refusal(value: Any) -> tuple[str, str] | None:
    """Step 10 outcome for a status value the relying party read (0, 1, 2 or ``"unavailable"``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return REVOCATION_STATUS_UNAVAILABLE, "The mandate status could not be established."
    if value == STATUS_VALID:
        return None
    if value == STATUS_INVALID:
        return MANDATE_REVOKED, "The mandate is revoked."
    if value == STATUS_SUSPENDED:
        return MANDATE_SUSPENDED, "The mandate is suspended."
    return REVOCATION_STATUS_UNAVAILABLE, "The status list holds an unknown status value."


async def verify_oacp_mandate(
    mandate: Any,
    *,
    issuer: str,
    jwks: Mapping[str, Any] | JwksSource,
    now: datetime,
    expected_type: str | None = None,
    expected_audience: str | None = None,
    status: int | str | None = None,
    status_reader: StatusReader | None = None,
    allow_local_endpoints: bool = False,
) -> MandateVerification:
    """Steps 1-10 for one mandate. Never raises: any failure is a refusal code (fail closed).

    ``jwks`` is a key set or an async source (called with ``refresh``; one
    refresh for an unknown kid). ``status`` is a status the caller already read;
    otherwise ``status_reader(uri, idx, jwks)`` reads the pinned status list.
    """
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    now_epoch = int(moment.timestamp())
    try:
        key_set = await jwks(False) if callable(jwks) else jwks
    # enterprise-gate: broad-except-ok reason=an-unavailable-jwks-source-is-an-untrusted-key-fail-closed
    except Exception:
        return MandateVerification(valid=False, refusal_code=ISSUER_KEY_UNTRUSTED, message="JWKS unavailable.")
    result = verify_mandate_signed(
        mandate,
        issuer=issuer,
        jwks=key_set,
        now_epoch=now_epoch,
        expected_type=expected_type,
        expected_audience=expected_audience,
    )
    if result.kid_not_found and callable(jwks):
        try:
            key_set = await jwks(True)
        # enterprise-gate: broad-except-ok reason=keep-the-first-refusal-when-the-refresh-fails
        except Exception:
            return result
        result = verify_mandate_signed(
            mandate,
            issuer=issuer,
            jwks=key_set,
            now_epoch=now_epoch,
            expected_type=expected_type,
            expected_audience=expected_audience,
        )
    if not result.valid or result.claims is None:
        return result

    # 10.
    entry = result.claims["status"]["status_list"]
    uri, idx = str(entry["uri"]), int(entry["idx"])

    def refuse(code: str, message: str) -> MandateVerification:
        return MandateVerification(
            valid=False, refusal_code=code, message=message, mandate_type=result.mandate_type, jti=result.jti
        )

    if not (uri.startswith("https://") or (allow_local_endpoints and uri.startswith("http://"))):
        return refuse(REVOCATION_STATUS_UNAVAILABLE, "The mandate does not name an https status list.")
    value: Any
    if status is not None:
        value = status
    elif status_reader is None:
        return refuse(REVOCATION_STATUS_UNAVAILABLE, "No status list reader is configured.")
    else:
        try:
            value = await status_reader(uri, idx, key_set)
        # enterprise-gate: broad-except-ok reason=an-unreadable-status-list-refuses-the-mandate-fail-closed
        except Exception:
            return refuse(REVOCATION_STATUS_UNAVAILABLE, "The status list cannot be read.")
    problem = status_refusal(value)
    if problem is not None:
        return refuse(*problem)
    return MandateVerification(
        valid=True,
        mandate_type=result.mandate_type,
        jti=result.jti,
        kid=result.kid,
        mandate_hash=result.mandate_hash,
        claims=result.claims,
        status="valid",
    )


def check_mandate_chain(
    *,
    intent: Mapping[str, Any] | None = None,
    cart: Mapping[str, Any] | None = None,
    payment: Mapping[str, Any] | None = None,
) -> str | None:
    """Verified mandate claims checked against each other: None when consistent, else ``mandate_chain_mismatch``."""

    def same(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        keys = ("tenant_id", "merchant_id", "sub", "buyer_agent_id", "consent_ref", "iss")
        return all(a.get(k) == b.get(k) for k in keys)

    if intent is not None and cart is not None:
        cap = (intent.get("intent") or {}).get("max_amount") or {}
        total = (cart.get("cart") or {}).get("total") or {}
        if not same(intent, cart) or cart.get("intent_mandate_jti") != intent.get("jti"):
            return MANDATE_CHAIN_MISMATCH
        value = total.get("value")
        if total.get("currency") != cap.get("currency") or not _safe_int(value) or value > cap.get("value", -1):
            return MANDATE_CHAIN_MISMATCH
        if cart.get("merchant_id") not in ((intent.get("intent") or {}).get("merchants") or []):
            return MANDATE_CHAIN_MISMATCH
        if cart.get("exp", 0) > intent.get("exp", 0):
            return MANDATE_CHAIN_MISMATCH
        if intent.get("presence") == "human_present" and cart.get("presence") != "human_present":
            return MANDATE_CHAIN_MISMATCH
    if cart is not None and payment is not None:
        pay = payment.get("payment") or {}
        body = cart.get("cart") or {}
        total = body.get("total") or {}
        if not same(cart, payment) or pay.get("cart_mandate_jti") != cart.get("jti"):
            return MANDATE_CHAIN_MISMATCH
        if (
            pay.get("transaction_id") != body.get("checkout_hash")
            or pay.get("amount") != total.get("value")
            or pay.get("currency") != total.get("currency")
            or payment.get("presence") != cart.get("presence")
            or payment.get("exp", 0) > cart.get("exp", 0)
        ):
            return MANDATE_CHAIN_MISMATCH
    if intent is not None and payment is not None and cart is None:
        return MANDATE_CHAIN_MISMATCH
    return None


@dataclass(frozen=True)
class MandateExpectation:
    """What the server expects of one checkout's mandates (never taken from the request)."""

    tenant_id: str  # the Grantex commerce tenant the merchant is configured under
    merchant_id: str  # the Grantex merchant id (the mandates' aud)
    checkout_hash: str  # computed by the server from the session
    amount_minor_units: int
    currency: str


@dataclass(frozen=True)
class VerifiedMandateChain:
    """The outcome of ``verify_mandate_chain``: three verified, linked, bound mandates, or a refusal."""

    valid: bool
    refusal_code: str | None = None
    failed_type: str | None = None
    jtis: dict[str, str] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    presence: str | None = None
    amr: tuple[str, ...] = ()
    passport_ref: str | None = None
    consent_ref: str | None = None
    buyer_agent_id: str | None = None
    intent_max_amount_minor_units: int | None = None
    intent_currency: str | None = None
    expires_at: datetime | None = None
    verified_at: datetime | None = None

    @property
    def verification_ref(self) -> str | None:
        """The server-side ref the arbiter reads: the payment mandate jti, its hash and the verification time."""
        if not self.valid or self.verified_at is None:
            return None
        return verification_ref(self.jtis["payment"], self.hashes["payment"], self.verified_at)

    def to_evidence(self) -> dict[str, Any]:
        """The chain as decision and ledger evidence (jtis and hashes; no token)."""
        return {
            "valid": self.valid,
            "refusal_code": self.refusal_code,
            "failed_type": self.failed_type,
            "jtis": dict(self.jtis),
            "hashes": dict(self.hashes),
            "presence": self.presence,
            "verified_at": _iso_z(self.verified_at) if self.verified_at else None,
        }


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def verification_ref(jti: str, hash_hex: str, verified_at: datetime) -> str:
    return f"{VERIFICATION_REF_PROFILE};jti={jti};mandate_hash={hash_hex};verified_at={_iso_z(verified_at)}"


def parse_verification_ref(ref: Any) -> dict[str, str] | None:
    """The jti, mandate_hash and verified_at of a ref ``verification_ref`` produced, else None."""
    if not isinstance(ref, str):
        return None
    parts = ref.split(";")
    if len(parts) != 4 or parts[0] != VERIFICATION_REF_PROFILE:
        return None
    out: dict[str, str] = {}
    for part, name in zip(parts[1:], ("jti", "mandate_hash", "verified_at"), strict=True):
        prefix = f"{name}="
        if not part.startswith(prefix) or len(part) == len(prefix):
            return None
        out[name] = part[len(prefix) :]
    if not _full(_HEX64, out["mandate_hash"]) or not _full(_REF, out["jti"]):
        return None
    return out


async def verify_mandate_chain(
    mandates: Mapping[str, str],
    *,
    issuer: str,
    jwks: Mapping[str, Any] | JwksSource,
    now: datetime,
    expected: MandateExpectation,
    status_reader: StatusReader | None = None,
    allow_local_endpoints: bool = False,
) -> VerifiedMandateChain:
    """Verify the intent, cart and payment mandates of one checkout, link them, and bind them to ``expected``."""
    claims: dict[str, dict[str, Any]] = {}
    jtis: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for kind in MANDATE_TYPES:
        result = await verify_oacp_mandate(
            mandates.get(kind),
            issuer=issuer,
            jwks=jwks,
            now=now,
            expected_type=kind,
            expected_audience=expected.merchant_id,
            status_reader=status_reader,
            allow_local_endpoints=allow_local_endpoints,
        )
        if not result.valid or result.claims is None or result.jti is None or result.mandate_hash is None:
            return VerifiedMandateChain(valid=False, refusal_code=result.refusal_code, failed_type=kind)
        claims[kind], jtis[kind], hashes[kind] = result.claims, result.jti, result.mandate_hash
    chain = check_mandate_chain(intent=claims["intent"], cart=claims["cart"], payment=claims["payment"])
    if chain is not None:
        return VerifiedMandateChain(valid=False, refusal_code=chain, jtis=jtis, hashes=hashes)
    payment = claims["payment"]["payment"]
    cart = claims["cart"]["cart"]
    bound = (
        all(c.get("tenant_id") == expected.tenant_id for c in claims.values())
        and cart.get("checkout_hash") == expected.checkout_hash
        and payment.get("amount") == expected.amount_minor_units
        and str(payment.get("currency")) == expected.currency.upper()
    )
    if not bound:
        return VerifiedMandateChain(valid=False, refusal_code=MANDATE_BINDING_MISMATCH, jtis=jtis, hashes=hashes)
    cap = claims["intent"]["intent"]["max_amount"]
    expiry = min(int(c["exp"]) for c in claims.values())
    return VerifiedMandateChain(
        valid=True,
        jtis=jtis,
        hashes=hashes,
        presence=str(claims["payment"]["presence"]),
        amr=tuple(str(m) for m in claims["cart"].get("amr", [])),
        passport_ref=str(claims["payment"]["passport_ref"]),
        consent_ref=str(claims["payment"]["consent_ref"]),
        buyer_agent_id=str(claims["payment"]["buyer_agent_id"]),
        intent_max_amount_minor_units=int(cap["value"]),
        intent_currency=str(cap["currency"]),
        expires_at=datetime.fromtimestamp(expiry, tz=UTC),
        verified_at=now if now.tzinfo is not None else now.replace(tzinfo=UTC),
    )


def trust_sources(verifier: Any, now: datetime) -> tuple[JwksSource, StatusReader, bool]:
    """The pinned JWKS source and status-list reader of an ``OacpGrantexArtifactVerifier``."""
    now_iso = _iso_z(now if now.tzinfo is not None else now.replace(tzinfo=UTC))

    async def jwks(refresh: bool) -> Mapping[str, Any]:
        return await verifier.jwks(force_refresh=refresh)

    async def status(uri: str, idx: int, key_set: Mapping[str, Any]) -> int:
        return int(await verifier.status_at(uri, idx, key_set, now_iso))

    return jwks, status, bool(verifier.config.allow_local_endpoints)
