"""Bounded admission control for resource-intensive local runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


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

    @property
    def snapshot(self) -> CapacitySnapshot:
        return CapacitySnapshot(limit=self.limit, active=self._active, waiting=self._waiting)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Acquire one execution slot or fail without starting the workload."""
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
        try:
            yield
        finally:
            self._active -= 1
            self._semaphore.release()
