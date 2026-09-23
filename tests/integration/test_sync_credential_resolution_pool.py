# SPDX-License-Identifier: Apache-2.0
"""A synchronous database bridge must not poison the shared connection pool.

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
    db_mod.install_cross_loop_guard(engine)  # as on the shared engine
    factory = async_sessionmaker(engine, class_=db_mod.AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "_shared_session_factory", factory)
    # Wrapped as the module-level one is, so a direct binder is checked here too.
    monkeypatch.setattr(
        db_mod, "async_session_factory", db_mod._GuardedSessionFactory(factory, engine)
    )
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


async def test_the_report_generator_bridge_does_not_poison_the_pool(
    shared_pool: AsyncEngine,
) -> None:
    """The same bridge in `core/reports/generator.py`.

    `_run_coroutine` takes its helper-thread branch when the caller already
    has a running loop, which is what happens when the sandbox pilot runs the
    report task body inside its own loop
    (`core/marketing/weekly_report_sandbox_pilot.py` -> `generate_report.run`),
    and the coroutine it runs (`api.v1.kpis._build_kpi_response`) opens tenant
    sessions. Before the fix that branch submitted `asyncio.run` to the
    thread, which borrowed from and returned to the shared pool with no
    context propagation.
    """
    from core.database import get_tenant_session
    from core.reports.generator import _run_coroutine

    tenant_id = uuid.uuid4()
    assert shared_pool.pool.checkedin() == 0, "the pool must be cold for this test"

    async def _reads_the_database() -> int:
        async with get_tenant_session(tenant_id) as session:
            return int((await session.execute(text("SELECT 1"))).scalar_one())

    # Called straight from this coroutine: the bridge sees a running loop in
    # its own thread, exactly as the sandbox pilot leaves it.
    assert _run_coroutine(_reads_the_database) == 1

    assert shared_pool.pool.checkedin() == 0
    assert await _unrelated_request(tenant_id) == 1


async def test_the_guard_refuses_the_shared_pool_on_a_foreign_loop(
    shared_pool: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structural half: a bridge that reaches the shared pool fails loudly.

    A synchronous caller that runs `asyncio.run` against the shared engine —
    the next careless `asyncio.to_thread` under `api/` — gets a named error at
    the moment of the mistake instead of `'NoneType' object has no attribute
    'send'` in whatever request checks that connection out later.
    """
    import core.database as db_mod

    monkeypatch.setattr(db_mod, "_guard_mode_value", "raise")
    tenant_id = uuid.uuid4()
    await _unrelated_request(tenant_id)  # binds the pool to this loop

    def _use_the_shared_engine_on_another_loop() -> None:
        asyncio.run(_unrelated_request(tenant_id))

    with pytest.raises(db_mod.CrossLoopConnectionError, match="second event loop"):
        await asyncio.to_thread(_use_the_shared_engine_on_another_loop)

    # The mistake cost the pool nothing: this loop's request still works.
    assert await _unrelated_request(tenant_id) == 1


async def test_the_guard_only_reports_by_default(
    shared_pool: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warn is the default: the guard reports, and the outcome is unchanged."""
    import core.database as db_mod

    tenant_id = uuid.uuid4()
    await _unrelated_request(tenant_id)
    assert db_mod.cross_loop_guard_mode() == "warn"

    def _use_the_shared_engine_on_another_loop() -> None:
        asyncio.run(_unrelated_request(tenant_id))

    # Warn mode lets it through, and it then fails the way it always did: the
    # guard adds a log line and a counter, never a different outcome.
    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - asyncpg's own failure
        await asyncio.to_thread(_use_the_shared_engine_on_another_loop)
    assert not isinstance(exc_info.value, db_mod.CrossLoopConnectionError)


async def test_the_guard_sees_a_warm_pooled_connection_reused_on_another_loop(
    shared_pool: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The masked failure: a warm connection, handed out with nothing opened.

    A caller that binds `async_session_factory` itself — as the modules in
    FINDINGS A-53 do — is handed the connection this loop left in the pool, so
    no `connect` event fires. The pool's own `checkout` event does see this
    case, but not always: `pool_pre_ping` sends its ping on the foreign loop
    and can fail before the listener is reached. The factory is wrapped for
    exactly that, and it is the wrapper that raises here.
    """
    import core.database as db_mod

    tenant_id = uuid.uuid4()
    await _unrelated_request(tenant_id)  # binds the pool and leaves a connection
    assert shared_pool.pool.checkedin() == 1
    monkeypatch.setattr(db_mod, "_guard_mode_value", "raise")

    def _reuse_the_warm_connection() -> None:
        async def _bypass_the_seam() -> None:
            async with db_mod.async_session_factory() as session:
                await session.execute(text("SELECT 1"))

        asyncio.run(_bypass_the_seam())

    with pytest.raises(db_mod.CrossLoopConnectionError, match="opening a session"):
        await asyncio.to_thread(_reuse_the_warm_connection)
