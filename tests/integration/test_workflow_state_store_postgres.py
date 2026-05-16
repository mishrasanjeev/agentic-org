from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from core.models.workflow import WorkflowRunState, WorkflowStateTransition
from workflows.state_store import SqlAlchemyWorkflowStateRepository, WorkflowStateStore


async def _isolated_session_factory(db_engine: AsyncEngine):
    import core.models  # noqa: F401 - registers all ORM models
    from core.models.base import BaseModel as ORMBase

    engine = create_async_engine(
        db_engine.url.render_as_string(hide_password=False),
        echo=False,
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_workflow_state_store_writes_and_reads_postgres_source_of_truth(db_engine) -> None:
    engine, session_factory = await _isolated_session_factory(db_engine)
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
        await engine.dispose()
