"""Backfill ``knowledge_documents.embedding_bge_m3`` from row content.

PR-A of the BGE-M3 embedding upgrade. Reads every row whose target
column is ``NULL``, re-embeds the source text with ``BAAI/bge-m3``,
and writes a 1024-dim vector to ``embedding_bge_m3``. Idempotent —
re-running picks up only the rows still ``NULL``.

The cutover (PR-B, ``v496_bge_m3_cutover``) refuses to drop the old
``embedding`` column unless this script reports zero rows where
``embedding IS NOT NULL AND embedding_bge_m3 IS NULL`` — that gate
prevents the SECRET_KEY-style "rotated and orphaned" incident
captured in ``feedback_key_rotation_discipline.md`` from recurring
for embeddings.

Usage::

    # Dry-run — print how many rows would be touched, no writes.
    python -m core.embeddings_backfill --dry-run

    # Real run, default batch size 64, all tenants.
    python -m core.embeddings_backfill

    # Limit to one tenant + smaller batch (useful in low-RAM envs).
    python -m core.embeddings_backfill --tenant=<uuid> --batch-size=16

    # Verify completeness (exit 0 if backfill complete, 1 otherwise).
    python -m core.embeddings_backfill --verify

The CLI exits non-zero on any embedding/db error so a CI / cron run
fails loudly instead of partially completing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import text

from core.database import async_session_factory
from core.embeddings import embed_with, model_dim

logger = structlog.get_logger()

TARGET_MODEL = "BAAI/bge-m3"
TARGET_COLUMN = "embedding_bge_m3"
SOURCE_TEXT_COLUMN = "content"  # knowledge_documents.content holds the original text


def _validate_target_dim() -> None:
    """Fail loudly if the target model's dim doesn't match the column.

    The migration created ``vector(1024)``. If a future model swap
    bumps bge-m3 to a different dim, the INSERT will silently
    truncate or 500 — neither is acceptable. Pin the contract here.
    """
    if (dim := model_dim(TARGET_MODEL)) != 1024:
        raise SystemExit(
            f"backfill aborted: TARGET_MODEL={TARGET_MODEL} "
            f"reports dim={dim}, but the column is vector(1024). "
            "Either fix _MODEL_DIMS or change TARGET_COLUMN."
        )


async def _column_exists(session: Any, column: str) -> bool:
    """Check that the target column exists before any work happens.

    Without this guard the script would hit a 500 on the first batch
    and operators wouldn't know whether the migration was missed or
    the rows were just empty.
    """
    result = await session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'knowledge_documents' AND column_name = :col"
        ),
        {"col": column},
    )
    return result.scalar_one_or_none() is not None


async def _count_pending(session: Any, tenant_id: uuid.UUID | None) -> int:
    """Count rows where the target column is still NULL (and source text exists).

    The column names interpolated into the SQL are module-level
    constants; ruff S608 is suppressed for this file in pyproject.toml.
    """
    q = (
        f"SELECT COUNT(*) FROM knowledge_documents "  # nosec B608 — TARGET_COLUMN/SOURCE_TEXT_COLUMN are module-level constants, not user input
        f"WHERE {TARGET_COLUMN} IS NULL AND {SOURCE_TEXT_COLUMN} IS NOT NULL "
        f"AND length({SOURCE_TEXT_COLUMN}) > 0"
    )
    params: dict[str, Any] = {}
    if tenant_id is not None:
        q += " AND tenant_id = :tid"
        params["tid"] = str(tenant_id)
    result = await session.execute(text(q), params)
    return int(result.scalar_one() or 0)


async def _count_orphan_risk(session: Any, tenant_id: uuid.UUID | None) -> int:
    """Count rows where the OLD column is set but the NEW column is not.

    These are the rows the cutover would orphan if it ran today.
    """
    q = (
        f"SELECT COUNT(*) FROM knowledge_documents "  # nosec B608 — TARGET_COLUMN is a module-level constant, not user input
        f"WHERE embedding IS NOT NULL AND {TARGET_COLUMN} IS NULL"
    )
    params: dict[str, Any] = {}
    if tenant_id is not None:
        q += " AND tenant_id = :tid"
        params["tid"] = str(tenant_id)
    result = await session.execute(text(q), params)
    return int(result.scalar_one() or 0)


async def _fetch_batch(
    session: Any, tenant_id: uuid.UUID | None, batch_size: int
) -> list[tuple[uuid.UUID, str]]:
    """Fetch the next batch of (id, content) pairs needing backfill.

    Order by id so progress is monotonic across runs and we never
    accidentally re-process the same row twice in a single CLI run.
    """
    q = (
        f"SELECT id, {SOURCE_TEXT_COLUMN} FROM knowledge_documents "  # nosec B608 — TARGET_COLUMN/SOURCE_TEXT_COLUMN are module-level constants, not user input
        f"WHERE {TARGET_COLUMN} IS NULL AND {SOURCE_TEXT_COLUMN} IS NOT NULL "
        f"AND length({SOURCE_TEXT_COLUMN}) > 0"
    )
    params: dict[str, Any] = {"limit": batch_size}
    if tenant_id is not None:
        q += " AND tenant_id = :tid"
        params["tid"] = str(tenant_id)
    q += " ORDER BY id LIMIT :limit"
    result = await session.execute(text(q), params)
    return [(row[0], row[1]) for row in result.all()]


async def _write_batch(
    session: Any, ids: list[uuid.UUID], vectors: list[list[float]]
) -> None:
    """Persist a batch of vectors. pgvector accepts the literal text form."""
    if len(ids) != len(vectors):
        raise RuntimeError(
            f"_write_batch length mismatch: {len(ids)} ids vs {len(vectors)} vectors"
        )
    for row_id, vec in zip(ids, vectors, strict=True):
        # pgvector accepts a string literal "[v1,v2,...]" — encoded via
        # cast(:vec AS vector) so we don't risk arg-binding quirks.
        await session.execute(
            text(
                f"UPDATE knowledge_documents SET {TARGET_COLUMN} = "  # nosec B608 — TARGET_COLUMN is a module-level constant, not user input
                "CAST(:vec AS vector) WHERE id = :id"
            ),
            {"vec": "[" + ",".join(repr(float(x)) for x in vec) + "]", "id": str(row_id)},
        )
    await session.commit()


async def _verify(tenant_id: uuid.UUID | None) -> int:
    """Return non-zero if the cutover would orphan rows.

    Used by the PR-B Alembic migration's pre-flight gate and by
    operators answering "is the backfill done?".
    """
    async with async_session_factory() as session:
        if not await _column_exists(session, TARGET_COLUMN):
            print(
                f"verify: column {TARGET_COLUMN} does not exist — "
                "PR-A migration v495_bge_m3_column has not been applied",
                file=sys.stderr,
            )
            return 2
        orphan_risk = await _count_orphan_risk(session, tenant_id)
        if orphan_risk == 0:
            print("verify: 0 rows would be orphaned by cutover (OK)")
            return 0
        print(
            f"verify: {orphan_risk} rows have embedding set but "
            f"{TARGET_COLUMN} is still NULL — backfill is not complete",
            file=sys.stderr,
        )
        return 1


async def run(
    tenant_id: uuid.UUID | None,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Main backfill loop. Returns process exit code."""
    _validate_target_dim()
    async with async_session_factory() as session:
        if not await _column_exists(session, TARGET_COLUMN):
            print(
                f"abort: column {TARGET_COLUMN} does not exist — "
                "apply migration v495_bge_m3_column first",
                file=sys.stderr,
            )
            return 2
        pending = await _count_pending(session, tenant_id)
    print(f"pending rows: {pending} (model={TARGET_MODEL}, batch={batch_size})")
    if dry_run:
        print("dry-run: no writes performed")
        return 0
    if pending == 0:
        return 0

    written = 0
    started = time.monotonic()
    while True:
        async with async_session_factory() as session:
            batch = await _fetch_batch(session, tenant_id, batch_size)
            if not batch:
                break
            ids = [row[0] for row in batch]
            texts = [row[1] for row in batch]
            try:
                vectors = embed_with(TARGET_MODEL, texts)
            except Exception as exc:
                logger.exception("backfill_embed_failed", error=str(exc))
                # Surfacing the first failure is more useful than
                # silently skipping — operators can fix the bad row
                # (or pin its tenant) and re-run.
                return 1
            await _write_batch(session, ids, vectors)
            written += len(ids)
            elapsed = time.monotonic() - started
            rate = written / elapsed if elapsed > 0 else 0.0
            print(
                f"  batch wrote {len(ids)} rows  "
                f"(total={written}/{pending}, "
                f"rate={rate:.1f} rows/s)"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant",
        type=str,
        default=None,
        help="Limit backfill to a single tenant_id (UUID).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Rows per embedding+UPDATE batch (default: 64).",
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
            "Exit 0 if no rows would be orphaned by the PR-B cutover, "
            "1 otherwise."
        ),
    )
    args = parser.parse_args(argv)

    tenant = uuid.UUID(args.tenant) if args.tenant else None

    if args.verify:
        return asyncio.run(_verify(tenant))
    return asyncio.run(run(tenant, args.batch_size, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
