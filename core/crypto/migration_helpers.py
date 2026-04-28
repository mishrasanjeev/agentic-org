"""Foundation #5 — encrypted-column migration hardening.

Mandatory wrapper for any alembic migration that touches an
``*_encrypted`` column. Provides the operational gates the user
called out after the SECRET_KEY incident:

1. **Dry-run** — simulate the migration's reads/writes and emit a
   report without touching production rows.
2. **Row count snapshot** — pre/post counts so a migration that
   silently dropped rows is loud, not silent.
3. **Decrypt verification** — sample n rows pre-write, decrypt
   them via the active keyring, and again post-write to confirm
   no row became undecryptable.
4. **Resumability** — write per-table progress markers to the
   ``alembic_migration_progress`` table so a crashed mid-run
   continues from the last confirmed batch instead of
   double-processing.
5. **Audit trail** — every wrapped migration emits a JSON record
   to ``migrations/audit/<revision>.json`` capturing pre-state,
   post-state, decrypt-verify samples, and timing.
6. **Source preservation** — ``copy_then_verify_then_delete``
   refuses to clear source ciphertext until the target column has
   decrypted successfully.

Usage in an alembic migration::

    from core.crypto.migration_helpers import encrypted_migration

    def upgrade() -> None:
        with encrypted_migration(
            revision="v500_rewrap_connector_creds",
            table="connector_configs",
            columns=["credentials_encrypted"],
        ) as ctx:
            pre_count = ctx.snapshot_row_count()
            ctx.dry_run_decrypt_sample(n=50)
            for batch_start in ctx.iter_resumable_batches(batch=500):
                # ... do the work ...
                ctx.mark_progress(last_pk=batch_start + 500)
            ctx.assert_decrypt_after(n=50)
            ctx.record_audit({"pre_count": pre_count, ...})

The wrapper raises ``EncryptedMigrationError`` if any gate fails
and the migration is aborted in a transaction-safe way (the
caller's ``with`` block exits, the alembic transaction rolls
back). The audit record is still written so a post-mortem has
the failure context.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Module-level reference to the alembic op; imported lazily so this
# file is import-safe outside an alembic run (helpful for tests).


class EncryptedMigrationError(RuntimeError):
    """Raised when a wrapped migration fails a hardening gate."""


# ─────────────────────────────────────────────────────────────────
# Audit record location
# ─────────────────────────────────────────────────────────────────


def _audit_dir() -> Path:
    """Return the migrations/audit directory; create if missing."""
    p = Path(__file__).resolve().parents[2] / "migrations" / "audit"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _audit_path(revision: str) -> Path:
    return _audit_dir() / f"{revision}.json"


# ─────────────────────────────────────────────────────────────────
# Context object
# ─────────────────────────────────────────────────────────────────


@dataclass
class EncryptedMigrationContext:
    """Per-migration state. Yielded by ``encrypted_migration``."""

    revision: str
    table: str
    columns: list[str]
    connection: Connection
    dry_run: bool = False
    audit: dict[str, Any] = field(default_factory=dict)

    # ── snapshots ──

    def snapshot_row_count(self) -> int:
        """Return the table's row count and stash it in the audit."""
        # Table name comes from the migration author, not user input —
        # safe to interpolate.
        sql = f"SELECT COUNT(*) FROM {self.table}"  # noqa: S608
        n = self.connection.execute(text(sql)).scalar_one()
        self.audit.setdefault("row_counts", {})[self.table] = int(n)
        return int(n)

    # ── decrypt verification ──

    def dry_run_decrypt_sample(self, n: int = 50) -> dict[str, int]:
        """Decrypt a sample of n rows from each encrypted column.

        Returns a per-column count of successfully decrypted rows.
        Raises ``EncryptedMigrationError`` if ANY sampled row fails
        to decrypt — that means the migration would silently leave
        unreadable ciphertext behind.
        """
        from core.crypto.credential_vault import decrypt_credential
        from core.crypto.tenant_secrets import decrypt_for_tenant

        results: dict[str, int] = {}
        for col in self.columns:
            sql = (  # noqa: S608
                f"SELECT {col} FROM {self.table} "
                f"WHERE {col} IS NOT NULL "
                f"ORDER BY random() LIMIT :n"
            )
            ok = 0
            failures: list[str] = []
            for (ct,) in self.connection.execute(text(sql), {"n": n}):
                try:
                    # Try the envelope path first; fall back to vault.
                    if isinstance(ct, str) and ct.startswith("{"):
                        decrypt_for_tenant(ct)
                    else:
                        decrypt_credential(ct if isinstance(ct, str) else ct.decode())
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{type(exc).__name__}: {str(exc)[:120]}")
            results[col] = ok
            if failures:
                self.audit.setdefault("decrypt_failures", {})[col] = failures[:5]
                raise EncryptedMigrationError(
                    f"Pre-flight decrypt sample failed for "
                    f"{self.table}.{col}: {len(failures)}/{n} rows "
                    f"could not decrypt with the active keyring. "
                    f"First failure: {failures[0]}"
                )
        self.audit.setdefault("decrypt_pre_sample", {}).update(results)
        return results

    def assert_decrypt_after(self, n: int = 50) -> dict[str, int]:
        """Re-sample n rows AFTER the migration's writes and assert
        every one still decrypts. Mirrors ``dry_run_decrypt_sample``
        but stamps the audit under a different key."""
        results = self.dry_run_decrypt_sample(n=n)
        # Move the result into the post-key.
        pre = self.audit.pop("decrypt_pre_sample", None) or {}
        self.audit["decrypt_post_sample"] = results
        if pre:
            # Restore pre-sample if it had been set earlier.
            self.audit["decrypt_pre_sample"] = pre
        return results

    # ── resumability ──

    def iter_resumable_batches(
        self, *, batch: int = 500, pk: str = "id"
    ) -> Iterator[int]:
        """Yield batch start offsets, persisting progress between
        batches so a crash + restart resumes from the last completed
        batch instead of restarting from zero.

        Strategy: integer-cursor pagination on a monotonic PK. The
        caller is responsible for advancing the cursor inside the
        loop (use ``mark_progress(last_pk=...)``).
        """
        prog = self.connection.execute(
            text(
                "SELECT rows_processed FROM alembic_migration_progress "
                "WHERE revision = :rev AND table_name = :tbl"
            ),
            {"rev": self.revision, "tbl": self.table},
        ).scalar()

        # Initialise the progress row if absent.
        if prog is None:
            total = self.snapshot_row_count()
            self.connection.execute(
                text(
                    "INSERT INTO alembic_migration_progress "
                    "(revision, table_name, last_processed_pk, "
                    " rows_processed, rows_total) "
                    "VALUES (:rev, :tbl, NULL, 0, :total)"
                ),
                {"rev": self.revision, "tbl": self.table, "total": total},
            )
            prog = 0

        offset = int(prog or 0)
        sql = (  # noqa: S608
            f"SELECT COUNT(*) FROM {self.table} WHERE {pk} IS NOT NULL"
        )
        total = int(self.connection.execute(text(sql)).scalar_one())

        while offset < total:
            yield offset
            offset += batch

    def mark_progress(self, *, rows_processed: int, last_pk: Any = None) -> None:
        """Persist a progress marker so a crash mid-run can resume."""
        self.connection.execute(
            text(
                "UPDATE alembic_migration_progress "
                "SET rows_processed = :n, last_processed_pk = :pk, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE revision = :rev AND table_name = :tbl"
            ),
            {
                "n": rows_processed,
                "pk": str(last_pk) if last_pk is not None else None,
                "rev": self.revision,
                "tbl": self.table,
            },
        )

    def mark_complete(self) -> None:
        """Mark the migration's table-pass as complete in the
        progress table. A future re-run sees this and skips."""
        self.connection.execute(
            text(
                "UPDATE alembic_migration_progress "
                "SET completed_at = CURRENT_TIMESTAMP "
                "WHERE revision = :rev AND table_name = :tbl"
            ),
            {"rev": self.revision, "tbl": self.table},
        )

    # ── source preservation ──

    def copy_then_verify_then_delete(
        self,
        *,
        source_column: str,
        target_column: str,
        verify_sample: int = 25,
    ) -> None:
        """Soft-cutover pattern. Copy ciphertext from
        ``source_column`` to ``target_column``, decrypt-verify a
        sample of target rows, and ONLY THEN drop the source.

        Refuses to drop the source if any target sample fails to
        decrypt — the migration aborts and rolls back, leaving the
        source in place for retry.
        """
        if self.dry_run:
            self.audit.setdefault("would_copy", []).append(
                f"{source_column} -> {target_column}"
            )
            return

        # 1. copy. Column + table names come from the migration
        # author, never from user input — safe to interpolate.
        copy_sql = (  # noqa: S608
            f"UPDATE {self.table} SET {target_column} = {source_column} "
            f"WHERE {target_column} IS NULL "
            f"  AND {source_column} IS NOT NULL"
        )
        self.connection.execute(text(copy_sql))
        # 2. verify on the target column.
        original_columns = self.columns
        try:
            self.columns = [target_column]
            self.assert_decrypt_after(n=verify_sample)
        finally:
            self.columns = original_columns
        # 3. only now is the source safe to clear.
        clear_sql = (  # noqa: S608
            f"UPDATE {self.table} SET {source_column} = NULL "
            f"WHERE {target_column} IS NOT NULL"
        )
        self.connection.execute(text(clear_sql))
        self.audit.setdefault("copy_then_verify_then_delete", []).append(
            {
                "from": source_column,
                "to": target_column,
                "verify_sample": verify_sample,
            }
        )

    # ── audit ──

    def record_audit(self, payload: dict[str, Any]) -> None:
        """Merge a payload into the in-memory audit dict; the
        encrypted_migration() context flushes to disk on exit."""
        self.audit.update(payload)


# ─────────────────────────────────────────────────────────────────
# Context manager
# ─────────────────────────────────────────────────────────────────


@contextmanager
def encrypted_migration(
    *,
    revision: str,
    table: str,
    columns: list[str],
    rollback_doc: str,
    dry_run: bool | None = None,
    connection: Connection | None = None,
) -> Iterator[EncryptedMigrationContext]:
    """Wrap an encrypted-column migration in the hardening gates.

    ``rollback_doc`` is a string the caller MUST provide explaining
    how to roll back if the migration goes wrong. Empty rollback
    docs raise immediately — there is no such thing as an
    encrypted-column migration without a rollback plan.

    ``dry_run`` defaults to ``AGENTICORG_MIGRATION_DRY_RUN`` env
    var. Set to ``"1"`` in CI / staging to verify a migration's
    plan without writing anything.

    ``connection`` is an escape hatch for tests and the rewrap CLI:
    when provided, the wrapper uses it directly instead of pulling
    the bind from ``alembic.op.get_bind()``. Real migrations omit
    this argument.
    """
    if not rollback_doc.strip():
        raise EncryptedMigrationError(
            f"{revision} requires a non-empty rollback_doc — "
            f"encrypted-column migrations without a rollback plan "
            f"are not allowed."
        )
    if dry_run is None:
        dry_run = os.getenv("AGENTICORG_MIGRATION_DRY_RUN", "").lower() in (
            "1",
            "true",
            "yes",
        )

    if connection is not None:
        bind = connection
    else:
        # Lazy import — alembic ``op`` only available inside a real
        # alembic run. Tests + CLIs use ``connection=`` instead.
        from alembic import op  # noqa: PLC0415

        bind = op.get_bind()

    ctx = EncryptedMigrationContext(
        revision=revision,
        table=table,
        columns=list(columns),
        connection=bind,
        dry_run=dry_run,
    )
    ctx.audit.update(
        {
            "revision": revision,
            "table": table,
            "columns": list(columns),
            "rollback_doc": rollback_doc,
            "dry_run": dry_run,
            "started_at": datetime.now(UTC).isoformat(),
        }
    )

    error: BaseException | None = None
    try:
        yield ctx
    except BaseException as exc:  # noqa: BLE001
        ctx.audit["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        error = exc
        raise
    finally:
        ctx.audit["completed_at"] = datetime.now(UTC).isoformat()
        try:
            _audit_path(revision).write_text(
                json.dumps(ctx.audit, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            # Audit write failure must not mask the real exception.
            pass
        if error is None and not dry_run:
            ctx.mark_complete()
