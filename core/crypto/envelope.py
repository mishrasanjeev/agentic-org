"""Envelope encryption with optional customer-managed keys (BYOK / CMEK).

Envelope encryption pattern:
  - Each plaintext is encrypted with a per-message Data Encryption Key (DEK).
  - The DEK itself is wrapped (encrypted) by a Key Encryption Key (KEK).
  - We store the encrypted payload + wrapped DEK together.

Why it matters:
  - We can rotate KEKs without re-encrypting every payload (just re-wrap
    the DEKs).
  - Customers who want BYOK can point us at their own KMS key; their
    wrapped DEKs can only be unwrapped with their key, so we can't
    read their data out-of-band.

Default behavior:
  - Platform-managed KEK via Google Cloud KMS at
    ``projects/.../locations/.../keyRings/agenticorg/cryptoKeys/dek-kek``.
  - Override per tenant by setting ``tenants.byok_kek_resource`` to a
    customer-owned KMS resource name.

Plaintext format:
  {
    "version": 1,
    "kek": "<kms-resource-name>",
    "wrapped_dek": "<base64>",
    "nonce": "<base64>",
    "ciphertext": "<base64>"
  }
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Platform default KEK — settable via environment.
_DEFAULT_KEK_RESOURCE = os.getenv("AGENTICORG_PLATFORM_KEK", "")

# Import the KMS client lazily so tests can run without it installed.
_kms_client = None


def _get_kms():
    global _kms_client
    if _kms_client is not None:
        return _kms_client
    try:
        from google.cloud import kms
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-kms is required for BYOK/CMEK. "
            "Run: pip install google-cloud-kms"
        ) from exc
    _kms_client = kms.KeyManagementServiceClient()
    return _kms_client


@dataclass
class EncryptedPayload:
    """Tagged envelope — serialize with ``to_json()``."""

    kek: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes
    version: int = 1

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "kek": self.kek,
                "wrapped_dek": base64.b64encode(self.wrapped_dek).decode(),
                "nonce": base64.b64encode(self.nonce).decode(),
                "ciphertext": base64.b64encode(self.ciphertext).decode(),
            }
        )

    @classmethod
    def from_json(cls, payload: str) -> EncryptedPayload:
        d = json.loads(payload)
        return cls(
            version=int(d["version"]),
            kek=d["kek"],
            wrapped_dek=base64.b64decode(d["wrapped_dek"]),
            nonce=base64.b64decode(d["nonce"]),
            ciphertext=base64.b64decode(d["ciphertext"]),
        )


def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, plaintext, None)
    return nonce, ct


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext, None)


def _wrap_dek(kek_resource: str, dek: bytes) -> bytes:
    """Wrap a DEK with the KEK via Cloud KMS."""
    client = _get_kms()
    response = client.encrypt(request={"name": kek_resource, "plaintext": dek})
    return response.ciphertext


def _unwrap_dek(kek_resource: str, wrapped: bytes) -> bytes:
    client = _get_kms()
    response = client.decrypt(request={"name": kek_resource, "ciphertext": wrapped})
    return response.plaintext


def encrypt(
    plaintext: bytes,
    kek_resource: str | None = None,
) -> EncryptedPayload:
    """Encrypt bytes with envelope encryption.

    ``kek_resource`` overrides the platform default — pass a customer's
    KMS resource name here to get BYOK behaviour for that payload.
    """
    # Read the env var at call time (not import time) so tests can
    # set/unset it dynamically.
    kek = kek_resource or _DEFAULT_KEK_RESOURCE or os.getenv("AGENTICORG_PLATFORM_KEK", "")
    if not kek:
        raise RuntimeError(
            "No KEK configured. Set AGENTICORG_PLATFORM_KEK or pass kek_resource."
        )

    dek = secrets.token_bytes(32)  # 256-bit AES-GCM DEK
    nonce, ciphertext = _aes_gcm_encrypt(dek, plaintext)
    wrapped = _wrap_dek(kek, dek)

    return EncryptedPayload(
        kek=kek,
        wrapped_dek=wrapped,
        nonce=nonce,
        ciphertext=ciphertext,
    )


def decrypt(payload: EncryptedPayload) -> bytes:
    """Inverse of ``encrypt`` — unwraps the DEK via the stamped KEK."""
    dek = _unwrap_dek(payload.kek, payload.wrapped_dek)
    try:
        return _aes_gcm_decrypt(dek, payload.nonce, payload.ciphertext)
    finally:
        # Best effort — scrub the DEK from memory.
        del dek


def encrypt_to_string(plaintext: bytes, kek_resource: str | None = None) -> str:
    """Convenience — return the JSON-encoded envelope as a string."""
    return encrypt(plaintext, kek_resource).to_json()


def decrypt_from_string(payload: str) -> bytes:
    return decrypt(EncryptedPayload.from_json(payload))
