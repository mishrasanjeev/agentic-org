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
from sqlalchemy.exc import SQLAlchemyError

import core.database as database
from core.crypto.credential_vault import (
    decrypt_credential as _legacy_decrypt,
)
from core.crypto.credential_vault import (
    encrypt_credential as _legacy_encrypt,
)
from core.crypto.envelope import decrypt_from_string, encrypt_to_string
from core.models.tenant import Tenant

logger = structlog.get_logger()

_ENVELOPE_PREFIX = "env1:"


async def _resolve_kek(tenant_id: uuid.UUID) -> str:
    """Pick the right KEK resource for this tenant.

    Order:
      1. ``tenants.byok_kek_resource`` if set (customer-managed)
      2. ``AGENTICORG_PLATFORM_KEK`` env var (platform-managed) — only when
         the tenant row exists and has no BYOK key
      3. Empty string → caller falls back to legacy Fernet

    Fails closed: a database error or a missing tenant row raises instead
    of silently encrypting a BYOK tenant's secret under the platform KEK.
    """
    try:
        async with database.async_session_factory() as session:
            result = await session.execute(
                select(Tenant.byok_kek_resource).where(Tenant.id == tenant_id)
            )
            row = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.error("tenant_kek_lookup_failed", tenant_id=str(tenant_id))
        raise RuntimeError(
            "tenant KEK lookup failed; refusing to fall back to the platform KEK"
        ) from exc
    if row is None:
        raise LookupError(f"tenant {tenant_id} not found; cannot resolve its KEK")
    if row:
        return row
    return os.getenv("AGENTICORG_PLATFORM_KEK", "")


async def resolve_tenant_kek(tenant_id: uuid.UUID) -> str:
    """The KEK resource ``encrypt_for_tenant`` would use ("" means legacy Fernet).

    For callers that must not open a second database session while holding
    one, such as a row lock: resolve first, then ``encrypt_with_kek``.
    """
    return await _resolve_kek(tenant_id)


def encrypt_with_kek(plaintext: str, kek: str) -> str:
    """Encrypt with a KEK from ``resolve_tenant_kek``. Synchronous (KMS is gRPC); call via ``asyncio.to_thread``."""
    if kek:
        return _ENVELOPE_PREFIX + encrypt_to_string(plaintext.encode(), kek)
    # Fallback — legacy Fernet
    return _legacy_encrypt(plaintext)


async def encrypt_for_tenant(plaintext: str, tenant_id: uuid.UUID) -> str:
    """Return a ciphertext string for storage.

    Uses envelope encryption with the tenant's BYOK KEK if available;
    otherwise falls back to legacy Fernet so this is safe to drop into
    existing call sites.
    """
    return encrypt_with_kek(plaintext, await _resolve_kek(tenant_id))


def decrypt_for_tenant(ciphertext: str) -> str:
    """Reverse of ``encrypt_for_tenant``. Auto-detects format.

    Synchronous because Fernet is synchronous and we don't want to make
    every read site async.
    """
    if ciphertext.startswith(_ENVELOPE_PREFIX):
        payload = ciphertext[len(_ENVELOPE_PREFIX):]
        return decrypt_from_string(payload).decode()
    return _legacy_decrypt(ciphertext)
