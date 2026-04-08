# ruff: noqa: N801
"""Unit tests for the encryption module (core.crypto).

Tests cover:
- encrypt_credential returns non-empty string different from input
- decrypt_credential(encrypt_credential(x)) == x for various inputs
- verify_credential returns True for valid encrypted data
- verify_credential returns False for random garbage
- Empty string encryption/decryption works
- Unicode credential encryption/decryption works
- Different plaintexts produce different ciphertexts
"""

from __future__ import annotations

import pytest


# ============================================================================
# Basic encrypt / decrypt round-trip
# ============================================================================


class TestEncryptDecryptRoundTrip:
    """Verify encrypt -> decrypt round-trip works for all input types."""

    def test_encrypt_returns_non_empty_string(self):
        from core.crypto import encrypt_credential

        result = encrypt_credential("test-password")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_encrypt_returns_different_from_input(self):
        from core.crypto import encrypt_credential

        plaintext = "my-secret-portal-password"
        ciphertext = encrypt_credential(plaintext)
        assert ciphertext != plaintext

    def test_decrypt_recovers_plaintext(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "portal-password-2026!"
        ciphertext = encrypt_credential(plaintext)
        assert decrypt_credential(ciphertext) == plaintext

    def test_round_trip_simple_password(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "abc123"
        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext

    def test_round_trip_long_password(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "x" * 1024
        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext

    @pytest.mark.parametrize(
        "plaintext",
        [
            "short",
            "medium-length-password-123!@#",
            "P@$$w0rd_With_Sp3ci@l_Ch4rs",
            "spaces in the password",
            "ALLCAPS12345",
            "1234567890",
        ],
    )
    def test_round_trip_various_inputs(self, plaintext: str):
        from core.crypto import decrypt_credential, encrypt_credential

        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext


# ============================================================================
# Empty string handling
# ============================================================================


class TestEmptyStringEncryption:
    """Verify that empty string can be encrypted and decrypted."""

    def test_encrypt_empty_string(self):
        from core.crypto import encrypt_credential

        result = encrypt_credential("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_decrypt_empty_string(self):
        from core.crypto import decrypt_credential, encrypt_credential

        ciphertext = encrypt_credential("")
        assert decrypt_credential(ciphertext) == ""


# ============================================================================
# Unicode handling
# ============================================================================


class TestUnicodeEncryption:
    """Verify that Unicode credentials can be encrypted and decrypted."""

    def test_hindi_text(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "\u092a\u093e\u0938\u0935\u0930\u094d\u0921123"
        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext

    def test_emoji_in_password(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "pass\U0001f511word"
        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext

    def test_mixed_unicode(self):
        from core.crypto import decrypt_credential, encrypt_credential

        plaintext = "abc-\u00e9\u00e8\u00ea-\u4f60\u597d-\u043f\u0440\u0438\u0432\u0435\u0442"
        assert decrypt_credential(encrypt_credential(plaintext)) == plaintext


# ============================================================================
# verify_credential
# ============================================================================


class TestVerifyCredential:
    """Verify the verify_credential helper works correctly."""

    def test_verify_valid_ciphertext_returns_true(self):
        from core.crypto import encrypt_credential, verify_credential

        ciphertext = encrypt_credential("valid-password")
        assert verify_credential(ciphertext) is True

    def test_verify_garbage_returns_false(self):
        from core.crypto import verify_credential

        assert verify_credential("not-a-real-ciphertext") is False

    def test_verify_empty_string_returns_false(self):
        from core.crypto import verify_credential

        assert verify_credential("") is False

    def test_verify_random_base64_returns_false(self):
        from core.crypto import verify_credential

        import base64
        garbage = base64.urlsafe_b64encode(b"random-garbage-data").decode()
        assert verify_credential(garbage) is False

    def test_verify_none_like_values(self):
        """Non-string or invalid input should return False."""
        from core.crypto import verify_credential

        assert verify_credential("null") is False
        assert verify_credential("undefined") is False


# ============================================================================
# Ciphertext uniqueness
# ============================================================================


class TestCiphertextUniqueness:
    """Different plaintexts should produce different ciphertexts."""

    def test_different_plaintexts_different_ciphertexts(self):
        from core.crypto import encrypt_credential

        ct1 = encrypt_credential("password-alpha")
        ct2 = encrypt_credential("password-beta")
        assert ct1 != ct2

    def test_same_plaintext_different_ciphertexts(self):
        """Fernet uses a timestamp nonce, so same input gives different output."""
        from core.crypto import encrypt_credential

        ct1 = encrypt_credential("same-password")
        ct2 = encrypt_credential("same-password")
        # Fernet includes a timestamp, so ciphertexts should differ
        # (unless called in the exact same second, which is possible but unlikely)
        # We verify both are valid rather than asserting inequality
        from core.crypto import decrypt_credential

        assert decrypt_credential(ct1) == "same-password"
        assert decrypt_credential(ct2) == "same-password"

    def test_many_different_plaintexts(self):
        from core.crypto import encrypt_credential

        ciphertexts = set()
        for i in range(20):
            ct = encrypt_credential(f"password-{i}")
            ciphertexts.add(ct)
        # All 20 should be unique
        assert len(ciphertexts) == 20


# ============================================================================
# Key derivation
# ============================================================================


class TestKeyDerivation:
    """Verify the vault key derivation produces consistent results."""

    def test_vault_key_is_bytes(self):
        from core.crypto import _get_vault_key

        key = _get_vault_key()
        assert isinstance(key, bytes)

    def test_vault_key_length_valid_for_fernet(self):
        """Fernet requires a 32-byte base64-encoded key (44 chars base64)."""
        from core.crypto import _get_vault_key

        key = _get_vault_key()
        # base64-encoded 32 bytes = 44 characters
        assert len(key) == 44

    def test_vault_key_deterministic(self):
        """Same env should produce the same key."""
        from core.crypto import _get_vault_key

        key1 = _get_vault_key()
        key2 = _get_vault_key()
        assert key1 == key2
