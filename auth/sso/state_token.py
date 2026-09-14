"""Stateless OIDC login-flow state (bug sheet 2026-09-14 row 7).

Until 2026-09-14 the SSO flow kept ``{tenant_id, nonce, verifier, return_to}``
in Redis under ``sso:state:*``, so a Redis outage turned every SSO login into
HTTP 503. The flow state now travels with the browser in two pieces:

* ``state`` query parameter (sent to the IdP and echoed back): an
  HMAC-SHA256-signed token carrying the tenant, provider key, nonce,
  return path, issue/expiry times and a random ``jti``. It holds nothing
  secret — the nonce is echoed inside the id_token anyway.
* ``agenticorg_sso_flow`` cookie: HttpOnly, path-scoped, AES-256-GCM
  encrypted ``{"jti", "verifier"}``. The PKCE verifier never appears in a
  URL, and the callback only succeeds in the browser that started the flow
  (cookie ``jti`` must equal state ``jti``) — login-CSRF protection.

Both keys are derived from ``settings.secret_key`` with distinct
domain-separation labels, so the state MAC key, the cookie encryption key
and the JWT signing key are never the same bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.responses import Response

from core.config import is_strict_runtime_env, settings

STATE_VERSION = 1
FLOW_TTL_SECONDS = 600
CLOCK_SKEW_SECONDS = 60
FLOW_COOKIE_NAME = "agenticorg_sso_flow"
FLOW_COOKIE_PATH = "/api/v1/auth/sso"
_MAX_STATE_LENGTH = 4096
_MAX_COOKIE_LENGTH = 1024

_STATE_MAC_LABEL = b"agenticorg-sso-state-v1"
_FLOW_COOKIE_KEY_LABEL = b"agenticorg-sso-flow-cookie-v1"
_FLOW_COOKIE_AAD = b"agenticorg-sso-flow-v1"


class SSOStateError(Exception):
    """State token or flow cookie rejected. ``reason`` is a log-safe slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SSOStateClaims:
    tenant_id: uuid.UUID
    provider_key: str
    nonce: str
    return_to: str
    iat: int
    exp: int
    jti: str


def _derive_key(label: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode(), label, hashlib.sha256).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _mac(body: str) -> bytes:
    return hmac.new(_derive_key(_STATE_MAC_LABEL), body.encode("ascii"), hashlib.sha256).digest()


def issue_state_token(
    *,
    tenant_id: uuid.UUID,
    provider_key: str,
    nonce: str,
    return_to: str,
    now: int | None = None,
) -> tuple[str, SSOStateClaims]:
    """Return ``(signed_state, claims)`` for a new login flow."""
    iat = int(time.time()) if now is None else int(now)
    claims = SSOStateClaims(
        tenant_id=tenant_id,
        provider_key=provider_key,
        nonce=nonce,
        return_to=return_to,
        iat=iat,
        exp=iat + FLOW_TTL_SECONDS,
        jti=_b64e(secrets.token_bytes(16)),
    )
    payload = {
        "v": STATE_VERSION,
        "tid": str(claims.tenant_id),
        "pk": claims.provider_key,
        "n": claims.nonce,
        "rt": claims.return_to,
        "iat": claims.iat,
        "exp": claims.exp,
        "jti": claims.jti,
    }
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_b64e(_mac(body))}", claims


def verify_state_token(token: str, *, provider_key: str, now: int | None = None) -> SSOStateClaims:
    """Verify signature, version, lifetime and provider binding; fail closed."""
    if not isinstance(token, str) or not token or len(token) > _MAX_STATE_LENGTH:
        raise SSOStateError("state_malformed")
    body, sep, sig = token.partition(".")
    if not sep or not body or not sig:
        raise SSOStateError("state_malformed")
    try:
        supplied = _b64d(sig)
        body.encode("ascii")
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise SSOStateError("state_malformed") from exc
    if not hmac.compare_digest(supplied, _mac(body)):
        raise SSOStateError("state_bad_signature")
    # Only authenticated bytes are parsed below.
    try:
        payload = json.loads(_b64d(body))
    except (binascii.Error, ValueError) as exc:
        raise SSOStateError("state_malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != STATE_VERSION:
        raise SSOStateError("state_bad_version")
    try:
        iat = payload["iat"]
        exp = payload["exp"]
        if type(iat) is not int or type(exp) is not int:
            raise TypeError("iat/exp must be integers")
        claims = SSOStateClaims(
            tenant_id=uuid.UUID(str(payload["tid"])),
            provider_key=str(payload["pk"]),
            nonce=str(payload["n"]),
            return_to=str(payload["rt"]),
            iat=iat,
            exp=exp,
            jti=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SSOStateError("state_malformed") from exc
    current = int(time.time()) if now is None else int(now)
    if claims.exp <= current:
        raise SSOStateError("state_expired")
    if claims.iat > current + CLOCK_SKEW_SECONDS or claims.exp - claims.iat > FLOW_TTL_SECONDS:
        raise SSOStateError("state_bad_lifetime")
    if not hmac.compare_digest(claims.provider_key.encode(), provider_key.encode()):
        raise SSOStateError("state_provider_mismatch")
    if not claims.jti or not claims.nonce:
        raise SSOStateError("state_malformed")
    return claims


def encrypt_flow_cookie(*, jti: str, verifier: str) -> str:
    """AES-256-GCM seal ``{"jti", "verifier"}`` for the browser-bound cookie."""
    nonce = os.urandom(12)
    plaintext = json.dumps({"jti": jti, "verifier": verifier}, separators=(",", ":")).encode()
    sealed = AESGCM(_derive_key(_FLOW_COOKIE_KEY_LABEL)).encrypt(nonce, plaintext, _FLOW_COOKIE_AAD)
    return _b64e(nonce + sealed)


def decrypt_flow_cookie(value: str | None, *, expected_jti: str) -> str:
    """Return the PKCE verifier when the cookie is authentic and bound to ``expected_jti``."""
    if not value:
        raise SSOStateError("flow_cookie_missing")
    if len(value) > _MAX_COOKIE_LENGTH:
        raise SSOStateError("flow_cookie_malformed")
    try:
        raw = _b64d(value)
    except (binascii.Error, ValueError) as exc:
        raise SSOStateError("flow_cookie_malformed") from exc
    if len(raw) < 12 + 16:
        raise SSOStateError("flow_cookie_malformed")
    try:
        plaintext = AESGCM(_derive_key(_FLOW_COOKIE_KEY_LABEL)).decrypt(raw[:12], raw[12:], _FLOW_COOKIE_AAD)
    except InvalidTag as exc:
        raise SSOStateError("flow_cookie_bad_tag") from exc
    try:
        payload = json.loads(plaintext)
        jti = payload["jti"]
        verifier = payload["verifier"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SSOStateError("flow_cookie_malformed") from exc
    if not isinstance(jti, str) or not isinstance(verifier, str) or not verifier:
        raise SSOStateError("flow_cookie_malformed")
    if not hmac.compare_digest(jti.encode(), expected_jti.encode()):
        raise SSOStateError("flow_cookie_jti_mismatch")
    return verifier


def _cookie_secure() -> bool:
    # Same rule as ``api.v1.auth._set_session_cookie``.
    return is_strict_runtime_env(os.getenv("AGENTICORG_ENV", settings.env))


def set_flow_cookie(response: Response, *, jti: str, verifier: str) -> None:
    response.set_cookie(
        key=FLOW_COOKIE_NAME,
        value=encrypt_flow_cookie(jti=jti, verifier=verifier),
        max_age=FLOW_TTL_SECONDS,
        path=FLOW_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        # The IdP redirect back to the callback is a top-level GET, which
        # SameSite=Lax still sends.
        samesite="lax",
    )


def clear_flow_cookie(response: Response) -> None:
    response.delete_cookie(
        key=FLOW_COOKIE_NAME,
        path=FLOW_COOKIE_PATH,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )


def clear_flow_cookie_header() -> str:
    """``Set-Cookie`` value that expires the flow cookie (for error responses)."""
    carrier = Response()
    clear_flow_cookie(carrier)
    return carrier.headers["set-cookie"]
