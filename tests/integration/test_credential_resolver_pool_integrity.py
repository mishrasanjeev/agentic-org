# SPDX-License-Identifier: Apache-2.0
"""A-49 on PostgreSQL: a synchronous credential read must leave the shared pool usable.

``get_provider_credential_sync`` runs the async resolver on a loop it creates and closes. Before
the fix those sessions came from the application's shared, pooled engine, so an asyncpg connection
bound to the throwaway loop went back into the pool; the next unrelated request that checked it out
failed with "got Future attached to a different loop", and ``pool_pre_ping`` did not rescue it
because that error is not classified as a disconnect. This exercises the real engine against a real
database: several sync reads from inside a running loop, then ordinary queries that must all work.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from core.ai_providers.resolver import ProviderNotConfigured, get_provider_credential_sync
from core.database import async_session_factory, engine

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="integration tests require AGENTICORG_DB_URL"
)

TENANT = uuid.UUID("0c9f2a5e-0000-4000-8000-0000000000a9")


@pytest.fixture(autouse=True)
async def _fresh_pool() -> Any:
    """Start each test with an empty pool.

    Every test here runs on its own event loop while the engine is a module-level singleton, so a
    connection pooled by the previous test would belong to a loop that is already closed - a
    property of the test harness, not of the code under test. Disposing first means the only way a
    pooled connection can end up on the wrong loop is the behaviour these tests are about.
    """
    await engine.dispose()
    yield
    await engine.dispose()


async def _select_one() -> int:
    async with async_session_factory() as session:
        return int((await session.execute(text("SELECT 1"))).scalar_one())


def _read_credential() -> None:
    """A sync call site, as ``core/langgraph/llm_factory.py`` makes it. No credential is expected."""
    try:
        get_provider_credential_sync(TENANT, "gemini")
    except ProviderNotConfigured:
        pass


async def test_a_sync_credential_read_from_a_running_loop_leaves_the_shared_pool_usable() -> None:
    # Warm the pool so the connections the worker loops see are pooled ones.
    assert await _select_one() == 1
    assert await _select_one() == 1

    for _ in range(4):
        await asyncio.to_thread(_read_credential)
        # Every subsequent query runs on the application's loop, over the shared pool.
        for _ in range(3):
            assert await _select_one() == 1

    # Nothing in the pool is bound to a loop that no longer exists.
    async with engine.connect() as connection:
        assert int((await connection.execute(text("SELECT 1"))).scalar_one()) == 1


async def test_concurrent_sync_credential_reads_do_not_poison_each_other() -> None:
    await asyncio.gather(*(asyncio.to_thread(_read_credential) for _ in range(4)))
    results = await asyncio.gather(*(_select_one() for _ in range(6)))
    assert results == [1] * 6
