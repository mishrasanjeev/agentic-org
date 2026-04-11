"""Envelope encryption + tenant_secrets unit tests.

Mocks ``google.cloud.kms`` so we don't hit a real KMS project. The
real BYOK integration test against a test KMS resource is deferred to
the integration suite (will use Workload Identity Federation in CI).

Coverage:
  - encrypt → decrypt round-trip with a mocked platform KEK
  - encrypt → decrypt round-trip with a customer KEK override
  - tenant_secrets format detection (env1: prefix vs legacy Fernet)
  - encrypt_for_tenant fallback to Fernet when no KEK is configured
  - decrypt_for_tenant correctly routes both formats
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

from core.crypto.credential_vault import (
    decrypt_credential as fernet_decrypt,
)
from core.crypto.credential_vault import (
    encrypt_credential as fernet_encrypt,
)
from core.crypto.envelope import (
    EncryptedPayload,
    decrypt,
    decrypt_from_string,
    encrypt,
    encrypt_to_string,
)
from core.crypto.tenant_secrets import (
    _ENVELOPE_PREFIX,
    decrypt_for_tenant,
    encrypt_for_tenant,
)

# ── Mocked KMS client ───────────────────────────────────────────────


class _StubKMSClient:
    """In-memory KMS — wrap = identity tag, unwrap = identity untag.

    Simulates Cloud KMS by storing the plaintext DEK alongside a fake
    ciphertext blob keyed by the KEK resource name. Good enough to
    verify the envelope module's call shape.
    """

    def __init__(self):
        self._wrapped: dict[bytes, bytes] = {}

    def encrypt(self, *, request):
        plaintext = request["plaintext"]
        # The "ciphertext" is just a tagged copy of the plaintext —
        # easy to recognize in assertions.
        kek = request["name"].encode()
        wrapped = b"WRAPPED::" + kek + b"::" + plaintext
        self._wrapped[wrapped] = plaintext
        response = MagicMock()
        response.ciphertext = wrapped
        return response

    def decrypt(self, *, request):
        wrapped = request["ciphertext"]
        plaintext = self._wrapped.get(wrapped)
        if plaintext is None:
            # Fall back to parsing the tagged form so the test still
            # works after process restart (no shared cache).
            assert wrapped.startswith(b"WRAPPED::")
            _, _, rest = wrapped.partition(b"::")
            _, _, plaintext = rest.partition(b"::")
        response = MagicMock()
        response.plaintext = plaintext
        return response


@pytest.fixture
def stub_kms(monkeypatch):
    client = _StubKMSClient()
    # Reset the lazy module-level KMS client
    import core.crypto.envelope as env_mod

    env_mod._kms_client = client
    monkeypatch.setenv(
        "AGENTICORG_PLATFORM_KEK",
        "projects/test/locations/global/keyRings/r/cryptoKeys/k",
    )
    yield client
    env_mod._kms_client = None


# ── Envelope round-trip ─────────────────────────────────────────────


class TestEnvelopeRoundTrip:
    def test_encrypt_decrypt_with_platform_kek(self, stub_kms):
        plaintext = b"super-secret-credential"
        payload = encrypt(plaintext)
        assert isinstance(payload, EncryptedPayload)
        assert payload.kek == os.environ["AGENTICORG_PLATFORM_KEK"]
        assert payload.ciphertext != plaintext  # actually encrypted
        assert len(payload.nonce) == 12  # AES-GCM nonce size

        decrypted = decrypt(payload)
        assert decrypted == plaintext

    def test_encrypt_decrypt_with_customer_kek(self, stub_kms):
        customer_kek = "projects/cust/locations/asia-south1/keyRings/r/cryptoKeys/k"
        payload = encrypt(b"customer-data", kek_resource=customer_kek)
        assert payload.kek == customer_kek
        assert decrypt(payload) == b"customer-data"

    def test_encrypt_to_string_round_trip(self, stub_kms):
        s = encrypt_to_string(b"hello world")
        assert isinstance(s, str)
        assert decrypt_from_string(s) == b"hello world"

    def test_encrypt_without_kek_raises(self, monkeypatch):
        monkeypatch.delenv("AGENTICORG_PLATFORM_KEK", raising=False)
        import core.crypto.envelope as env_mod

        env_mod._DEFAULT_KEK_RESOURCE = ""
        env_mod._kms_client = _StubKMSClient()
        with pytest.raises(RuntimeError, match="No KEK configured"):
            encrypt(b"x")

    def test_encrypted_payload_serialization_round_trip(self, stub_kms):
        original = encrypt(b"json-serialize-me")
        as_json = original.to_json()
        restored = EncryptedPayload.from_json(as_json)
        assert restored.kek == original.kek
        assert restored.wrapped_dek == original.wrapped_dek
        assert restored.nonce == original.nonce
        assert restored.ciphertext == original.ciphertext
        assert decrypt(restored) == b"json-serialize-me"


# ── tenant_secrets — format detection + auto-routing ────────────────


class TestTenantSecretsRouting:
    def test_decrypt_for_tenant_handles_legacy_fernet(self):
        """Old rows in the DB are still readable after the migration."""
        legacy = fernet_encrypt("legacy-pw")
        assert not legacy.startswith(_ENVELOPE_PREFIX)
        assert decrypt_for_tenant(legacy) == "legacy-pw"

    def test_decrypt_for_tenant_handles_envelope_format(self, stub_kms):
        """New rows tagged with env1: are routed to the envelope module."""
        envelope_payload = encrypt_to_string(b"new-pw")
        tagged = _ENVELOPE_PREFIX + envelope_payload
        assert decrypt_for_tenant(tagged) == "new-pw"

    @patch("core.crypto.tenant_secrets.async_session_factory")
    def test_encrypt_for_tenant_falls_back_to_fernet_when_no_kek(
        self, mock_session_factory, monkeypatch
    ):
        """No platform KEK + no tenant BYOK → legacy Fernet."""
        monkeypatch.delenv("AGENTICORG_PLATFORM_KEK", raising=False)

        # Tenant has no BYOK key
        from contextlib import asynccontextmanager

        session = MagicMock()
        scalar = MagicMock()
        scalar.scalar_one_or_none = MagicMock(return_value="")
        from unittest.mock import AsyncMock as _AsyncMock

        session.execute = _AsyncMock(return_value=scalar)

        @asynccontextmanager
        async def _ctx():
            yield session

        mock_session_factory.side_effect = lambda: _ctx()

        async def run():
            return await encrypt_for_tenant("plaintext", uuid.uuid4())

        result = asyncio.run(run())
        # Should be legacy Fernet (no env1: prefix)
        assert not result.startswith(_ENVELOPE_PREFIX)
        # And it round-trips through fernet_decrypt
        assert fernet_decrypt(result) == "plaintext"

    @patch("core.crypto.tenant_secrets.async_session_factory")
    def test_encrypt_for_tenant_uses_byok_when_set(
        self, mock_session_factory, stub_kms
    ):
        """Tenant byok_kek_resource set → envelope path."""
        from contextlib import asynccontextmanager

        customer_kek = "projects/cust/locations/asia-south1/keyRings/r/cryptoKeys/k"
        session = MagicMock()
        scalar = MagicMock()
        scalar.scalar_one_or_none = MagicMock(return_value=customer_kek)
        from unittest.mock import AsyncMock as _AsyncMock

        session.execute = _AsyncMock(return_value=scalar)

        @asynccontextmanager
        async def _ctx():
            yield session

        mock_session_factory.side_effect = lambda: _ctx()

        async def run():
            return await encrypt_for_tenant("byok-secret", uuid.uuid4())

        result = asyncio.run(run())
        assert result.startswith(_ENVELOPE_PREFIX)
        # Round-trip
        assert decrypt_for_tenant(result) == "byok-secret"
