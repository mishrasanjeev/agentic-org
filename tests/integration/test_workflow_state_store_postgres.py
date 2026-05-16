from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.models.workflow import WorkflowRunState, WorkflowStateTransition
from workflows.state_store import SqlAlchemyWorkflowStateRepository, WorkflowStateStore


@pytest.mark.asyncio
async def test_workflow_state_store_writes_and_reads_postgres_source_of_truth(db_engine) -> None:
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    repo = SqlAlchemyWorkflowStateRepository(session_factory)
    redis = AsyncMock()
    redis.set.side_effect = ConnectionError("redis unavailable")
    store = WorkflowStateStore(repository=repo, redis=redis)
    run_id = f"wfr_test_{uuid.uuid4().hex[:12]}"
    state = {
        "id": run_id,
        "status": "waiting_event",
        "waiting_step_id": "wait-1",
        "definition": {"steps": []},
        "step_results": {},
    }

    try:
        await store.save(state, actor="test", step_id="wait-1")

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(WorkflowRunState).where(WorkflowRunState.run_id == run_id)
                )
            ).scalar_one()
            transition = (
                await session.execute(
                    select(WorkflowStateTransition).where(
                        WorkflowStateTransition.run_id == run_id
                    )
                )
            ).scalar_one()

        assert row.status == "waiting_event"
        assert row.waiting_step_id == "wait-1"
        assert row.state["id"] == run_id
        assert transition.actor == "test"
        assert transition.step_id == "wait-1"

        loaded = await store.load(run_id)
        assert loaded == state
    finally:
        async with session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(WorkflowRunState).where(WorkflowRunState.run_id == run_id)
                    )
                ).scalar_one_or_none()
                if row is not None:
                    await session.delete(row)
