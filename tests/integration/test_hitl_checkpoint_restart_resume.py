# SPDX-License-Identifier: Apache-2.0
"""Interrupt, restart the process, resume (PRD F-2, §8.3 integration matrix).

This process runs an agent to its approval interrupt with the Postgres
checkpoint store and the scripted model, records the server-generated thread
on the approval row, and closes its store. A separate interpreter
(``checkpoint_resume_child.py``) then finds the thread through the approval
row and resumes the run to completion. Nothing but the database connects the
two processes.

Requires ``AGENTICORG_DB_URL``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.langgraph import checkpointer as cp
from core.langgraph.thread_ids import new_thread_id, thread_belongs_to_tenant
from core.test_doubles.scripted_model import final

DB_URL = os.getenv("AGENTICORG_DB_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")

REPO = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).resolve().parent / "checkpoint_resume_child.py"
KEYRING = "rst1:integration-restart-checkpoint-key"


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=factory)


def _sync_conn() -> Any:
    import psycopg

    return psycopg.connect(cp.checkpoint_conninfo(DB_URL or ""), autocommit=True)


def _prepare_schema() -> None:
    from sqlalchemy import create_engine

    import core.models  # noqa: F401 - registers every ORM model
    from core.models.base import BaseModel

    engine = create_engine((DB_URL or "").replace("+asyncpg", ""))
    try:
        BaseModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    path = REPO / "migrations" / "versions" / "v6_z22_langgraph_checkpoints.py"
    spec = importlib.util.spec_from_file_location("v6_z22_langgraph_checkpoints", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with _sync_conn() as conn, conn.transaction():
        migration.op = type("_Op", (), {"execute": staticmethod(lambda sql: conn.execute(sql))})
        migration.upgrade()


def _seed(tenant_id: uuid.UUID, agent_id: uuid.UUID | None = None) -> None:
    with _sync_conn() as conn:
        conn.execute(
            "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
            "VALUES (%s, %s, %s, 'enterprise', 'IN', '{}'::jsonb) ON CONFLICT (id) DO NOTHING",
            (tenant_id, f"restart-{tenant_id.hex[:8]}", f"restart-{tenant_id.hex}"),
        )
        if agent_id is not None:
            conn.execute(
                "INSERT INTO agents (id, tenant_id, name, agent_type, domain, system_prompt_ref, prompt_variables, "
                "llm_model, llm_config, confidence_floor, hitl_condition, max_retries, retry_backoff, "
                "authorized_tools, visibility, status, version, shadow_min_samples, shadow_accuracy_floor, "
                "shadow_sample_count, shadow_scored_sample_count, shadow_feedback_count, cost_controls, scaling, "
                "tags, config, routing_filter, is_builtin, org_level) "
                "VALUES (%s, %s, 'Restart agent', 'ap_processor', 'finance', 'inline://restart', '{}'::jsonb, "
                "'scripted', '{}'::jsonb, 0.5, 'total > 500000', 3, 'exponential', '[]'::jsonb, 'tenant', "
                "'active', '1.0.0', 10, 0.8, 0, 0, 0, '{}'::jsonb, '{}'::jsonb, '{}', '{}'::jsonb, '{}'::jsonb, "
                "false, 0)",
                (agent_id, tenant_id),
            )


def _cleanup(tenant_ids: list[uuid.UUID], thread_id: str | None) -> None:
    with _sync_conn() as conn:
        for tid in tenant_ids:
            conn.execute("DELETE FROM hitl_queue WHERE tenant_id = %s", (tid,))
            conn.execute("DELETE FROM agents WHERE tenant_id = %s", (tid,))
            conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))
        if thread_id:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))  # noqa: S608


@pytest.fixture
def store_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import core.database as db

    _prepare_schema()
    env = {
        "AGENTICORG_DB_URL": DB_URL or "",
        "AGENTICORG_LANGGRAPH_CHECKPOINTER": "postgres",
        "AGENTICORG_LANGGRAPH_CHECKPOINT_DB_URL": cp.checkpoint_conninfo(DB_URL or ""),
        "AGENTICORG_VAULT_KEYRING": KEYRING,
        "AGENTICORG_ENV": "test",
    }
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", KEYRING)
    monkeypatch.setattr(cp.settings, "langgraph_checkpointer", "postgres")
    monkeypatch.setattr(cp.settings, "langgraph_checkpoint_db_url", env["AGENTICORG_LANGGRAPH_CHECKPOINT_DB_URL"])
    monkeypatch.setattr(cp, "_postgres_store", None)
    monkeypatch.setattr(cp, "_open_lock", None)
    # Connections must not outlive the event loop that opened them.
    engine = create_async_engine(DB_URL or "", poolclass=NullPool)
    monkeypatch.setattr(db, "engine", engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    yield env


def _child(env: dict[str, str], tenant_id: uuid.UUID, hitl_id: uuid.UUID, decision: dict[str, Any]) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(CHILD), str(tenant_id), str(hitl_id), json.dumps(decision)],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, **env},
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


async def _pause_run(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> tuple[dict[str, Any], uuid.UUID]:
    from core.database import get_tenant_session
    from core.langgraph import runner
    from core.models.hitl import HITLQueue

    thread_id = new_thread_id(tenant_id)
    try:
        with (
            patch("core.billing.metering.gate_agent_run", new=AsyncMock(return_value=None)),
            patch("core.billing.metering.meter_agent_run", new=AsyncMock(return_value=None)),
            patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        ):
            result = await runner.run_agent(
                agent_id=str(agent_id),
                agent_type="ap_processor",
                domain="finance",
                tenant_id=str(tenant_id),
                system_prompt="scripted",
                authorized_tools=[],
                task_input={"action": "settle", "inputs": {"invoice": "INV-0001"}, "context": {}},
                confidence_floor=0.5,
                hitl_condition="total > 500000",
                grant_token="grant-sentinel-restart-0001",
                thread_id=thread_id,
            )
        assert result["status"] == "hitl_triggered", result
        async with get_tenant_session(tenant_id) as session:
            row = HITLQueue(
                tenant_id=tenant_id,
                agent_id=agent_id,
                workflow_run_id=None,
                title="HITL: ap_processor - total > 500000",
                trigger_type="policy_condition",
                priority="normal",
                status="pending",
                assignee_role="finance",
                decision_options={"options": ["approve", "reject"]},
                context={"output": result["output"]},
                expires_at=datetime.now(UTC) + timedelta(hours=4),
                checkpoint_thread_id=result["thread_id"],
            )
            session.add(row)
            await session.flush()
            hitl_id = row.id
        return result, hitl_id
    finally:
        # "Process exit": the pool, saver and graph of this process are gone.
        await cp.close_checkpointer()


def test_interrupted_run_resumes_to_completion_after_a_process_restart(
    store_env: dict[str, str], scripted_model: Any
) -> None:
    tenant_a, tenant_b, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _seed(tenant_a, agent_id)
    _seed(tenant_b)
    model = scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])
    thread_id: str | None = None
    try:
        paused, hitl_id = _run(_pause_run(tenant_a, agent_id))
        thread_id = paused["thread_id"]
        assert thread_belongs_to_tenant(thread_id, tenant_a)
        assert model.remaining == 0
        assert cp._postgres_store is None

        with _sync_conn() as conn:
            checkpoints = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,)).fetchone()
        assert checkpoints and checkpoints[0] > 0

        # Tenant B, in its own fresh process, cannot reach A's approval row.
        denied = _child(store_env, tenant_b, hitl_id, {"action": "approve"})
        assert denied["outcome"] == "approval_not_found"

        resumed = _child(store_env, tenant_a, hitl_id, {"action": "approve"})
        assert resumed["outcome"] == "resumed"
        assert resumed["pid"] != os.getpid()
        result = resumed["result"]
        assert result["status"] == "completed", result
        assert result["output"]["total"] == 750000
        assert any("HITL decision" in step and "approve" in step for step in result["reasoning_trace"])

        # The thread is finished: nothing is left waiting at the approval gate.
        async def _state() -> Any:
            from core.langgraph.agent_graph import build_agent_graph

            try:
                graph = build_agent_graph(
                    system_prompt="scripted", authorized_tools=[], confidence_floor=0.5, hitl_condition="total > 500000"
                )
                compiled = graph.compile(checkpointer=await cp.get_checkpointer())
                return await compiled.aget_state({"configurable": {"thread_id": thread_id}})
            finally:
                await cp.close_checkpointer()

        final_state = _run(_state())
        assert final_state.next == ()
        assert final_state.values["status"] == "completed"
    finally:
        _cleanup([tenant_a, tenant_b], thread_id)


def test_hitl_row_cannot_hold_another_tenants_thread(store_env: dict[str, str]) -> None:
    import psycopg

    tenant_a, tenant_b, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _seed(tenant_a)
    _seed(tenant_b, agent_id)
    try:
        with _sync_conn() as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO hitl_queue (id, tenant_id, agent_id, title, trigger_type, priority, status, "
                "assignee_role, decision_options, context, expires_at, checkpoint_thread_id) "
                "VALUES (%s, %s, %s, 't', 'policy_condition', 'normal', 'pending', 'finance', '{}'::jsonb, "
                "'{}'::jsonb, now() + interval '1 hour', %s)",
                (uuid.uuid4(), tenant_b, agent_id, new_thread_id(tenant_a)),
            )
    finally:
        _cleanup([tenant_a, tenant_b], None)
