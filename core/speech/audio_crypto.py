# SPDX-License-Identifier: Apache-2.0
"""Tenant-bound encrypted audio stored as UTF-8 vault/envelope bytes."""

from __future__ import annotations

import asyncio
import base64
import json
from uuid import UUID

from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_with_kek, resolve_tenant_kek
from core.speech.audio import SpeechError


def _audio_json(tenant_id: UUID, data: bytes) -> str:
    return json.dumps({"tenant_id": str(tenant_id), "data": base64.b64encode(data).decode("ascii")})


async def encrypt_audio(tenant_id: UUID, data: bytes) -> bytes:
    """Encrypt audio before persistence; key lookup failures never store raw audio."""
    try:
        if not isinstance(tenant_id, UUID) or not isinstance(data, bytes):
            raise ValueError("Invalid audio encryption input")
        kek = await resolve_tenant_kek(tenant_id)
        plaintext = await asyncio.to_thread(_audio_json, tenant_id, data)
        ciphertext = await asyncio.to_thread(encrypt_with_kek, plaintext, kek)
        return await asyncio.to_thread(str.encode, ciphertext, "utf-8")
    # enterprise-gate: broad-except-ok reason=audio-encryption-failure-refuses-storage-without-raw-fallback
    except Exception:
        raise SpeechError(500, "audio_encryption_failed", "The recording could not be encrypted") from None


def _decrypt_audio(tenant_id: UUID, content: bytes) -> bytes:
    if not isinstance(tenant_id, UUID) or not isinstance(content, bytes):
        raise ValueError("Invalid audio decryption input")
    payload = json.loads(decrypt_for_tenant(content.decode("utf-8")))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"tenant_id", "data"}
        or payload["tenant_id"] != str(tenant_id)
        or not isinstance(payload["data"], str)
    ):
        raise ValueError("Invalid tenant-bound audio payload")
    return base64.b64decode(payload["data"], validate=True)


async def decrypt_audio(tenant_id: UUID, content: bytes) -> bytes:
    """Decrypt, validate tenant binding, and decode audio off the event loop.

    Legacy raw audio, malformed ciphertext, and another tenant's payload are
    refused with the same safe error. There is no plaintext compatibility path.
    """
    try:
        return await asyncio.to_thread(_decrypt_audio, tenant_id, content)
    # enterprise-gate: broad-except-ok reason=unreadable-or-cross-tenant-audio-refused-without-raw-fallback
    except Exception:
        raise SpeechError(500, "audio_decryption_failed", "The recording could not be decrypted") from None
