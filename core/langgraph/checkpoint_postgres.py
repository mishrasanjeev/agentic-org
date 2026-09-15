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
serializer into ``checkpoint_blobs`` instead, and binds every serializer call
to the thread and namespace it reads or writes (``checkpoint_binding``).

The overrides mirror langgraph-checkpoint-postgres 3.1.2 and import a private
type of langgraph-checkpoint 4.2.0. Both are pinned exactly in pyproject and
requirements, and ``open_sealed_saver`` refuses to start
(``checkpoint_library_unverified``) with any other installed version.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from core.langgraph.checkpointer import CheckpointerUnavailableError, checkpoint_binding

CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")
# The exact versions SealedAsyncPostgresSaver was written and tested against.
VERIFIED_LIBRARY_VERSIONS = {"langgraph-checkpoint-postgres": "3.1.2", "langgraph-checkpoint": "4.2.0"}


def verify_library_versions() -> None:
    """Refuse to run the sealed saver on a checkpoint library it was not verified against."""
    for package, expected in VERIFIED_LIBRARY_VERSIONS.items():
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "missing"
        if installed != expected:
            raise CheckpointerUnavailableError(
                "checkpoint_library_unverified", f"{package} is {installed}, verified {expected}"
            )


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

    # Every serializer call happens inside a binding to the thread it belongs to.

    def _dump_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        values: dict[str, Any],
        versions: ChannelVersions,
    ) -> list[tuple[str, str, str, str, str, bytes | None]]:
        with checkpoint_binding(thread_id, checkpoint_ns):
            return super()._dump_blobs(thread_id, checkpoint_ns, values, versions)

    def _dump_writes(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        task_id: str,
        task_path: str,
        writes: Sequence[tuple[str, Any]],
    ) -> list[tuple[str, str, str, str, str, int, str, str, bytes]]:
        with checkpoint_binding(thread_id, checkpoint_ns):
            return super()._dump_writes(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, writes)

    async def _load_checkpoint_tuple(self, value: Any) -> CheckpointTuple:
        # asyncio.to_thread (used for the pending writes) copies this context.
        with checkpoint_binding(value["thread_id"], value["checkpoint_ns"]):
            return await super()._load_checkpoint_tuple(value)

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        configurable = config["configurable"]
        with checkpoint_binding(configurable["thread_id"], configurable.get("checkpoint_ns", "")):
            return await super().aget_delta_channel_history(config=config, channels=channels)


async def delete_threads_with_prefix(pool: AsyncConnectionPool[Any], prefix: str) -> int:
    """Delete every checkpoint row whose thread id starts with ``prefix``. Returns the threads removed."""
    async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        await cur.execute(
            "SELECT count(DISTINCT thread_id) AS threads FROM checkpoints WHERE starts_with(thread_id, %s)",
            (prefix,),
        )
        row = await cur.fetchone()
        for statement in (
            "DELETE FROM checkpoint_writes WHERE starts_with(thread_id, %s)",
            "DELETE FROM checkpoint_blobs WHERE starts_with(thread_id, %s)",
            "DELETE FROM checkpoints WHERE starts_with(thread_id, %s)",
        ):
            await cur.execute(statement, (prefix,))
    return int(row["threads"]) if row else 0


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
    verify_library_versions()
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
