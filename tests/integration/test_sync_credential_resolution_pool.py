# SPDX-License-Identifier: Apache-2.0
"""A synchronous credential lookup must not poison the shared connection pool.

`get_provider_credential_sync` runs the async resolver on a fresh event loop
(in a worker thread when the caller is already async). Pooled asyncpg
connections stay bound to the loop that opened them, so a lookup that borrowed
the shared `QueuePool` left a connection behind that belongs to a loop nobody
runs any more. The next unrelated request to check that connection out fails,
and `pool_pre_ping` does not rescue it: the cross-loop `RuntimeError` is not a
disconnect, so SQLAlchemy does not recycle the connection and retry.

These tests hold the pool to a single connection so the reuse is deterministic
(FINDINGS A-49).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

DB_URL = os.getenv("AGENTICORG_DB_URL")

pytestmark = pytest.mark.skipif(not DB_URL, reason="needs AGENTICORG_DB_URL")


@pytest_asyncio.fixture
async def shared_pool(monkeypatch: pytest.MonkeyPatch, _setup_schema: None) -> AsyncIterator[AsyncEngine]:
    """Put a one-connection pooled engine in place of the shared one.

    `pool_size=1` with no overflow makes the next checkout reuse exactly the
    connection the previous caller returned, which is what a busy process does
    by chance and this test does on purpose.
    """
    assert DB_URL
    import core.database as db_mod

    # The production engine's pooling (AsyncAdaptedQueuePool via the default,
    # with pre-ping), held to one connection.
    engine = create_async_engine(
        DB_URL,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, class_=db_mod.AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _unrelated_request(tenant_id: uuid.UUID) -> int:
    """What any later request does: a tenant-scoped query on the shared pool."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        return int((await session.execute(text("SELECT 1"))).scalar_one())


@pytest.fixture
def resolve_sync() -> Callable[..., Any]:
    from core.ai_providers.resolver import ProviderNotConfigured, get_provider_credential_sync

    def _call(tenant_id: uuid.UUID) -> None:
        # The lookup itself is expected to find nothing; what matters is the
        # database connection it used on the way.
        try:
            get_provider_credential_sync(str(tenant_id), "openai", "llm")
        except ProviderNotConfigured:
            pass

    return _call


async def test_a_sync_lookup_does_not_poison_the_pool_for_the_next_request(
    shared_pool: AsyncEngine, resolve_sync: Callable[..., Any]
) -> None:
    """The cross-request damage: a later request on this loop must still work.

    With the pool cold, the sync lookup opens the connection itself on its own
    loop and hands it back. Before the fix this test failed here with
    ``AttributeError: 'NoneType' object has no attribute 'send'`` — asyncpg's
    protocol for a loop that is gone — and `pool_pre_ping` did not catch it.
    """
    tenant_id = uuid.uuid4()
    assert shared_pool.pool.checkedin() == 0, "the pool must be cold for this test"

    await asyncio.to_thread(resolve_sync, tenant_id)

    # Nothing of the lookup's is in the shared pool: it ran on an engine of
    # its own. Any direct use of `async_session_factory` on that path would
    # show up here as a connection this loop did not open.
    assert shared_pool.pool.checkedin() == 0

    assert await _unrelated_request(tenant_id) == 1
    assert await _unrelated_request(tenant_id) == 1


async def test_a_sync_lookup_without_a_running_loop_does_not_poison_the_pool(
    shared_pool: AsyncEngine, resolve_sync: Callable[..., Any]
) -> None:
    """The Celery shape: no running loop in the calling thread."""
    tenant_id = uuid.uuid4()

    def _call_without_a_running_loop() -> None:
        resolve_sync(tenant_id)

    await asyncio.to_thread(_call_without_a_running_loop)

    assert await _unrelated_request(tenant_id) == 1


async def test_a_sync_lookup_reports_the_real_outcome_on_a_warm_pool(
    shared_pool: AsyncEngine, resolve_sync: Callable[..., Any]
) -> None:
    """The masked failure the prefetch works around.

    With a connection already in the pool from this loop, the lookup used to
    borrow it, fail with "attached to a different loop", and report that as a
    credential that "could not be decrypted" — which also refuses the platform
    fallback. It must report what it actually found instead.
    """
    from core.ai_providers.resolver import ProviderNotConfigured

    tenant_id = uuid.uuid4()
    await _unrelated_request(tenant_id)  # leave a connection in the pool
    assert shared_pool.pool.checkedin() == 1

    outcome: dict[str, str] = {}

    def _call() -> None:
        from core.ai_providers.resolver import get_provider_credential_sync

        try:
            get_provider_credential_sync(str(tenant_id), "openai", "llm")
        except ProviderNotConfigured as exc:
            outcome["refused"] = str(exc)

    await asyncio.to_thread(_call)

    assert "refused" in outcome, "the tenant has no credential, so the lookup must refuse"
    assert "could not be decrypted" not in outcome["refused"], outcome["refused"]
    assert "No BYO credential" in outcome["refused"], outcome["refused"]
    assert await _unrelated_request(tenant_id) == 1


async def test_a_sync_lookup_gives_the_shared_pool_slot_back(
    shared_pool: AsyncEngine, resolve_sync: Callable[..., Any]
) -> None:
    """The private engine must not consume the shared pool's single slot."""
    tenant_id = uuid.uuid4()
    await _unrelated_request(tenant_id)
    await asyncio.to_thread(resolve_sync, tenant_id)

    # `pool_size=1, max_overflow=0`: this checkout can only succeed if the
    # sync path left the slot alone.
    async with shared_pool.connect() as connection:
        assert int((await connection.execute(text("SELECT 1"))).scalar_one()) == 1
