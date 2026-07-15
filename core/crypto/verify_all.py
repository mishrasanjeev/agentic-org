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
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from core.database import async_session_factory, get_tenant_session

# Vault stamp prefix — matches credential_vault.encrypt_credential output.
_VAULT_PREFIX = re.compile(r"^agko_v([^$]+)\$")


class KeyStillReferencedError(RuntimeError):
    """Raised when an attempted key retirement would orphan ciphertext."""

    def __init__(self, key_ref: str, locations: dict[str, set[str]]):
        self.key_ref = key_ref
        self.locations = locations
        cols = sorted(loc for loc, refs in locations.items() if key_ref in refs)
        super().__init__(f"Cannot retire key {key_ref!r}: still referenced in {len(cols)} column(s): {cols}")


@dataclass(frozen=True)
class KeyRef:
    """A single (kind, ref) entry — kind is 'vault' or 'envelope'."""

    kind: str  # "vault" | "envelope"
    ref: str  # key id (vault) or KMS resource (envelope)


@dataclass(frozen=True)
class TenantCompanyScope:
    """One exact RLS scope visited by crypto maintenance commands."""

    tenant_id: UUID
    company_id: UUID | None = None


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
    ("connector_configs.credentials_encrypted", "core.models.connector_config:ConnectorConfig:credentials_encrypted"),
    ("gstn_credentials.password_encrypted", "core.models.gstn_credential:GSTNCredential:password_encrypted"),
    # Foundation #4 iter 4 — tenant_ai_credentials must rewrap too.
    # The cache invalidator below ensures decrypted plaintext held in
    # ``core.ai_providers.resolver._CACHE`` is dropped after a row is
    # restamped so callers don't get stale plaintext from the
    # 120s-TTL cache.
    (
        "tenant_ai_credentials.credentials_encrypted",
        "core.models.tenant_ai_credential:TenantAICredential:credentials_encrypted",
    ),
]


# Foundation #4 iter 4 — cache invalidation on rotated_at.
#
# Some encrypted columns feed in-process caches that hold the
# decrypted plaintext. After ``rewrap`` UPDATEs a row in such a
# column, the cache must be told to drop any entry that may now be
# pointing at a stale plaintext (cf. the SECRET_KEY incident — same
# class of failure: rotated under us, downstream lookup served pre-
# rotation data). Each invalidator is a dotted ``module:callable``
# that takes no args and flushes its whole cache. Whole-cache flush
# (rather than per-row) is intentional: rewrap runs in operator-
# initiated bursts, and the caches re-warm on next access in O(1
# round-trip).
_CACHE_INVALIDATORS: dict[str, list[str]] = {
    "tenant_ai_credentials.credentials_encrypted": [
        "core.ai_providers.resolver:invalidate_cache",
    ],
}


def _resolve_scanner_model(dotted: str, *, strict: bool = False) -> Any | None:
    """Resolve a scanner model, failing closed for maintenance scans."""

    try:
        mod_path, model_name, _col_name = dotted.split(":")
        mod = __import__(mod_path, fromlist=[model_name])
        return getattr(mod, model_name)
    except (AttributeError, ImportError, ValueError) as exc:
        if strict:
            raise RuntimeError(f"verify-all: registered scanner is unavailable: {dotted!r}") from exc
        return None


async def _fetch_column(
    session,
    dotted: str,
    *,
    scope: TenantCompanyScope | None = None,
    exact_company_scope: bool = False,
    strict: bool = False,
) -> Iterable:
    """Yield every value of an encrypted column.

    ``dotted`` form: ``"module.path:Model:column_attr"``.
    Dynamic import keeps verify_all importable even when the model
    module isn't loaded (e.g. running the CLI before app startup).
    """
    try:
        _mod_path, _model_name, col_name = dotted.split(":")
    except ValueError as exc:
        if strict:
            raise RuntimeError(f"verify-all: invalid encrypted-column scanner: {dotted!r}") from exc
        return []
    model_cls = _resolve_scanner_model(dotted, strict=strict)
    if model_cls is None:
        return []
    column_attr = getattr(model_cls, col_name, None)
    if column_attr is None:
        if strict:
            raise RuntimeError(f"verify-all: registered encrypted column is unavailable: {dotted!r}")
        return []
    statement = select(column_attr)
    if scope is not None:
        tenant_attr = getattr(model_cls, "tenant_id", None)
        if tenant_attr is None:
            raise RuntimeError(f"verify-all: registered encrypted model has no tenant_id: {dotted!r}")
        statement = statement.where(tenant_attr == scope.tenant_id)
        if exact_company_scope:
            company_attr = getattr(model_cls, "company_id", None)
            if company_attr is None:
                raise RuntimeError(f"verify-all: company-scoped scanner has no company_id: {dotted!r}")
            statement = statement.where(
                company_attr == scope.company_id if scope.company_id is not None else company_attr.is_(None)
            )

    result = await session.execute(statement)
    return [row[0] for row in result.all()]


async def discover_tenant_company_scopes() -> list[TenantCompanyScope]:
    """Enumerate every tenant-global and tenant/company RLS scope.

    The raw session reads only the trusted non-tenant ``tenants`` catalog.
    Company discovery enters the tenant RLS context.  Errors propagate so an
    incomplete discovery pass can never be reported as a clean key scan.
    """

    from core.models.company import Company
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        # This is not an application bypass switch.  PostgreSQL raises if RLS
        # would filter the trusted maintenance catalog and the role lacks
        # BYPASSRLS, preventing an empty/partial tenant list from looking clean.
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_result = await session.execute(select(Tenant.id).order_by(Tenant.id))
        tenant_ids = [UUID(str(value)) for value in tenant_result.scalars().all()]

    scopes: list[TenantCompanyScope] = []
    for tenant_id in tenant_ids:
        scopes.append(TenantCompanyScope(tenant_id=tenant_id))
        async with get_tenant_session(tenant_id) as session:
            company_result = await session.execute(
                select(Company.id).where(Company.tenant_id == tenant_id).order_by(Company.id)
            )
            company_ids = [UUID(str(value)) for value in company_result.scalars().all()]
        scopes.extend(TenantCompanyScope(tenant_id=tenant_id, company_id=company_id) for company_id in company_ids)
    return scopes


def scopes_for_scanner(
    dotted: str,
    scopes: Iterable[TenantCompanyScope],
) -> tuple[list[TenantCompanyScope], bool]:
    """Return applicable scopes and whether the model has ``company_id``."""

    model_cls = _resolve_scanner_model(dotted, strict=True)
    exact_company_scope = getattr(model_cls, "company_id", None) is not None
    selected = list(scopes)
    if not exact_company_scope:
        selected = [scope for scope in selected if scope.company_id is None]
    return selected, exact_company_scope


def _collect_key_refs(
    refs: dict[str, set[KeyRef]],
    label: str,
    values: Iterable,
) -> None:
    for raw in values:
        kr = parse_jsonb_credentials(raw) if label.endswith("credentials_encrypted") else parse_ciphertext(raw)
        if kr is not None:
            refs.setdefault(label, set()).add(kr)


async def scan_encrypted_columns(session) -> dict[str, set[KeyRef]]:
    """Walk every registered encrypted column and return key references.

    Returns ``{column_label: {KeyRef, KeyRef, …}}``.
    """
    refs: dict[str, set[KeyRef]] = {}
    for label, dotted in _SCANNERS:
        values = await _fetch_column(session, dotted)
        _collect_key_refs(refs, label, values)
    return refs


async def scan_encrypted_columns_all_scopes() -> dict[str, set[KeyRef]]:
    """Scan registered columns through every exact RLS scope.

    Company-bearing tables are queried once for the tenant-global scope and
    once per company.  Tenant-only tables are queried once per tenant.  Each
    query has explicit scope predicates in addition to transaction-local RLS
    settings; no privileged or client-settable bypass is used.
    """

    discovered_scopes = await discover_tenant_company_scopes()
    refs: dict[str, set[KeyRef]] = {}
    for label, dotted in _SCANNERS:
        scanner_scopes, exact_company_scope = scopes_for_scanner(dotted, discovered_scopes)
        for scope in scanner_scopes:
            async with get_tenant_session(scope.tenant_id, scope.company_id) as session:
                values = await _fetch_column(
                    session,
                    dotted,
                    scope=scope,
                    exact_company_scope=exact_company_scope,
                    strict=True,
                )
            _collect_key_refs(refs, label, values)
    return refs


async def assert_key_unreferenced(key_ref: str, session=None) -> None:
    """Raise ``KeyStillReferencedError`` if the key is in use.

    ``key_ref`` is matched against the ``KeyRef.ref`` field — works
    for both vault key ids ("v1", "legacy") and envelope KMS resources
    ("projects/.../keyRings/.../cryptoKeys/v1").  ``session`` remains an
    accepted deprecated argument for caller compatibility, but retirement is
    always decided by the complete exact-scope scan; a caller-supplied raw or
    tenant-only session must never weaken the global guarantee.
    """
    del session
    refs_by_col = await scan_encrypted_columns_all_scopes()
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
        help="exit non-zero if the given key id / KEK resource is still referenced anywhere",
    )
    args = parser.parse_args(argv)

    async def _run() -> int:
        refs = await scan_encrypted_columns_all_scopes()
        _print_report(refs)
        if args.check:
            for refset in refs.values():
                if any(kr.ref == args.check for kr in refset):
                    print(
                        f"\nverify-all: ERROR — key {args.check!r} is still referenced. Refuse to retire.",
                        flush=True,
                    )
                    return 2
            print(f"\nverify-all: OK — key {args.check!r} is not referenced.")
        return 0

    return asyncio.run(_run())


if __name__ == "__main__":
    import sys

    sys.exit(main())
