# SPDX-License-Identifier: Apache-2.0
"""Tenant-scoped checkpoint thread ids and their enforcement in the runner (PRD F-2)."""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Interrupt

from core.langgraph import runner
from core.langgraph import thread_ids as ti

TENANT_A = "0000000a-0000-4000-8000-00000000000a"
TENANT_B = "0000000b-0000-4000-8000-00000000000b"


def test_new_thread_id_is_tenant_prefixed_and_unguessable() -> None:
    ids = {ti.new_thread_id(TENANT_A) for _ in range(50)}
    assert len(ids) == 50
    for thread in ids:
        assert re.fullmatch(rf"tenant:{TENANT_A}:run:[0-9a-f]{{32}}", thread)
        assert ti.thread_tenant(thread) == TENANT_A
        assert ti.thread_belongs_to_tenant(thread, TENANT_A)
        assert ti.thread_belongs_to_tenant(thread, uuid.UUID(TENANT_A))
        assert not ti.thread_belongs_to_tenant(thread, TENANT_B)


def test_new_thread_id_canonicalises_the_tenant() -> None:
    assert ti.new_thread_id(TENANT_A.upper()).startswith(f"tenant:{TENANT_A}:run:")


@pytest.mark.parametrize("tenant", ["", None, "not-a-uuid", "tenant-a"])
def test_new_thread_id_refuses_an_invalid_tenant(tenant: str | None) -> None:
    with pytest.raises(ti.CheckpointThreadError) as exc_info:
        ti.new_thread_id(tenant)  # type: ignore[arg-type]
    assert exc_info.value.reason == "checkpoint_thread_tenant_invalid"


@pytest.mark.parametrize(
    "thread",
    [
        None,
        "",
        "run-1",
        f"tenant:{TENANT_A.upper()}:run:00",
        f"tenant:{TENANT_A}:",
        f"tenant:{TENANT_A}:run:x y",
        f" tenant:{TENANT_A}:run:00",
        f"tenant:{TENANT_A}:run:00\n",
    ],
)
def test_malformed_threads_belong_to_no_tenant(thread: str | None) -> None:
    assert ti.thread_tenant(thread) is None
    assert not ti.thread_belongs_to_tenant(thread, TENANT_A)


def test_scoped_thread_id_generates_keeps_and_namespaces() -> None:
    generated = ti.scoped_thread_id(TENANT_A, None)
    assert ti.thread_belongs_to_tenant(generated, TENANT_A)
    assert ti.scoped_thread_id(TENANT_A, generated) == generated
    assert ti.scoped_thread_id(TENANT_A, "voice:CA0001") == f"tenant:{TENANT_A}:voice:CA0001"


def test_scoped_thread_id_refuses_another_tenants_thread() -> None:
    foreign = ti.new_thread_id(TENANT_B)
    with pytest.raises(ti.CheckpointThreadError) as mismatch:
        ti.scoped_thread_id(TENANT_A, foreign)
    assert mismatch.value.reason == "checkpoint_thread_tenant_mismatch"
    # A malformed "tenant:" id is not re-namespaced into A either.
    with pytest.raises(ti.CheckpointThreadError):
        ti.scoped_thread_id(TENANT_A, "tenant:guess:run:00")
    with pytest.raises(ti.CheckpointThreadError) as invalid:
        ti.scoped_thread_id(TENANT_A, "voice call; drop")
    assert invalid.value.reason == "checkpoint_thread_id_invalid"


# ── Runner ─────────────────────────────────────────────────────────────────


def _paused_state() -> dict:
    return {
        "messages": [AIMessage(content="x")],
        "status": "completed",
        "output": {},
        "confidence": 0.4,
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
        "__interrupt__": [Interrupt(value={"type": "hitl_approval", "hitl_trigger": "confidence 0.400 < floor 0.88"})],
    }


async def _run(tenant_id: str, thread_id: str | None) -> tuple[dict, MagicMock]:
    compiled = MagicMock()
    compiled.ainvoke = AsyncMock(return_value=_paused_state())
    graph = MagicMock()
    graph.compile = MagicMock(return_value=compiled)
    with (
        patch.object(runner, "build_agent_graph", return_value=graph),
        patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        patch("core.billing.metering.gate_agent_run", new=AsyncMock(return_value=None)),
    ):
        result = await runner.run_agent(
            agent_id="agent-1",
            agent_type="t",
            domain="ops",
            tenant_id=tenant_id,
            system_prompt="s",
            authorized_tools=[],
            task_input={"action": "process", "inputs": {}, "context": {}},
            thread_id=thread_id,
        )
    return result, compiled


async def test_run_without_a_thread_checkpoints_under_a_generated_tenant_thread() -> None:
    result, compiled = await _run(TENANT_A, None)
    thread = compiled.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"]
    assert ti.thread_belongs_to_tenant(thread, TENANT_A)
    assert result["thread_id"] == thread


async def test_run_keeps_the_servers_thread_id() -> None:
    thread = ti.new_thread_id(TENANT_A)
    result, compiled = await _run(TENANT_A, thread)
    assert compiled.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"] == thread
    assert result["thread_id"] == thread


async def test_run_cannot_write_into_another_tenants_thread() -> None:
    with pytest.raises(ti.CheckpointThreadError) as exc_info:
        await _run(TENANT_A, ti.new_thread_id(TENANT_B))
    assert exc_info.value.reason == "checkpoint_thread_tenant_mismatch"


async def test_postgres_backend_refuses_a_run_without_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "configured_backend", lambda: "postgres")
    with pytest.raises(ti.CheckpointThreadError) as exc_info:
        await _run("", None)
    assert exc_info.value.reason == "checkpoint_thread_tenant_invalid"


async def _resume(tenant_id: str | None, thread_id: str) -> tuple[dict, MagicMock]:
    compiled = MagicMock()
    compiled.ainvoke = AsyncMock(return_value={"status": "completed", "messages": []})
    graph = MagicMock()
    graph.compile = MagicMock(return_value=compiled)
    with (
        patch.object(runner, "build_agent_graph", return_value=graph) as build,
        patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
    ):
        result = await runner.resume_agent(
            agent_id="agent-1",
            thread_id=thread_id,
            decision={"action": "approve"},
            system_prompt="s",
            authorized_tools=[],
            tenant_id=tenant_id,
        )
    compiled.build = build
    return result, compiled


async def test_resume_of_another_tenants_thread_is_refused_before_any_checkpoint_read() -> None:
    result, compiled = await _resume(TENANT_B, ti.new_thread_id(TENANT_A))
    assert result == {
        "status": "failed",
        "error": "checkpoint_thread_tenant_mismatch",
        "reason": "checkpoint_thread_tenant_mismatch",
    }
    compiled.build.assert_not_called()
    compiled.ainvoke.assert_not_awaited()


async def test_resume_with_a_guessed_suffix_under_the_callers_own_prefix_reads_only_the_callers_namespace() -> None:
    victim = ti.new_thread_id(TENANT_A)
    guessed = victim.replace(TENANT_A, TENANT_B)
    result, compiled = await _resume(TENANT_B, guessed)
    # Allowed through to the store, but only as B's own (different) thread id.
    assert compiled.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"] == guessed != victim
    assert result["status"] == "completed"


async def test_resume_of_an_unscoped_thread_is_refused_for_a_tenant() -> None:
    result, compiled = await _resume(TENANT_A, "thread-36")
    assert result["error"] == "checkpoint_thread_tenant_mismatch"
    compiled.ainvoke.assert_not_awaited()


async def test_postgres_backend_refuses_a_resume_without_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "configured_backend", lambda: "postgres")
    result, compiled = await _resume(None, ti.new_thread_id(TENANT_A))
    assert result["error"] == "checkpoint_thread_tenant_invalid"
    compiled.ainvoke.assert_not_awaited()


# ── Approval record ────────────────────────────────────────────────────────


def test_hitl_row_constraint_ties_the_thread_to_the_rows_tenant() -> None:
    from sqlalchemy import CheckConstraint

    from core.models.hitl import HITLQueue

    constraints = {c.name: str(c.sqltext) for c in HITLQueue.__table__.constraints if isinstance(c, CheckConstraint)}
    assert "starts_with(checkpoint_thread_id, 'tenant:' || tenant_id::text || ':')" in (
        constraints["ck_hitl_queue_checkpoint_thread_tenant"]
    )
    assert HITLQueue.__table__.c.checkpoint_thread_id.nullable


def test_approval_api_never_returns_the_checkpoint_thread() -> None:
    from api.v1.approvals import _hitl_to_dict

    item = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_run_id=None,
        agent_id=uuid.uuid4(),
        title="t",
        trigger_type="policy_condition",
        priority="normal",
        status="pending",
        assignee_role="finance",
        decision_options={},
        context={},
        decision=None,
        decision_by=None,
        requested_by_user_id=None,
        decision_at=None,
        decision_notes=None,
        expires_at=None,
        created_at=None,
        checkpoint_thread_id=ti.new_thread_id(TENANT_A),
    )
    rendered = _hitl_to_dict(item)  # type: ignore[arg-type]
    assert "checkpoint_thread_id" not in rendered
    assert item.checkpoint_thread_id not in repr(rendered)


def test_migration_adds_the_column_and_constraint() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z23_hitl_checkpoint_thread.py"
    spec = importlib.util.spec_from_file_location("v6_z23_hitl_checkpoint_thread", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "v6z23_hitl_checkpoint_thread" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z22_langgraph_checkpoints"

    executed: list[str] = []
    with patch.object(migration, "op", new=type("_Op", (), {"execute": staticmethod(executed.append)})):
        migration.upgrade()
    sql = executed[0]
    assert "ADD COLUMN IF NOT EXISTS checkpoint_thread_id VARCHAR(255)" in sql
    assert "starts_with(checkpoint_thread_id, 'tenant:' || tenant_id::text || ':')" in sql
    assert "NOT VALID" in sql and "VALIDATE CONSTRAINT ck_hitl_queue_checkpoint_thread_tenant" in sql
