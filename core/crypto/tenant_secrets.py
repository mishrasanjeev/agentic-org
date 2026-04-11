"""Tenant-aware secret encryption — wraps envelope + legacy Fernet.

This is the runtime helper that real callers should use. It chooses
between three storage formats:

  1. **Envelope (BYOK)** — when ``tenants.byok_kek_resource`` is set,
     we use the customer's KMS key. Stored as a JSON blob with prefix
     ``"env1:"`` so we can detect it on decrypt.
  2. **Envelope (platform)** — when ``AGENTICORG_PLATFORM_KEK`` is set
     but the tenant has no BYOK key. Same JSON shape, also ``env1:``.
  3. **Legacy Fernet** — when no KMS is configured. Same payload format
     ``encrypt_credential`` produces today, no prefix.

Decryption auto-detects the format by inspecting the prefix, so existing
Fernet rows in the database keep working unchanged.
"""

from __future__ import annotations

import os
import uuid

import structlog
from sqlalchemy import select

from core.crypto.credential_vault import (
    decrypt_credential as _legacy_decrypt,
)
from core.crypto.credential_vault import (
    encrypt_credential as _legacy_encrypt,
)
from core.crypto.envelope import decrypt_from_string, encrypt_to_string
from core.database import async_session_factory
from core.models.tenant import Tenant

logger = structlog.get_logger()

_ENVELOPE_PREFIX = "env1:"


async def _resolve_kek(tenant_id: uuid.UUID) -> str:
    """Pick the right KEK resource for this tenant.

    Order:
      1. ``tenants.byok_kek_resource`` if set (customer-managed)
      2. ``AGENTICORG_PLATFORM_KEK`` env var (platform-managed)
      3. Empty string → caller falls back to legacy Fernet
    """
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Tenant.byok_kek_resource).where(Tenant.id == tenant_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return row
    except Exception:
        logger.debug("tenant_kek_lookup_failed", tenant_id=str(tenant_id))
    return os.getenv("AGENTICORG_PLATFORM_KEK", "")


async def encrypt_for_tenant(plaintext: str, tenant_id: uuid.UUID) -> str:
    """Return a ciphertext string for storage.

    Uses envelope encryption with the tenant's BYOK KEK if available;
    otherwise falls back to legacy Fernet so this is safe to drop into
    existing call sites.
    """
    kek = await _resolve_kek(tenant_id)
    if kek:
        try:
            return _ENVELOPE_PREFIX + encrypt_to_string(plaintext.encode(), kek)
        except Exception:
            logger.exception("envelope_encrypt_failed_falling_back", tenant_id=str(tenant_id))
    # Fallback — legacy Fernet
    return _legacy_encrypt(plaintext)


def decrypt_for_tenant(ciphertext: str) -> str:
    """Reverse of ``encrypt_for_tenant``. Auto-detects format.

    Synchronous because Fernet is synchronous and we don't want to make
    every read site async.
    """
    if ciphertext.startswith(_ENVELOPE_PREFIX):
        payload = ciphertext[len(_ENVELOPE_PREFIX):]
        return decrypt_from_string(payload).decode()
    return _legacy_decrypt(ciphertext)
