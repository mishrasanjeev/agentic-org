from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from workflows.state_store import InMemoryWorkflowStateRepository, WorkflowStateStore


def _state(run_id: str = "run-1", status: str = "running") -> dict:
    return {
        "id": run_id,
        "status": status,
        "definition": {"steps": []},
        "step_results": {},
        "steps_completed": 0,
    }


@pytest.mark.asyncio
async def test_save_writes_durable_store_before_redis_cache() -> None:
    repo = InMemoryWorkflowStateRepository()
    redis = AsyncMock()

    async def _redis_set(*_args, **_kwargs):
        assert "run-1" in repo.states

    redis.set.side_effect = _redis_set
    store = WorkflowStateStore(repository=repo, redis=redis)

    await store.save(_state())

    assert repo.states["run-1"]["state"]["status"] == "running"
    assert repo.states["run-1"]["version"] == 1
    assert repo.transitions[-1]["new_state_hash"] == repo.states["run-1"]["state_hash"]
    redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_load_prefers_durable_store_over_redis_cache() -> None:
    repo = InMemoryWorkflowStateRepository()
    redis = AsyncMock()
    redis.get.return_value = json.dumps(_state(status="failed"))
    store = WorkflowStateStore(repository=repo, redis=redis)

    await store.save(_state(status="completed"))
    loaded = await store.load("run-1")

    assert loaded is not None
    assert loaded["status"] == "completed"
    redis.get.assert_not_called()


@pytest.mark.asyncio
async def test_load_survives_redis_flush_or_unavailable_cache() -> None:
    repo = InMemoryWorkflowStateRepository()
    redis = AsyncMock()
    redis.set.side_effect = ConnectionError("redis down")
    redis.get.side_effect = ConnectionError("redis down")
    store = WorkflowStateStore(repository=repo, redis=redis)

    await store.save(_state(status="waiting_event"))
    redis.get.reset_mock()

    loaded = await store.load("run-1")

    assert loaded is not None
    assert loaded["status"] == "waiting_event"
    redis.get.assert_not_called()


@pytest.mark.asyncio
async def test_redis_legacy_fallback_backfills_durable_store() -> None:
    repo = InMemoryWorkflowStateRepository()
    redis = AsyncMock()
    redis.get.return_value = json.dumps(_state(status="waiting_delay"))
    store = WorkflowStateStore(repository=repo, redis=redis)

    loaded = await store.load("run-1")

    assert loaded is not None
    assert loaded["status"] == "waiting_delay"
    assert repo.states["run-1"]["state"]["status"] == "waiting_delay"
    assert repo.transitions[-1]["actor"] == "redis_legacy_backfill"


@pytest.mark.asyncio
async def test_celery_wait_resume_updates_durable_state_not_redis_only() -> None:
    from core.tasks import workflow_tasks

    repo = InMemoryWorkflowStateRepository()
    redis = AsyncMock()
    store = WorkflowStateStore(repository=repo, redis=redis)
    store.init = AsyncMock()
    store.close = AsyncMock()

    waiting = _state("run-wait", "waiting_event")
    waiting["waiting_step_id"] = "wait-1"
    await store.save(waiting)

    with patch.object(workflow_tasks, "_state_store", return_value=store):
        result = await workflow_tasks._resume_workflow_wait_async("run-wait", "wait-1")

    assert result["status"] == "resumed"
    durable_state = repo.states["run-wait"]["state"]
    assert durable_state["status"] == "running"
    assert durable_state["step_results"]["wait-1"]["status"] == "completed"
    assert "waiting_step_id" not in durable_state
    assert repo.transitions[-1]["actor"] == "celery.resume_workflow_wait"
    assert repo.transitions[-1]["step_id"] == "wait-1"
