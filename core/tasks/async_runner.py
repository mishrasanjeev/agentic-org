"""Fork-safe async bridge for synchronous Celery task entry points.

Celery's prefork workers execute task bodies synchronously while the database
and several task implementations are asynchronous. Creating a new event loop
with :func:`asyncio.run` for every task is unsafe with SQLAlchemy's pooled
asyncpg engine: pooled connections stay bound to the loop that created them
and fail when a later task tries to use them from a new loop.

Keep one event loop per worker process instead. The PID guard prevents reuse
of a parent-process loop after ``fork()``, and the lock serializes eager or
threaded calls as well as normal prefork task execution.

That persistent loop is only safe where it is the process's *only* loop. In
an API process the shared engine's connections belong to the server's loop, so
running task code on the runner loop there would leave connections in the
shared pool bound to a loop no request runs on — the defect in FINDINGS A-49.
:func:`run_async` therefore decides by the **role of the process**, not by
which thread happens to call it: a worker process (marked by the Celery
``worker_process_init`` signal, or by ``AGENTICORG_WORKER_PROCESS=1``) uses the
persistent loop; anywhere else the work goes through
``core.database.run_db_coroutine_sync``, which gives it a private engine of
its own.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable

WORKER_PROCESS_ENV = "AGENTICORG_WORKER_PROCESS"
TRUTHY = frozenset({"1", "true", "yes", "on"})

_runner_lock = threading.RLock()
_runner_loop: asyncio.AbstractEventLoop | None = None
_runner_pid: int | None = None
_worker_process_pid: int | None = None


def mark_worker_process() -> None:
    """Record that this process is a Celery worker (or beat) process.

    Called from the Celery signals in ``core/tasks/celery_app.py``. The pid is
    stored rather than a flag so a forked child of an API process cannot
    inherit the mark.
    """
    global _worker_process_pid
    _worker_process_pid = os.getpid()


def is_worker_process() -> bool:
    """Whether this process runs Celery work, so the runner loop is its only loop."""
    if _worker_process_pid == os.getpid():
        return True
    return os.getenv(WORKER_PROCESS_ENV, "").strip().casefold() in TRUTHY


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
    """Run an awaitable on the loop this process should use.

    In a worker process that is the persistent runner loop. Elsewhere — an API
    process importing task code, a script, a test — it is a throwaway loop with
    a private database engine, so the shared pool never receives a connection
    bound to a loop that serves nothing.
    """
    if not is_worker_process():
        from core.database import run_db_coroutine_sync  # noqa: PLC0415 - avoids an import cycle

        return run_db_coroutine_sync(lambda: awaitable)

    with _runner_lock:
        loop = _loop_for_current_process()
        if loop.is_running():
            raise RuntimeError(
                "run_async cannot be called from inside the Celery async runner loop"
            )
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(awaitable)
