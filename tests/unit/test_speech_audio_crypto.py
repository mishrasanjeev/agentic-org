# SPDX-License-Identifier: Apache-2.0
"""Real audio encryption, tenant binding, and fail-closed asynchronous boundaries."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from core.crypto import envelope, tenant_secrets
from core.speech import audio_crypto
from core.speech.audio import SpeechError

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
AUDIO = b"RIFF\x00\x01\xffWAVEsynthetic-private-audio" * 64
KEK = "projects/test/locations/global/keyRings/audio/cryptoKeys/tenant"


@pytest.fixture(autouse=True)
def local_vault(monkeypatch):
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "audio:synthetic-audio-test-key")
    resolver = AsyncMock(return_value="")
    monkeypatch.setattr(audio_crypto, "resolve_tenant_kek", resolver)
    return resolver


@pytest.mark.asyncio
@pytest.mark.parametrize("data", (b"", AUDIO, bytes(range(256)) * 1024))
async def test_real_vault_roundtrip_stores_ciphertext_bytes_not_audio(data, local_vault):
    stored = await audio_crypto.encrypt_audio(TENANT, data)
    assert isinstance(stored, bytes)
    assert stored.startswith(b"agko_vaudio$")
    assert str(TENANT).encode() not in stored
    if data:
        assert data not in stored
        assert base64.b64encode(data) not in stored
    assert json.loads(tenant_secrets.decrypt_for_tenant(stored.decode())) == {
        "tenant_id": str(TENANT),
        "data": base64.b64encode(data).decode("ascii"),
    }
    assert await audio_crypto.decrypt_audio(TENANT, stored) == data
    assert await audio_crypto.encrypt_audio(TENANT, data) != stored
    local_vault.assert_awaited_with(TENANT)


@pytest.mark.asyncio
async def test_real_envelope_roundtrip_and_tenant_binding(monkeypatch, local_vault):
    # Only the remote KMS wrapping boundary is replaced; AES-GCM and JSON are real.
    wrapping_key = Fernet(Fernet.generate_key())

    def wrap(kek, dek):
        assert kek == KEK
        return wrapping_key.encrypt(dek)

    def unwrap(kek, wrapped):
        assert kek == KEK
        return wrapping_key.decrypt(wrapped)

    monkeypatch.setattr(envelope, "_wrap_dek", wrap)
    monkeypatch.setattr(envelope, "_unwrap_dek", unwrap)
    local_vault.return_value = KEK
    stored = await audio_crypto.encrypt_audio(TENANT, AUDIO)
    assert stored.startswith(b"env1:")
    assert AUDIO not in stored
    assert base64.b64encode(AUDIO) not in stored
    assert await audio_crypto.decrypt_audio(TENANT, stored) == AUDIO
    with pytest.raises(SpeechError, match="could not be decrypted") as error:
        await audio_crypto.decrypt_audio(OTHER_TENANT, stored)
    assert error.value.code == "audio_decryption_failed"


@pytest.mark.asyncio
async def test_shared_vault_key_does_not_allow_cross_tenant_audio():
    stored = await audio_crypto.encrypt_audio(TENANT, AUDIO)
    with pytest.raises(SpeechError) as error:
        await audio_crypto.decrypt_audio(OTHER_TENANT, stored)
    assert error.value.status == 500
    assert error.value.code == "audio_decryption_failed"
    assert str(TENANT) not in str(error.value)
    assert str(OTHER_TENANT) not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    (
        AUDIO,
        b'{"tenant_id":"11111111-1111-1111-1111-111111111111","data":"YWJj"}',
        b"\xff\xfe\x00",
        b"env1:not-json",
        b"agko_vaudio$invalid-token",
        b"",
    ),
)
async def test_raw_legacy_plaintext_and_corrupt_content_are_never_returned(content):
    with pytest.raises(SpeechError) as error:
        await audio_crypto.decrypt_audio(TENANT, content)
    assert error.value.code == "audio_decryption_failed"
    assert error.value.message == "The recording could not be decrypted"


@pytest.mark.asyncio
async def test_tampered_real_ciphertext_is_refused():
    stored = await audio_crypto.encrypt_audio(TENANT, AUDIO)
    tampered = stored[:-8] + (b"A" if stored[-8:-7] != b"A" else b"B") + stored[-7:]
    with pytest.raises(SpeechError):
        await audio_crypto.decrypt_audio(TENANT, tampered)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        [],
        {"data": "YWJj"},
        {"tenant_id": str(TENANT)},
        {"tenant_id": None, "data": "YWJj"},
        {"tenant_id": str(TENANT), "data": 123},
        {"tenant_id": str(TENANT), "data": "%%%"},
        {"tenant_id": str(TENANT), "data": "YW Jj"},
        {"tenant_id": str(TENANT), "data": "\u2603"},
        {"tenant_id": str(TENANT), "data": "YWJj", "unexpected": True},
    ),
)
async def test_authenticated_but_invalid_audio_payload_is_refused(payload):
    stored = tenant_secrets.encrypt_with_kek(json.dumps(payload), "").encode()
    with pytest.raises(SpeechError):
        await audio_crypto.decrypt_audio(TENANT, stored)


@pytest.mark.asyncio
async def test_key_lookup_failure_refuses_storage_without_encryption(monkeypatch, local_vault):
    local_vault.side_effect = RuntimeError("sensitive key lookup details")
    encrypt = AsyncMock()
    monkeypatch.setattr(audio_crypto, "encrypt_with_kek", encrypt)
    with pytest.raises(SpeechError) as error:
        await audio_crypto.encrypt_audio(TENANT, AUDIO)
    assert error.value.code == "audio_encryption_failed"
    assert "sensitive" not in str(error.value)
    encrypt.assert_not_called()


@pytest.mark.asyncio
async def test_encryption_failure_is_safe_and_never_returns_raw(monkeypatch):
    def fail(*_args):
        raise RuntimeError("sensitive encryption details")

    monkeypatch.setattr(audio_crypto, "encrypt_with_kek", fail)
    with pytest.raises(SpeechError) as error:
        await audio_crypto.encrypt_audio(TENANT, AUDIO)
    assert error.value.message == "The recording could not be encrypted"
    assert "sensitive" not in str(error.value)


@pytest.mark.asyncio
async def test_serialization_encryption_and_full_decryption_run_off_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    called = []

    def off_loop(name, original):
        def checked(*args):
            assert threading.get_ident() != main_thread
            called.append(name)
            return original(*args)

        return checked

    for name in ("_audio_json", "encrypt_with_kek", "_decrypt_audio"):
        monkeypatch.setattr(audio_crypto, name, off_loop(name, getattr(audio_crypto, name)))
    stored = await audio_crypto.encrypt_audio(TENANT, AUDIO)
    assert await audio_crypto.decrypt_audio(TENANT, stored) == AUDIO
    assert called == ["_audio_json", "encrypt_with_kek", "_decrypt_audio"]


@pytest.mark.asyncio
async def test_cancellation_is_not_hidden_as_crypto_failure(local_vault):
    local_vault.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await audio_crypto.encrypt_audio(TENANT, AUDIO)
