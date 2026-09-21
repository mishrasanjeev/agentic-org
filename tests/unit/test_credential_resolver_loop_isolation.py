# SPDX-License-Identifier: Apache-2.0
"""A credential read on a throwaway event loop must not touch the shared connection pool (A-49).

``get_provider_credential_sync`` runs the async resolver on a loop it creates and then closes -
directly, or in a worker thread when the caller is already inside a loop. The resolver opens
database sessions (the tenant's credential, its fallback policy, the ``last_used_at`` stamp). On
the application's shared engine those sessions take pooled asyncpg connections, which belong to
the loop that opened them; returned to the pool and handed to the next unrelated request, they
fail with "got Future attached to a different loop", and ``pool_pre_ping`` does not treat that as
a disconnect. These tests pin that every such session runs on a private, unpooled engine instead.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core import database
from core.ai_providers import resolver


async def test_the_shared_session_factory_is_used_outside_a_private_scope() -> None:
    assert database.session_factory() is database.async_session_factory


async def test_a_private_scope_binds_sessions_to_its_own_unpooled_engine() -> None:
    shared = database.engine
    async with database.private_engine_scope() as private:
        assert private is not shared
        assert private.pool.__class__.__name__ == "NullPool"
        maker = database.session_factory()
        assert maker is not database.async_session_factory
        assert maker.kw["bind"] is private
    # The scope is over: the shared factory is back and the private engine is disposed.
    assert database.session_factory() is database.async_session_factory


async def test_a_private_scope_is_restored_even_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError):
        async with database.private_engine_scope():
            raise RuntimeError("boom")
    assert database.session_factory() is database.async_session_factory


def _record_engines(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture the engine each resolver call would open sessions on."""
    seen: list[Any] = []

    async def fake_resolve(*_args: Any, **_kwargs: Any) -> str:
        seen.append(database.session_factory().kw["bind"])
        return "resolved"

    monkeypatch.setattr(resolver, "get_provider_credential", fake_resolve)
    return seen


def test_the_sync_wrapper_reads_on_a_private_engine_when_there_is_no_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_engines(monkeypatch)
    assert resolver.get_provider_credential_sync(None, "gemini") == "resolved"
    assert seen and all(bind is not database.engine for bind in seen)


async def test_the_sync_wrapper_reads_on_a_private_engine_from_inside_a_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_engines(monkeypatch)
    result = await asyncio.to_thread(resolver.get_provider_credential_sync, None, "gemini")
    assert result == "resolved"
    assert seen and all(bind is not database.engine for bind in seen)


async def test_a_session_opened_in_a_worker_loop_never_comes_from_the_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression itself: the worker loop's sessions must not be able to reach the shared pool."""
    binds: list[Any] = []

    async def fake_resolve(*_args: Any, **_kwargs: Any) -> str:
        # Exactly what the real resolver does: open a tenant session and a raw one.
        binds.append(database.session_factory().kw["bind"])
        async with database.private_engine_scope() as nested:
            # A nested scope still never reaches the shared engine.
            binds.append(nested)
        binds.append(database.session_factory().kw["bind"])
        return "resolved"

    monkeypatch.setattr(resolver, "get_provider_credential", fake_resolve)
    await asyncio.to_thread(resolver.get_provider_credential_sync, None, "gemini")
    assert binds and database.engine not in binds
