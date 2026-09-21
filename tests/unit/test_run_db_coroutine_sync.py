# SPDX-License-Identifier: Apache-2.0
"""`core.database.run_db_coroutine_sync`: a private engine per synchronous call.

The database side (a real pool, poisoned or not) is in
tests/integration/test_sync_credential_resolution_pool.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.pool import NullPool

import core.database as db_mod


def test_it_refuses_to_run_inside_a_running_event_loop() -> None:
    async def _caller() -> None:
        db_mod.run_db_coroutine_sync(lambda: asyncio.sleep(0))

    with pytest.raises(RuntimeError, match="cannot be called from a running event loop"):
        asyncio.run(_caller())


def test_the_coroutine_sees_a_private_null_pool_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []
    disposed: list[object] = []

    class _FakeEngine:
        def __init__(self, url: str, **kwargs: Any) -> None:
            self.url = url
            self.kwargs = kwargs

        async def dispose(self) -> None:
            disposed.append(self)

    def _create_async_engine(url: str, **kwargs: Any) -> _FakeEngine:
        engine = _FakeEngine(url, **kwargs)
        created.append({"url": url, **kwargs})
        return engine

    def _async_sessionmaker(engine: Any, **kwargs: Any) -> Any:
        return f"factory-for-{id(engine)}"

    monkeypatch.setattr(db_mod, "create_async_engine", _create_async_engine)
    monkeypatch.setattr(db_mod, "async_sessionmaker", _async_sessionmaker)

    seen: list[Any] = []

    async def _work() -> str:
        seen.append(db_mod.current_session_factory())
        return "done"

    assert db_mod.run_db_coroutine_sync(_work) == "done"

    assert len(created) == 1
    # Built from the engine in place, not from settings, so a replaced engine
    # (a test fixture, a second database) is followed.
    assert created[0]["url"] == db_mod.engine.url.render_as_string(hide_password=False)
    assert created[0]["echo"] == db_mod.engine.echo
    assert created[0]["poolclass"] is NullPool
    # The coroutine used the private factory, not the shared one.
    assert seen and seen[0] != db_mod.async_session_factory
    assert len(disposed) == 1


def test_the_override_is_cleared_and_the_engine_disposed_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed: list[object] = []

    class _FakeEngine:
        async def dispose(self) -> None:
            disposed.append(self)

    monkeypatch.setattr(db_mod, "create_async_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr(db_mod, "async_sessionmaker", lambda *a, **k: "private-factory")

    async def _boom() -> None:
        db_mod.current_session_factory()  # builds the private engine
        raise ValueError("the caller's error, not ours")

    with pytest.raises(ValueError, match="the caller's error"):
        db_mod.run_db_coroutine_sync(_boom)

    assert len(disposed) == 1
    assert db_mod.current_session_factory() is db_mod.async_session_factory


def test_outside_a_sync_call_the_shared_factory_is_used() -> None:
    assert db_mod.current_session_factory() is db_mod.async_session_factory


def test_no_engine_is_built_for_a_coroutine_that_opens_no_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thunk that never touches the database must not pay for a connection."""
    created: list[Any] = []

    def _create_async_engine(*args: Any, **kwargs: Any) -> Any:
        created.append(kwargs)
        raise AssertionError("no engine should be built for this coroutine")

    monkeypatch.setattr(db_mod, "create_async_engine", _create_async_engine)

    async def _no_database() -> str:
        return "nothing to do"

    assert db_mod.run_db_coroutine_sync(_no_database) == "nothing to do"
    assert created == []
