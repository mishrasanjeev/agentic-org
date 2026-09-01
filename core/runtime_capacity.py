"""Bounded admission control for resource-intensive local runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, TypeVar

_T = TypeVar("_T")


class CapacityLimitError(RuntimeError):
    """Raised when work cannot enter a bounded runtime before its deadline."""

    def __init__(self, workload: str, timeout_seconds: float) -> None:
        super().__init__(f"{workload} capacity is busy; retry after queued work completes")
        self.workload = workload
        self.timeout_seconds = timeout_seconds


@dataclass(frozen=True)
class CapacitySnapshot:
    """Point-in-time state used by tests and operational diagnostics."""

    limit: int
    active: int
    waiting: int


class AsyncCapacityGate:
    """Limit concurrent expensive jobs and bound their queue wait."""

    def __init__(self, workload: str, *, limit: int, queue_timeout_seconds: float) -> None:
        if limit < 1:
            raise ValueError("capacity limit must be at least one")
        if queue_timeout_seconds <= 0:
            raise ValueError("queue timeout must be positive")
        self.workload = workload
        self.limit = limit
        self.queue_timeout_seconds = queue_timeout_seconds
        self._semaphore = asyncio.Semaphore(limit)
        self._active = 0
        self._waiting = 0
        self._draining_tasks: set[asyncio.Task[None]] = set()

    @property
    def snapshot(self) -> CapacitySnapshot:
        return CapacitySnapshot(limit=self.limit, active=self._active, waiting=self._waiting)

    async def _acquire(self) -> None:
        self._waiting += 1
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.queue_timeout_seconds,
                )
            except TimeoutError as exc:
                raise CapacityLimitError(
                    self.workload,
                    self.queue_timeout_seconds,
                ) from exc
        finally:
            self._waiting -= 1
        self._active += 1

    def _release(self) -> None:
        self._active -= 1
        self._semaphore.release()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Acquire one execution slot or fail without starting the workload."""
        await self._acquire()
        try:
            yield
        finally:
            self._release()

    async def run(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        """Run one async operation under this production capacity gate."""
        async with self.slot():
            return await operation()

    async def _release_when_done(self, task: asyncio.Task[Any]) -> None:
        with suppress(BaseException):
            await task
        self._release()

    async def run_blocking(
        self,
        operation: Callable[..., _T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Run blocking work without releasing capacity on request cancellation.

        Cancelling ``asyncio.to_thread`` only cancels the awaiter, not the worker
        thread. When a caller disconnects, keep the permit until that underlying
        thread exits so replacement requests cannot oversubscribe the process.
        """
        await self._acquire()
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        release_here = True
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                release_here = False
                draining = asyncio.create_task(self._release_when_done(task))
                self._draining_tasks.add(draining)
                draining.add_done_callback(self._draining_tasks.discard)
            raise
        finally:
            if release_here:
                self._release()
