"""Encryption utilities for credential vault.

Uses Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256).
The encryption key is loaded from the AGENTICORG_VAULT_KEY environment
variable or falls back to the main secret key (dev only).

In production, use GCP Secret Manager to store the vault key and
rotate it periodically.  The encryption_key_ref column on
gstn_credentials lets us track which key version was used.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_vault_key() -> bytes:
    """Derive a Fernet-compatible key from the vault secret.

    Fernet requires a 32-byte base64-encoded key.  We derive it from
    the raw secret using SHA-256 to ensure correct length.
    """
    raw = os.environ.get(
        "AGENTICORG_VAULT_KEY",
        os.environ.get("AGENTICORG_SECRET_KEY", "dev-only-vault-key"),
    )
    # SHA-256 gives 32 bytes → base64-encode for Fernet
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a credential string.  Returns base64-encoded ciphertext."""
    f = Fernet(_get_vault_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    """Decrypt a credential string.  Returns plaintext."""
    f = Fernet(_get_vault_key())
    return f.decrypt(ciphertext.encode()).decode()


def verify_credential(ciphertext: str) -> bool:
    """Check if a ciphertext can be decrypted (key is valid)."""
    try:
        decrypt_credential(ciphertext)
        return True
    except Exception:
        return False
