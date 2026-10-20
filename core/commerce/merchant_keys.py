# SPDX-License-Identifier: Apache-2.0
"""Per-merchant ES256 signing keys for UCP (profile ``keys[]``, RFC 9421 signatures, detached JWS).

Each merchant commerce config holds its signing keys in the server-managed UCP
state (``public_publishing["ucp"]["signing_keys"]``, see
:mod:`core.commerce.ucp.state`). A key record keeps the public JWK in clear and
the PKCS#8 private key encrypted for the tenant (the ``encrypt_for_tenant``
envelope: the tenant's BYOK KEK, else the platform KEK, else the legacy Fernet
vault). The private key is decrypted only to sign, inside this module.

* ``kid`` is the key's RFC 7638 JWK SHA-256 thumbprint, so the same key also
  serves Web Bot Auth lookups (UCP overview, Business Profile).
* A key is generated when a merchant is onboarded with
  ``AGENTICORG_UCP_PROFILE_ENABLED`` on, or by the explicit operator action for
  existing merchants; never on a read.
* Rotation (UCP signatures, Key Rotation): the new key becomes active and signs
  from then on; the previous one is ``retired`` and stays in the published key
  set for the grace window (``AGENTICORG_UCP_KEY_ROTATION_GRACE_DAYS``, at
  least 7 days) so signatures made before the rotation keep verifying, then it
  is dropped at the next rotation or provisioning.
* Signatures are ECDSA P-256 / SHA-256 in raw ``r||s`` form (64 bytes), the
  encoding RFC 9421 §3.3.4 and JWS (RFC 7518 §3.4) both use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from core.commerce.oacp_jws import b64url_encode

logger = structlog.get_logger()

MERCHANT_KEY_ALG = "ES256"
MERCHANT_KEY_CRV = "P-256"
KEY_STATUS_ACTIVE = "active"
KEY_STATUS_RETIRED = "retired"
MIN_ROTATION_GRACE_DAYS = 7


class MerchantKeyError(RuntimeError):
    """A merchant signing key is missing, malformed or could not be decrypted."""


@dataclass(frozen=True)
class MerchantSigningKey:
    """A decrypted merchant signing key. Never logged, never serialised."""

    kid: str
    _private_key: ec.EllipticCurvePrivateKey

    def __repr__(self) -> str:
        return f"MerchantSigningKey(kid={self.kid!r})"

    def sign(self, signing_input: bytes) -> bytes:
        """ES256 over ``signing_input``: raw ``r||s`` (64 bytes)."""
        der = self._private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def public_jwk_for(private_key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    """The public JWK (``kid`` = RFC 7638 thumbprint, ``use: sig``, ``alg: ES256``)."""
    numbers = private_key.public_key().public_numbers()
    x = b64url_encode(numbers.x.to_bytes(32, "big"))
    y = b64url_encode(numbers.y.to_bytes(32, "big"))
    return {
        "kty": "EC",
        "crv": MERCHANT_KEY_CRV,
        "x": x,
        "y": y,
        "kid": jwk_thumbprint({"kty": "EC", "crv": MERCHANT_KEY_CRV, "x": x, "y": y}),
        "use": "sig",
        "alg": MERCHANT_KEY_ALG,
    }


def jwk_thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638 SHA-256 thumbprint of an EC public JWK (required members in lexicographic order)."""
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    return b64url_encode(hashlib.sha256(canonical.encode("utf-8")).digest())


async def _encrypt_private_key(pem: str, tenant_id: str) -> str:
    # encrypt_for_tenant, split so the KMS call (synchronous gRPC) runs off the event loop.
    from core.crypto.tenant_secrets import encrypt_with_kek, resolve_tenant_kek

    kek = await resolve_tenant_kek(uuid.UUID(tenant_id))
    return await asyncio.to_thread(encrypt_with_kek, pem, kek)


async def _decrypt_private_key(ciphertext: str) -> str:
    from core.crypto.tenant_secrets import decrypt_for_tenant

    return await asyncio.to_thread(decrypt_for_tenant, ciphertext)


async def new_merchant_key_record(tenant_id: str, *, now: datetime) -> dict[str, Any]:
    """Generate a P-256 key pair and return its storable record (private key encrypted for the tenant)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_jwk = public_jwk_for(private_key)
    ciphertext = await _encrypt_private_key(pem, tenant_id)
    return {
        "kid": public_jwk["kid"],
        "alg": MERCHANT_KEY_ALG,
        "public_jwk": public_jwk,
        "private_key_ciphertext": ciphertext,
        "status": KEY_STATUS_ACTIVE,
        "created_at": _iso(now),
    }


def _grace(grace_days: int | None) -> timedelta:
    if grace_days is None:
        from core.config import settings

        grace_days = int(getattr(settings, "ucp_key_rotation_grace_days", MIN_ROTATION_GRACE_DAYS))
    return timedelta(days=max(MIN_ROTATION_GRACE_DAYS, int(grace_days)))


def _published(record: Mapping[str, Any], now: datetime) -> bool:
    status = record.get("status")
    if status == KEY_STATUS_ACTIVE:
        return True
    if status == KEY_STATUS_RETIRED:
        retire_until = _parse_iso(record.get("retire_until"))
        return retire_until is not None and now < retire_until
    return False


def active_key_record(key_records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    active = [record for record in key_records if record.get("status") == KEY_STATUS_ACTIVE]
    if len(active) > 1:
        logger.warning("ucp_merchant_key_multiple_active", count=len(active))
    return active[-1] if active else None


def merchant_public_jwks(key_records: Sequence[Mapping[str, Any]], *, now: datetime) -> list[dict[str, str]]:
    """Public JWKs to publish: the active key and retired keys still inside their grace window.

    Only the public members are returned (``kty crv x y kid use alg``); a record
    whose stored JWK carries any private member is skipped.
    """
    jwks: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in key_records:
        if not _published(record, now):
            continue
        jwk = record.get("public_jwk")
        if not isinstance(jwk, Mapping) or any(member in jwk for member in ("d", "p", "q", "dp", "dq", "qi", "k")):
            continue
        public = {member: str(jwk[member]) for member in ("kty", "crv", "x", "y", "kid", "use", "alg") if member in jwk}
        if public.get("kid") in seen or public.get("kid") != record.get("kid"):
            continue
        seen.add(public["kid"])
        jwks.append(public)
    # The active key first, then retired keys in rotation order.
    active_kid = (active_key_record(key_records) or {}).get("kid")
    jwks.sort(key=lambda item: 0 if item["kid"] == active_kid else 1)
    return jwks


def add_active_key(
    key_records: Sequence[Mapping[str, Any]],
    new_record: Mapping[str, Any],
    *,
    now: datetime,
    grace_days: int | None = None,
) -> list[dict[str, Any]]:
    """Rotate in ``new_record``: previous active keys retire with a grace window; expired retirees are dropped."""
    retire_until = _iso(now + _grace(grace_days))
    updated: list[dict[str, Any]] = []
    for record in key_records:
        item = dict(record)
        if item.get("status") == KEY_STATUS_ACTIVE:
            item["status"] = KEY_STATUS_RETIRED
            item["retired_at"] = _iso(now)
            item["retire_until"] = retire_until
        if item.get("status") == KEY_STATUS_RETIRED and not _published(item, now):
            continue
        updated.append(item)
    updated.append(dict(new_record))
    return updated


def public_key_records_view(key_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Key records without the encrypted private key (operator status responses)."""
    return [{key: value for key, value in record.items() if key != "private_key_ciphertext"} for record in key_records]


async def signing_key_from_record(record: Mapping[str, Any]) -> MerchantSigningKey:
    ciphertext = record.get("private_key_ciphertext")
    kid = record.get("kid")
    if not isinstance(ciphertext, str) or not isinstance(kid, str):
        raise MerchantKeyError("merchant signing key record is incomplete")
    try:
        pem = await _decrypt_private_key(ciphertext)
        private_key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    # enterprise-gate: broad-except-ok reason=decrypt failure fails closed as MerchantKeyError
    except Exception as exc:
        logger.error("ucp_merchant_key_decrypt_failed", kid=kid)
        raise MerchantKeyError("merchant signing key could not be decrypted") from exc
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or private_key.curve.name != "secp256r1":
        raise MerchantKeyError("merchant signing key is not P-256")
    if public_jwk_for(private_key)["kid"] != kid:
        raise MerchantKeyError("merchant signing key does not match its kid")
    return MerchantSigningKey(kid=kid, _private_key=private_key)


async def load_active_signing_key(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
) -> MerchantSigningKey:
    """The merchant's active signing key, decrypted (a key provider for webhook and response signing).

    Raises :class:`MerchantKeyError` when the merchant has no active key.
    """
    from core.commerce.ucp.state import ucp_state
    from core.commerce.ucp.store import load_merchant_config_row

    row = await load_merchant_config_row(tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id)
    if row is None:
        raise MerchantKeyError("merchant commerce config not found")
    record = active_key_record(ucp_state(row.public_publishing).get("signing_keys") or [])
    if record is None:
        raise MerchantKeyError("merchant has no active UCP signing key")
    return await signing_key_from_record(record)


async def sign_with_merchant_key(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    signing_input: bytes,
) -> tuple[str, bytes]:
    """Sign ``signing_input`` with the merchant's active key; returns ``(kid, raw r||s)``.

    ``signing_input`` is the RFC 9421 signature base or the JWS signing input
    (``b64url(header) + "." + b64url(payload)``); for a detached JWS the caller
    assembles ``<header>..<b64url(signature)>``.
    """
    key = await load_active_signing_key(tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id)
    return key.kid, key.sign(signing_input)
