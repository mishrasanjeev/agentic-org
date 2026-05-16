"""Parallel step executor with wait_for policies."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from workflows.step_results import normalize_child_result


async def execute_parallel(
    tasks: list[Callable[[], Coroutine]],
    wait_for: str = "all",
    n: int = 1,
) -> list[dict[str, Any]]:
    async def _run_one(index: int, task_factory: Callable[[], Coroutine]) -> dict[str, Any]:
        try:
            value = await task_factory()
        except Exception as exc:
            return normalize_child_result(exc, child_id=str(index))
        return normalize_child_result(value, child_id=str(index))

    if wait_for == "all":
        return await asyncio.gather(
            *[_run_one(index, task) for index, task in enumerate(tasks)],
            return_exceptions=False,
        )
    elif wait_for == "any":
        running = [
            asyncio.create_task(_run_one(index, task))
            for index, task in enumerate(tasks)
        ]
        results: list[dict[str, Any]] = []
        pending = set(running)
        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                result = task.result()
                results.append(result)
                if result.get("status") == "completed":
                    for p in pending:
                        p.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return results
        return results
    else:
        count = int(wait_for) if wait_for.isdigit() else n
        results: list[dict[str, Any]] = []
        coros = [
            asyncio.create_task(_run_one(index, task))
            for index, task in enumerate(tasks)
        ]
        successes = 0
        for coro in asyncio.as_completed(coros):
            result = await coro
            results.append(result)
            if result.get("status") == "completed":
                successes += 1
            if successes >= count:
                for c in coros:
                    c.cancel()
                await asyncio.gather(*coros, return_exceptions=True)
                break
        return results
