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
from core.crypto.verify_all import _CACHE_INVALIDATORS, _SCANNERS
from core.database import async_session_factory

logger = structlog.get_logger()


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


async def _fetch_rows(
    session: Any,
    label: str,
    *,
    only_kid: str | None,
    active_kid: str,
    limit: int,
) -> list[tuple[Any, Any]]:
    """Return ``[(row_id, raw_value), …]`` for rows that need rewrapping.

    ``only_kid`` filter is applied client-side because the column may
    be JSONB (cannot pattern-match the embedded ciphertext in SQL
    portably). The ``limit`` is a load-control knob — we never read
    the entire table into memory at once.
    """
    table, column = _split_column_label(label)
    q = (
        f"SELECT id, {column} FROM {table} "  # nosec B608 — table/column from _SCANNERS module-level constants
        f"WHERE {column} IS NOT NULL "
        f"ORDER BY id "
        f"LIMIT :limit"
    )
    result = await session.execute(text(q), {"limit": limit})
    out: list[tuple[Any, Any]] = []
    for row in result.all():
        ct = _extract_ciphertext(label, row[1])
        kid = _stamp_kid(ct)
        if kid is None:
            continue
        if only_kid is not None and kid != only_kid:
            continue
        if kid == active_kid:
            continue
        out.append((row[0], row[1]))
    return out


async def _count_pending(
    session: Any,
    label: str,
    *,
    only_kid: str | None,
    active_kid: str,
) -> int:
    """Count rows whose stamp != active key id (matches _fetch_rows shape)."""
    table, column = _split_column_label(label)
    q = (
        f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"  # nosec B608 — table/column from _SCANNERS
    )
    result = await session.execute(text(q))
    n = 0
    for row in result.all():
        ct = _extract_ciphertext(label, row[1])
        kid = _stamp_kid(ct)
        if kid is None:
            continue
        if only_kid is not None and kid != only_kid:
            continue
        if kid != active_kid:
            n += 1
    return n


async def _update_row(
    session: Any,
    label: str,
    row_id: Any,
    new_value: Any,
) -> None:
    """Persist ``new_value`` on the named column for ``row_id``.

    JSONB columns get cast to JSON; TEXT columns pass through. We
    serialize the new_value to JSON for the JSONB path so SQLAlchemy
    binds the parameter correctly across asyncpg versions.
    """
    table, column = _split_column_label(label)
    if label.endswith("credentials_encrypted"):
        params = {"v": json.dumps(new_value), "id": str(row_id)}
        await session.execute(
            text(
                f"UPDATE {table} SET {column} = CAST(:v AS jsonb) "  # nosec B608 — table/column from _SCANNERS
                "WHERE id = :id"
            ),
            params,
        )
    else:
        params = {"v": new_value, "id": str(row_id)}
        await session.execute(
            text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),  # nosec B608 — table/column from _SCANNERS
            params,
        )


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
    columns = [
        (label, dotted)
        for label, dotted in _SCANNERS
        if only_column is None or label == only_column
    ]
    if only_column is not None and not columns:
        print(
            f"abort: --column={only_column!r} is not registered in "
            f"core.crypto.verify_all._SCANNERS",
            file=sys.stderr,
        )
        return 2

    total_pending = 0
    async with async_session_factory() as session:
        for label, _dotted in columns:
            n = await _count_pending(
                session, label, only_kid=only_kid, active_kid=active_kid
            )
            print(f"  {label}: {n} row(s) pending")
            total_pending += n
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
        while True:
            async with async_session_factory() as session:
                rows = await _fetch_rows(
                    session,
                    label,
                    only_kid=only_kid,
                    active_kid=active_kid,
                    limit=batch_size,
                )
                if not rows:
                    break
                touched_columns.add(label)
                for row_id, raw in rows:
                    ct = _extract_ciphertext(label, raw)
                    if ct is None:
                        continue
                    old_kid = _stamp_kid(ct)
                    try:
                        plaintext = decrypt_credential(ct)
                    except Exception as exc:
                        logger.exception(
                            "rewrap_decrypt_failed",
                            column=label,
                            row_id=str(row_id),
                            old_kid=old_kid,
                        )
                        print(
                            f"abort: decrypt failed on {label} row {row_id} "
                            f"(stamp={old_kid!r}): {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )
                        return 1
                    new_ct = encrypt_credential(plaintext)
                    new_value = _wrap_ciphertext_for_column(label, new_ct)
                    await _update_row(session, label, row_id, new_value)
                    written += 1
                    print(
                        json.dumps(
                            {
                                "ts": datetime.now(UTC).isoformat(),
                                "column": label,
                                "row_id": str(row_id),
                                "old_kid": old_kid,
                                "new_kid": active_kid,
                            }
                        )
                    )
                await session.commit()
            elapsed = time.monotonic() - started
            rate = written / elapsed if elapsed > 0 else 0.0
            print(
                f"  progress: {written}/{total_pending} "
                f"(rate={rate:.1f} rows/s)",
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
    columns = [
        (label, dotted)
        for label, dotted in _SCANNERS
        if only_column is None or label == only_column
    ]
    pending: dict[str, int] = {}
    async with async_session_factory() as session:
        for label, _dotted in columns:
            n = await _count_pending(
                session, label, only_kid=None, active_kid=active_kid
            )
            if n:
                pending[label] = n
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
        help=(
            "Limit to one registered column (e.g. "
            "'connector_configs.credentials_encrypted')."
        ),
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
        help=(
            "Exit 0 if every row is on the active key, 1 otherwise. "
            "No writes."
        ),
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
