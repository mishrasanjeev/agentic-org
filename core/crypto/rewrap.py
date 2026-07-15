"""``rewrap`` — re-encrypt vault ciphertext under the current active key.

Foundation #4 iteration 3 — the operational counterpart to
``verify_all`` (iter 2). The lifecycle:

    1. Operator adds a new keyring entry as the FIRST (active) entry.
    2. ``encrypt_credential`` writes new ciphertext stamped with the
       new active id; old ciphertext is still decryptable because the
       previous key is still in the keyring.
    3. **rewrap** (this module) walks every encrypted column and
       re-encrypts rows whose stamp != active id, so they get
       restamped with the new active id.
    4. ``verify_all --check=<old_id>`` returns 0 references → safe to
       drop the old keyring entry.

Without this loop you cannot retire an old key without leaving live
ciphertext orphaned (the same incident class as the SECRET_KEY
rotation captured in ``feedback_key_rotation_discipline.md``).

Usage::

    # Dry-run — count rows that would be rewrapped, no writes.
    python -m core.crypto.rewrap --dry-run

    # Rewrap everything not on the active key. Idempotent.
    python -m core.crypto.rewrap

    # Limit to one column (registered in core.crypto.verify_all).
    python -m core.crypto.rewrap --column=connector_configs.credentials_encrypted

    # Limit to ciphertext stamped with a specific old key id.
    python -m core.crypto.rewrap --key-id=legacy

    # Verify every row landed on the active key (read-only check).
    python -m core.crypto.rewrap --verify

The script exits 0 on success, 1 on any decrypt/encrypt error, 2 on
config errors (missing column, no keyring set, etc.). Errors surface
the first failing row so operators can pin its tenant and re-run.

Audit log: every UPDATE prints a single JSONL line to stdout with
``{ts, column, row_id, old_kid, new_kid}`` so an operator can pipe
the run into a file and have a complete record of what changed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from core.crypto.credential_vault import (
    _PREFIX_RE,
    _load_keyring,
    decrypt_credential,
    encrypt_credential,
)
from core.crypto.verify_all import (
    _CACHE_INVALIDATORS,
    _SCANNERS,
    TenantCompanyScope,
    discover_tenant_company_scopes,
    scopes_for_scanner,
)
from core.database import get_tenant_session

logger = structlog.get_logger()


class _RewrapRowError(RuntimeError):
    """Abort the current scoped transaction without committing partial work."""


def _active_kid() -> str:
    """Return the id of the FIRST keyring entry — the active encryption key."""
    keyring = _load_keyring()
    if not keyring:
        raise RuntimeError(
            "rewrap: keyring is empty — set AGENTICORG_VAULT_KEYRING "
            "or AGENTICORG_VAULT_KEY/AGENTICORG_SECRET_KEY before running."
        )
    return keyring[0][0]


def _stamp_kid(ciphertext: str | None) -> str | None:
    """Return the key id stamped in the ciphertext, or 'legacy' if unstamped.

    Mirrors the parser logic in ``verify_all.parse_ciphertext`` but only
    for the vault format (envelope rewrap is a separate concern handled
    by the envelope module).
    """
    if not isinstance(ciphertext, str) or not ciphertext.strip():
        return None
    m = _PREFIX_RE.match(ciphertext)
    if m:
        return m.group(1)
    return "legacy"


def _split_column_label(label: str) -> tuple[str, str]:
    """``connector_configs.credentials_encrypted`` -> (table, column)."""
    if "." not in label:
        raise ValueError(f"rewrap: column label missing '.': {label!r}")
    table, column = label.split(".", 1)
    return table, column


def _fire_cache_invalidators(label: str) -> None:
    """Run every cache-invalidator registered for ``label``.

    Each invalidator is a ``"module.path:callable"`` string. We import
    on demand so verify_all/rewrap stay importable in environments
    where the consuming module isn't loaded (CLI before app startup).
    Errors are logged but never block the rewrap — the worst case is
    callers get cached plaintext for up to one TTL window after
    rewrap completes, which is the same as the pre-iter-4 baseline.
    """
    for dotted in _CACHE_INVALIDATORS.get(label, []):
        try:
            mod_path, attr = dotted.split(":", 1)
            mod = __import__(mod_path, fromlist=[attr])
            callable_obj = getattr(mod, attr, None)
            if callable(callable_obj):
                callable_obj()
                logger.info(
                    "rewrap_cache_invalidated",
                    column=label,
                    invalidator=dotted,
                )
            else:
                logger.warning(
                    "rewrap_cache_invalidator_not_callable",
                    column=label,
                    invalidator=dotted,
                )
        # enterprise-gate: broad-except-ok reason=rewrap-cache-invalidator-failure-logs-after-durable-rewrap
        except Exception as exc:
            logger.warning(
                "rewrap_cache_invalidator_failed",
                column=label,
                invalidator=dotted,
                error=str(exc),
            )


# ──────────────────────────────────────────────────────────────────────
# Per-column helpers
# ──────────────────────────────────────────────────────────────────────
#
# The two registered encrypted columns ship two different storage
# shapes:
#
#   1. ``connector_configs.credentials_encrypted`` JSONB column with
#      ``{"_encrypted": "<ciphertext>"}``. Read + UPDATE need the JSON
#      shape preserved.
#   2. ``gstn_credentials.password_encrypted`` plain TEXT column.
#
# These two helpers normalise the load + store paths so the main loop
# can stay column-shape-agnostic.


def _extract_ciphertext(label: str, raw: Any) -> str | None:
    """Pull the actual vault ciphertext string out of the column value."""
    if raw is None:
        return None
    if label.endswith("credentials_encrypted"):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return raw  # bare ciphertext — treat it as such
        if isinstance(raw, dict):
            ct = raw.get("_encrypted")
            return ct if isinstance(ct, str) else None
        return None
    return raw if isinstance(raw, str) else None


def _wrap_ciphertext_for_column(label: str, ct: str) -> Any:
    """Reverse of ``_extract_ciphertext`` — wrap rewrapped ciphertext for UPDATE."""
    if label.endswith("credentials_encrypted"):
        return {"_encrypted": ct}
    return ct


# ──────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────


def _scope_sql(
    scope: TenantCompanyScope | None,
    *,
    exact_company_scope: bool,
) -> tuple[str, dict[str, Any]]:
    """Return explicit predicates in addition to the session's RLS GUCs."""

    if scope is None:
        return "", {}
    clauses = ["tenant_id = CAST(:tenant_id AS UUID)"]
    params: dict[str, Any] = {"tenant_id": str(scope.tenant_id)}
    if exact_company_scope:
        clauses.append("company_id IS NOT DISTINCT FROM CAST(:company_id AS UUID)")
        params["company_id"] = str(scope.company_id) if scope.company_id is not None else None
    return " AND " + " AND ".join(clauses), params


async def _fetch_rows(
    session: Any,
    label: str,
    *,
    only_kid: str | None,
    active_kid: str,
    limit: int,
    scope: TenantCompanyScope | None = None,
    exact_company_scope: bool = False,
    after_id: Any | None = None,
) -> tuple[list[tuple[Any, Any]], Any | None, bool]:
    """Return one bounded keyset page and its pending rows.

    ``only_kid`` filter is applied client-side because the column may
    be JSONB.  The cursor always advances across the raw page, even when all
    rows in that page are already active or do not match ``only_kid``.  This
    avoids both unbounded ``result.all()`` calls and LIMIT starvation.
    """
    table, column = _split_column_label(label)
    scope_sql, params = _scope_sql(scope, exact_company_scope=exact_company_scope)
    cursor_sql = ""
    if after_id is not None:
        cursor_sql = " AND id > CAST(:after_id AS UUID)"
        params["after_id"] = str(after_id)
    params["limit"] = limit
    q = (
        f"SELECT id, {column} FROM {table} "  # nosec B608 — table/column from _SCANNERS module-level constants
        f"WHERE {column} IS NOT NULL{scope_sql}{cursor_sql} "
        "ORDER BY id LIMIT CAST(:limit AS INTEGER)"
    )
    result = await session.execute(text(q), params)
    raw_rows = result.all()
    out: list[tuple[Any, Any]] = []
    for row in raw_rows:
        ct = _extract_ciphertext(label, row[1])
        kid = _stamp_kid(ct)
        if kid is None:
            continue
        if only_kid is not None and kid != only_kid:
            continue
        if kid == active_kid:
            continue
        out.append((row[0], row[1]))
    last_id = raw_rows[-1][0] if raw_rows else after_id
    return out, last_id, len(raw_rows) < limit


async def _count_pending(
    session: Any,
    label: str,
    *,
    only_kid: str | None,
    active_kid: str,
    scope: TenantCompanyScope | None = None,
    exact_company_scope: bool = False,
    page_size: int = 1000,
) -> int:
    """Count pending rows with bounded-memory keyset pagination."""

    n = 0
    after_id: Any | None = None
    while True:
        rows, last_id, exhausted = await _fetch_rows(
            session,
            label,
            only_kid=only_kid,
            active_kid=active_kid,
            limit=page_size,
            scope=scope,
            exact_company_scope=exact_company_scope,
            after_id=after_id,
        )
        n += len(rows)
        if exhausted:
            return n
        if last_id is None or last_id == after_id:
            raise RuntimeError(f"rewrap: keyset cursor did not advance for {label}")
        after_id = last_id


async def _update_row(
    session: Any,
    label: str,
    row_id: Any,
    new_value: Any,
    *,
    scope: TenantCompanyScope | None = None,
    exact_company_scope: bool = False,
) -> None:
    """Persist ``new_value`` on the named column for ``row_id``.

    JSONB columns get cast to JSON; TEXT columns pass through. We
    serialize the new_value to JSON for the JSONB path so SQLAlchemy
    binds the parameter correctly across asyncpg versions.
    """
    table, column = _split_column_label(label)
    scope_sql, scope_params = _scope_sql(scope, exact_company_scope=exact_company_scope)
    if label.endswith("credentials_encrypted"):
        params = {"v": json.dumps(new_value), "id": str(row_id), **scope_params}
        result = await session.execute(
            text(
                f"UPDATE {table} SET {column} = CAST(:v AS jsonb) "  # nosec B608 — table/column from _SCANNERS
                f"WHERE id = CAST(:id AS UUID){scope_sql}"
            ),
            params,
        )
    else:
        params = {"v": new_value, "id": str(row_id), **scope_params}
        result = await session.execute(
            text(f"UPDATE {table} SET {column} = :v WHERE id = CAST(:id AS UUID){scope_sql}"),  # nosec B608
            params,
        )
    if scope is not None and getattr(result, "rowcount", None) != 1:
        raise RuntimeError(
            "rewrap: scoped UPDATE did not affect exactly one row "
            f"({label}, row={row_id}, tenant={scope.tenant_id}, "
            f"company={scope.company_id})"
        )


async def _build_scope_plan(
    columns: list[tuple[str, str]],
) -> dict[str, tuple[list[TenantCompanyScope], bool]]:
    discovered_scopes = await discover_tenant_company_scopes()
    return {label: scopes_for_scanner(dotted, discovered_scopes) for label, dotted in columns}


# ──────────────────────────────────────────────────────────────────────
# Core runs
# ──────────────────────────────────────────────────────────────────────


async def run(
    *,
    only_column: str | None,
    only_kid: str | None,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Main rewrap loop. Returns process exit code."""
    active_kid = _active_kid()
    print(f"active key id: {active_kid}")
    columns = [(label, dotted) for label, dotted in _SCANNERS if only_column is None or label == only_column]
    if only_column is not None and not columns:
        print(
            f"abort: --column={only_column!r} is not registered in core.crypto.verify_all._SCANNERS",
            file=sys.stderr,
        )
        return 2
    if batch_size < 1:
        print("abort: --batch-size must be at least 1", file=sys.stderr)
        return 2

    try:
        scope_plan = await _build_scope_plan(columns)
    # enterprise-gate: broad-except-ok reason=scope-discovery-failure-returns-explicit-nonzero
    except Exception as exc:
        logger.exception("rewrap_scope_discovery_failed")
        print(
            f"abort: exact-scope discovery failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    total_pending = 0
    try:
        for label, _dotted in columns:
            label_pending = 0
            scanner_scopes, exact_company_scope = scope_plan[label]
            for scope in scanner_scopes:
                async with get_tenant_session(scope.tenant_id, scope.company_id) as session:
                    label_pending += await _count_pending(
                        session,
                        label,
                        only_kid=only_kid,
                        active_kid=active_kid,
                        scope=scope,
                        exact_company_scope=exact_company_scope,
                    )
            print(f"  {label}: {label_pending} row(s) pending")
            total_pending += label_pending
    # enterprise-gate: broad-except-ok reason=rewrap-scoped-count-failure-must-return-nonzero
    except Exception as exc:
        logger.exception("rewrap_scoped_count_failed")
        print(
            f"abort: exact-scope count failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(f"pending total: {total_pending}")
    if dry_run:
        print("dry-run: no writes performed")
        return 0
    if total_pending == 0:
        return 0

    written = 0
    started = time.monotonic()
    touched_columns: set[str] = set()
    for label, _dotted in columns:
        scanner_scopes, exact_company_scope = scope_plan[label]
        for scope in scanner_scopes:
            after_id: Any | None = None
            while True:
                try:
                    async with get_tenant_session(scope.tenant_id, scope.company_id) as session:
                        rows, last_id, exhausted = await _fetch_rows(
                            session,
                            label,
                            only_kid=only_kid,
                            active_kid=active_kid,
                            limit=batch_size,
                            scope=scope,
                            exact_company_scope=exact_company_scope,
                            after_id=after_id,
                        )
                        if rows:
                            touched_columns.add(label)
                        for row_id, raw in rows:
                            ct = _extract_ciphertext(label, raw)
                            if ct is None:
                                continue
                            old_kid = _stamp_kid(ct)
                            try:
                                plaintext = decrypt_credential(ct)
                            # enterprise-gate: broad-except-ok reason=rewrap-decrypt-failure-rolls-back-scoped-batch
                            except Exception as exc:
                                logger.exception(
                                    "rewrap_decrypt_failed",
                                    column=label,
                                    row_id=str(row_id),
                                    old_kid=old_kid,
                                    tenant_id=str(scope.tenant_id),
                                    company_id=(str(scope.company_id) if scope.company_id is not None else None),
                                )
                                raise _RewrapRowError(
                                    f"decrypt failed on {label} row {row_id} "
                                    f"(stamp={old_kid!r}): "
                                    f"{type(exc).__name__}: {exc}"
                                ) from exc
                            new_ct = encrypt_credential(plaintext)
                            new_value = _wrap_ciphertext_for_column(label, new_ct)
                            await _update_row(
                                session,
                                label,
                                row_id,
                                new_value,
                                scope=scope,
                                exact_company_scope=exact_company_scope,
                            )
                            written += 1
                            print(
                                json.dumps(
                                    {
                                        "ts": datetime.now(UTC).isoformat(),
                                        "column": label,
                                        "row_id": str(row_id),
                                        "tenant_id": str(scope.tenant_id),
                                        "company_id": (str(scope.company_id) if scope.company_id is not None else None),
                                        "old_kid": old_kid,
                                        "new_kid": active_kid,
                                    }
                                )
                            )
                # enterprise-gate: broad-except-ok reason=rewrap-scoped-batch-failure-rolls-back-and-returns-nonzero
                except Exception as exc:
                    logger.exception(
                        "rewrap_scoped_batch_failed",
                        column=label,
                        tenant_id=str(scope.tenant_id),
                        company_id=(str(scope.company_id) if scope.company_id is not None else None),
                    )
                    print(f"abort: {exc}", file=sys.stderr)
                    return 1
                if exhausted:
                    break
                if last_id is None or last_id == after_id:
                    print(
                        f"abort: keyset cursor did not advance for {label}",
                        file=sys.stderr,
                    )
                    return 1
                after_id = last_id
                elapsed = time.monotonic() - started
                rate = written / elapsed if elapsed > 0 else 0.0
                print(
                    f"  progress: {written}/{total_pending} (rate={rate:.1f} rows/s)",
                    file=sys.stderr,
                )
    # Foundation #4 iter 4: flush downstream credential caches for any
    # column we actually wrote to. Eager flush; cache re-warms on next
    # access. Failures don't propagate — see _fire_cache_invalidators.
    for label in sorted(touched_columns):
        _fire_cache_invalidators(label)
    return 0


async def verify(only_column: str | None) -> int:
    """Read-only check: every row's stamp == active key id, or exit 1."""
    active_kid = _active_kid()
    columns = [(label, dotted) for label, dotted in _SCANNERS if only_column is None or label == only_column]
    if only_column is not None and not columns:
        print(
            f"abort: --column={only_column!r} is not registered in core.crypto.verify_all._SCANNERS",
            file=sys.stderr,
        )
        return 2
    try:
        scope_plan = await _build_scope_plan(columns)
    # enterprise-gate: broad-except-ok reason=verify-scope-discovery-failure-returns-explicit-nonzero
    except Exception as exc:
        logger.exception("rewrap_verify_scope_discovery_failed")
        print(
            f"verify: exact-scope discovery failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    pending: dict[str, int] = {}
    try:
        for label, _dotted in columns:
            n = 0
            scanner_scopes, exact_company_scope = scope_plan[label]
            for scope in scanner_scopes:
                async with get_tenant_session(scope.tenant_id, scope.company_id) as session:
                    n += await _count_pending(
                        session,
                        label,
                        only_kid=None,
                        active_kid=active_kid,
                        scope=scope,
                        exact_company_scope=exact_company_scope,
                    )
            if n:
                pending[label] = n
    # enterprise-gate: broad-except-ok reason=rewrap-verify-scoped-read-failure-must-not-false-green
    except Exception as exc:
        logger.exception("rewrap_verify_scoped_read_failed")
        print(
            f"verify: exact-scope read failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if not pending:
        print(f"verify: every row is on active key {active_kid!r} (OK)")
        return 0
    for label, n in pending.items():
        print(
            f"verify: {label}: {n} row(s) NOT on active key {active_kid!r}",
            file=sys.stderr,
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--column",
        type=str,
        default=None,
        help=("Limit to one registered column (e.g. 'connector_configs.credentials_encrypted')."),
    )
    parser.add_argument(
        "--key-id",
        dest="key_id",
        type=str,
        default=None,
        help=(
            "Only rewrap rows whose stamp matches this id (e.g. 'legacy'). "
            "Default: rewrap every row whose stamp != active key id."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per UPDATE batch (default: 100).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending count without writing anything.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=("Exit 0 if every row is on the active key, 1 otherwise. No writes."),
    )
    args = parser.parse_args(argv)

    if args.verify:
        return asyncio.run(verify(args.column))
    return asyncio.run(
        run(
            only_column=args.column,
            only_kid=args.key_id,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
