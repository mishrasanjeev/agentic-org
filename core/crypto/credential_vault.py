"""Credential vault — Fernet symmetric encryption with keyring support.

Used for things like GSTN portal passwords, OAuth refresh tokens, and
other per-tenant credentials where we don't need the full envelope-
encryption flow.

Foundation #4 — keyring (replaces single-key fallback)

Key derivation reads ``AGENTICORG_VAULT_KEYRING`` first. The keyring
is an ordered list of ``id:source`` entries, separated by commas:

    AGENTICORG_VAULT_KEYRING=v3:<raw3>,v2:<raw2>,v1:<raw1>

The FIRST entry is the active encryption key. Every entry is allowed
for decryption. New ciphertext is stamped with the producing key id
(``agko_v{id}$<base64>``) so a future rotation can find the right key
without trial-and-error.

Backwards compatibility:
- If ``AGENTICORG_VAULT_KEYRING`` is unset, the keyring is a single
  ``"legacy"`` entry derived from ``AGENTICORG_VAULT_KEY`` (or
  ``AGENTICORG_SECRET_KEY`` in dev) — exactly the pre-Foundation-#4
  behaviour. Old un-prefixed ciphertext continues to decrypt because
  the legacy keyring contains the same key.
- A new keyring entry can be added (rotation) without removing the old
  one. Both keys decrypt; new encrypts use the active (first) key.
- A migration that re-encrypts every row under the active key is the
  pre-condition for retiring an old key (Foundation #4 verify-all
  CLI, follow-up iteration).

For large blobs and customer BYOK see ``core.crypto.envelope``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

from cryptography.fernet import Fernet, InvalidToken

# Stamp prefix on all NEW ciphertext: agko_v{id}$<base64-fernet-token>
_PREFIX_RE = re.compile(r"^agko_v([^$]+)\$(.*)$", re.DOTALL)


def _derive_fernet_key(raw: str) -> bytes:
    """Derive a Fernet-compatible 32-byte base64-encoded key from raw input.

    Same SHA-256-then-urlsafe-base64 derivation as before. Lifted out
    so the keyring loader can reuse it per entry.
    """
    digest = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _load_keyring() -> list[tuple[str, bytes]]:
    """Return ordered ``[(key_id, fernet_key_bytes), …]``.

    First entry = active encryption key. All entries allowed for
    decryption.

    Reading order:
      1. ``AGENTICORG_VAULT_KEYRING=id1:raw1,id2:raw2,…`` (multi-key)
      2. fallback to single-key keyring derived from
         ``AGENTICORG_VAULT_KEY`` or ``AGENTICORG_SECRET_KEY`` with id
         ``"legacy"``.
    """
    spec = os.environ.get("AGENTICORG_VAULT_KEYRING", "").strip()
    if spec:
        out: list[tuple[str, bytes]] = []
        for entry in spec.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    "AGENTICORG_VAULT_KEYRING entry missing 'id:' prefix: "
                    f"{entry!r}. Expected format: id1:raw1,id2:raw2,…"
                )
            kid, raw = entry.split(":", 1)
            kid = kid.strip()
            if not kid:
                raise ValueError(
                    f"AGENTICORG_VAULT_KEYRING entry has empty id: {entry!r}"
                )
            out.append((kid, _derive_fernet_key(raw)))
        if out:
            return out

    # Legacy fallback — exactly the pre-keyring single-key behaviour.
    raw = os.environ.get(
        "AGENTICORG_VAULT_KEY",
        os.environ.get("AGENTICORG_SECRET_KEY", "dev-only-vault-key"),
    )
    return [("legacy", _derive_fernet_key(raw))]


def _get_vault_key() -> bytes:
    """Return the ACTIVE Fernet key (first entry in the keyring).

    Kept as a module-level helper for backward compatibility — anything
    that imported this name continues to work, but ``encrypt_credential``
    and ``decrypt_credential`` now use the full keyring directly.
    """
    return _load_keyring()[0][1]


def encrypt_credential(plaintext: str) -> str:
    """Encrypt with the active key. Output: ``agko_v{id}$<base64-fernet>``.

    The key id stamped in the prefix is the id of the FIRST entry in
    the keyring at encrypt time. A subsequent rotation that demotes
    that entry but keeps it in the keyring still allows decryption.
    """
    keyring = _load_keyring()
    kid, kbytes = keyring[0]
    f = Fernet(kbytes)
    token = f.encrypt(plaintext.encode()).decode()
    return f"agko_v{kid}${token}"


def decrypt_credential(ciphertext: str) -> str:
    """Decrypt under any allowed key in the keyring.

    Strategy:
    1. If the ciphertext is stamped (``agko_v{id}$…``), look up the
       matching key in the keyring and try it first. If that fails
       (key in keyring but Fernet rejects), or if no key matches the
       stamp, fall through to step 2.
    2. Try every key in the keyring against the (possibly un-stamped)
       payload. This covers (a) legacy ciphertext written before
       Foundation #4 landed, (b) ciphertext whose stamp id was retired
       (re-encrypt in progress), and (c) recovery cases where the
       stamp was corrupted.
    3. If nothing decrypts, raise InvalidToken with a precise message.
    """
    keyring = _load_keyring()

    m = _PREFIX_RE.match(ciphertext)
    if m:
        kid_target = m.group(1)
        token = m.group(2)
        # Try the matching key first
        for kid, kbytes in keyring:
            if kid == kid_target:
                try:
                    return Fernet(kbytes).decrypt(token.encode()).decode()
                except InvalidToken:
                    break  # fall through to brute-force the others
        # Try every other key (matching key not in keyring or rejected)
        for kid, kbytes in keyring:
            if kid == kid_target:
                continue
            try:
                return Fernet(kbytes).decrypt(token.encode()).decode()
            except InvalidToken:
                continue
        raise InvalidToken(
            f"No keyring entry decrypted ciphertext stamped 'v{kid_target}'. "
            f"Keyring has {len(keyring)} entr{'y' if len(keyring) == 1 else 'ies'}: "
            f"{[k for k, _ in keyring]}"
        )

    # Un-stamped (legacy) ciphertext — try every key
    for kid, kbytes in keyring:
        try:
            return Fernet(kbytes).decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            continue
    raise InvalidToken(
        "No keyring entry decrypted legacy unprefixed ciphertext. "
        f"Keyring tried: {[k for k, _ in keyring]}"
    )


def verify_credential(ciphertext: str) -> bool:
    """Check if a ciphertext can be decrypted (any key in the keyring is valid)."""
    try:
        decrypt_credential(ciphertext)
        return True
    except InvalidToken:
        return False
    except Exception:
        return False
