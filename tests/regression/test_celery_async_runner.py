"""Regression coverage for Celery's sync-to-async runtime bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.tasks.async_runner import run_async


class _LoopBoundResource:
    """Model the loop affinity enforced by asyncpg pooled connections."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None

    async def use(self) -> asyncio.AbstractEventLoop:
        running = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = running
        elif self.loop is not running:
            raise RuntimeError("Future attached to a different loop")
        return running


def test_run_async_reuses_one_loop_for_successive_celery_tasks() -> None:
    resource = _LoopBoundResource()

    first = run_async(resource.use())
    second = run_async(resource.use())

    assert first is second
    assert not first.is_closed()


def test_run_async_propagates_failure_and_keeps_loop_usable() -> None:
    async def fail() -> None:
        raise ValueError("task failed")

    async def recover() -> str:
        return "ok"

    with pytest.raises(ValueError, match="task failed"):
        run_async(fail())

    assert run_async(recover()) == "ok"


def test_celery_task_modules_do_not_create_per_invocation_event_loops() -> None:
    task_dir = Path(__file__).resolve().parents[2] / "core" / "tasks"
    offenders: list[str] = []
    for path in task_dir.glob("*.py"):
        if path.name == "async_runner.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "asyncio.run(" in source or "asyncio.new_event_loop(" in source:
            offenders.append(path.name)

    assert offenders == [], (
        "Celery task entry points must use core.tasks.async_runner.run_async; "
        f"per-task loops break pooled async resources: {offenders}"
    )
