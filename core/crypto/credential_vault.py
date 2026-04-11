"""Credential vault — Fernet symmetric encryption for stored secrets.

Used for things like GSTN portal passwords, OAuth refresh tokens, and
other per-tenant credentials where we don't need the full envelope-
encryption flow. The key is derived from ``AGENTICORG_VAULT_KEY`` (or
the main ``AGENTICORG_SECRET_KEY`` in dev).

For large blobs and customer BYOK see ``core.crypto.envelope``.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_vault_key() -> bytes:
    """Derive a Fernet-compatible key from the vault secret.

    Fernet requires a 32-byte base64-encoded key. We derive it from
    the raw secret using SHA-256 to ensure correct length.
    """
    raw = os.environ.get(
        "AGENTICORG_VAULT_KEY",
        os.environ.get("AGENTICORG_SECRET_KEY", "dev-only-vault-key"),
    )
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a credential string. Returns base64-encoded ciphertext."""
    f = Fernet(_get_vault_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    """Decrypt a credential string. Returns plaintext."""
    f = Fernet(_get_vault_key())
    return f.decrypt(ciphertext.encode()).decode()


def verify_credential(ciphertext: str) -> bool:
    """Check if a ciphertext can be decrypted (key is valid)."""
    try:
        decrypt_credential(ciphertext)
        return True
    except Exception:
        return False
