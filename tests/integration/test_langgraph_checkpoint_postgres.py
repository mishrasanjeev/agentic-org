# SPDX-License-Identifier: Apache-2.0
"""Postgres checkpoint store against a real database (PRD F-2).

* The v6z22 migration builds exactly the catalog ``AsyncPostgresSaver.setup()``
  builds for the pinned library version.
* A graph paused for approval is stored encrypted, with no plaintext channel
  value, and resumes through a newly opened store.
* A missing or stale schema and tampered checkpoint data are refused.

Each test works in its own scratch schema (``search_path`` on the connection)
and drops it afterwards. Requires ``AGENTICORG_DB_URL``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import uuid
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command

from core.langgraph import checkpointer as cp
from core.test_doubles.scripted_model import final

DB_URL = os.getenv("AGENTICORG_DB_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z22_langgraph_checkpoints.py"
GRANT_SENTINEL = "grant-sentinel-integration-0001"


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg's async driver cannot use the Windows proactor loop.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=factory)


def _conninfo(schema: str) -> str:
    base = cp.checkpoint_conninfo(DB_URL or "")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options={quote(f'-csearch_path={schema}')}"


def _sync_conn() -> Any:
    import psycopg

    return psycopg.connect(cp.checkpoint_conninfo(DB_URL or ""), autocommit=True)


def _in_schema(template: str, schema: str) -> Any:
    from psycopg import sql

    return sql.SQL(template).format(schema=sql.Identifier(schema))


@pytest.fixture
def scratch_schema() -> Iterator[str]:
    name = f"lg_ckpt_{uuid.uuid4().hex[:10]}"
    with _sync_conn() as conn:
        conn.execute(_in_schema("CREATE SCHEMA {schema}", name))
    try:
        yield name
    finally:
        with _sync_conn() as conn:
            conn.execute(_in_schema("DROP SCHEMA {schema} CASCADE", name))


def _apply_migration(schema: str) -> None:
    spec = importlib.util.spec_from_file_location("v6_z22_langgraph_checkpoints", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with _sync_conn() as conn, conn.transaction():
        conn.execute(_in_schema("SET LOCAL search_path TO {schema}", schema))
        migration.op = type("_Op", (), {"execute": staticmethod(lambda sql: conn.execute(sql))})
        migration.upgrade()


def _catalog(schema: str) -> dict[str, Any]:
    with _sync_conn() as conn:
        columns = conn.execute(
            "SELECT table_name, column_name, ordinal_position, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = %s ORDER BY table_name, ordinal_position",
            (schema,),
        ).fetchall()
        keys = conn.execute(
            "SELECT tc.table_name, tc.constraint_type, kcu.column_name, kcu.ordinal_position "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
            "WHERE tc.table_schema = %s ORDER BY 1, 2, 4",
            (schema,),
        ).fetchall()
        indexes = conn.execute(
            "SELECT tablename, indexname, replace(indexdef, %s, '') FROM pg_indexes "
            "WHERE schemaname = %s ORDER BY 1, 2",
            (f"{schema}.", schema),
        ).fetchall()
        stamped = conn.execute(_in_schema("SELECT v FROM {schema}.checkpoint_migrations ORDER BY v", schema))
        versions = [row[0] for row in stamped]
    return {"columns": columns, "keys": keys, "indexes": indexes, "versions": versions}


def test_migration_catalog_matches_library_setup(scratch_schema: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    reference = f"{scratch_schema}_ref"
    with _sync_conn() as conn:
        conn.execute(_in_schema("CREATE SCHEMA {schema}", reference))
    try:
        _apply_migration(scratch_schema)

        async def _setup() -> None:
            async with AsyncPostgresSaver.from_conn_string(_conninfo(reference)) as saver:
                await saver.setup()

        _run(_setup())

        ours, library = _catalog(scratch_schema), _catalog(reference)
        assert {row[0] for row in ours["columns"]} == {
            "checkpoint_migrations",
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
        }
        assert ours == library
        assert ours["versions"] == list(range(len(AsyncPostgresSaver.MIGRATIONS)))
    finally:
        with _sync_conn() as conn:
            conn.execute(_in_schema("DROP SCHEMA {schema} CASCADE", reference))


def _use_store(monkeypatch: pytest.MonkeyPatch, schema: str) -> None:
    monkeypatch.setattr(cp.settings, "langgraph_checkpointer", "postgres")
    monkeypatch.setattr(cp.settings, "langgraph_checkpoint_db_url", _conninfo(schema))
    monkeypatch.setattr(cp.settings, "langgraph_checkpoint_connect_timeout_seconds", 5.0)
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "it1:integration-checkpoint-key")
    monkeypatch.setattr(cp, "_postgres_store", None)
    monkeypatch.setattr(cp, "_open_lock", None)


def _initial_state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="settle invoice INV-0001")],
        "agent_id": "agent-ckpt-it",
        "agent_type": "ap_processor",
        "domain": "finance",
        "tenant_id": "00000000-0000-0000-0000-00000000c0de",
        "grant_token": GRANT_SENTINEL,
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


def _graph() -> Any:
    from core.langgraph.agent_graph import build_agent_graph

    return build_agent_graph(
        system_prompt="scripted", authorized_tools=[], confidence_floor=0.5, hitl_condition="total > 500000"
    )


def test_paused_run_is_stored_encrypted_and_resumes_from_a_new_store(
    scratch_schema: str, monkeypatch: pytest.MonkeyPatch, scripted_model: Any
) -> None:
    _apply_migration(scratch_schema)
    _use_store(monkeypatch, scratch_schema)
    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    config = {"configurable": {"thread_id": f"it-{uuid.uuid4().hex}"}}

    async def _pause() -> dict[str, Any]:
        try:
            saver = await cp.get_checkpointer()
            assert type(saver).__name__ == "SealedAsyncPostgresSaver"
            return await _graph().compile(checkpointer=saver).ainvoke(_initial_state(), config)
        finally:
            await cp.close_checkpointer()

    paused = _run(_pause())
    assert paused["__interrupt__"][0].value["type"] == "hitl_approval"

    with _sync_conn() as conn:
        conn.execute(_in_schema("SET search_path TO {schema}", scratch_schema))
        checkpoint_json = " ".join(row[0] for row in conn.execute("SELECT checkpoint::text FROM checkpoints"))
        metadata_json = " ".join(row[0] for row in conn.execute("SELECT metadata::text FROM checkpoints"))
        blob_types = {row[0] for row in conn.execute("SELECT type FROM checkpoint_blobs")}
        write_types = {row[0] for row in conn.execute("SELECT type FROM checkpoint_writes")}
        raw_blobs = b"".join(
            bytes(row[0]) for row in conn.execute("SELECT blob FROM checkpoint_blobs WHERE blob IS NOT NULL")
        )
        inline_values = [row[0] for row in conn.execute("SELECT checkpoint->'channel_values' FROM checkpoints")]
    # No channel value is kept inline (the stock saver would inline every str/int/bool).
    assert inline_values and all(values == {} for values in inline_values)
    # Distinctive strings only: numbers can occur by chance in ids, timestamps and ciphertext.
    for secret in (GRANT_SENTINEL, "INV-0001", "ap_processor"):
        assert secret not in checkpoint_json
        assert secret not in metadata_json
        assert secret.encode() not in raw_blobs
    assert blob_types and all(t == "empty" or t.endswith("+fernet") for t in blob_types)
    assert write_types and all(t.endswith("+fernet") for t in write_types)

    async def _resume() -> dict[str, Any]:
        # A new pool, saver and graph: nothing from the pausing store is reused.
        try:
            saver = await cp.get_checkpointer()
            compiled = _graph().compile(checkpointer=saver)
            state = await compiled.aget_state(config)
            assert state.next == ("hitl_gate",)
            assert state.values["grant_token"] == GRANT_SENTINEL
            return await compiled.ainvoke(Command(resume={"action": "reject", "reason": "over limit"}), config)
        finally:
            await cp.close_checkpointer()

    resumed = _run(_resume())
    assert resumed["status"] == "failed"
    assert "over limit" in resumed["error"]


def test_unencrypted_checkpoint_rows_are_refused(
    scratch_schema: str, monkeypatch: pytest.MonkeyPatch, scripted_model: Any
) -> None:
    _apply_migration(scratch_schema)
    _use_store(monkeypatch, scratch_schema)
    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    config = {"configurable": {"thread_id": f"it-{uuid.uuid4().hex}"}}

    async def _pause() -> None:
        try:
            await _graph().compile(checkpointer=await cp.get_checkpointer()).ainvoke(_initial_state(), config)
        finally:
            await cp.close_checkpointer()

    _run(_pause())
    plain_type, plain_blob = cp.JsonPlusSerializer().dumps_typed({"status": "completed"})
    with _sync_conn() as conn:
        conn.execute(
            _in_schema("UPDATE {schema}.checkpoint_blobs SET type = %s, blob = %s WHERE channel = %s", scratch_schema),
            (plain_type, plain_blob, "status"),
        )

    async def _load() -> None:
        try:
            await _graph().compile(checkpointer=await cp.get_checkpointer()).aget_state(config)
        finally:
            await cp.close_checkpointer()

    with pytest.raises(cp.CheckpointIntegrityError) as exc_info:
        _run(_load())
    assert exc_info.value.reason == "checkpoint_not_encrypted"


def _count_sql(schema: str, table: str) -> Any:
    from psycopg import sql

    return sql.SQL("SELECT count(*) FROM {schema}.{table} WHERE thread_id = %s").format(
        schema=sql.Identifier(schema), table=sql.Identifier(table)
    )


TENANT_A = "0000000a-0000-4000-8000-00000000000a"
TENANT_B = "0000000b-0000-4000-8000-00000000000b"


def _pause_two_tenants(scratch_schema: str, monkeypatch: pytest.MonkeyPatch, scripted_model: Any) -> tuple[dict, dict]:
    _apply_migration(scratch_schema)
    _use_store(monkeypatch, scratch_schema)
    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})] * 2)
    config_a = {"configurable": {"thread_id": f"tenant:{TENANT_A}:run:{uuid.uuid4().hex}"}}
    config_b = {"configurable": {"thread_id": f"tenant:{TENANT_B}:run:{uuid.uuid4().hex}"}}

    async def _pause() -> None:
        try:
            saver = await cp.get_checkpointer()
            for config in (config_a, config_b):
                await _graph().compile(checkpointer=saver).ainvoke(_initial_state(), config)
        finally:
            await cp.close_checkpointer()

    _run(_pause())
    return config_a, config_b


def test_blob_copied_to_another_tenants_thread_is_refused(
    scratch_schema: str, monkeypatch: pytest.MonkeyPatch, scripted_model: Any
) -> None:
    config_a, config_b = _pause_two_tenants(scratch_schema, monkeypatch, scripted_model)
    with _sync_conn() as conn:
        # Tenant A's encrypted grant token written over tenant B's, byte for byte.
        conn.execute(
            _in_schema(
                "UPDATE {schema}.checkpoint_blobs AS b SET type = a.type, blob = a.blob "
                "FROM {schema}.checkpoint_blobs AS a "
                "WHERE a.thread_id = %s AND b.thread_id = %s "
                "AND a.channel = 'grant_token' AND b.channel = 'grant_token'",
                scratch_schema,
            ),
            (config_a["configurable"]["thread_id"], config_b["configurable"]["thread_id"]),
        )

    async def _load(config: dict) -> Any:
        try:
            return await _graph().compile(checkpointer=await cp.get_checkpointer()).aget_state(config)
        finally:
            await cp.close_checkpointer()

    with pytest.raises(cp.CheckpointIntegrityError) as exc_info:
        _run(_load(config_b))
    assert exc_info.value.reason == "checkpoint_binding_mismatch"
    # The source thread is untouched and still loads.
    assert _run(_load(config_a)).values["grant_token"] == GRANT_SENTINEL


def test_tenant_offboarding_deletes_only_that_tenants_checkpoints(
    scratch_schema: str, monkeypatch: pytest.MonkeyPatch, scripted_model: Any
) -> None:
    config_a, config_b = _pause_two_tenants(scratch_schema, monkeypatch, scripted_model)

    async def _delete() -> int:
        try:
            return await cp.delete_tenant_checkpoints(TENANT_A)
        finally:
            await cp.close_checkpointer()

    assert _run(_delete()) == 1
    with _sync_conn() as conn:
        counts = {
            table: {
                thread: conn.execute(
                    _count_sql(scratch_schema, table),
                    (config["configurable"]["thread_id"],),
                ).fetchone()[0]
                for thread, config in (("a", config_a), ("b", config_b))
            }
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
        }
    assert all(per_thread["a"] == 0 for per_thread in counts.values()), counts
    assert all(per_thread["b"] > 0 for per_thread in counts.values()), counts


def test_missing_or_stale_checkpoint_schema_is_refused(scratch_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_store(monkeypatch, scratch_schema)

    async def _open() -> None:
        try:
            await cp.get_checkpointer()
        finally:
            await cp.close_checkpointer()

    with pytest.raises(cp.CheckpointerUnavailableError) as missing:
        _run(_open())
    assert missing.value.reason == "checkpoint_schema_missing"

    _apply_migration(scratch_schema)
    with _sync_conn() as conn:
        conn.execute(_in_schema("DELETE FROM {schema}.checkpoint_migrations WHERE v = 9", scratch_schema))
    with pytest.raises(cp.CheckpointerUnavailableError) as stale:
        _run(_open())
    assert stale.value.reason == "checkpoint_schema_stale"

    with _sync_conn() as conn:
        conn.execute(_in_schema("INSERT INTO {schema}.checkpoint_migrations (v) VALUES (9), (10)", scratch_schema))
    with pytest.raises(cp.CheckpointerUnavailableError) as ahead:
        _run(_open())
    assert ahead.value.reason == "checkpoint_schema_ahead"
    assert cp._postgres_store is None
