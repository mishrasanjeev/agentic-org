"""Regression coverage for Celery's sync-to-async runtime bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.tasks import async_runner
from core.tasks.async_runner import run_async


@pytest.fixture
def worker_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the process in the state a Celery worker is in.

    `run_async` keeps its persistent loop only for a worker process; in an API
    process it runs the work on a private engine instead, because the shared
    pool there belongs to the server's loop (FINDINGS A-54). These tests are
    about the worker behaviour, so they say so explicitly.
    """
    monkeypatch.setattr(async_runner, "_worker_process_pid", None)
    monkeypatch.delenv(async_runner.WORKER_PROCESS_ENV, raising=False)
    async_runner.mark_worker_process()


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


def test_run_async_reuses_one_loop_for_successive_celery_tasks(worker_process: None) -> None:
    resource = _LoopBoundResource()

    first = run_async(resource.use())
    second = run_async(resource.use())

    assert first is second
    assert not first.is_closed()


def test_run_async_propagates_failure_and_keeps_loop_usable(worker_process: None) -> None:
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


def test_outside_a_worker_each_call_gets_its_own_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: an API process must not reuse the runner loop.

    Its connections would go into the shared pool bound to a loop no request
    runs on, which is what FINDINGS A-54 described.
    """
    monkeypatch.setattr(async_runner, "_worker_process_pid", None)
    monkeypatch.delenv(async_runner.WORKER_PROCESS_ENV, raising=False)

    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    first = run_async(loop_id())
    second = run_async(loop_id())
    assert first != second
