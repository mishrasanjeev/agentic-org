"""Parallel step executor with wait_for policies."""
from __future__ import annotations
import asyncio
from typing import Any, Callable, Coroutine

async def execute_parallel(
    tasks: list[Callable[[], Coroutine]], wait_for: str = "all", n: int = 1,
) -> list[Any]:
    if wait_for == "all":
        return await asyncio.gather(*[t() for t in tasks], return_exceptions=True)
    elif wait_for == "any":
        done, pending = await asyncio.wait(
            [asyncio.create_task(t()) for t in tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
        return [d.result() for d in done]
    else:
        count = int(wait_for) if wait_for.isdigit() else n
        results = []
        coros = [asyncio.create_task(t()) for t in tasks]
        for coro in asyncio.as_completed(coros):
            results.append(await coro)
            if len(results) >= count:
                for c in coros:
                    c.cancel()
                break
        return results
