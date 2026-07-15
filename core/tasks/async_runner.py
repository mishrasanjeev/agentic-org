"""Fork-safe async bridge for synchronous Celery task entry points.

Celery's prefork workers execute task bodies synchronously while the database
and several task implementations are asynchronous. Creating a new event loop
with :func:`asyncio.run` for every task is unsafe with SQLAlchemy's pooled
asyncpg engine: pooled connections stay bound to the loop that created them
and fail when a later task tries to use them from a new loop.

Keep one event loop per worker process instead. The PID guard prevents reuse
of a parent-process loop after ``fork()``, and the lock serializes eager or
threaded calls as well as normal prefork task execution.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable

_runner_lock = threading.RLock()
_runner_loop: asyncio.AbstractEventLoop | None = None
_runner_pid: int | None = None


def _loop_for_current_process() -> asyncio.AbstractEventLoop:
    global _runner_loop, _runner_pid

    pid = os.getpid()
    if _runner_loop is None or _runner_loop.is_closed() or _runner_pid != pid:
        # Never reuse an event loop inherited from another PID. Replacing the
        # child-local reference is sufficient; parent resources are untouched.
        _runner_loop = asyncio.new_event_loop()
        _runner_pid = pid
    return _runner_loop


def run_async[T](awaitable: Awaitable[T]) -> T:
    """Run an awaitable on the persistent loop for this worker process."""

    with _runner_lock:
        loop = _loop_for_current_process()
        if loop.is_running():
            raise RuntimeError(
                "run_async cannot be called from inside the Celery async runner loop"
            )
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(awaitable)
