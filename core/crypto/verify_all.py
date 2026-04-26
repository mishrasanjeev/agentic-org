"""``verify-all`` — scan encrypted columns and enforce key lifecycle.

Foundation #4 iteration 2 — implements the user directive item #3
("reject deleting an old key while ciphertext still references it").

Two responsibilities:

1. **Parsers** — given a single ciphertext string, return the key
   reference it depends on. Two formats:
   - vault (Fernet keyring): ``agko_v{id}$<base64>`` → key id, or
     ``"legacy"`` for un-stamped legacy ciphertext.
   - envelope (KMS-wrapped): ``{"version":…,"kek":"<resource>",…}`` →
     KMS resource name.

2. **Scanner** — given an async DB session, walk every encrypted
   column and return a map of ``column_path -> set of key refs``.
   The ``assert_key_unreferenced(key_id_or_kek, session)`` helper
   raises ``KeyStillReferencedError`` if the key is still in use,
   so a future "retire key" CLI cannot delete a referenced key.

CLI usage::

    python -m core.crypto.verify_all
    # prints all key references found in the DB

    python -m core.crypto.verify_all --check=v1
    # exits non-zero if 'v1' is still referenced anywhere

This is the gate that makes Foundation #3 case 3 satisfiable. The
rewrap job (Foundation #4 iteration 3) will complete the lifecycle:
rewrap → re-run verify_all → no references to the old key → safe
to retire.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

# Vault stamp prefix — matches credential_vault.encrypt_credential output.
_VAULT_PREFIX = re.compile(r"^agko_v([^$]+)\$")


class KeyStillReferencedError(RuntimeError):
    """Raised when an attempted key retirement would orphan ciphertext."""

    def __init__(self, key_ref: str, locations: dict[str, set[str]]):
        self.key_ref = key_ref
        self.locations = locations
        cols = sorted(loc for loc, refs in locations.items() if key_ref in refs)
        super().__init__(
            f"Cannot retire key {key_ref!r}: still referenced in "
            f"{len(cols)} column(s): {cols}"
        )


@dataclass(frozen=True)
class KeyRef:
    """A single (kind, ref) entry — kind is 'vault' or 'envelope'."""

    kind: str  # "vault" | "envelope"
    ref: str   # key id (vault) or KMS resource (envelope)


# ── Parsers ──────────────────────────────────────────────────────────


def parse_ciphertext(blob: str | bytes | dict | None) -> KeyRef | None:
    """Return the KeyRef this ciphertext depends on, or None if unparseable.

    Accepts:
    - ``str`` — either ``"agko_v{id}$..."`` (vault) or a JSON envelope.
    - ``dict`` — already-parsed envelope payload.
    - ``bytes`` — best-effort decode as utf-8.
    - ``None`` — returns None.
    """
    if blob is None:
        return None
    if isinstance(blob, bytes):
        try:
            blob = blob.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(blob, dict):
        kek = blob.get("kek")
        if isinstance(kek, str) and kek and "wrapped_dek" in blob:
            return KeyRef(kind="envelope", ref=kek)
        return None
    if not isinstance(blob, str):
        return None
    s = blob.strip()
    if not s:
        return None

    # Vault stamp
    m = _VAULT_PREFIX.match(s)
    if m:
        return KeyRef(kind="vault", ref=m.group(1))

    # Envelope JSON
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict):
            kek = d.get("kek")
            if isinstance(kek, str) and kek and "wrapped_dek" in d:
                return KeyRef(kind="envelope", ref=kek)

    # Un-stamped vault ciphertext (legacy, pre-keyring) — implicitly
    # decrypted under the keyring entry id "legacy".
    return KeyRef(kind="vault", ref="legacy")


def parse_jsonb_credentials(value: dict | str | None) -> KeyRef | None:
    """Wrapper for the ``ConnectorConfig.credentials_encrypted`` shape.

    The column is a JSONB dict like ``{"_encrypted": "<ciphertext>"}``.
    Sometimes stored as a serialized JSON string for the same shape.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            # If it looks like a vault stamp directly, parse it.
            return parse_ciphertext(value)
    if isinstance(value, dict):
        return parse_ciphertext(value.get("_encrypted"))
    return None


# ── Scanner ──────────────────────────────────────────────────────────


# Each entry: (column_label, async_fetch_callable). The scanner
# treats them as opaque columns to scan; adding a new encrypted
# column is a one-line registration here.
_SCANNERS: list[tuple[str, str]] = [
    ("connector_configs.credentials_encrypted",
     "core.models.connector_config:ConnectorConfig:credentials_encrypted"),
    ("gstn_credentials.password_encrypted",
     "core.models.gstn_credential:GSTNCredential:password_encrypted"),
]


async def _fetch_column(session, dotted: str) -> Iterable:
    """Yield every value of an encrypted column.

    ``dotted`` form: ``"module.path:Model:column_attr"``.
    Dynamic import keeps verify_all importable even when the model
    module isn't loaded (e.g. running the CLI before app startup).
    """
    mod_path, model_name, col_name = dotted.split(":")
    try:
        mod = __import__(mod_path, fromlist=[model_name])
    except ImportError:
        return []
    model_cls = getattr(mod, model_name, None)
    if model_cls is None:
        return []
    column_attr = getattr(model_cls, col_name, None)
    if column_attr is None:
        return []
    from sqlalchemy import select

    result = await session.execute(select(column_attr))
    return [row[0] for row in result.all()]


async def scan_encrypted_columns(session) -> dict[str, set[KeyRef]]:
    """Walk every registered encrypted column and return key references.

    Returns ``{column_label: {KeyRef, KeyRef, …}}``.
    """
    refs: dict[str, set[KeyRef]] = {}
    for label, dotted in _SCANNERS:
        values = await _fetch_column(session, dotted)
        for raw in values:
            kr = (
                parse_jsonb_credentials(raw)
                if label.endswith("credentials_encrypted")
                else parse_ciphertext(raw)
            )
            if kr is None:
                continue
            refs.setdefault(label, set()).add(kr)
    return refs


async def assert_key_unreferenced(key_ref: str, session) -> None:
    """Raise ``KeyStillReferencedError`` if the key is in use.

    ``key_ref`` is matched against the ``KeyRef.ref`` field — works
    for both vault key ids ("v1", "legacy") and envelope KMS resources
    ("projects/.../keyRings/.../cryptoKeys/v1").
    """
    refs_by_col = await scan_encrypted_columns(session)
    locations = {col: {kr.ref for kr in krs} for col, krs in refs_by_col.items()}
    for ref_set in locations.values():
        if key_ref in ref_set:
            raise KeyStillReferencedError(key_ref, locations)


# ── CLI ──────────────────────────────────────────────────────────────


def _print_report(refs: dict[str, set[KeyRef]]) -> None:
    if not refs:
        print("verify-all: no encrypted ciphertext found.")
        return
    print("verify-all: key references in encrypted columns")
    print("=" * 64)
    for col in sorted(refs.keys()):
        print(f"  {col}:")
        # Group by kind for readability
        vault_refs = sorted(kr.ref for kr in refs[col] if kr.kind == "vault")
        env_refs = sorted(kr.ref for kr in refs[col] if kr.kind == "envelope")
        for r in vault_refs:
            print(f"    [vault]    {r}")
        for r in env_refs:
            print(f"    [envelope] {r}")
    print("=" * 64)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="verify-all")
    parser.add_argument(
        "--check",
        metavar="KEY_REF",
        help="exit non-zero if the given key id / KEK resource is still "
             "referenced anywhere",
    )
    args = parser.parse_args(argv)

    from core.database import get_async_session

    async def _run() -> int:
        async with get_async_session() as session:
            refs = await scan_encrypted_columns(session)
        _print_report(refs)
        if args.check:
            for refset in refs.values():
                if any(kr.ref == args.check for kr in refset):
                    print(
                        f"\nverify-all: ERROR — key {args.check!r} is still "
                        "referenced. Refuse to retire.",
                        flush=True,
                    )
                    return 2
            print(f"\nverify-all: OK — key {args.check!r} is not referenced.")
        return 0

    return asyncio.run(_run())


if __name__ == "__main__":
    import sys

    sys.exit(main())
