# SPDX-License-Identifier: Apache-2.0
"""Postgres checkpoint saver that keeps no channel value in plaintext.

Imported only when ``AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres``, so the
memory backend never needs libpq. ``core/langgraph/checkpointer.py`` owns the
lifecycle; this module opens the pool, verifies the Alembic-managed schema and
builds the saver.

``AsyncPostgresSaver.aput`` writes ``str``/``int``/``float``/``bool``/``None``
channel values inline into the ``checkpoints.checkpoint`` JSONB column,
bypassing the serializer. Agent state carries the grant token as a string, so
the stock saver would persist a bearer credential in plaintext.
``SealedAsyncPostgresSaver`` sends every channel value through the (encrypting)
serializer into ``checkpoint_blobs`` instead. The override mirrors ``aput`` of
langgraph-checkpoint-postgres 3.1.2; ``tests/unit/test_langgraph_checkpointer.py``
pins that version so an upgrade is reviewed against this copy.
"""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from core.langgraph.checkpointer import CheckpointerUnavailableError

CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


class SealedAsyncPostgresSaver(AsyncPostgresSaver):
    """``AsyncPostgresSaver`` whose ``aput`` stores every channel value as a serialized blob."""

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = config["configurable"].copy()
        thread_id = configurable.pop("thread_id")
        checkpoint_ns = configurable.pop("checkpoint_ns")
        checkpoint_id = configurable.pop("checkpoint_id", None)

        copy = checkpoint.copy()
        copy["channel_values"] = copy["channel_values"].copy()
        next_config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

        # The only change from the library: primitives are not kept inline.
        blob_values: dict[str, Any] = {}
        for key, value in checkpoint["channel_values"].items():
            blob_values[key] = copy["channel_values"].pop(key)
            if isinstance(value, _DeltaSnapshot):
                copy["channel_values"][key] = True

        async with self._cursor(pipeline=True) as cur:
            if blob_versions := {k: v for k, v in new_versions.items() if k in blob_values}:
                await cur.executemany(
                    self.UPSERT_CHECKPOINT_BLOBS_SQL,
                    await asyncio.to_thread(
                        self._dump_blobs,
                        thread_id,
                        checkpoint_ns,
                        blob_values,
                        blob_versions,
                    ),
                )
            await cur.execute(
                self.UPSERT_CHECKPOINTS_SQL,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    checkpoint_id,
                    Jsonb(copy),
                    Jsonb(get_serializable_checkpoint_metadata(config, metadata)),
                ),
            )
        return next_config


async def _verify_schema(pool: AsyncConnectionPool[Any], expected_version: int) -> None:
    async with pool.connection() as conn, conn.cursor() as cur:
        for table in CHECKPOINT_TABLES:
            await cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
            row = await cur.fetchone()
            if not row or not row["present"]:
                raise CheckpointerUnavailableError(
                    "checkpoint_schema_missing",
                    f"table {table} is missing; run scripts/alembic_migrate.py",
                )
        await cur.execute("SELECT max(v) AS version FROM checkpoint_migrations")
        row = await cur.fetchone()
    version = row["version"] if row else None
    if version is None or version < expected_version:
        raise CheckpointerUnavailableError(
            "checkpoint_schema_stale",
            f"checkpoint_migrations is at {version}, the library needs {expected_version}",
        )
    if version > expected_version:
        raise CheckpointerUnavailableError(
            "checkpoint_schema_ahead",
            f"checkpoint_migrations is at {version}, the library knows {expected_version}",
        )


async def open_sealed_saver(
    *,
    conninfo: str,
    serde: SerializerProtocol,
    max_size: int,
    timeout_seconds: float,
) -> tuple[SealedAsyncPostgresSaver, AsyncConnectionPool[Any]]:
    """Open a connection pool, verify the checkpoint schema and build the saver.

    Raises ``CheckpointerUnavailableError``; the pool is closed on every failure.
    """
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        timeout=timeout_seconds,
        name="langgraph-checkpoints",
        check=AsyncConnectionPool.check_connection,
        # What AsyncPostgresSaver requires of its connections.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        try:
            await pool.open(wait=True, timeout=timeout_seconds)
            await _verify_schema(pool, len(AsyncPostgresSaver.MIGRATIONS) - 1)
        except (PoolTimeout, psycopg.Error, OSError) as exc:
            # The message can carry the host; never the password. Keep only the type.
            raise CheckpointerUnavailableError("checkpoint_store_unreachable", type(exc).__name__) from exc
    except BaseException:
        await pool.close()
        raise
    return SealedAsyncPostgresSaver(pool, serde=serde), pool
