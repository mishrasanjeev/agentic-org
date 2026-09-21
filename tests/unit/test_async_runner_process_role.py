# SPDX-License-Identifier: Apache-2.0
"""`run_async` picks its loop by the role of the process, and survives a fork.

A worker process uses one persistent loop, which is safe because it is the
only loop there. Anywhere else — an API process importing task code, a script,
a test — the work runs on a throwaway loop with a private database engine, so
the shared pool never receives a connection bound to a loop nothing runs
(FINDINGS A-54).
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from typing import Any

import pytest

from core.tasks import async_runner


@pytest.fixture(autouse=True)
def _unmarked_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test in a process that is not marked as a worker."""
    monkeypatch.setattr(async_runner, "_worker_process_pid", None)
    monkeypatch.delenv(async_runner.WORKER_PROCESS_ENV, raising=False)


def test_a_plain_process_is_not_a_worker() -> None:
    assert not async_runner.is_worker_process()


def test_marking_makes_this_process_a_worker() -> None:
    async_runner.mark_worker_process()
    assert async_runner.is_worker_process()


def test_the_mark_belongs_to_the_process_that_set_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forked child must not inherit its parent's mark."""
    async_runner.mark_worker_process()
    pretend_child_pid = os.getpid() + 1
    monkeypatch.setattr(os, "getpid", lambda: pretend_child_pid)
    assert not async_runner.is_worker_process()


def test_the_environment_variable_marks_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(async_runner.WORKER_PROCESS_ENV, "1")
    assert async_runner.is_worker_process()


def test_outside_a_worker_the_work_runs_through_the_private_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the change: no shared-pool loop outside a worker."""
    import core.database as db_mod

    calls: list[Any] = []

    def _fake_run_db_coroutine_sync(make_coroutine: Any) -> str:
        calls.append(make_coroutine)
        return asyncio.run(make_coroutine())

    monkeypatch.setattr(db_mod, "run_db_coroutine_sync", _fake_run_db_coroutine_sync)

    async def _work() -> str:
        return "done"

    assert async_runner.run_async(_work()) == "done"
    assert len(calls) == 1
    # And the persistent loop was never created.
    assert async_runner._runner_loop is None


def test_in_a_worker_the_persistent_loop_is_used_and_reused() -> None:
    async_runner.mark_worker_process()

    async def _loop_id() -> int:
        return id(asyncio.get_running_loop())

    first = async_runner.run_async(_loop_id())
    second = async_runner.run_async(_loop_id())
    assert first == second, "a worker process must reuse its loop across tasks"


def test_run_async_refuses_to_nest_inside_the_runner_loop() -> None:
    async_runner.mark_worker_process()

    async def _nested() -> None:
        async_runner.run_async(asyncio.sleep(0))

    with pytest.raises(RuntimeError, match="inside the Celery async runner loop"):
        async_runner.run_async(_nested())


_FORK_CHILD_PROGRAM = textwrap.dedent(
    """
    import asyncio, json, os, sys

    sys.path.insert(0, {repo!r})
    os.environ["AGENTICORG_WORKER_PROCESS"] = "1"
    os.environ.setdefault("AGENTICORG_SECRET_KEY", "fork-test-secret-key-minimum-16")
    from core.tasks import async_runner

    async def loop_id():
        return id(asyncio.get_running_loop())

    parent_loop = async_runner.run_async(loop_id())
    parent_pid = os.getpid()

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        child_loop = async_runner.run_async(loop_id())
        payload = json.dumps({{"loop": child_loop, "pid": os.getpid()}}).encode()
        with os.fdopen(write_fd, "wb") as handle:
            handle.write(payload)
        os._exit(0)

    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as handle:
        child = json.loads(handle.read().decode())
    os.waitpid(pid, 0)
    print(json.dumps({{"parent_loop": parent_loop, "parent_pid": parent_pid, **child}}))
    """
)


@pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="os.fork() is POSIX only; this runs on Linux CI, not on Windows or macOS spawn",
)
def test_a_forked_child_builds_its_own_loop(tmp_path: Any) -> None:
    """The PID guard: a child must not run tasks on the parent's loop.

    A loop inherited across ``fork()`` carries the parent's selector state and
    the parent's pooled connections; reusing it in the child corrupts both.
    """
    import json
    import subprocess

    repo_root = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    program = tmp_path / "fork_runner.py"
    program.write_text(_FORK_CHILD_PROGRAM.format(repo=repo_root), encoding="utf-8")

    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(program)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["pid"] != result["parent_pid"], "the child must be a separate process"
    assert result["loop"] != result["parent_loop"], (
        "the forked child reused the parent's event loop; the PID guard in "
        "core/tasks/async_runner.py is not working"
    )
