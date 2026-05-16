from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from core.models.workflow import WorkflowEventWait
from workflows.event_waits import (
    SqlAlchemyWorkflowEventWaitRepository,
    WorkflowEventWaitStore,
)


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
async def test_workflow_event_wait_store_writes_and_claims_postgres_source_of_truth(
    db_engine,
) -> None:
    engine, session_factory = await _isolated_session_factory(db_engine)
    repo = SqlAlchemyWorkflowEventWaitRepository(session_factory)
    redis = AsyncMock()
    redis.set.side_effect = ConnectionError("redis unavailable")
    store = WorkflowEventWaitStore(repository=repo, redis=redis)
    run_id = f"wfr_evt_{uuid.uuid4().hex[:12]}"

    try:
        await store.register(
            engine_run_id=run_id,
            step_id="wait-1",
            event_type="email.opened",
            match_criteria={"campaign_id": "camp-pg"},
            timeout_at=datetime.now(UTC) + timedelta(hours=1),
        )

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(WorkflowEventWait).where(
                        WorkflowEventWait.engine_run_id == run_id
                    )
                )
            ).scalar_one()
        assert row.status == "waiting"
        assert row.match_criteria == {"campaign_id": "camp-pg"}

        store.redis = None
        matched = await store.claim_matching_waits(
            event_types=["email.opened"],
            event_fields={"campaign_id": "camp-pg"},
            matched_event_id="evt-pg",
        )
        duplicate = await store.claim_matching_waits(
            event_types=["email.opened"],
            event_fields={"campaign_id": "camp-pg"},
            matched_event_id="evt-pg",
        )

        assert [(m.engine_run_id, m.step_id) for m in matched] == [(run_id, "wait-1")]
        assert duplicate == []
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(WorkflowEventWait).where(
                        WorkflowEventWait.engine_run_id == run_id
                    )
                )
            ).scalar_one()
        assert row.status == "matched"
        assert row.matched_event_id == "evt-pg"
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(WorkflowEventWait).where(
                        WorkflowEventWait.engine_run_id == run_id
                    )
                )
        await engine.dispose()
