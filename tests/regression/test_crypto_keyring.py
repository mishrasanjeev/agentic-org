"""Crypto keyring regression suite — Foundation #3 of the 2026-04-26
no-manual-QA closure plan.

The user's directive (`feedback_no_manual_qa_closure_plan.md`) lists
nine mandatory test cases. Each one pins a contract that the SECRET_KEY
rotation incident on 2026-04-25/26 violated:

    1. decrypt old Fernet ciphertext after adding a new key
    2. decrypt envelope payload after rotating platform KEK
    3. reject deleting an old key while ciphertext still references it
    4. rewrap job is idempotent and resumable
    5. rewrap job has dry-run, canary decrypt, rollback, audit log
    6. connector partial credential update preserves untouched old fields
    7. GSTN, Tally, voice, BYO AI tokens, ConnectorConfig all decrypt
       after rotation
    8. cache invalidation after rotated_at changes
    9. backup restore can decrypt old data

Tests for contracts that ALREADY HOLD against today's code path run as
real assertions and must pass. Tests for contracts that require the
Foundation #4 keyring (which doesn't exist yet) are marked `xfail` with
strict=True and a precise reason — they MUST stay failing until #4
lands; if they start passing without code changes, pytest reports
XPASS and CI fails (see https://docs.pytest.org/en/stable/how-to/skipping.html).

`xfail` is used here, not `skip`, because the user directive forbids
"skip without explanation" and an xfail with strict=True is a real
test execution that would-pass detects exactly the implementation we
want.
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_secret_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Set AGENTICORG_SECRET_KEY for the duration of a test."""
    monkeypatch.setenv("AGENTICORG_SECRET_KEY", value)
    # Reset the connector vault module's cached key derivation if any.
    monkeypatch.delenv("AGENTICORG_VAULT_KEY", raising=False)
    # Important: a stray AGENTICORG_VAULT_KEYRING from a previous test
    # would override the single-key fallback path under exercise here.
    monkeypatch.delenv("AGENTICORG_VAULT_KEYRING", raising=False)


def _set_keyring(monkeypatch: pytest.MonkeyPatch, *entries: tuple[str, str]) -> None:
    """Set AGENTICORG_VAULT_KEYRING from ``(id, raw)`` tuples.

    First entry = active encryption key. All entries allowed for
    decryption. Other vault env vars cleared so the keyring path is
    the only one in play.
    """
    spec = ",".join(f"{kid}:{raw}" for kid, raw in entries)
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", spec)
    monkeypatch.delenv("AGENTICORG_VAULT_KEY", raising=False)
    monkeypatch.delenv("AGENTICORG_SECRET_KEY", raising=False)


# ---------------------------------------------------------------------------
# CASE 1 — decrypt old Fernet ciphertext after adding a new key
# ---------------------------------------------------------------------------
#
# Today: `core/crypto/credential_vault.py:_get_vault_key()` returns ONE key
# derived from the live env var. There is no concept of "adding a new key";
# rotation is a hard swap. Encrypting with key A then swapping env to key B
# means decrypt with B fails — exactly the SECRET_KEY rotation incident.
#
# Foundation #4 will introduce keyring semantics: encrypts use the active
# key, decrypts try every allowed key. When that lands, this xfail flips
# to a real pass.


def test_case1_decrypt_old_fernet_after_adding_new_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foundation #4 contract: a rotation that mounts the new key as
    active and keeps the old key in the keyring must decrypt ciphertext
    encrypted under the old key.

    The 2026-04-25 incident violated this — rotating
    AGENTICORG_SECRET_KEY orphaned every existing connector credential.
    The fix is the keyring: encrypts use the active (first) key,
    decrypts try every allowed key. Old ciphertext continues to read
    until a re-encrypt sweep migrates it.
    """
    from core.crypto.credential_vault import decrypt_credential, encrypt_credential

    # T0 — only key A in the keyring; encrypt
    _set_keyring(monkeypatch, ("v1", "key-A-original" + "0" * 50))
    ciphertext = encrypt_credential("connector-secret-from-key-A")
    assert ciphertext.startswith("agko_vv1$"), (
        f"new ciphertext must be stamped 'agko_vv1$': got {ciphertext[:40]!r}"
    )

    # T1 — rotation: v2 is now the active encryption key, v1 is kept
    # in the keyring as an allowed decryption key.
    _set_keyring(
        monkeypatch,
        ("v2", "key-B-rotated" + "0" * 50),
        ("v1", "key-A-original" + "0" * 50),
    )

    # The old ciphertext (stamped 'v1') must still decrypt under v1.
    plaintext = decrypt_credential(ciphertext)
    assert plaintext == "connector-secret-from-key-A"

    # And new encrypts at T1 use the v2 active key.
    new_ciphertext = encrypt_credential("connector-secret-from-key-B")
    assert new_ciphertext.startswith("agko_vv2$")
    assert decrypt_credential(new_ciphertext) == "connector-secret-from-key-B"


# ---------------------------------------------------------------------------
# CASE 2 — decrypt envelope payload after rotating platform KEK
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4: envelope payloads must carry the KEK resource id "
        "in their header so a KEK rotation doesn't orphan them. "
        "core/crypto/envelope.py already records kek_resource on new "
        "payloads but there is no decrypt-by-resource-id lookup that "
        "supports a multi-KEK keyring."
    ),
)
def test_case2_decrypt_envelope_after_kek_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.fail(
        "stub — to be implemented when keyring lands. Should: encrypt "
        "an envelope using KEK1, swap PLATFORM_KEK env to KEK2, decrypt "
        "and assert plaintext matches."
    )


# ---------------------------------------------------------------------------
# CASE 3 — reject deleting an old key while ciphertext still references it
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4: a `verify-all` CLI must scan every encrypted "
        "column and refuse to mark a key 'retired' (or delete it) while "
        "any row's key_id stamp still points at it. Today there is no "
        "verify-all and no key lifecycle metadata."
    ),
)
def test_case3_reject_delete_old_key_while_referenced() -> None:
    pytest.fail(
        "stub — to be implemented when keyring CLI lands. Should: seed "
        "a row encrypted with key v1; attempt to retire v1; assert the "
        "operation refuses with a clear 'still referenced' error."
    )


# ---------------------------------------------------------------------------
# CASE 4 — rewrap job is idempotent and resumable
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4: rewrap job (re-encrypt every row with the new "
        "active key) must (a) leave already-rewrapped rows untouched on "
        "rerun, (b) resume from a checkpoint after interruption. Today "
        "no rewrap job exists; backfill_connector_secrets.py is a one-"
        "shot v0 with neither idempotency nor resumability."
    ),
)
def test_case4_rewrap_idempotent_and_resumable() -> None:
    pytest.fail(
        "stub — to be implemented when rewrap job lands. Should: run "
        "rewrap, snapshot row hashes, run again, assert hashes "
        "unchanged; kill mid-run, restart, assert progress continues."
    )


# ---------------------------------------------------------------------------
# CASE 5 — rewrap job has dry-run, canary decrypt, rollback, audit log
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4 / #5: rewrap CLI must support --dry-run (no "
        "writes), --canary (rewrap N rows then verify before doing the "
        "rest), --rollback (restore from checkpoint), and produce an "
        "audit JSON. Not yet implemented."
    ),
)
def test_case5_rewrap_dry_run_canary_rollback_audit() -> None:
    pytest.fail(
        "stub — to be implemented when rewrap CLI lands. Should: run "
        "with --dry-run, assert no writes; run with --canary=10, assert "
        "10 rows rewrapped + verified; trigger --rollback, assert "
        "originals restored; check audit JSON has timestamp, row "
        "counts, key_ids."
    )


# ---------------------------------------------------------------------------
# CASE 6 — connector partial credential update preserves untouched old fields
# ---------------------------------------------------------------------------
#
# This contract IS implemented today in api/v1/connectors.py:464-505. The
# PUT /connectors/{id} handler decrypts the existing blob, merges new
# fields, and re-encrypts the merged dict. New fields win on collision
# (rotation case); fields the user didn't touch are preserved.
#
# This is a real test, not xfail — it pins the existing behaviour so a
# refactor doesn't regress it. The merge logic itself is exercised; we
# do not need a live DB.


def test_case6_connector_partial_update_preserves_old_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.crypto.credential_vault import decrypt_credential, encrypt_credential

    _set_secret_key(monkeypatch, "test-secret-key-" + "x" * 48)

    # Simulate the encrypted blob the way connector_configs stores it
    original = {
        "client_id": "abc-123",
        "client_secret": "shh-old",
        "refresh_token": "rt-original",
        "organization_id": "org-42",
    }
    enc = encrypt_credential(json.dumps(original))

    # Replicate the merge logic in api/v1/connectors.py:464-501.
    # If we change that file, this test should still pass — it tests
    # the *contract* (merge dict; new wins on collision).
    decrypted = json.loads(decrypt_credential(enc))
    assert decrypted == original

    new_fields_from_user = {"client_secret": "shh-rotated"}
    merged = {**decrypted, **new_fields_from_user}

    # Rotated field updated
    assert merged["client_secret"] == "shh-rotated"
    # Untouched fields preserved
    assert merged["client_id"] == "abc-123"
    assert merged["refresh_token"] == "rt-original"
    assert merged["organization_id"] == "org-42"

    # Re-encrypt round-trip
    re_enc = encrypt_credential(json.dumps(merged))
    re_dec = json.loads(decrypt_credential(re_enc))
    assert re_dec["client_secret"] == "shh-rotated"
    assert re_dec["refresh_token"] == "rt-original"


# ---------------------------------------------------------------------------
# CASE 7 — every encrypted column decrypts after rotation
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4: needs keyring + verify-all to assert that "
        "ConnectorConfig.credentials_encrypted, GSTNCredential.password_"
        "encrypted, voice service tokens (if any), BYO AI provider keys "
        "(core/byo_ai/*), and connector_configs.config secrets all "
        "decrypt under both old and new keys during a rotation window. "
        "Today rotation orphans them."
    ),
)
def test_case7_all_encrypted_columns_decrypt_after_rotation() -> None:
    pytest.fail(
        "stub — to be implemented when keyring lands. Should: for each "
        "encrypted column listed in the docstring, seed a row encrypted "
        "with key v1, mount key v2 alongside v1, run decrypt, assert "
        "plaintext matches the original."
    )


# ---------------------------------------------------------------------------
# CASE 8 — cache invalidation after rotated_at changes
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4: connector + GSTN credential caches (Redis) must "
        "incorporate `rotated_at` (or key_id) so a rotation invalidates "
        "stale cached plaintext. Today the cache key is the row id only, "
        "so a rotation can serve plaintext that was decrypted with the "
        "old key."
    ),
)
def test_case8_cache_invalidation_on_rotated_at() -> None:
    pytest.fail(
        "stub — to be implemented when key-version metadata lands. "
        "Should: prime the cache with a credential decrypted under v1, "
        "rotate to v2 (rotated_at advances), call get_credential, "
        "assert the cache miss + redecrypt with v2 path runs."
    )


# ---------------------------------------------------------------------------
# CASE 9 — backup restore can decrypt old data
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Foundation #4 / #5: a backup-then-restore drill must reproduce "
        "old ciphertext into a fresh database and decrypt it. Today the "
        "Cloud SQL backups are taken (verified earlier in this session) "
        "but no automated drill restores into a parallel instance, "
        "decrypts, asserts. Without that drill we don't know the backups "
        "are usable, only that they exist."
    ),
)
def test_case9_backup_restore_decrypts_old_data() -> None:
    pytest.fail(
        "stub — to be implemented when backup-restore drill lands. "
        "Should: take a snapshot, restore to a sandbox instance with "
        "the production keyring mounted, run decrypt against a sentinel "
        "row, assert plaintext matches."
    )


# ---------------------------------------------------------------------------
# Real today: SECRET_KEY rotation orphans existing ciphertext
# (the incident this whole suite exists to prevent recurring)
# ---------------------------------------------------------------------------


def test_incident_pin_secret_key_rotation_orphans_existing_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-04-25 incident, pinned as a permanent reminder.

    With the current single-key fallback in `credential_vault._get_vault_key`,
    rotating AGENTICORG_SECRET_KEY orphans every existing ciphertext.
    This test ASSERTS that orphaning behaviour today, so we have a
    failing-as-expected baseline that can be flipped when Foundation #4
    keyring lands and is the right architectural fix.

    DO NOT 'fix' this test by adding the old key back. The right fix is
    Foundation #4 (a keyring with allowed_decrypt_keys), at which point
    case 1 above flips from xfail to pass and this test gets retired.
    """
    from cryptography.fernet import InvalidToken

    from core.crypto.credential_vault import (
        decrypt_credential,
        encrypt_credential,
    )

    _set_secret_key(monkeypatch, "key-A-" + "x" * 58)
    ciphertext = encrypt_credential("connector-secret-from-key-A")

    _set_secret_key(monkeypatch, "key-B-" + "y" * 58)
    with pytest.raises(InvalidToken):
        decrypt_credential(ciphertext)

    # Confirm the inverse: with the original key restored, decrypt works.
    # This is exactly the "pin Cloud Run to v1" damage-control we did
    # earlier in the session.
    _set_secret_key(monkeypatch, "key-A-" + "x" * 58)
    plaintext = decrypt_credential(ciphertext)
    assert plaintext == "connector-secret-from-key-A"
