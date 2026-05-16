from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from workflows.event_waits import (
    InMemoryWorkflowEventWaitRepository,
    WorkflowEventWaitStore,
)


def _store(redis: AsyncMock | None = None) -> tuple[
    WorkflowEventWaitStore,
    InMemoryWorkflowEventWaitRepository,
]:
    repo = InMemoryWorkflowEventWaitRepository()
    return WorkflowEventWaitStore(repository=repo, redis=redis), repo


@pytest.mark.asyncio
async def test_event_wait_registration_persists_to_durable_repository() -> None:
    redis = AsyncMock()
    redis.set.side_effect = ConnectionError("redis unavailable")
    store, repo = _store(redis)

    await store.register(
        engine_run_id="run-1",
        step_id="wait-1",
        event_type="email.opened",
        match_criteria={"campaign_id": "camp-1", "email": "u@example.com"},
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
    )

    record = repo.records[("run-1", "wait-1")]
    assert record.status == "waiting"
    assert record.event_type == "email.opened"
    assert record.match_criteria["campaign_id"] == "camp-1"


@pytest.mark.asyncio
async def test_redis_flush_after_registration_does_not_prevent_event_matching() -> None:
    redis = AsyncMock()
    store, _repo = _store(redis)
    await store.register(
        engine_run_id="run-2",
        step_id="wait-1",
        event_type="email.opened",
        match_criteria={"campaign_id": "camp-2"},
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
    )
    store.redis = None

    matched = await store.claim_matching_waits(
        event_types=["email.opened"],
        event_fields={"campaign_id": "camp-2", "email": "u@example.com"},
        matched_event_id="evt-1",
    )

    assert [(m.engine_run_id, m.step_id) for m in matched] == [("run-2", "wait-1")]


@pytest.mark.asyncio
async def test_duplicate_event_delivery_claims_workflow_once() -> None:
    store, _repo = _store()
    await store.register(
        engine_run_id="run-dup",
        step_id="wait-1",
        event_type="email.clicked",
        match_criteria={"campaign_id": "camp-dup"},
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
    )

    first = await store.claim_matching_waits(
        event_types=["email.clicked"],
        event_fields={"campaign_id": "camp-dup"},
        matched_event_id="evt-dup",
    )
    second = await store.claim_matching_waits(
        event_types=["email.clicked"],
        event_fields={"campaign_id": "camp-dup"},
        matched_event_id="evt-dup",
    )

    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_event_timeout_marks_listener_timed_out_without_clobbering_matched() -> None:
    store, repo = _store()
    await store.register(
        engine_run_id="run-timeout",
        step_id="wait-timeout",
        event_type="email.opened",
    )
    timed_out = await store.mark_timed_out(
        engine_run_id="run-timeout",
        step_id="wait-timeout",
    )

    await store.register(
        engine_run_id="run-matched",
        step_id="wait-matched",
        event_type="email.opened",
    )
    await store.claim_matching_waits(
        event_types=["email.opened"],
        event_fields={},
        matched_event_id="evt-matched",
    )
    matched_after_timeout = await store.mark_timed_out(
        engine_run_id="run-matched",
        step_id="wait-matched",
    )

    assert timed_out is not None
    assert timed_out.status == "timed_out"
    assert repo.records[("run-timeout", "wait-timeout")].status == "timed_out"
    assert matched_after_timeout is not None
    assert matched_after_timeout.status == "matched"
    assert repo.records[("run-matched", "wait-matched")].status == "matched"


@pytest.mark.asyncio
async def test_tenant_mismatch_does_not_resume_other_tenant_workflow() -> None:
    store, _repo = _store()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await store.register(
        engine_run_id="run-tenant-a",
        step_id="wait-1",
        event_type="email.opened",
        tenant_id=tenant_a,
    )

    matched = await store.claim_matching_waits(
        event_types=["email.opened"],
        event_fields={"tenant_id": str(tenant_b)},
        tenant_id=tenant_b,
        matched_event_id="evt-tenant",
    )

    assert matched == []


@pytest.mark.asyncio
async def test_webhook_event_matching_uses_durable_store_when_redis_unavailable() -> None:
    from api.v1 import webhooks

    redis = AsyncMock()
    redis.hset.side_effect = ConnectionError("redis unavailable")
    redis.aclose = AsyncMock()
    store, _repo = _store()
    tenant_id = uuid.uuid4()
    await store.register(
        engine_run_id="run-webhook",
        step_id="wait-1",
        event_type="email.opened",
        tenant_id=tenant_id,
        match_criteria={"campaign_id": "camp-web", "email": "u@example.com"},
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with patch("api.v1.webhooks._get_redis", return_value=redis), patch(
        "api.v1.webhooks._workflow_event_wait_store",
        return_value=store,
    ), patch("core.tasks.workflow_tasks.resume_workflow_wait.delay") as delay:
        await webhooks._store_email_event(
            campaign_id="camp-web",
            email="u@example.com",
            event_type="open",
            timestamp=1,
            extra={"tenant_id": str(tenant_id)},
        )
        await webhooks._store_email_event(
            campaign_id="camp-web",
            email="u@example.com",
            event_type="open",
            timestamp=1,
            extra={"tenant_id": str(tenant_id)},
        )

    delay.assert_called_once_with("run-webhook", "wait-1")


@pytest.mark.asyncio
async def test_celery_event_timeout_marks_durable_listener_timed_out() -> None:
    from core.tasks import workflow_tasks
    from workflows.state_store import InMemoryWorkflowStateRepository, WorkflowStateStore

    state_repo = InMemoryWorkflowStateRepository()
    state_store = WorkflowStateStore(repository=state_repo, redis=AsyncMock())
    state_store.init = AsyncMock()
    state_store.close = AsyncMock()
    waiting_state = {
        "id": "run-timeout-task",
        "status": "waiting_event",
        "waiting_step_id": "wait-1",
        "definition": {"steps": []},
        "step_results": {},
        "steps_completed": 0,
    }
    await state_store.save(waiting_state)

    event_store, event_repo = _store(AsyncMock())
    await event_store.register(
        engine_run_id="run-timeout-task",
        step_id="wait-1",
        event_type="email.opened",
    )

    with patch.object(workflow_tasks, "_state_store", return_value=state_store), patch.object(
        workflow_tasks,
        "_event_wait_store",
        return_value=event_store,
    ):
        result = await workflow_tasks._timeout_workflow_event_async(
            "run-timeout-task",
            "wait-1",
        )

    assert result["status"] == "timed_out"
    assert event_repo.records[("run-timeout-task", "wait-1")].status == "timed_out"
    durable_state = state_repo.states["run-timeout-task"]["state"]
    assert durable_state["status"] == "running"
    assert durable_state["step_results"]["wait-1"]["status"] == "timed_out"
