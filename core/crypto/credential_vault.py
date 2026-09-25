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
  ``"legacy"`` entry derived from ``AGENTICORG_VAULT_KEY``, else
  ``AGENTICORG_SECRET_KEY``. Old un-prefixed ciphertext continues to
  decrypt because the legacy keyring contains the same key.

Fail closed:
- Only an explicitly local or test runtime (``AGENTICORG_ENV`` in
  ``core.config.RELAXED_ENVS``) may fall back to the code default
  ``"dev-only-vault-key"`` or use a key that is a placeholder published
  in this repository (``core.config.PUBLISHED_PLACEHOLDER_SECRETS``). An
  unset or unknown ``AGENTICORG_ENV`` is strict. Keys are read from the
  process environment only; ``.env`` values loaded into ``Settings``
  are not seen here, so a strict runtime configured only through
  ``.env`` is refused rather than silently using the default.
- A keyring that is set but yields no entry, or an entry with blank key
  material, is refused in every runtime.
- Refusals raise ``VaultKeyNotConfiguredError`` and never quote key
  material. ``assert_vault_key_configured`` runs at API startup and in
  every worker process.
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

# Written in this repository, so anyone can derive keys from it.
_DEVELOPMENT_VAULT_KEY = "dev-only-vault-key"


class VaultKeyNotConfiguredError(ValueError):
    """No usable vault key. The message names the setting, never key material."""


def _runtime_env() -> str:
    return os.environ.get("AGENTICORG_ENV", "")


def _relaxed_runtime() -> bool:
    from core.config import is_relaxed_env

    return is_relaxed_env(_runtime_env())


def _refuse_published_default(raw: str, where: str, relaxed: bool) -> None:
    from core.config import is_published_placeholder_secret

    if not relaxed and is_published_placeholder_secret(raw):
        raise VaultKeyNotConfiguredError(
            f"{where} is a placeholder published in this repository, which is only "
            f"allowed in a local, dev, development, test or CI runtime "
            f"(AGENTICORG_ENV={_runtime_env()!r}). Set AGENTICORG_VAULT_KEYRING "
            "to a real key."
        )


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
      3. the development default, in local and test runtimes only.

    Raises ``VaultKeyNotConfiguredError`` when none of these yields a
    usable key (see the module docstring).
    """
    relaxed = _relaxed_runtime()
    spec = os.environ.get("AGENTICORG_VAULT_KEYRING", "").strip()
    if spec:
        out: list[tuple[str, bytes]] = []
        # Errors name an entry by position or id and never quote it: an entry
        # without its "id:" prefix is the raw key itself.
        for position, entry in enumerate(spec.split(","), start=1):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    f"AGENTICORG_VAULT_KEYRING entry {position} has no 'id:' prefix. "
                    "Expected format: id1:raw1,id2:raw2,…"
                )
            kid, raw = entry.split(":", 1)
            kid = kid.strip()
            if not kid:
                raise ValueError(f"AGENTICORG_VAULT_KEYRING entry {position} has an empty id")
            if not raw.strip():
                raise VaultKeyNotConfiguredError(f"AGENTICORG_VAULT_KEYRING entry {kid!r} has no key material")
            _refuse_published_default(raw, f"AGENTICORG_VAULT_KEYRING entry {kid!r}", relaxed)
            out.append((kid, _derive_fernet_key(raw)))
        if not out:
            raise VaultKeyNotConfiguredError(
                "AGENTICORG_VAULT_KEYRING is set but has no usable entry. Expected format: id1:raw1,id2:raw2,…"
            )
        return out

    # Single-key fallback — the pre-keyring behaviour, minus the published default
    # outside local and test runtimes. A blank variable counts as unset.
    for name in ("AGENTICORG_VAULT_KEY", "AGENTICORG_SECRET_KEY"):
        raw = os.environ.get(name, "")
        if raw.strip():
            _refuse_published_default(raw, name, relaxed)
            return [("legacy", _derive_fernet_key(raw))]
    if relaxed:
        return [("legacy", _derive_fernet_key(_DEVELOPMENT_VAULT_KEY))]
    raise VaultKeyNotConfiguredError(
        "No credential-vault key is configured: set AGENTICORG_VAULT_KEYRING "
        "(or AGENTICORG_VAULT_KEY) in the process environment. The development "
        "default is only used in a local, dev, development, test or CI runtime "
        f"(AGENTICORG_ENV={_runtime_env()!r})."
    )


def assert_vault_key_configured() -> None:
    """Raise ``VaultKeyNotConfiguredError`` unless the vault has a usable key.

    Called at API startup and when a worker process initialises, so a
    misconfigured runtime stops before it reads or writes a credential.
    """
    _load_keyring()


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
    for _kid, kbytes in keyring:
        try:
            return Fernet(kbytes).decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            continue
    raise InvalidToken(
        f"No keyring entry decrypted legacy unprefixed ciphertext. Keyring tried: {[k for k, _ in keyring]}"
    )


def verify_credential(ciphertext: str) -> bool:
    """Check if a ciphertext can be decrypted (any key in the keyring is valid)."""
    try:
        decrypt_credential(ciphertext)
        return True
    except InvalidToken:
        return False
    except (TypeError, ValueError):
        return False
